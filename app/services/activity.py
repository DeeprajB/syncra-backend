import base64
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from app.core.time import as_utc, utc_now
from app.models.activity import Activity
from app.models.auth.user import User
from app.realtime.protocol import build_envelope
from app.repositories import activities as activities_repository
from app.repositories import users as users_repository
from app.schemas.activity import WorkspaceActivityItem, WorkspaceActivityListResponse
from app.schemas.workspace_member import public_profile
from app.services.workspace_access import require_workspace_member


def persist_fields(
    *,
    action: str,
    workspace_id: str,
    actor_id: str,
    metadata: Dict[str, Any],
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    occurred = occurred_at or utc_now()
    stored = dict(metadata)
    document = {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "task_id": task_id,
        "actor_id": actor_id,
        "action": action,
        "metadata": stored,
        "created_at": occurred,
    }
    event = build_envelope(
        type=action,
        workspace_id=workspace_id,
        actor_id=actor_id,
        project_id=project_id,
        task_id=task_id,
        data=stored,
        occurred_at=occurred,
    )
    return document, event


async def record_activity(
    *,
    action: str,
    workspace_id: str,
    actor_id: str,
    metadata: Dict[str, Any],
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    session: Any = None,
) -> Dict[str, Any]:
    document, event = persist_fields(
        action=action,
        workspace_id=workspace_id,
        actor_id=actor_id,
        metadata=metadata,
        project_id=project_id,
        task_id=task_id,
    )
    await activities_repository.insert(document, session=session)
    return event


def _encode_cursor(activity: Activity) -> str:
    raw = "{0}|{1}".format(as_utc(activity.created_at).isoformat(), activity.id)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8").rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[datetime, str]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        timestamp, activity_id = raw.split("|", 1)
        if not activity_id:
            raise ValueError
        return as_utc(datetime.fromisoformat(timestamp)), activity_id
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
        )


async def list_workspace_activity(
    workspace_id: str,
    current_user: User,
    *,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> WorkspaceActivityListResponse:
    access = await require_workspace_member(workspace_id, current_user)
    activities = await activities_repository.list_for_workspace(access.workspace.id)
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        activities = [
            activity
            for activity in activities
            if (as_utc(activity.created_at), activity.id) < (cursor_time, cursor_id)
        ]
    page = activities[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(activities) > limit and page else None
    users = await users_repository.find_by_ids([activity.actor_id for activity in page])
    users_by_id = {user.id: user for user in users if user.id}
    items = []
    for activity in page:
        profile = public_profile(users_by_id.get(activity.actor_id), activity.actor_id)
        items.append(
            WorkspaceActivityItem(
                id=activity.id,
                project_id=activity.project_id,
                task_id=activity.task_id,
                actor_id=activity.actor_id,
                actor_name=profile["name"],
                avatar_url=profile["avatar_url"],
                action=activity.action,
                metadata=activity.metadata,
                created_at=activity.created_at,
            )
        )
    return WorkspaceActivityListResponse(items=items, next_cursor=next_cursor)


async def record_member_event(
    workspace_id: str,
    user_id: str,
    action: str,
    session: Any = None,
) -> Dict[str, Any]:
    user = await users_repository.find_by_id(user_id)
    return await record_activity(
        action=action,
        workspace_id=workspace_id,
        actor_id=user_id,
        metadata=public_profile(user, user_id),
        session=session,
    )
