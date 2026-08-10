from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.job import Job


def _to_model(doc: dict[str, Any]) -> Job:
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    return Job.model_validate(doc)


async def create_job(db: AsyncIOMotorDatabase, job: Job) -> Job:
    data = job.model_dump(by_alias=True, exclude_none=True)
    data["_id"] = ObjectId(job.id)
    await db.jobs.insert_one(data)
    return job


async def get_job(db: AsyncIOMotorDatabase, job_id: str) -> Job | None:
    if not ObjectId.is_valid(job_id):
        return None
    doc = await db.jobs.find_one({"_id": ObjectId(job_id)})
    return _to_model(doc) if doc else None


async def update_job(db: AsyncIOMotorDatabase, job_id: str, **fields: Any) -> None:
    from bson import ObjectId

    if not ObjectId.is_valid(job_id):
        raise ValueError(f"invalid job id: {job_id}")
    await db.jobs.update_one({"_id": ObjectId(job_id)}, {"$set": fields})
