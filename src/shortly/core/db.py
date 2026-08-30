"""Async SQLite connection management and schema initialization.

We deliberately use raw aiosqlite (no SQLAlchemy ORM) so the dependency
footprint stays small and the queries stay obvious. A single connection
is shared per request via FastAPI's dependency injection.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from .config import get_settings


# --- Schema ------------------------------------------------------------------
# Idempotent DDL. Keep one CREATE TABLE per logical entity so it's easy to
# extend later without rewriting the whole file.

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shorts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT    NOT NULL UNIQUE,
    source        TEXT    NOT NULL,
    target_url    TEXT    NOT NULL,
    max_chars     INTEGER NOT NULL DEFAULT 280,
    note          TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    hit_count     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shorts_code     ON shorts(code);
CREATE INDEX IF NOT EXISTS idx_shorts_source   ON shorts(source);
CREATE INDEX IF NOT EXISTS idx_shorts_created  ON shorts(created_at);

CREATE TABLE IF NOT EXISTS redirect_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    short_id    INTEGER NOT NULL,
    occurred_at TEXT    NOT NULL DEFAULT (datetime('now')),
    user_agent  TEXT,
    referer     TEXT,
    FOREIGN KEY(short_id) REFERENCES shorts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_short ON redirect_events(short_id);
"""


async def init_db() -> None:
    """Create tables if they don't exist and enable FK enforcement."""
    settings = get_settings()
    db_path = _resolve_db_path(settings.database_url)
    # Ensure parent directory exists for non-default paths.
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON;")
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Yield a connection per request, enabling FKs and row factory.

    Usage from a FastAPI route:
        @router.get(...)
        async def list_items(db: aiosqlite.Connection = Depends(get_db)):
            ...
    """
    settings = get_settings()
    db_path = _resolve_db_path(settings.database_url)

    conn = await aiosqlite.connect(db_path)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
    finally:
        await conn.close()


def _resolve_db_path(database_url: str) -> str:
    """Coerce config value into a filesystem path aiosqlite understands.

    If a SQLAlchemy-style ``sqlite:///./x.db`` URL sneaks in via env, strip
    the prefix so the actual file is used instead of a literal "sqlite:" file.
    """
    if not database_url:
        return "shortly.db"
    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///"):]
    if database_url.startswith("sqlite://"):
        return database_url[len("sqlite://"):]
    return database_url
