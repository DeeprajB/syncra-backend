import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from app.realtime.protocol import build_envelope


logger = logging.getLogger(__name__)


@dataclass
class Connection:
    connection_id: str
    user_id: str
    workspace_id: str
    socket: Any
    name: str
    avatar_url: str
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    cursor_project_id: Optional[str] = None
    cursor_task_id: Optional[str] = None
    cursor_column: Optional[str] = None


@dataclass
class DisconnectResult:
    connection: Connection
    last_for_user: bool
    stopped_task_id: Optional[str]
    stopped_project_id: Optional[str]


class ConnectionManager:
    """In-process workspace sockets. One Render instance only."""

    def __init__(self) -> None:
        self._rooms: Dict[str, Dict[str, Connection]] = {}

    def reset(self) -> None:
        self._rooms.clear()

    def connection_count(self, workspace_id: str) -> int:
        return len(self._rooms.get(workspace_id, {}))

    def connect(
        self,
        workspace_id: str,
        user_id: str,
        socket: Any,
        name: str,
        avatar_url: str,
    ) -> Tuple[Connection, bool]:
        room = self._rooms.setdefault(workspace_id, {})
        first = not any(item.user_id == user_id for item in room.values())
        connection = Connection(
            connection_id=uuid4().hex,
            user_id=user_id,
            workspace_id=workspace_id,
            socket=socket,
            name=name,
            avatar_url=avatar_url,
        )
        room[connection.connection_id] = connection
        return connection, first

    def disconnect(self, workspace_id: str, connection_id: str) -> Optional[DisconnectResult]:
        room = self._rooms.get(workspace_id)
        if room is None or connection_id not in room:
            return None
        connection = room.pop(connection_id)
        remaining = [item for item in room.values() if item.user_id == connection.user_id]
        if not room:
            self._rooms.pop(workspace_id, None)
        stopped_task_id = None
        stopped_project_id = None
        if connection.task_id and not any(item.task_id == connection.task_id for item in remaining):
            stopped_task_id = connection.task_id
            stopped_project_id = connection.project_id
        return DisconnectResult(
            connection=connection,
            last_for_user=not remaining,
            stopped_task_id=stopped_task_id,
            stopped_project_id=stopped_project_id,
        )

    def set_viewing(
        self,
        workspace_id: str,
        connection_id: str,
        project_id: str,
        task_id: str,
    ) -> Optional[Tuple[str, str]]:
        connection = self._connection(workspace_id, connection_id)
        if connection is None:
            return None
        previous_task = connection.task_id
        previous_project = connection.project_id
        connection.project_id = project_id
        connection.task_id = task_id
        if not previous_task or previous_task == task_id or previous_project is None:
            return None
        if self._user_still_views(workspace_id, connection.user_id, previous_task):
            return None
        return previous_task, previous_project

    def clear_viewing(
        self,
        workspace_id: str,
        connection_id: str,
        task_id: Optional[str] = None,
    ) -> Optional[Tuple[Connection, str, str]]:
        connection = self._connection(workspace_id, connection_id)
        if connection is None or connection.task_id is None or connection.project_id is None:
            return None
        if task_id is not None and connection.task_id != task_id:
            return None
        previous_task = connection.task_id
        previous_project = connection.project_id
        connection.task_id = None
        connection.project_id = None
        if self._user_still_views(workspace_id, connection.user_id, previous_task):
            return None
        return connection, previous_task, previous_project

    def set_cursor(
        self,
        workspace_id: str,
        connection_id: str,
        project_id: Optional[str],
        task_id: Optional[str],
        column: Optional[str],
    ) -> bool:
        connection = self._connection(workspace_id, connection_id)
        if connection is None:
            return False
        connection.cursor_project_id = project_id
        connection.cursor_task_id = task_id
        connection.cursor_column = column
        return True

    def snapshot(self, workspace_id: str) -> List[Dict[str, Any]]:
        by_user: Dict[str, Dict[str, Any]] = {}
        for connection in self._rooms.get(workspace_id, {}).values():
            current = by_user.get(connection.user_id)
            row = _member_row(connection)
            if current is None:
                by_user[connection.user_id] = row
                continue
            if connection.task_id and not current.get("task_id"):
                current["task_id"] = connection.task_id
                current["project_id"] = connection.project_id
            if _has_cursor(connection) and not _row_has_cursor(current):
                current["cursor_project_id"] = connection.cursor_project_id
                current["cursor_task_id"] = connection.cursor_task_id
                current["cursor_column"] = connection.cursor_column
        return list(by_user.values())

    def publish(self, event: Dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.broadcast_event(event))

    async def broadcast_event(
        self,
        event: Dict[str, Any],
        exclude_connection_id: Optional[str] = None,
    ) -> None:
        try:
            workspace_id = event.get("workspace_id")
            if not workspace_id:
                return
            dropped = await self._deliver(
                workspace_id,
                event,
                exclude_connection_id,
                only_user_id=event.get("recipient_id"),
            )
            await self._announce(dropped)
        except Exception:
            logger.exception("Realtime broadcast failed")

    async def finish_disconnect(self, result: Optional[DisconnectResult]) -> None:
        if result is None:
            return
        try:
            await self._announce([result])
        except Exception:
            logger.exception("Realtime presence update failed")

    async def disconnect_user(self, workspace_id: str, user_id: str, code: int = 4403) -> None:
        room = self._rooms.get(workspace_id, {})
        targets = [item for item in room.values() if item.user_id == user_id]
        results = []
        for connection in targets:
            result = self.disconnect(workspace_id, connection.connection_id)
            if result is not None:
                results.append(result)
        for connection in targets:
            try:
                await connection.socket.close(code=code)
            except Exception:
                logger.warning("Failed to close a revoked websocket")
        await self._announce(results)

    async def close_all(self) -> None:
        rooms = self._rooms
        self._rooms = {}
        for room in rooms.values():
            for connection in room.values():
                try:
                    await connection.socket.close(code=1001)
                except Exception:
                    logger.warning("Failed to close a websocket during shutdown")

    def _connection(self, workspace_id: str, connection_id: str) -> Optional[Connection]:
        return self._rooms.get(workspace_id, {}).get(connection_id)

    def _user_still_views(self, workspace_id: str, user_id: str, task_id: str) -> bool:
        room = self._rooms.get(workspace_id, {})
        return any(item.user_id == user_id and item.task_id == task_id for item in room.values())

    async def _deliver(
        self,
        workspace_id: str,
        payload: Dict[str, Any],
        exclude_connection_id: Optional[str] = None,
        only_user_id: Optional[str] = None,
    ) -> List[DisconnectResult]:
        room = self._rooms.get(workspace_id, {})
        dropped = []
        for connection in list(room.values()):
            if connection.connection_id == exclude_connection_id:
                continue
            if only_user_id and connection.user_id != only_user_id:
                continue
            try:
                await connection.socket.send_json(payload)
            except Exception:
                logger.warning("Closing a websocket that failed to receive")
                result = self.disconnect(workspace_id, connection.connection_id)
                if result is not None:
                    dropped.append(result)
        return dropped

    async def _announce(self, results: List[DisconnectResult]) -> None:
        for result in results:
            connection = result.connection
            if result.stopped_task_id and result.stopped_project_id:
                await self._deliver(
                    connection.workspace_id,
                    build_envelope(
                        type="task.stopped_viewing",
                        workspace_id=connection.workspace_id,
                        actor_id=connection.user_id,
                        project_id=result.stopped_project_id,
                        task_id=result.stopped_task_id,
                        data={
                            "user_id": connection.user_id,
                            "name": connection.name,
                            "avatar_url": connection.avatar_url,
                            "task_id": result.stopped_task_id,
                            "project_id": result.stopped_project_id,
                        },
                    ),
                )
            if _has_cursor(connection):
                remaining = self._remaining_cursor(connection.workspace_id, connection.user_id)
                if remaining is None:
                    await self._deliver(
                        connection.workspace_id,
                        _cursor_event(connection, None, None, None),
                    )
                else:
                    await self._deliver(
                        connection.workspace_id,
                        _cursor_event(
                            remaining,
                            remaining.cursor_project_id,
                            remaining.cursor_task_id,
                            remaining.cursor_column,
                        ),
                    )
            if result.last_for_user:
                await self._deliver(
                    connection.workspace_id,
                    build_envelope(
                        type="presence.left",
                        workspace_id=connection.workspace_id,
                        actor_id=connection.user_id,
                        data={
                            "user_id": connection.user_id,
                            "name": connection.name,
                            "avatar_url": connection.avatar_url,
                        },
                    ),
                )

    def _remaining_cursor(self, workspace_id: str, user_id: str) -> Optional[Connection]:
        for connection in self._rooms.get(workspace_id, {}).values():
            if connection.user_id == user_id and _has_cursor(connection):
                return connection
        return None


def _has_cursor(connection: Connection) -> bool:
    return bool(connection.cursor_task_id or connection.cursor_column)


def _row_has_cursor(row: Dict[str, Any]) -> bool:
    return bool(row.get("cursor_task_id") or row.get("cursor_column"))


def _member_row(connection: Connection) -> Dict[str, Any]:
    return {
        "user_id": connection.user_id,
        "name": connection.name,
        "avatar_url": connection.avatar_url,
        "task_id": connection.task_id,
        "project_id": connection.project_id,
        "cursor_project_id": connection.cursor_project_id,
        "cursor_task_id": connection.cursor_task_id,
        "cursor_column": connection.cursor_column,
    }


def _cursor_event(
    connection: Connection,
    project_id: Optional[str],
    task_id: Optional[str],
    column: Optional[str],
) -> Dict[str, Any]:
    return build_envelope(
        type="presence.cursor",
        workspace_id=connection.workspace_id,
        actor_id=connection.user_id,
        project_id=project_id,
        task_id=task_id,
        data={
            "user_id": connection.user_id,
            "name": connection.name,
            "avatar_url": connection.avatar_url,
            "project_id": project_id,
            "task_id": task_id,
            "column": column,
        },
    )


manager = ConnectionManager()
