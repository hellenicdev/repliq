import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import close_db, connect_db, create_indexes
from .routes import health, jobs, videos
from .services import storage

logging.basicConfig(level=logging.INFO)

PRUNE_INTERVAL_SECONDS = 6 * 3600

FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


async def _cache_prune_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(storage.prune_stale_cache)
        except Exception:  # noqa: BLE001 - pruning must never take the service down
            logging.exception("cache prune failed")
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        connect_db()
        await create_indexes()
        logging.info("MongoDB connected")
    except Exception as exc:  # noqa: BLE001 - server must still boot so /api/health reports the problem
        logging.warning("MongoDB unavailable at startup: %s", exc)
    try:
        await asyncio.to_thread(storage.prune_stale_cache)
    except Exception as exc:  # noqa: BLE001
        logging.warning("cache prune at startup failed: %s", exc)
    task = asyncio.create_task(_cache_prune_loop())
    yield
    task.cancel()
    await close_db()


app = FastAPI(title="Dialogue Video Generator", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(videos.router)
app.include_router(jobs.router)


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    @app.get("/")
    async def root():
        return {"service": "Dialogue Video Generator", "docs": "/docs", "health": "/api/health"}
