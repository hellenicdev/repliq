from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.video import Video


def _to_model(doc: dict[str, Any]) -> Video:
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    return Video.model_validate(doc)


async def list_videos(db: AsyncIOMotorDatabase) -> list[Video]:
    cursor = db.videos.find().sort("createdAt", -1)
    return [_to_model(doc) async for doc in cursor]


async def get_video(db: AsyncIOMotorDatabase, video_id: str) -> Video | None:
    from bson import ObjectId

    if not ObjectId.is_valid(video_id):
        return None
    doc = await db.videos.find_one({"_id": ObjectId(video_id)})
    return _to_model(doc) if doc else None


async def update_video(db: AsyncIOMotorDatabase, video_id: str, **fields: Any) -> None:
    from bson import ObjectId

    if not ObjectId.is_valid(video_id):
        raise ValueError(f"invalid video id: {video_id}")
    await db.videos.update_one({"_id": ObjectId(video_id)}, {"$set": fields})
