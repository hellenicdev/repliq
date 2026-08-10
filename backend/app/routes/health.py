from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..database import get_db
from ..utils.turnstile import TurnstileError

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health(db: AsyncIOMotorDatabase = Depends(get_db)):
    try:
        await db.command("ping")
        db_status = "connected"
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"
    return {
        "status": "ok" if db_status == "connected" else "degraded",
        "db": db_status,
        "turnstile": "configured" if _turnstile_configured() else "not configured",
    }


def _turnstile_configured() -> bool:
    from ..config import settings

    return bool(settings.turnstile_secret_key)
