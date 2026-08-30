"""Application settings via pydantic-settings.

All env-driven so deployments can override per-environment without code changes.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration.

    Values are read from environment variables (and an optional .env file).
    The defaults below make the service runnable locally with no setup
    while still being explicit about what the deployment can override.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Persistence ---------------------------------------------------------
    # aiosqlite expects a plain filesystem path, NOT a SQLAlchemy URL.
    # If you need to point at a different location, set DATABASE_URL in env.
    database_url: str = Field(
        default="shortly.db",
        description="Filesystem path to the aiosqlite database file.",
    )

    # --- HTTP ----------------------------------------------------------------
    cors_origins: List[str] = Field(
        default_factory=lambda: ["*"],
        description="Origins allowed by CORS. Use a JSON list in env when set.",
    )

    # --- Observability -------------------------------------------------------
    log_level: str = Field(default="INFO", description="Loguru/uvicorn log level.")

    # --- Domain knobs --------------------------------------------------------
    default_max_chars: int = Field(
        default=280,
        ge=20,
        le=10_000,
        description="Default maximum length for shortened output.",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v):
        """Accept CORS origins as either a list or a comma-separated string.

        Lets ops set CORS_ORIGINS="https://a.com,https://b.com" in .env
        without having to write JSON.
        """
        if v is None or v == "":
            return ["*"]
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        v = (v or "INFO").upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor so we parse env exactly once per process."""
    return Settings()
