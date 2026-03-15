from __future__ import annotations

"""Delta context packaging helpers for multi-step agent workflows."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from contextbudget.core.tokens import estimate_tokens


@dataclass(slots=True)
class DeltaBudget:
    """Token accounting for a delta package against the full current pack."""

    original_tokens: int
    delta_tokens: int
    tokens_saved: int


@dataclass(slots=True)
class DeltaPackageEntry:
    """Single operation inside a delta package."""

    operation: str
    path: str
    tokens: int
    text: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeltaPackage:
    """Machine-readable delta payload that can be sent between agent steps."""

    entries: list[DeltaPackageEntry]
    files_included: list[str]
    files_removed: list[str]


@dataclass(slots=True)
class DeltaReport:
    """Comparison result between a previous and current pack artifact."""

    previous_run: str
    files_added: list[str]
    files_removed: list[str]
    changed_files: list[str]
    changed_slices: list[dict[str, Any]]
    changed_symbols: list[dict[str, Any]]
    budget: DeltaBudget
    package: DeltaPackage


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _compressed_context_map(run_data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_context = run_data.get("compressed_context", [])
    mapping: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_context, list):
        return mapping
    for item in raw_context:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        mapping[path] = dict(item)
    return mapping


def _normalized_ranges(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_ranges = entry.get("selected_ranges", [])
    if not isinstance(raw_ranges, list):
        return []
    ranges: list[dict[str, Any]] = []
    for item in raw_ranges:
        if not isinstance(item, dict):
            continue
        normalized = {
            "start_line": _to_int(item.get("start_line")),
            "end_line": _to_int(item.get("end_line")),
            "kind": str(item.get("kind", "")),
        }
        symbol = str(item.get("symbol", "")).strip()
        if symbol:
            normalized["symbol"] = symbol
        ranges.append(normalized)
    return ranges


def _entry_changed(old_entry: Mapping[str, Any], new_entry: Mapping[str, Any]) -> bool:
    if str(old_entry.get("strategy", "")) != str(new_entry.get("strategy", "")):
        return True
    if str(old_entry.get("chunk_strategy", "")) != str(new_entry.get("chunk_strategy", "")):
        return True
    if _normalized_ranges(old_entry) != _normalized_ranges(new_entry):
        return True
    return str(old_entry.get("text", "")) != str(new_entry.get("text", ""))


def _symbol_map(entry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for item in _normalized_ranges(entry):
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        kind = str(item.get("kind", "")).strip() or "symbol"
        key = f"{kind}:{symbol}"
        mapping[key] = item
    return mapping


def _slice_change(path: str, old_entry: Mapping[str, Any], new_entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": path,
        "old_strategy": str(old_entry.get("chunk_strategy", "")),
        "new_strategy": str(new_entry.get("chunk_strategy", "")),
        "old_ranges": _normalized_ranges(old_entry),
        "new_ranges": _normalized_ranges(new_entry),
        "content_changed": str(old_entry.get("text", "")) != str(new_entry.get("text", "")),
    }


def _symbol_change(path: str, old_entry: Mapping[str, Any], new_entry: Mapping[str, Any]) -> dict[str, Any] | None:
    old_symbols = _symbol_map(old_entry)
    new_symbols = _symbol_map(new_entry)
    if not old_symbols and not new_symbols:
        return None

    added = sorted(set(new_symbols) - set(old_symbols))
    removed = sorted(set(old_symbols) - set(new_symbols))
    changed = sorted(key for key in (set(old_symbols) & set(new_symbols)) if old_symbols[key] != new_symbols[key])

    if not added and not removed and not changed:
        return None
    return {
        "path": path,
        "added_symbols": added,
        "removed_symbols": removed,
        "changed_symbols": changed,
    }


def _removal_instruction(path: str) -> str:
    return f"# Remove: {path}"


def build_delta_report(
    previous_run: Mapping[str, Any],
    current_run: Mapping[str, Any],
    *,
    previous_label: str = "",
    token_estimator: Callable[[str], int] = estimate_tokens,
) -> dict[str, Any]:
    """Build a delta package between two pack artifacts."""

    previous_context = _compressed_context_map(previous_run)
    current_context = _compressed_context_map(current_run)

    previous_paths = set(previous_context)
    current_paths = set(current_context)

    files_added = sorted(current_paths - previous_paths)
    files_removed = sorted(previous_paths - current_paths)

    changed_slices: list[dict[str, Any]] = []
    changed_symbols: list[dict[str, Any]] = []
    changed_files: list[str] = []

    for path in sorted(previous_paths & current_paths):
        old_entry = previous_context[path]
        new_entry = current_context[path]
        entry_changed = _entry_changed(old_entry, new_entry)
        symbol_change = _symbol_change(path, old_entry, new_entry)

        if entry_changed:
            changed_slices.append(_slice_change(path, old_entry, new_entry))
        if symbol_change is not None:
            changed_symbols.append(symbol_change)
        if entry_changed or symbol_change is not None:
            changed_files.append(path)

    package_entries: list[DeltaPackageEntry] = []
    for path in files_added:
        entry = dict(current_context[path])
        package_entries.append(
            DeltaPackageEntry(
                operation="add",
                path=path,
                tokens=_to_int(entry.get("compressed_tokens")),
                context=entry,
            )
        )
    for path in changed_files:
        entry = dict(current_context[path])
        package_entries.append(
            DeltaPackageEntry(
                operation="update",
                path=path,
                tokens=_to_int(entry.get("compressed_tokens")),
                context=entry,
            )
        )
    for path in files_removed:
        text = _removal_instruction(path)
        package_entries.append(
            DeltaPackageEntry(
                operation="remove",
                path=path,
                tokens=token_estimator(text),
                text=text,
            )
        )

    original_tokens = _to_int((current_run.get("budget", {}) or {}).get("estimated_input_tokens"))
    delta_tokens = sum(item.tokens for item in package_entries)
    budget = DeltaBudget(
        original_tokens=original_tokens,
        delta_tokens=delta_tokens,
        tokens_saved=max(0, original_tokens - delta_tokens),
    )
    package = DeltaPackage(
        entries=package_entries,
        files_included=files_added + changed_files,
        files_removed=files_removed,
    )

    label = previous_label
    if not label:
        source = previous_run.get("generated_at", "")
        label = str(source) if source else "<previous-run>"

    return asdict(
        DeltaReport(
            previous_run=label,
            files_added=files_added,
            files_removed=files_removed,
            changed_files=changed_files,
            changed_slices=changed_slices,
            changed_symbols=changed_symbols,
            budget=budget,
            package=package,
        )
    )


def normalize_delta_report(run_data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized delta report block from a run artifact."""

    raw = run_data.get("delta", {})
    if not isinstance(raw, dict) or not raw:
        return {}
    return dict(raw)


