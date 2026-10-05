from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pymongo import AsyncMongoClient

from app.core.config import settings

client = AsyncMongoClient(settings.mongodb_uri, tz_aware=True)

database = client[settings.mongodb_database]

async def check_database_connection() -> bool:
    try:
        await client.admin.command("ping")
        return True
    except Exception:
        return False


@asynccontextmanager
async def transaction() -> AsyncIterator[Any]:
    async with client.start_session() as session:
        async with await session.start_transaction():
            yield session