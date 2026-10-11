import base64
import logging
from datetime import datetime
from typing import Optional, Set, Tuple

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from app.core import events
from app.core.time import as_utc, utc_now
from app.models.auth.user import User
from app.models.notification import Notification
from app.realtime.protocol import build_envelope
from app.repositories import comments as comments_repository
from app.repositories import notifications as notifications_repository
from app.repositories import workspace_members as members_repository
from app.schemas.notification import (
    NotificationItem,
    NotificationListResponse,
    UnreadCountResponse,
    notification_item,
)
from app.schemas.workspace_member import display_name
from app.services.workspace_access import require_user_id


logger = logging.getLogger(__name__)


def _excerpt(content: str) -> str:
    compact = " ".join(content.split())
    if len(compact) <= 140:
        return compact
    return compact[:137] + "..."


async def notify_task_assigned(
    *,
    workspace_id: str,
    project_id: str,
    task_id: str,
    task_title: str,
    actor: User,
    recipient_id: Optional[str],
    version: int,
) -> None:
    actor_id = actor.id or ""
    if not recipient_id or recipient_id == actor_id:
        return
    try:
        if await members_repository.find_by_workspace_and_user(workspace_id, recipient_id) is None:
            return
        event = await _create(
            recipient_id=recipient_id,
            workspace_id=workspace_id,
            project_id=project_id,
            task_id=task_id,
            actor_id=actor_id,
            notification_type="task.assigned",
            entity_type="task",
            entity_id=task_id,
            title="{0} assigned you a task".format(display_name(actor)),
            body=task_title,
            deduplication_key="task.assigned:{0}:{1}:{2}".format(task_id, recipient_id, version),
        )
    except Exception:
        logger.exception("Failed to record a task assignment notification")
        return
    _publish(event)


async def notify_comment_created(
    *,
    workspace_id: str,
    project_id: str,
    task_id: str,
    task_title: str,
    assignee_id: Optional[str],
    comment_id: str,
    content: str,
    actor: User,
) -> None:
    actor_id = actor.id or ""
    try:
        recipients = await _comment_recipients(task_id, assignee_id, actor_id, workspace_id)
        excerpt = _excerpt(content)
        title = "{0} commented on {1}".format(display_name(actor), task_title)
        for recipient_id in recipients:
            event = await _create(
                recipient_id=recipient_id,
                workspace_id=workspace_id,
                project_id=project_id,
                task_id=task_id,
                actor_id=actor_id,
                notification_type="comment.created",
                entity_type="comment",
                entity_id=comment_id,
                title=title,
                body=excerpt,
                deduplication_key="comment.created:{0}:{1}".format(comment_id, recipient_id),
            )
            _publish(event)
    except Exception:
        logger.exception("Failed to record a comment notification")


async def list_notifications(
    current_user: User,
    *,
    limit: int = 20,
    cursor: Optional[str] = None,
) -> NotificationListResponse:
    recipient_id = require_user_id(current_user)
    items = await notifications_repository.list_for_recipient(recipient_id)
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        items = [
            item
            for item in items
            if (as_utc(item.created_at), item.id) < (cursor_time, cursor_id)
        ]
    page = items[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(items) > limit and page else None
    return NotificationListResponse(
        items=[notification_item(item) for item in page],
        next_cursor=next_cursor,
    )


async def unread_count(current_user: User) -> UnreadCountResponse:
    recipient_id = require_user_id(current_user)
    return UnreadCountResponse(count=await notifications_repository.count_unread(recipient_id))


async def mark_read(notification_id: str, current_user: User) -> NotificationItem:
    updated = await notifications_repository.mark_read(
        notification_id,
        require_user_id(current_user),
        utc_now(),
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return notification_item(updated)


async def mark_all_read(current_user: User) -> None:
    await notifications_repository.mark_all_read(require_user_id(current_user), utc_now())


async def _comment_recipients(
    task_id: str,
    assignee_id: Optional[str],
    actor_id: str,
    workspace_id: str,
) -> Set[str]:
    recipients = set()
    if assignee_id:
        recipients.add(assignee_id)
    for comment in await comments_repository.list_for_task(task_id):
        recipients.add(comment.author_id)
    recipients.discard(actor_id)
    allowed = set()
    for recipient_id in recipients:
        member = await members_repository.find_by_workspace_and_user(workspace_id, recipient_id)
        if member is not None:
            allowed.add(recipient_id)
    return allowed


async def _create(
    *,
    recipient_id: str,
    workspace_id: str,
    project_id: str,
    task_id: str,
    actor_id: str,
    notification_type: str,
    entity_type: str,
    entity_id: str,
    title: str,
    body: str,
    deduplication_key: str,
) -> Optional[dict]:
    now = utc_now()
    try:
        notification = await notifications_repository.insert(
            {
                "recipient_id": recipient_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "task_id": task_id,
                "actor_id": actor_id,
                "type": notification_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "title": title,
                "body": body,
                "created_at": now,
                "read_at": None,
                "deduplication_key": deduplication_key,
            }
        )
    except DuplicateKeyError:
        return None
    event = build_envelope(
        type="notification.created",
        workspace_id=workspace_id,
        project_id=project_id,
        task_id=task_id,
        actor_id=actor_id,
        data=_event_data(notification),
        occurred_at=now,
    )
    event["recipient_id"] = recipient_id
    return event


def _event_data(notification: Notification) -> dict:
    item = notification_item(notification)
    payload = item.model_dump(mode="json")
    payload["deduplication_key"] = notification.deduplication_key
    return payload


def _publish(event: Optional[dict]) -> None:
    if event is not None:
        events.publish(event)


def _encode_cursor(notification: Notification) -> str:
    raw = "{0}|{1}".format(as_utc(notification.created_at).isoformat(), notification.id)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8").rstrip("=")


def _decode_cursor(cursor: str) -> Tuple[datetime, str]:
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        timestamp, notification_id = raw.split("|", 1)
        if not notification_id:
            raise ValueError
        return as_utc(datetime.fromisoformat(timestamp)), notification_id
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
        )
