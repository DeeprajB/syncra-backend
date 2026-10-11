import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, WebSocket
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.models.auth.user import User
from app.realtime.manager import manager
from app.realtime.protocol import build_envelope
from app.realtime.tickets import tickets
from app.repositories import projects as projects_repository
from app.repositories import tasks as tasks_repository
from app.repositories import users as users_repository
from app.repositories import workspace_members as members_repository
from app.repositories import workspaces as workspaces_repository
from app.schemas.workspace_member import public_profile
from app.services.auth import sync_current_user
from app.services.workspace_access import require_workspace_member


logger = logging.getLogger(__name__)

router = APIRouter(tags=["Realtime"])

_AUTH_TIMEOUT_SECONDS = 5
_HEARTBEAT_SECONDS = 30
_MAX_MISSES = 3
_MAX_MESSAGE_BYTES = 8192
_RATE_LIMIT = 30
_RATE_WINDOW_SECONDS = 10
_TICKET_PROTOCOL = "syncra.ticket."


class TicketResponse(BaseModel):
    ticket: str
    expires_in: int


@router.post(
    "/api/v1/workspaces/{workspace_id}/ws-ticket",
    response_model=TicketResponse,
    summary="Issue a short-lived WebSocket ticket",
    description=(
        "Returns a single-use ticket for the signed-in member. "
        "The browser sends it as the first WebSocket message. "
        "Do not put the ticket in the socket URL."
    ),
)
async def issue_ws_ticket(
    workspace_id: str,
    current_user: User = Depends(sync_current_user),
) -> TicketResponse:
    access = await require_workspace_member(workspace_id, current_user)
    ticket, expires_in = tickets.issue(access.membership.user_id, access.workspace.id)
    return TicketResponse(ticket=ticket, expires_in=expires_in)


@router.websocket("/ws/workspaces/{workspace_id}")
async def workspace_events(websocket: WebSocket, workspace_id: str) -> None:
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if origin not in settings.allowed_ws_origins:
        await websocket.close(code=1008)
        return

    protocol = _offered_ticket_protocol(websocket)
    user_id = None
    if protocol is not None:
        user_id = await _redeem(protocol[len(_TICKET_PROTOCOL) :], workspace_id)
        if user_id is None:
            await websocket.close(code=1008)
            return
        await websocket.accept(subprotocol=protocol)
    else:
        await websocket.accept()
        user_id = await _authenticate_first_message(websocket, workspace_id)
        if user_id is None:
            return

    user = await users_repository.find_by_id(user_id)
    profile = public_profile(user, user_id)
    connection, first = manager.connect(
        workspace_id,
        user_id,
        websocket,
        profile["name"],
        profile["avatar_url"],
    )
    try:
        await websocket.send_json(
            _envelope("connection.ready", workspace_id, user_id, {"user_id": user_id})
        )
        await websocket.send_json(
            _envelope(
                "presence.snapshot",
                workspace_id,
                user_id,
                {"members": manager.snapshot(workspace_id)},
            )
        )
        if first:
            await manager.broadcast_event(
                _envelope("presence.joined", workspace_id, user_id, profile),
                exclude_connection_id=connection.connection_id,
            )
        await _receive_loop(websocket, workspace_id, connection.connection_id, user_id, profile)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Workspace websocket closed")
    finally:
        result = manager.disconnect(workspace_id, connection.connection_id)
        await manager.finish_disconnect(result)


def _offered_ticket_protocol(websocket: WebSocket) -> Optional[str]:
    for protocol in websocket.scope.get("subprotocols") or []:
        if protocol.startswith(_TICKET_PROTOCOL) and len(protocol) > len(_TICKET_PROTOCOL):
            return protocol
    return None


async def _redeem(ticket: str, workspace_id: str) -> Optional[str]:
    user_id = tickets.redeem(ticket, workspace_id)
    if user_id is None or not await _still_member(workspace_id, user_id):
        return None
    return user_id


async def _still_member(workspace_id: str, user_id: str) -> bool:
    workspace = await workspaces_repository.find_active_by_id(workspace_id)
    if workspace is None:
        return False
    membership = await members_repository.find_by_workspace_and_user(workspace.id, user_id)
    return membership is not None


async def _authenticate_first_message(websocket: WebSocket, workspace_id: str) -> Optional[str]:
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=_AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await _deny(websocket, workspace_id, "Authentication timed out")
        return None
    if message.get("type") == "websocket.disconnect":
        return None
    payload = _parse_payload(message.get("text"))
    ticket = None if payload is None else payload.get("ticket")
    if payload is None or payload.get("type") != "connection.auth" or not isinstance(ticket, str):
        await _deny(websocket, workspace_id, "Authentication required")
        return None
    user_id = await _redeem(ticket, workspace_id)
    if user_id is None:
        await _deny(websocket, workspace_id, "Authentication failed")
        return None
    return user_id


