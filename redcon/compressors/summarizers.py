from __future__ import annotations

"""Deterministic and adapter-based summarization helpers."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from redcon.schemas.models import SummarizerReport


SUMMARIZER_BACKEND_DETERMINISTIC = "deterministic"
SUMMARIZER_BACKEND_EXTERNAL = "external"
SUMMARIZER_EFFECTIVE_UNUSED = "unused"
SUMMARIZER_EFFECTIVE_MIXED = "mixed"


@dataclass(slots=True)
class SummaryRequest:
    """Inputs required to build a file summary."""

    task: str
    path: str
    text: str
    line_limit: int
    score: float
    keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SummaryResult:
    """Single summary generation outcome."""

    text: str
    chunk_reason: str
    chunk_strategy: str
    provider: str
    effective_backend: str
    from_cache: bool = False
    fallback_used: bool = False


class SummaryAdapter(ABC):
    """Adapter interface for external or built-in summarizers."""

    name = ""
    backend = SUMMARIZER_BACKEND_EXTERNAL

    @abstractmethod
    def summarize(self, request: SummaryRequest) -> str:
        """Return summary body text for a file."""

    def cache_identity(self) -> str:
        """Return stable cache identity for this summarizer."""

        return self.name or self.__class__.__name__.lower()


class ExternalSummaryAdapter(SummaryAdapter):
    """Marker base class for optional external summarizers."""

    backend = SUMMARIZER_BACKEND_EXTERNAL


class DeterministicSummaryAdapter(SummaryAdapter):
    """Local deterministic summary preview built from leading non-empty lines.

    Skips leading license headers, module docstrings, and shebang lines
    so the preview contains actual code or meaningful content.
    """

    name = SUMMARIZER_BACKEND_DETERMINISTIC
    backend = SUMMARIZER_BACKEND_DETERMINISTIC

    def summarize(self, request: SummaryRequest) -> str:
        raw_lines = request.text.splitlines()
        start = _skip_leading_boilerplate(raw_lines)
        lines = [line.strip() for line in raw_lines[start:] if line.strip()]
        first_lines = lines[: request.line_limit]
        summary = "\n".join(first_lines)
        if len(lines) > request.line_limit:
            summary += "\n..."
        return summary if summary else "<empty file>"


def _skip_leading_boilerplate(lines: list[str]) -> int:
    """Return the index of the first meaningful line, skipping boilerplate.

    Skips: shebang, encoding declarations, license/copyright block comments,
    and module-level triple-quoted docstrings.
    """
    i = 0
    n = len(lines)

    # Skip shebang and encoding.
    while i < n:
        s = lines[i].strip()
        if s.startswith("#!") or s.startswith("# -*-") or s.startswith("# coding"):
            i += 1
        else:
            break

    # Skip blank lines.
    while i < n and not lines[i].strip():
        i += 1

    # Skip block comment (/* ... */ style for JS/Go/Java/Rust).
    if i < n and lines[i].strip().startswith("/*"):
        while i < n:
            if "*/" in lines[i]:
                i += 1
                break
            i += 1

    # Skip leading # comment block (license/copyright headers in Python/Ruby).
    comment_start = i
    while i < n and lines[i].strip().startswith("#"):
        i += 1
    # Only skip if block was 3+ lines (likely a license header, not a short comment).
    if i - comment_start < 3:
        i = comment_start

    # Skip blank lines after comments.
    while i < n and not lines[i].strip():
        i += 1

    # Skip triple-quoted module docstring (Python).
    if i < n:
        s = lines[i].strip()
        for delim in ('"""', "'''"):
            if s.startswith(delim):
                rest = s[len(delim):]
                if rest.endswith(delim) and len(rest) >= len(delim):
                    i += 1  # Single-line docstring.
                    break
                i += 1
                while i < n and delim not in lines[i]:
                    i += 1
                if i < n:
                    i += 1  # Skip closing line.
                break

    # Skip blank lines after docstring.
    while i < n and not lines[i].strip():
        i += 1

    return i


