"""Pydantic v2 schemas for shorten / redirect / analytics.

Why a single module: the short-link domain has only four public shapes and
keeping them in one file makes the API surface easy to read at a glance.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ShortCreate(BaseModel):
    """Payload for ``POST /shorten``.

    ``url`` is the long URL to shorten. ``max_chars`` is the maximum
    character length for the generated short code (e.g. 6 -> "abc12x").
    ``note`` is an optional human-readable label.
    """

    url: HttpUrl = Field(..., description="The long URL to shorten.")
    max_chars: int = Field(
        default=6,
        ge=4,
        le=32,
        description="Length of the generated short code (4–32 chars).",
    )
    note: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Free-text note / label for the link (optional).",
    )


class ShortRead(BaseModel):
    """A single short-link record as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    target_url: str
    source: str
    max_chars: int
    note: Optional[str] = None
    created_at: datetime
    hit_count: int


class ShortList(BaseModel):
    """Response shape for ``GET /shorts`` — items wrapper."""

    items: List[ShortRead]
    total: int


class ShortenResponse(BaseModel):
    """Response shape for ``POST /shorten``.

    Mirrors the public contract documented in the project README:
    ``{"short": "abc12", "url": "https://..."}`` plus a few extras.
    """

    short: str = Field(..., description="The short code to use in /r/<short>")
    url: str = Field(..., description="The original long URL")
    short_url: str = Field(..., description="Full shortened URL path /r/<short>")
    note: Optional[str] = None


class AnalyticsResponse(BaseModel):
    """Top-level analytics for a single short link."""

    code: str
    target_url: str
    hit_count: int
    last_hit_at: Optional[datetime] = None
