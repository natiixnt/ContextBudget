from __future__ import annotations

"""Local metrics store for agent observability run history.

Persists AgentRunMetrics records to ``.contextbudget/observe-history.json``
inside the repository root (or a custom path).  The store is append-only and
capped at *max_entries* to prevent unbounded growth.
"""

import json
from pathlib import Path
from typing import Any


OBSERVE_HISTORY_FILE = ".contextbudget/observe-history.json"
OBSERVE_HISTORY_FORMAT_VERSION = 1
OBSERVE_HISTORY_DEFAULT_MAX = 500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _store_path(base_dir: Path, store_file: str) -> Path:
    candidate = Path(store_file)
    if candidate.is_absolute():
        return candidate
    return base_dir / candidate


def _empty_document() -> dict[str, Any]:
    return {
        "version": OBSERVE_HISTORY_FORMAT_VERSION,
        "entries": [],
    }


def _load_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_document()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_document()
    if not isinstance(data, dict):
        return _empty_document()
    if int(data.get("version", 0) or 0) != OBSERVE_HISTORY_FORMAT_VERSION:
        return _empty_document()
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    return {"version": OBSERVE_HISTORY_FORMAT_VERSION, "entries": entries}


def _save_document(path: Path, document: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def append_observe_entry(
    entry: dict[str, Any],
    *,
    base_dir: str | Path = ".",
    store_file: str = OBSERVE_HISTORY_FILE,
    max_entries: int = OBSERVE_HISTORY_DEFAULT_MAX,
) -> bool:
    """Append an observe report dict to the local metrics store.

    Parameters
    ----------
    entry:
        JSON-serialisable dict (typically the output of
        ``contextbudget.core.observe.observe_as_dict``).
    base_dir:
        Repository root used to resolve relative *store_file* paths.
    store_file:
        Path (absolute or relative to *base_dir*) to the metrics store.
    max_entries:
        Maximum number of entries to keep (oldest are dropped first).

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the store could not be written.
    """
    path = _store_path(Path(base_dir).resolve(), store_file)
    document = _load_document(path)
    entries: list[Any] = list(document.get("entries", []))
    entries.append(entry)
    if max_entries > 0:
        entries = entries[-max_entries:]
    document["entries"] = entries
    return _save_document(path, document)


def load_observe_history(
    *,
    base_dir: str | Path = ".",
    store_file: str = OBSERVE_HISTORY_FILE,
) -> list[dict[str, Any]]:
    """Load all stored observe entries from the local metrics store.

    Parameters
    ----------
    base_dir:
        Repository root used to resolve relative *store_file* paths.
    store_file:
        Path (absolute or relative to *base_dir*) to the metrics store.

    Returns
    -------
    list[dict]
        All stored entries, oldest first.  Returns an empty list when the
        store does not yet exist or cannot be read.
    """
    path = _store_path(Path(base_dir).resolve(), store_file)
    document = _load_document(path)
    return [e for e in document.get("entries", []) if isinstance(e, dict)]


def export_observe_history_json(
    *,
    base_dir: str | Path = ".",
    store_file: str = OBSERVE_HISTORY_FILE,
) -> dict[str, Any]:
    """Return the full metrics store as a JSON-serialisable dict.

    The returned dict has the shape::

        {
            "version": 1,
            "entries": [...],
            "total_runs": <int>,
        }
    """
    entries = load_observe_history(base_dir=base_dir, store_file=store_file)
    return {
        "version": OBSERVE_HISTORY_FORMAT_VERSION,
        "total_runs": len(entries),
        "entries": entries,
    }


__all__ = [
    "OBSERVE_HISTORY_FILE",
    "OBSERVE_HISTORY_FORMAT_VERSION",
    "append_observe_entry",
    "export_observe_history_json",
    "load_observe_history",
]