_EXTERNAL_SUMMARY_ADAPTERS: dict[str, ExternalSummaryAdapter] = {}


def register_external_summarizer_adapter(name: str, adapter: ExternalSummaryAdapter) -> None:
    """Register an external summarizer adapter under a stable name."""

    normalized = normalize_summarizer_adapter_name(name)
    if not isinstance(adapter, ExternalSummaryAdapter):
        raise TypeError("adapter must be an instance of ExternalSummaryAdapter")
    _EXTERNAL_SUMMARY_ADAPTERS[normalized] = adapter


def unregister_external_summarizer_adapter(name: str) -> None:
    """Remove a registered external summarizer adapter."""

    _EXTERNAL_SUMMARY_ADAPTERS.pop(normalize_summarizer_adapter_name(name), None)


def get_external_summarizer_adapter(name: str) -> ExternalSummaryAdapter | None:
    """Lookup an external summarizer adapter by name."""

    return _EXTERNAL_SUMMARY_ADAPTERS.get(normalize_summarizer_adapter_name(name))


def normalize_summarizer_backend_name(value: str | None) -> str:
    """Normalize configured summarizer backend names."""

    candidate = str(value or SUMMARIZER_BACKEND_DETERMINISTIC).strip().lower()
    aliases = {
        "builtin": SUMMARIZER_BACKEND_DETERMINISTIC,
        "default": SUMMARIZER_BACKEND_DETERMINISTIC,
        "deterministic": SUMMARIZER_BACKEND_DETERMINISTIC,
        "external": SUMMARIZER_BACKEND_EXTERNAL,
        "adapter": SUMMARIZER_BACKEND_EXTERNAL,
    }
    normalized = aliases.get(candidate)
    if normalized is None:
        raise ValueError(
            "Unsupported summarizer backend "
            f"{value!r}. Expected one of: deterministic, external."
        )
    return normalized


def normalize_summarizer_adapter_name(value: str | None) -> str:
    """Normalize adapter names used for config and registry lookups."""

    return str(value or "").strip().lower()


def normalize_summarizer_report(data: Mapping[str, Any]) -> dict[str, Any]:
    """Read summarizer metadata from a run artifact or report payload."""

    raw = data.get("summarizer")
    if not isinstance(raw, Mapping):
        return {
            "selected_backend": SUMMARIZER_BACKEND_DETERMINISTIC,
            "external_adapter": "",
            "effective_backend": SUMMARIZER_BACKEND_DETERMINISTIC,
            "external_configured": False,
            "external_resolved": False,
            "fallback_used": False,
            "fallback_count": 0,
            "summary_count": 0,
            "logs": [],
        }

    logs = raw.get("logs", [])
    return {
        "selected_backend": str(raw.get("selected_backend", SUMMARIZER_BACKEND_DETERMINISTIC)),
        "external_adapter": str(raw.get("external_adapter", "")),
        "effective_backend": str(raw.get("effective_backend", SUMMARIZER_BACKEND_DETERMINISTIC)),
        "external_configured": bool(raw.get("external_configured", False)),
        "external_resolved": bool(raw.get("external_resolved", False)),
        "fallback_used": bool(raw.get("fallback_used", False)),
        "fallback_count": _to_int(raw.get("fallback_count", 0)),
        "summary_count": _to_int(raw.get("summary_count", 0)),
        "logs": [str(item) for item in logs] if isinstance(logs, list) else [],
    }


def summarizer_report_as_dict(report: SummarizerReport) -> dict[str, Any]:
    """Convert typed summarizer metadata to a JSON-safe mapping."""

    return asdict(report)


