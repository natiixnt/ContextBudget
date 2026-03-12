from __future__ import annotations

"""Cache stage primitives for context summaries."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contextbudget.schemas.models import CACHE_FILE


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    writes: int = 0


class SummaryCache:
    """Persistent summary cache backed by a JSON file."""

    def __init__(self, repo_path: Path, cache_file: str = CACHE_FILE, enabled: bool = True) -> None:
        self.repo_path = repo_path
        self.cache_path = repo_path / cache_file
        self.enabled = enabled
        self.stats = CacheStats()
        self._data: dict[str, Any] = {"summaries": {}}
        self._load()

    def _load(self) -> None:
        if not self.enabled:
            return
        if not self.cache_path.exists():
            return
        try:
            self._data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {"summaries": {}}

    def get_summary(self, key: str) -> str | None:
        if not self.enabled:
            return None
        summaries = self._data.get("summaries", {})
        if key in summaries:
            self.stats.hits += 1
            return str(summaries[key])
        return None

    def put_summary(self, key: str, summary: str) -> None:
        if not self.enabled:
            return
        summaries = self._data.setdefault("summaries", {})
        if key not in summaries:
            self.stats.writes += 1
        summaries[key] = summary

    def save(self) -> None:
        if not self.enabled:
            return
        try:
            self.cache_path.write_text(
                json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError:
            return
