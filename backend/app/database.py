from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .config import settings

_client: AsyncIOMotorClient | None = None


def get_db() -> AsyncIOMotorDatabase:
    """Return the app database. The client is created lazily so the module
    imports cleanly even when MongoDB is unreachable (e.g. health checks)."""
    if _client is None:
        raise RuntimeError("MongoDB client not initialized; call connect_db() on startup")
    return _client[settings.mongodb_database]


def connect_db() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    return _client


async def close_db() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def create_indexes() -> None:
    db = get_db()
    await db.videos.create_index("source")
    await db.dialogue.create_index("videoId")
    await db.dialogue.create_index("text")
    await db.dialogue.create_index("startTime")
    await db.jobs.create_index("status")
    await db.jobs.create_index([("createdAt", -1)])
