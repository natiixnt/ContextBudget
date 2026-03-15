from __future__ import annotations

"""Miscellaneous utility helpers."""

from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def paginate(items: list, limit: int, offset: int) -> dict:
    total = len(items)
    page = items[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def sanitize_string(value: str, max_length: int = 1000) -> str:
    return value.strip()[:max_length]


def build_error_response(message: str, code: int = 400) -> dict:
    return {"error": message, "status": code}


def build_success_response(data: Any, message: str = "ok") -> dict:
    return {"data": data, "message": message, "status": 200}
