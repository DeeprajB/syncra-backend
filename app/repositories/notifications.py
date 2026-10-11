from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.core.time import as_utc
from app.models.notification import Notification


def serialize_notification(document: Dict[str, Any]) -> Notification:
    read_at = document.get("read_at")
    return Notification(
        id=str(document["_id"]),
        recipient_id=document["recipient_id"],
        workspace_id=document["workspace_id"],
        project_id=document["project_id"],
        task_id=document["task_id"],
        actor_id=document["actor_id"],
        type=document["type"],
        entity_type=document["entity_type"],
        entity_id=document["entity_id"],
        title=document["title"],
        body=document["body"],
        created_at=as_utc(document["created_at"]),
        read_at=None if read_at is None else as_utc(read_at),
        deduplication_key=document["deduplication_key"],
    )


async def ensure_indexes() -> None:
    await database.notifications.create_index(
        [("recipient_id", 1), ("created_at", -1), ("_id", -1)],
        name="recipient_notifications",
    )
    await database.notifications.create_index(
        [("recipient_id", 1), ("read_at", 1)],
        name="recipient_unread",
    )
    await database.notifications.create_index(
        "deduplication_key",
        unique=True,
        name="notification_dedup",
    )


async def insert(document: Dict[str, Any]) -> Notification:
    payload = dict(document)
    result = await database.notifications.insert_one(payload)
    payload["_id"] = result.inserted_id
    return serialize_notification(payload)


async def list_for_recipient(recipient_id: str) -> List[Notification]:
    documents = await database.notifications.find({"recipient_id": recipient_id}).to_list()
    notifications = [serialize_notification(document) for document in documents]
    notifications.sort(
        key=lambda item: (as_utc(item.created_at), item.id),
        reverse=True,
    )
    return notifications


async def count_unread(recipient_id: str) -> int:
    return await database.notifications.count_documents(
        {"recipient_id": recipient_id, "read_at": None}
    )


async def mark_read(
    notification_id: str,
    recipient_id: str,
    read_at: datetime,
) -> Optional[Notification]:
    object_id = parse_object_id(notification_id)
    if object_id is None:
        return None
    result = await database.notifications.update_one(
        {"_id": object_id, "recipient_id": recipient_id},
        {"$set": {"read_at": read_at}},
    )
    if result.matched_count != 1:
        return None
    document = await database.notifications.find_one(
        {"_id": object_id, "recipient_id": recipient_id}
    )
    if document is None:
        return None
    return serialize_notification(document)


async def mark_all_read(recipient_id: str, read_at: datetime) -> None:
    await database.notifications.update_many(
        {"recipient_id": recipient_id, "read_at": None},
        {"$set": {"read_at": read_at}},
    )