def effective_pack_metrics(run_data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the effective delivery metrics for a run, preferring delta package data."""

    delta = normalize_delta_report(run_data)
    if not delta:
        budget = run_data.get("budget", {})
        if not isinstance(budget, dict):
            budget = {}
        files_included = run_data.get("files_included", [])
        if not isinstance(files_included, list):
            files_included = []
        return {
            "delta_enabled": False,
            "estimated_input_tokens": _to_int(budget.get("estimated_input_tokens")),
            "estimated_saved_tokens": _to_int(budget.get("estimated_saved_tokens")),
            "files_included": list(files_included),
            "files_removed": [],
        }

    budget = delta.get("budget", {})
    if not isinstance(budget, dict):
        budget = {}
    package = delta.get("package", {})
    if not isinstance(package, dict):
        package = {}
    files_included = package.get("files_included", [])
    if not isinstance(files_included, list):
        files_included = []
    files_removed = package.get("files_removed", [])
    if not isinstance(files_removed, list):
        files_removed = []
    return {
        "delta_enabled": True,
        "estimated_input_tokens": _to_int(budget.get("delta_tokens")),
        "estimated_saved_tokens": _to_int(budget.get("tokens_saved")),
        "original_input_tokens": _to_int(budget.get("original_tokens")),
        "files_included": list(files_included),
        "files_removed": list(files_removed),
    }


def resolve_previous_run_label(run_artifact: str | Path | Mapping[str, Any]) -> str:
    """Return a stable label for a previous run reference."""

    if isinstance(run_artifact, Path):
        return str(run_artifact)
    if isinstance(run_artifact, str):
        return run_artifact
    generated_at = run_artifact.get("generated_at", "")
    return str(generated_at) if generated_at else "<previous-run>"
