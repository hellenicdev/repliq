from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.dialogue import Dialogue


def _to_model(doc: dict[str, Any]) -> Dialogue:
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    return Dialogue.model_validate(doc)


async def list_dialogue(db: AsyncIOMotorDatabase) -> list[Dialogue]:
    """Return all dialogue segments.

    Phase 1 loads the (tiny) dataset and scores in Python. In Phase 4 this
    moves inside the search service: Atlas $search / $vectorSearch queries.
    """
    cursor = db.dialogue.find().sort("startTime", 1)
    return [_to_model(doc) async for doc in cursor]
