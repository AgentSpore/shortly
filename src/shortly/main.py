"""Shortly — FastAPI entrypoint.

Wires CORS, a startup hook that initializes the SQLite schema, and mounts
the shortening API under ``/api``. The ``/r/{code}`` redirect lives in
``api.shorten`` too, so the public README contract is preserved.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.shorten import router as shorten_router
from .core.config import get_settings
from .core.db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create database tables on startup; nothing to close on shutdown."""
    await init_db()
    yield


app = FastAPI(
    title="Shortly",
    version=__version__,
    description="A URL shortener with SQLite storage, zero external dependencies.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe. Always 200 if the process is up."""
    return {"status": "ok", "version": __version__}


# Domain API. The router is defined with no trailing-slash paths so the
# final URLs are exactly /api/shorten, /api/shorts, /api/r/{code} etc.
app.include_router(shorten_router, prefix="/api")
