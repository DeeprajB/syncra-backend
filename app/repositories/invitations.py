from datetime import datetime
from typing import Any, Dict, Optional

from app.core.database import database
from app.core.mongo import parse_object_id
from app.models.invitation import Invitation


def serialize_invitation(document: Dict[str, Any]) -> Invitation:
    return Invitation(
        id=str(document["_id"]),
        workspace_id=document["workspace_id"],
        token_hash=document["token_hash"],
        created_by=document["created_by"],
        expires_at=document["expires_at"],
        used_at=document.get("used_at"),
        created_at=document["created_at"],
        revoked_at=document.get("revoked_at"),
    )


async def ensure_indexes() -> None:
    await database.invitations.create_index("token_hash", unique=True)


async def insert(document: Dict[str, Any], session: Any = None) -> Invitation:
    payload = dict(document)
    result = await database.invitations.insert_one(payload, session=session)
    payload["_id"] = result.inserted_id
    return serialize_invitation(payload)


async def find_by_token_hash(token_hash: str) -> Optional[Invitation]:
    document = await database.invitations.find_one({"token_hash": token_hash})
    if document is None:
        return None
    return serialize_invitation(document)


async def mark_used(
    invitation_id: str,
    used_at: datetime,
    session: Any = None,
) -> None:
    object_id = parse_object_id(invitation_id)
    if object_id is None:
        return
    await database.invitations.update_one(
        {"_id": object_id, "used_at": None},
        {"$set": {"used_at": used_at}},
        session=session,
    )
