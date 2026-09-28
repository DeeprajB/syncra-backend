from pymongo import AsyncMongoClient
from app.core.config import settings

client = AsyncMongoClient(settings.mongodb_uri)

database = client[settings.mongodb_database]

async def check_database_connection() -> bool:
    try:
        await client.admin.command("ping")
        return True
    except Exception:
        return False