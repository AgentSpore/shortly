"""URL shortening + redirect + analytics HTTP endpoints.

Mounted under ``/api`` by ``main.py`` so the final paths are
``/api/shorten``, ``/api/shorts``, ``/api/shorts/{code}`` and
``/api/shorts/{code}/analytics``. The public redirect path ``/r/{code}``
is also exposed here (per the README contract).
"""
from __future__ import annotations

import secrets
from typing import List, Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from ..core.config import get_settings
from ..core.db import get_db
from ..schemas.short import (
    AnalyticsResponse,
    ShortCreate,
    ShortList,
    ShortRead,
    ShortenResponse,
)


router = APIRouter(tags=["shorten"])


# Characters used in generated short codes. URL-safe, no padding '='.
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # omit confusable 0/O/1/l/I


def _generate_code(length: int) -> str:
    """Return a random code of exactly ``length`` chars from ``_ALPHABET``."""
    if length < 4:
        length = 4
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


@router.post("/shorten", response_model=ShortenResponse, status_code=status.HTTP_201_CREATED)
async def create_short(
    payload: ShortCreate,
    db: aiosqlite.Connection = Depends(get_db),
) -> ShortenResponse:
    """Create a new short link.

    Generates a code, persists it, and returns both the bare code and the
    full ``/r/<code>`` path. Collisions are extremely rare with even
    6-char codes from this alphabet, but we retry a few times defensively.
    """
    settings = get_settings()
    max_chars = max(4, min(payload.max_chars or settings.default_max_chars, 32))

    # Try up to 5 times to find a free code, expanding by 1 char on collision
    # so we never silently serve a duplicate.
    last_error: Optional[Exception] = None
    for attempt in range(5):
        candidate = _generate_code(max_chars + (attempt if attempt else 0))
        try:
            async with db.execute(
                """
                INSERT INTO shorts (code, source, target_url, max_chars, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("api", candidate, str(payload.url), max_chars, payload.note),
            ) as cur:
                short_id = cur.lastrowid
            await db.commit()
            return ShortenResponse(
                short=candidate,
                url=str(payload.url),
                short_url=f"/r/{candidate}",
                note=payload.note,
            )
        except Exception as exc:  # likely UNIQUE collision
            last_error = exc
            await db.rollback()
            continue

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"failed to allocate a unique short code: {last_error}",
    )


@router.get("/shorts", response_model=ShortList)
async def list_shorts(
    limit: int = 50,
    offset: int = 0,
    db: aiosqlite.Connection = Depends(get_db),
) -> ShortList:
    """List recent short links (newest first)."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    async with db.execute(
        "SELECT COUNT(*) AS n FROM shorts"
    ) as cur:
        row = await cur.fetchone()
    total = int(row["n"]) if row else 0

    async with db.execute(
        """
        SELECT id, code, target_url, source, max_chars, note,
               created_at, hit_count
        FROM shorts
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ) as cur:
        rows = await cur.fetchall()

    items = [ShortRead.model_validate(dict(r)) for r in rows]
    return ShortList(items=items, total=total)


@router.get("/shorts/{code}", response_model=ShortRead)
async def get_short(
    code: str,
    db: aiosqlite.Connection = Depends(get_db),
) -> ShortRead:
    """Fetch one short-link record by code."""
    async with db.execute(
        """
        SELECT id, code, target_url, source, max_chars, note,
               created_at, hit_count
        FROM shorts
        WHERE code = ?
        """,
        (code,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="short code not found")
    return ShortRead.model_validate(dict(row))


@router.get("/shorts/{code}/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    code: str,
    db: aiosqlite.Connection = Depends(get_db),
) -> AnalyticsResponse:
    """Return hit count + last hit timestamp for a short code.

    The service layer that backs this lands in G4; for now we compute the
    same thing inline so the endpoint is usable end-to-end.
    """
    async with db.execute(
        "SELECT id, target_url, hit_count FROM shorts WHERE code = ?",
        (code,),
    ) as cur:
        short = await cur.fetchone()
    if not short:
        raise HTTPException(status_code=404, detail="short code not found")

    async with db.execute(
        "SELECT MAX(occurred_at) AS last_at FROM redirect_events WHERE short_id = ?",
        (short["id"],),
    ) as cur:
        evt = await cur.fetchone()

    return AnalyticsResponse(
        code=code,
        target_url=short["target_url"],
        hit_count=int(short["hit_count"] or 0),
        last_hit_at=evt["last_at"] if evt and evt["last_at"] else None,
    )


@router.get("/r/{code}")
async def redirect_to_target(
    code: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
) -> RedirectResponse:
    """Public redirect endpoint. Increments hit count and 302s to target."""
    async with db.execute(
        "SELECT id, target_url FROM shorts WHERE code = ?",
        (code,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="short code not found")

    # Atomic-ish increment + event insert. Two statements in one transaction.
    try:
        await db.execute(
            "UPDATE shorts SET hit_count = hit_count + 1 WHERE id = ?",
            (row["id"],),
        )
        await db.execute(
            """
            INSERT INTO redirect_events (short_id, user_agent, referer)
            VALUES (?, ?, ?)
            """,
            (
                row["id"],
                request.headers.get("user-agent"),
                request.headers.get("referer"),
            ),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        # Don't block the redirect on analytics failures; just log via 200.
        pass

    return RedirectResponse(url=row["target_url"], status_code=302)
