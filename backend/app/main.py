import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import close_db, connect_db, create_indexes
from .routes import health, jobs, videos

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        connect_db()
        await create_indexes()
        logging.info("MongoDB connected")
    except Exception as exc:  # noqa: BLE001 - server must still boot so /api/health reports the problem
        logging.warning("MongoDB unavailable at startup: %s", exc)
    yield
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


@app.get("/")
async def root():
    return {"service": "Dialogue Video Generator", "docs": "/docs", "health": "/api/health"}
