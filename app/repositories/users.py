from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.models.auth.user import User

def serialize_user(document: Dict[str, Any]) -> User:
    return User(
        id=str(document["_id"]),
        sub=document["sub"],
        email=document.get("email") or "",
        given_name=document.get("given_name") or "",
        family_name=document.get("family_name") or "",
        picture=document.get("picture") or "",
        email_verified=bool(document.get("email_verified", False)),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )

async def ensure_indexes() -> None:
    await database.users.create_index("sub", unique=True)

async def upsert_by_sub(profile: Dict[str, Any]) -> User:
    now = datetime.now(timezone.utc)
    await database.users.update_one(
        {"sub": profile["sub"]},
        {
            "$set": {
                "email": profile.get("email") or "",
                "given_name": profile.get("given_name") or "",
                "family_name": profile.get("family_name") or "",
                "picture": profile.get("picture") or "",
                "email_verified": bool(profile.get("email_verified", False)),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    document = await database.users.find_one({"sub": profile["sub"]})
    if document is None:
        raise RuntimeError("Failed to load user after upsert")
    return serialize_user(document)

async def find_by_sub(sub: str) -> Optional[User]:
    document = await database.users.find_one({"sub": sub})
    if document is None:
        return None
    return serialize_user(document)


async def find_by_id(user_id: str) -> Optional[User]:
    object_id = parse_object_id(user_id)
    if object_id is None:
        return None
    document = await database.users.find_one({"_id": object_id})
    if document is None:
        return None
    return serialize_user(document)


async def find_by_ids(user_ids: List[str]) -> List[User]:
    object_ids = []
    for user_id in user_ids:
        object_id = parse_object_id(user_id)
        if object_id is not None:
            object_ids.append(object_id)
    if not object_ids:
        return []
    documents = await database.users.find({"_id": {"$in": object_ids}}).to_list()
    return [serialize_user(document) for document in documents]