async def _receive_loop(
    websocket: WebSocket,
    workspace_id: str,
    connection_id: str,
    user_id: str,
    profile: Dict[str, str],
) -> None:
    misses = 0
    recent = []
    while True:
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=_HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            misses += 1
            if misses >= _MAX_MISSES:
                await websocket.close(code=1001)
                return
            await websocket.send_json(_envelope("connection.ping", workspace_id, user_id, {}))
            continue
        if message.get("type") == "websocket.disconnect":
            return
        misses = 0
        text = message.get("text")
        if not isinstance(text, str):
            await _socket_error(websocket, workspace_id, user_id, "Unsupported message")
            continue
        if len(text.encode("utf-8")) > _MAX_MESSAGE_BYTES:
            await _socket_error(websocket, workspace_id, user_id, "Message is too large")
            continue
        now = time.monotonic()
        recent[:] = [stamp for stamp in recent if now - stamp < _RATE_WINDOW_SECONDS]
        recent.append(now)
        if len(recent) > _RATE_LIMIT:
            await _socket_error(websocket, workspace_id, user_id, "Too many messages")
            continue
        payload = _parse_payload(text)
        if payload is None:
            await _socket_error(websocket, workspace_id, user_id, "Invalid message")
            continue
        await _handle_client_message(
            websocket,
            workspace_id,
            connection_id,
            user_id,
            profile,
            payload,
        )


async def _handle_client_message(
    websocket: WebSocket,
    workspace_id: str,
    connection_id: str,
    user_id: str,
    profile: Dict[str, str],
    payload: Dict[str, Any],
) -> None:
    message_type = payload.get("type")
    if message_type in ("presence.ping", "connection.auth"):
        return
    if message_type == "task.viewing":
        await _start_viewing(websocket, workspace_id, connection_id, user_id, profile, payload)
        return
    if message_type == "task.stopped_viewing":
        await _stop_viewing(workspace_id, connection_id, user_id, profile, payload)
        return
    await _socket_error(websocket, workspace_id, user_id, "Unsupported message")


async def _start_viewing(
    websocket: WebSocket,
    workspace_id: str,
    connection_id: str,
    user_id: str,
    profile: Dict[str, str],
    payload: Dict[str, Any],
) -> None:
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        await _socket_error(websocket, workspace_id, user_id, "Task not found")
        return
    task = await tasks_repository.find_by_id(task_id)
    project = None if task is None else await projects_repository.find_active_by_id(task.project_id)
    if task is None or project is None or project.workspace_id != workspace_id:
        await _socket_error(websocket, workspace_id, user_id, "Task not found")
        return
    stopped = manager.set_viewing(workspace_id, connection_id, project.id, task.id)
    data = dict(profile)
    data["task_id"] = task.id
    data["project_id"] = project.id
    await manager.broadcast_event(
        _envelope(
            "task.viewing",
            workspace_id,
            user_id,
            data,
            project_id=project.id,
            task_id=task.id,
        )
    )
    if stopped is not None:
        previous_task, previous_project = stopped
        await _broadcast_stopped(workspace_id, user_id, profile, previous_project, previous_task)


async def _stop_viewing(
    workspace_id: str,
    connection_id: str,
    user_id: str,
    profile: Dict[str, str],
    payload: Dict[str, Any],
) -> None:
    task_id = payload.get("task_id")
    if task_id is not None and not isinstance(task_id, str):
        return
    cleared = manager.clear_viewing(
        workspace_id,
        connection_id,
        task_id if isinstance(task_id, str) else None,
    )
    if cleared is None:
        return
    _connection, previous_task, previous_project = cleared
    await _broadcast_stopped(workspace_id, user_id, profile, previous_project, previous_task)


async def _broadcast_stopped(
    workspace_id: str,
    user_id: str,
    profile: Dict[str, str],
    project_id: str,
    task_id: str,
) -> None:
    data = dict(profile)
    data["task_id"] = task_id
    data["project_id"] = project_id
    await manager.broadcast_event(
        _envelope(
            "task.stopped_viewing",
            workspace_id,
            user_id,
            data,
            project_id=project_id,
            task_id=task_id,
        )
    )


def _parse_payload(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _envelope(
    event_type: str,
    workspace_id: str,
    actor_id: str,
    data: Dict[str, Any],
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    return build_envelope(
        type=event_type,
        workspace_id=workspace_id,
        actor_id=actor_id,
        data=data,
        project_id=project_id,
        task_id=task_id,
    )


async def _deny(websocket: WebSocket, workspace_id: str, message: str) -> None:
    await _socket_error(websocket, workspace_id, "", message)
    await websocket.close(code=1008)


async def _socket_error(websocket: WebSocket, workspace_id: str, actor_id: str, message: str) -> None:
    try:
        await websocket.send_json(
            _envelope("connection.error", workspace_id, actor_id, {"message": message})
        )
    except Exception:
        logger.warning("Failed to send a websocket error")
