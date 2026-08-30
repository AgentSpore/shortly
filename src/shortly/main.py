"""Shortly — FastAPI entrypoint.

THIN shell: app, CORS, and `/health` only. Domain routes (shorten,
redirect) are added in later groups (G3 imports `api.shorten`).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__

app = FastAPI(
    title="Shortly",
    version=__version__,
    description="A URL shortener with SQLite storage, zero external dependencies.",
)

# Permissive CORS — a public URL shortener is the only use case today.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness probe. Always 200 if the process is up."""
    return {"status": "ok", "version": __version__}
