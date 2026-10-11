from typing import Any, Dict, List, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.core.time import as_utc
from app.models.comment import Comment


def serialize_comment(document: Dict[str, Any]) -> Comment:
    return Comment(
        id=str(document["_id"]),
        workspace_id=document["workspace_id"],
        project_id=document["project_id"],
        task_id=document["task_id"],
        author_id=document["author_id"],
        content=document["content"],
        created_at=as_utc(document["created_at"]),
        updated_at=as_utc(document["updated_at"]),
    )


async def ensure_indexes() -> None:
    await database.comments.create_index(
        [("task_id", 1), ("created_at", 1), ("_id", 1)],
        name="task_comments",
    )


async def insert(document: Dict[str, Any], session: Any = None) -> Comment:
    payload = dict(document)
    result = await database.comments.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_comment(payload)


async def find_by_id(comment_id: str, session: Any = None) -> Optional[Comment]:
    object_id = parse_object_id(comment_id)
    if object_id is None:
        return None
    document = await database.comments.find_one({"_id": object_id}, session=session)
    if document is None:
        return None
    return serialize_comment(document)


async def list_for_task(task_id: str) -> List[Comment]:
    documents = await database.comments.find({"task_id": task_id}).to_list()
    comments = [serialize_comment(document) for document in documents]
    comments.sort(key=lambda comment: (comment.created_at, comment.id))
    return comments


async def update_content(
    comment_id: str,
    content: str,
    updated_at: Any,
    session: Any = None,
) -> Optional[Comment]:
    object_id = parse_object_id(comment_id)
    if object_id is None:
        return None
    result = await database.comments.update_one(
        {"_id": object_id},
        {"$set": {"content": content, "updated_at": updated_at}},
        session=session,
    )
    if result.matched_count != 1:
        return None
    return await find_by_id(comment_id, session=session)


async def delete(comment_id: str, session: Any = None) -> bool:
    object_id = parse_object_id(comment_id)
    if object_id is None:
        return False
    result = await database.comments.delete_one({"_id": object_id}, session=session)
    return result.deleted_count == 1
