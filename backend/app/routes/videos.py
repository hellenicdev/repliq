from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..database import get_db
from ..repositories import videos as videos_repo

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.get("")
async def list_videos(db: AsyncIOMotorDatabase = Depends(get_db)):
    return await videos_repo.list_videos(db)
