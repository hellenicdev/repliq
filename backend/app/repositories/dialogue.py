from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.dialogue import Dialogue


def _to_model(doc: dict[str, Any]) -> Dialogue:
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    return Dialogue.model_validate(doc)


async def list_dialogue(db: AsyncIOMotorDatabase, match: dict[str, Any] | None = None) -> list[Dialogue]:
    """Return dialogue segments, optionally pre-filtered server-side.

    Phase 1 scores in Python but the 1M+ segment library is filtered down
    with an index-friendly $regex query before anything leaves MongoDB.
    """
    cursor = db.dialogue.find(match or {}).sort("startTime", 1)
    return [_to_model(doc) async for doc in cursor]
