from __future__ import annotations

"""Input validation helpers."""

import re

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email(email: str) -> None:
    if not email or not _EMAIL_RE.match(email):
        raise ValueError(f"Invalid email address: {email!r}")


def validate_display_name(name: str) -> None:
    if not name or not name.strip():
        raise ValueError("Display name must not be empty")
    if len(name) > 120:
        raise ValueError("Display name must be 120 characters or fewer")


def validate_task_title(title: str) -> None:
    if not title or not title.strip():
        raise ValueError("Task title must not be empty")
    if len(title) > 200:
        raise ValueError("Task title must be 200 characters or fewer")


def validate_pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    if offset < 0:
        raise ValueError("offset must be non-negative")
