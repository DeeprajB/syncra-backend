from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId


def parse_object_id(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None
