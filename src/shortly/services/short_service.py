"""Async aiosqlite service layer for the Shortly URL shortener.

This is the *only* module that should execute SQL against the ``shorts``
and ``redirect_events`` tables. Routers (``api/shorten.py``) and tests
both depend on this layer so the business rules — code generation,
collision handling, idempotency, redirect bookkeeping — have a single
home.

Design notes
------------
* All public methods are ``async`` and accept an
  ``aiosqlite.Connection`` (the same one FastAPI's ``get_db`` yields).
  We do not manage the connection lifecycle here; the caller does.
* ``create_short`` is idempotent for the default-options case: if the
  exact same target URL has already been shortened with no note and the
  default length, the existing code is returned instead of creating a
  duplicate row. Custom notes/lengths still create a new row.
* ``record_redirect`` increments ``hit_count`` and inserts a row into
  ``redirect_events`` in the same transaction so analytics can never
  drift from the public counter.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import aiosqlite

# Confusable-free alphabet (no 0/O, 1/l/I). 32 symbols → 5 bits each.
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"

# Default code length when the caller doesn't ask for anything specific.
DEFAULT_CODE_LENGTH = 6
MAX_CODE_LENGTH = 32
MIN_CODE_LENGTH = 4

# Hard cap on collision retries before we give up.
MAX_COLLISION_RETRIES = 5


class ShortServiceError(Exception):
    """Base class for service-level errors."""


class CodeCollisionError(ShortServiceError):
    """Raised when a free code cannot be allocated within the retry budget."""

    def __init__(self, attempts: int) -> None:
        super().__init__(f"failed to allocate a unique short code after {attempts} attempts")
        self.attempts = attempts


@dataclass(slots=True)
class ShortRecord:
    """Plain dataclass view of a row in ``shorts``.

    Routes convert this to a Pydantic ``ShortRead``; tests can compare
    fields directly without going through a serializer.
    """

    id: int
    code: str
    target_url: str
    source: str
    max_chars: int
    note: Optional[str]
    created_at: datetime
    hit_count: int


@dataclass(slots=True)
class Analytics:
    """Aggregated analytics for one short code."""

    code: str
    target_url: str
    hit_count: int
    last_hit_at: Optional[datetime]


def _row_to_record(row: aiosqlite.Row) -> ShortRecord:
    """Hydrate a ``ShortRecord`` from an aioSQLite row.

    ``created_at`` is stored as ISO-8601 text by the schema default
    ``datetime('now')``; parse it for downstream Pydantic models.
    """
    created_at_raw = row["created_at"]
    if isinstance(created_at_raw, datetime):
        created_at = created_at_raw
    else:
        # SQLite's datetime('now') returns "YYYY-MM-DD HH:MM:SS".
        created_at = datetime.fromisoformat(str(created_at_raw).replace(" ", "T"))
    return ShortRecord(
        id=int(row["id"]),
        code=str(row["code"]),
        target_url=str(row["target_url"]),
        source=str(row["source"]),
        max_chars=int(row["max_chars"]),
        note=row["note"],
        created_at=created_at,
        hit_count=int(row["hit_count"] or 0),
    )


def _parse_optional_dt(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace(" ", "T"))


def _generate_code(length: int) -> str:
    """Return a random code of exactly ``length`` characters.

    Uses ``secrets.choice`` so codes are unpredictable even when several
    are generated in the same millisecond.
    """
    length = max(MIN_CODE_LENGTH, min(length, MAX_CODE_LENGTH))
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class ShortService:
    """Async service object — methods take an aiosqlite connection."""

    # -- creation ---------------------------------------------------------

    async def create_short(
        self,
        db: aiosqlite.Connection,
        *,
        target_url: str,
        source: str = "api",
        max_chars: int = DEFAULT_CODE_LENGTH,
        note: Optional[str] = None,
        idempotent_default: bool = True,
    ) -> ShortRecord:
        """Create a new short link and return the persisted record.

        Parameters
        ----------
        db:
            The request-scoped aiosqlite connection.
        target_url:
            The long URL to shorten. Caller is responsible for validating
            scheme/format — this layer trusts the URL is well-formed.
        source:
            Free-text provenance label (e.g. ``"api"``, ``"import"``).
        max_chars:
            Requested code length. Clamped to ``[MIN, MAX]``.
        note:
            Optional human-readable label.
        idempotent_default:
            When True (the default) and ``note is None`` and
            ``max_chars`` equals ``DEFAULT_CODE_LENGTH``, a previous row
            for the same URL is returned as-is instead of creating a
            duplicate. Set False to force a new row.
        """
        max_chars = max(MIN_CODE_LENGTH, min(int(max_chars or DEFAULT_CODE_LENGTH), MAX_CODE_LENGTH))

        # Idempotency: avoid duplicate codes for the same URL under defaults.
        if idempotent_default and note is None and max_chars == DEFAULT_CODE_LENGTH:
            async with db.execute(
                """
                SELECT id, code, target_url, source, max_chars, note,
                       created_at, hit_count
                FROM shorts
                WHERE target_url = ?
                  AND note IS NULL
                  AND max_chars = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (target_url, max_chars),
            ) as cur:
                existing = await cur.fetchone()
            if existing is not None:
                return _row_to_record(existing)

        last_error: Optional[Exception] = None
        for attempt in range(MAX_COLLISION_RETRIES):
            # Bump length by 1 char on each retry so a hot prefix doesn't
            # force us to loop indefinitely against a single keyspace.
            candidate = _generate_code(max_chars + attempt)
            try:
                async with db.execute(
                    """
                    INSERT INTO shorts (code, source, target_url, max_chars, note)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source, candidate, target_url, max_chars, note),
                ) as cur:
                    new_id = cur.lastrowid
                await db.commit()

                async with db.execute(
                    """
                    SELECT id, code, target_url, source, max_chars, note,
                           created_at, hit_count
                    FROM shorts
                    WHERE id = ?
                    """,
                    (new_id,),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    # Extremely unlikely: someone deleted the row in
                    # between commit and re-read. Treat as collision.
                    raise CodeCollisionError(attempt + 1)
                return _row_to_record(row)
            except aiosqlite.IntegrityError as exc:
                last_error = exc
                await db.rollback()
                continue
            except CodeCollisionError:
                raise

        raise CodeCollisionError(MAX_COLLISION_RETRIES) from last_error

    # -- reads ------------------------------------------------------------

    async def get_short(self, db: aiosqlite.Connection, code: str) -> Optional[ShortRecord]:
        """Return the record for ``code`` or ``None`` if it doesn't exist."""
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
        return _row_to_record(row) if row else None

    async def list_shorts(
        self,
        db: aiosqlite.Connection,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[ShortRecord], int]:
        """Return ``(records, total)`` for the listing endpoint.

        Newest first, with a hard cap on ``limit`` so a misconfigured
        client can't drag the table.
        """
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        async with db.execute("SELECT COUNT(*) AS n FROM shorts") as cur:
            total_row = await cur.fetchone()
        total = int(total_row["n"]) if total_row else 0

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
        return [_row_to_record(r) for r in rows], total

    async def get_analytics(self, db: aiosqlite.Connection, code: str) -> Optional[Analytics]:
        """Return hit count + last event timestamp for a short code."""
        async with db.execute(
            "SELECT id, target_url, hit_count FROM shorts WHERE code = ?",
            (code,),
        ) as cur:
            short = await cur.fetchone()
        if not short:
            return None

        async with db.execute(
            "SELECT MAX(occurred_at) AS last_at FROM redirect_events WHERE short_id = ?",
            (short["id"],),
        ) as cur:
            evt = await cur.fetchone()

        return Analytics(
            code=code,
            target_url=str(short["target_url"]),
            hit_count=int(short["hit_count"] or 0),
            last_hit_at=_parse_optional_dt(evt["last_at"]) if evt else None,
        )

    # -- writes -----------------------------------------------------------

    async def record_redirect(
        self,
        db: aiosqlite.Connection,
        *,
        code: str,
        user_agent: Optional[str] = None,
        referer: Optional[str] = None,
    ) -> Optional[str]:
        """Atomically increment hit count and append a redirect event.

        Returns the target URL if the code exists, ``None`` otherwise.
        Wraps the UPDATE + INSERT in a single transaction so the public
        counter and the analytics stream can never disagree.
        """
        async with db.execute(
            "SELECT id, target_url FROM shorts WHERE code = ?",
            (code,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None

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
                (row["id"], user_agent, referer),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return str(row["target_url"])


# --- module-level accessor ---------------------------------------------------

_service_singleton: Optional[ShortService] = None


def get_short_service() -> ShortService:
    """Return a process-wide ``ShortService`` instance.

    The service is stateless apart from configuration, so a single
    shared instance is safe and avoids the per-request allocation cost.
    """
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = ShortService()
    return _service_singleton
