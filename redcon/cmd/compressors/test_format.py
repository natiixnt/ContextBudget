"""
Shared formatter for TestRunResult across runners.

pytest, cargo test, npm test, and go test all parse into the same canonical
TestRunResult. This module formats that result at any of the three levels,
so each runner's compressor only has to handle parsing.
"""

from __future__ import annotations

import re

from redcon.cmd.types import CompressionLevel, TestFailure, TestRunResult


_CLUSTER_MIN_FAILURES = 10
_CLUSTER_MIN_SIZE = 3
_CLUSTER_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<id>"),
    (re.compile(r"\b\d+\.\d+\b"), "<f>"),
    (re.compile(r"\b\d{2,}\b"), "<n>"),
    (re.compile(r"'[^']*'"), "'<s>'"),
    (re.compile(r'"[^"]*"'), '"<s>"'),
)


def format_test_result(result: TestRunResult, level: CompressionLevel) -> str:
    if level == CompressionLevel.ULTRA:
        return _format_ultra(result)
    if level == CompressionLevel.COMPACT:
        baseline = _format_compact(result)
        clustered = _maybe_format_compact_clustered(result)
        if clustered is None:
            return baseline
        from redcon.cmd._tokens_lite import estimate_tokens

        if estimate_tokens(clustered) < estimate_tokens(baseline):
            return clustered
        return baseline
    return _format_verbose(result)


def must_preserve_patterns_for_failures(failures: tuple[TestFailure, ...]) -> tuple[str, ...]:
    """
    Build a list of regex patterns that REQUIRE every failure to survive
    compression by name. Each pattern matches a single failing test name
    literally (escaped). Used by Compressor.must_preserve_patterns.
    """
    import re

    return tuple(re.escape(f.name) for f in failures)


# --- ultra ---


def _format_ultra(r: TestRunResult) -> str:
    duration = _format_duration(r.duration_seconds)
    head = (
        f"{r.runner}: {r.passed}/{r.total} passed, {r.failed} failed"
        f"{f', {r.skipped} skipped' if r.skipped else ''}{duration}"
    )
    if not r.failures:
        return head
    first = r.failures[0]
    extra = f", first_fail={first.name}"
    return head + extra


# --- compact ---


def _format_compact(r: TestRunResult) -> str:
    # Body / message lines drop the leading two-space indent: the FAIL line
    # immediately above provides context and dropping the prefix saves one
    # cl100k token per body line on long failure listings.
    lines = [_summary_line(r)]
    if r.failures:
        lines.append("")
        for failure in r.failures:
            location = _format_location(failure)
            head = f"FAIL {failure.name}" + (f" ({location})" if location else "")
            lines.append(head)
            short_msg = _first_meaningful_line(failure.message)
            if short_msg:
                lines.append(_clip(short_msg, 200))
    if r.warnings:
        lines.append("")
        lines.append(f"warnings: {len(r.warnings)}")
        for warning in r.warnings[:5]:
            lines.append(_clip(warning, 200))
    return "\n".join(lines)


# --- verbose ---


def _format_verbose(r: TestRunResult) -> str:
    lines = [_summary_line(r)]
    if r.failures:
        lines.append("")
        for failure in r.failures:
            location = _format_location(failure)
            head = f"FAIL {failure.name}" + (f" ({location})" if location else "")
            lines.append(head)
            for msg_line in failure.message.splitlines()[:6]:
                lines.append(msg_line)
            if failure.snippet:
                for snip_line in failure.snippet[:8]:
                    lines.append(f"| {snip_line}")
            lines.append("")
    if r.warnings:
        lines.append(f"warnings: {len(r.warnings)}")
        for warning in r.warnings[:20]:
            lines.append(_clip(warning, 300))
    return "\n".join(lines).rstrip()


# --- helpers ---


def _summary_line(r: TestRunResult) -> str:
    duration = _format_duration(r.duration_seconds)
    parts = [f"{r.runner}: {r.passed} passed"]
    if r.failed:
        parts.append(f"{r.failed} failed")
    if r.skipped:
        parts.append(f"{r.skipped} skipped")
    if r.errored:
        parts.append(f"{r.errored} errored")
    parts.append(f"({r.total} total)")
    return ", ".join(parts) + duration


def _format_duration(duration: float | None) -> str:
    if duration is None:
        return ""
    if duration >= 1.0:
        return f" in {duration:.2f}s"
    return f" in {duration * 1000:.0f}ms"


def _format_location(failure: TestFailure) -> str:
    if not failure.file:
        return ""
    if failure.line:
        return f"{failure.file}:{failure.line}"
    return failure.file


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


# --- clustering (V64) ---


def _failure_skeleton(failure: TestFailure) -> str:
    """Mask values out of the first message line; equal masks share a cluster."""
    msg = _first_meaningful_line(failure.message)
    for pattern, replacement in _CLUSTER_MASKS:
        msg = pattern.sub(replacement, msg)
    return msg


def cluster_failures_by_template(
    failures: tuple[TestFailure, ...],
) -> list[list[TestFailure]]:
    """Group failures by masked-message skeleton, preserving first-seen order."""
    bucket: dict[str, list[TestFailure]] = {}
    order: list[str] = []
    for f in failures:
        key = _failure_skeleton(f)
        if key not in bucket:
            bucket[key] = []
            order.append(key)
        bucket[key].append(f)
    return [bucket[k] for k in order]


def _maybe_format_compact_clustered(r: TestRunResult) -> str | None:
    """Return a clustered COMPACT body when worth doing, else None.

    Activation: at least _CLUSTER_MIN_FAILURES failures total AND at least
    one cluster of size >= _CLUSTER_MIN_SIZE. Otherwise the per-failure
    overhead of cluster headers makes things worse.
    """
    if len(r.failures) < _CLUSTER_MIN_FAILURES:
        return None
    clusters = cluster_failures_by_template(r.failures)
    if not any(len(c) >= _CLUSTER_MIN_SIZE for c in clusters):
        return None

    lines = [_summary_line(r)]
    lines.append("")
    for idx, cluster in enumerate(clusters, start=1):
        sample = cluster[0]
        location = _format_location(sample)
        head = f"FAIL-CLUSTER {idx} x{len(cluster)}" + (
            f" ({location})" if location else ""
        )
        lines.append(head)
        short_msg = _first_meaningful_line(sample.message)
        if short_msg:
            lines.append(_clip(short_msg, 200))
        # All failing names in this cluster, comma-joined and clipped.
        joined = ", ".join(f.name for f in cluster)
        lines.append("failed: " + _clip(joined, 600))

    if r.warnings:
        lines.append("")
        lines.append(f"warnings: {len(r.warnings)}")
        for warning in r.warnings[:5]:
            lines.append(_clip(warning, 200))
    return "\n".join(lines)
