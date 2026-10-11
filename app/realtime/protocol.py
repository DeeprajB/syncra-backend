from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from app.core.time import as_utc, utc_now


def iso_z(value: datetime) -> str:
    text = as_utc(value).isoformat()
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


def build_envelope(
    *,
    type: str,
    workspace_id: str,
    actor_id: str,
    data: Dict[str, Any],
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    when = occurred_at or utc_now()
    return {
        "event_id": uuid4().hex,
        "schema_version": 1,
        "type": type,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "task_id": task_id,
        "actor_id": actor_id,
        "occurred_at": iso_z(when),
        "data": data,
    }
