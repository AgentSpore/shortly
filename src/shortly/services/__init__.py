"""Domain services for Shortly.

A thin async-aioSQLite layer that the FastAPI routers call into. Keeping
all SQL here means route handlers stay declarative and tests can exercise
business logic without spinning up the full HTTP stack.
"""
from .short_service import (
    ShortService,
    ShortServiceError,
    CodeCollisionError,
    get_short_service,
)

__all__ = [
    "ShortService",
    "ShortServiceError",
    "CodeCollisionError",
    "get_short_service",
]