class SummarizationService:
    """Resolve configured summarizer behavior with deterministic fallback."""

    def __init__(self, *, backend: str = SUMMARIZER_BACKEND_DETERMINISTIC, adapter_name: str = "") -> None:
        selected_backend = normalize_summarizer_backend_name(backend)
        normalized_adapter_name = normalize_summarizer_adapter_name(adapter_name)
        self._deterministic = DeterministicSummaryAdapter()
        self._external = get_external_summarizer_adapter(normalized_adapter_name) if normalized_adapter_name else None
        self._effective_backends: set[str] = set()
        self._seen_logs: set[str] = set()
        self._report = SummarizerReport(
            selected_backend=selected_backend,
            external_adapter=normalized_adapter_name,
            effective_backend=SUMMARIZER_EFFECTIVE_UNUSED,
            external_configured=selected_backend == SUMMARIZER_BACKEND_EXTERNAL,
            external_resolved=self._external is not None,
        )

        if selected_backend == SUMMARIZER_BACKEND_EXTERNAL and normalized_adapter_name and self._external is None:
            self._record_log(
                f"External summarizer adapter '{normalized_adapter_name}' is not registered. "
                "Falling back to deterministic summaries."
            )

    def summarize(self, *, cache, cache_key_prefix: str, request: SummaryRequest) -> SummaryResult:
        """Return a summary using configured adapter behavior with deterministic fallback."""

        if self._report.selected_backend == SUMMARIZER_BACKEND_EXTERNAL and self._external is not None:
            external_key = self._build_cache_key(
                cache_key_prefix,
                backend=SUMMARIZER_BACKEND_EXTERNAL,
                provider_identity=self._external.cache_identity(),
                line_limit=request.line_limit,
            )
            cached = cache.get_summary(external_key)
            if cached is not None:
                return self._build_result(
                    text=cached,
                    chunk_reason=f"cached external summary via adapter '{self._external_label()}'",
                    chunk_strategy="summary-external",
                    provider=self._external_label(),
                    effective_backend=SUMMARIZER_BACKEND_EXTERNAL,
                    from_cache=True,
                )
            try:
                body = self._normalize_summary_body(self._external.summarize(request))
            except Exception as exc:
                return self._fallback_to_deterministic(
                    cache=cache,
                    cache_key_prefix=cache_key_prefix,
                    request=request,
                    reason=(
                        f"External summarizer adapter '{self._external_label()}' failed for {request.path} "
                        f"({exc.__class__.__name__}: {exc}). Falling back to deterministic summary."
                    ),
                )
            summary_text = self._format_summary_text(request.path, body)
            cache.put_summary(external_key, summary_text)
            return self._build_result(
                text=summary_text,
                chunk_reason=f"external summary via adapter '{self._external_label()}'",
                chunk_strategy="summary-external",
                provider=self._external_label(),
                effective_backend=SUMMARIZER_BACKEND_EXTERNAL,
                from_cache=False,
            )

        if self._report.selected_backend == SUMMARIZER_BACKEND_EXTERNAL:
            adapter_name = self._report.external_adapter or "external"
            return self._fallback_to_deterministic(
                cache=cache,
                cache_key_prefix=cache_key_prefix,
                request=request,
                reason=(
                    f"External summarizer adapter '{adapter_name}' is unavailable for {request.path}. "
                    "Falling back to deterministic summary."
                ),
            )

        return self._deterministic_result(
            cache=cache,
            cache_key_prefix=cache_key_prefix,
            request=request,
            fallback_used=False,
        )

    def snapshot(self) -> SummarizerReport:
        """Return artifact-ready summarizer metadata."""

        return SummarizerReport(
            selected_backend=self._report.selected_backend,
            external_adapter=self._report.external_adapter,
            effective_backend=self._effective_backend_label(),
            external_configured=self._report.external_configured,
            external_resolved=self._report.external_resolved,
            fallback_used=self._report.fallback_used,
            fallback_count=self._report.fallback_count,
            summary_count=self._report.summary_count,
            logs=list(self._report.logs),
        )

    def _deterministic_result(
        self,
        *,
        cache,
        cache_key_prefix: str,
        request: SummaryRequest,
        fallback_used: bool,
    ) -> SummaryResult:
        deterministic_key = self._build_cache_key(
            cache_key_prefix,
            backend=SUMMARIZER_BACKEND_DETERMINISTIC,
            provider_identity=self._deterministic.cache_identity(),
            line_limit=request.line_limit,
        )
        cached = cache.get_summary(deterministic_key)
        if cached is not None:
            chunk_reason = "cached deterministic summary preview"
            if fallback_used:
                chunk_reason = "cached deterministic fallback summary"
            return self._build_result(
                text=cached,
                chunk_reason=chunk_reason,
                chunk_strategy="summary-preview",
                provider=SUMMARIZER_BACKEND_DETERMINISTIC,
                effective_backend=SUMMARIZER_BACKEND_DETERMINISTIC,
                from_cache=True,
                fallback_used=fallback_used,
            )

        body = self._normalize_summary_body(self._deterministic.summarize(request))
        summary_text = self._format_summary_text(request.path, body)
        cache.put_summary(deterministic_key, summary_text)
        chunk_reason = "deterministic summary preview of leading content"
        if fallback_used:
            chunk_reason = "deterministic fallback summary"
        return self._build_result(
            text=summary_text,
            chunk_reason=chunk_reason,
            chunk_strategy="summary-preview",
            provider=SUMMARIZER_BACKEND_DETERMINISTIC,
            effective_backend=SUMMARIZER_BACKEND_DETERMINISTIC,
            from_cache=False,
            fallback_used=fallback_used,
        )

    def _fallback_to_deterministic(
        self,
        *,
        cache,
        cache_key_prefix: str,
        request: SummaryRequest,
        reason: str,
    ) -> SummaryResult:
        self._report.fallback_used = True
        self._report.fallback_count += 1
        self._record_log(reason)
        return self._deterministic_result(
            cache=cache,
            cache_key_prefix=cache_key_prefix,
            request=request,
            fallback_used=True,
        )

    def _build_result(
        self,
        *,
        text: str,
        chunk_reason: str,
        chunk_strategy: str,
        provider: str,
        effective_backend: str,
        from_cache: bool,
        fallback_used: bool = False,
    ) -> SummaryResult:
        self._effective_backends.add(effective_backend)
        self._report.summary_count += 1
        self._report.effective_backend = self._effective_backend_label()
        return SummaryResult(
            text=text,
            chunk_reason=chunk_reason,
            chunk_strategy=chunk_strategy,
            provider=provider,
            effective_backend=effective_backend,
            from_cache=from_cache,
            fallback_used=fallback_used,
        )

    def _effective_backend_label(self) -> str:
        if not self._effective_backends:
            return SUMMARIZER_EFFECTIVE_UNUSED
        if len(self._effective_backends) == 1:
            return next(iter(self._effective_backends))
        return SUMMARIZER_EFFECTIVE_MIXED

    def _external_label(self) -> str:
        if self._report.external_adapter:
            return self._report.external_adapter
        if self._external is not None and self._external.name:
            return self._external.name
        return SUMMARIZER_BACKEND_EXTERNAL

    @staticmethod
    def _build_cache_key(cache_key_prefix: str, *, backend: str, provider_identity: str, line_limit: int) -> str:
        # Include line_limit in key so changing summary_preview_lines
        # invalidates stale cached summaries.
        return f"{cache_key_prefix}:summary:{backend}:{provider_identity}:lines={line_limit}:v2"

    @staticmethod
    def _normalize_summary_body(value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("empty summary")
        return text

    @staticmethod
    def _format_summary_text(path: str, body: str) -> str:
        return f"# Summary: {path}\n{body}"

    def _record_log(self, message: str) -> None:
        normalized = str(message).strip()
        if not normalized or normalized in self._seen_logs:
            return
        self._seen_logs.add(normalized)
        self._report.logs.append(normalized)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
