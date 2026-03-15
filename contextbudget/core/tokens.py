from __future__ import annotations

"""Token estimation helpers and built-in backend resolution."""

from dataclasses import dataclass
from functools import lru_cache
import importlib
import math
from typing import Any, Callable, Mapping, Sequence

from contextbudget.schemas.models import TokenEstimatorReport


DEFAULT_MODEL_ALIGNED_MODEL = "gpt-4o-mini"

_HEURISTIC_BACKENDS = {"heuristic", "simple", "char4"}
_MODEL_ALIGNED_BACKENDS = {"model_aligned", "model-aligned"}
_EXACT_BACKENDS = {"exact", "exact_tiktoken", "exact-tiktoken", "tiktoken"}

_MODEL_CHAR_RATIO_PROFILES: tuple[tuple[str, float], ...] = (
    ("o4-mini", 3.45),
    ("o3", 3.45),
    ("gpt-4o-mini", 3.5),
    ("gpt-4o", 3.55),
    ("gpt-4.1-mini", 3.55),
    ("gpt-4.1", 3.6),
    ("claude", 3.9),
    ("codestral", 3.8),
    ("mistral", 3.85),
    ("ministral", 3.85),
    ("devstral", 3.8),
    ("local-llm", 4.0),
    ("gpt-oss", 3.75),
    ("llama", 4.0),
    ("qwen", 3.9),
    ("gemma", 4.0),
    ("phi", 4.0),
)


@dataclass(frozen=True, slots=True)
class _ResolvedBuiltinTokenEstimator:
    estimate: Callable[[str], int]
    selected_backend: str
    effective_backend: str
    uncertainty: str
    model: str
    encoding: str
    available: bool
    fallback_used: bool
    fallback_reason: str
    notes: tuple[str, ...]

    def to_report(self) -> TokenEstimatorReport:
        return TokenEstimatorReport(
            selected_backend=self.selected_backend,
            effective_backend=self.effective_backend,
            uncertainty=self.uncertainty,
            model=self.model,
            encoding=self.encoding,
            available=self.available,
            fallback_used=self.fallback_used,
            fallback_reason=self.fallback_reason,
            notes=list(self.notes),
        )


def _normalize_backend_name(name: str) -> str:
    normalized = str(name).strip().lower()
    if normalized in _MODEL_ALIGNED_BACKENDS:
        return "model_aligned"
    if normalized in _EXACT_BACKENDS:
        return "exact_tiktoken"
    return "heuristic"


def _model_chars_per_token(model: str) -> float:
    normalized = str(model).strip().lower()
    for prefix, ratio in _MODEL_CHAR_RATIO_PROFILES:
        if normalized.startswith(prefix):
            return ratio
    return 3.7


def _heuristic_estimate(text: str, chars_per_token: float) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / chars_per_token))


def estimate_tokens(text: str) -> int:
    """Estimate approximate token count using the default heuristic backend."""

    return estimate_tokens_heuristic(text)


def estimate_tokens_heuristic(text: str) -> int:
    """Estimate tokens with the default deterministic char/4 heuristic."""

    return _heuristic_estimate(text, 4.0)


def estimate_tokens_model_aligned(text: str, *, model: str = DEFAULT_MODEL_ALIGNED_MODEL) -> int:
    """Estimate tokens using a deterministic model-family character ratio."""

    return _heuristic_estimate(text, _model_chars_per_token(model))


def estimate_with_builtin_backend(
    text: str,
    *,
    backend: str,
    model: str = DEFAULT_MODEL_ALIGNED_MODEL,
    encoding: str = "",
    fallback_backend: str = "heuristic",
) -> int:
    """Estimate tokens using one of ContextBudget's built-in backends."""

    resolved = _resolve_builtin_token_estimator(
        backend=_normalize_backend_name(backend),
        model=model or DEFAULT_MODEL_ALIGNED_MODEL,
        encoding=encoding,
        fallback_backend=_normalize_backend_name(fallback_backend),
    )
    return resolved.estimate(text)


def describe_builtin_token_estimator(
    *,
    backend: str,
    model: str = DEFAULT_MODEL_ALIGNED_MODEL,
    encoding: str = "",
    fallback_backend: str = "heuristic",
) -> TokenEstimatorReport:
    """Return a stable report describing the configured built-in estimator backend."""

    resolved = _resolve_builtin_token_estimator(
        backend=_normalize_backend_name(backend),
        model=model or DEFAULT_MODEL_ALIGNED_MODEL,
        encoding=encoding,
        fallback_backend=_normalize_backend_name(fallback_backend),
    )
    return resolved.to_report()


def normalize_token_estimator_report(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize token-estimator metadata from current or legacy artifacts."""

    raw = data.get("token_estimator")
    if isinstance(raw, Mapping):
        return {
            "selected_backend": str(raw.get("selected_backend", "heuristic")),
            "effective_backend": str(raw.get("effective_backend", raw.get("selected_backend", "heuristic"))),
            "uncertainty": str(raw.get("uncertainty", "approximate")),
            "model": str(raw.get("model", "")),
            "encoding": str(raw.get("encoding", "")),
            "available": bool(raw.get("available", True)),
            "fallback_used": bool(raw.get("fallback_used", False)),
            "fallback_reason": str(raw.get("fallback_reason", "")),
            "notes": [str(item) for item in raw.get("notes", [])] if isinstance(raw.get("notes"), list) else [],
        }

    implementations = data.get("implementations", {})
    if isinstance(implementations, Mapping):
        plugin_name = str(implementations.get("token_estimator", "")).strip()
    else:
        plugin_name = ""
    selected_backend = {
        "builtin.model_aligned": "model_aligned",
        "builtin.exact_tiktoken": "exact_tiktoken",
    }.get(plugin_name, "heuristic")
    return {
        "selected_backend": selected_backend,
        "effective_backend": selected_backend,
        "uncertainty": "exact" if selected_backend == "exact_tiktoken" else "approximate",
        "model": "",
        "encoding": "",
        "available": True,
        "fallback_used": False,
        "fallback_reason": "",
        "notes": [],
    }


def compare_builtin_token_estimators(
    samples: Sequence[Mapping[str, Any]],
    *,
    model: str = DEFAULT_MODEL_ALIGNED_MODEL,
    encoding: str = "",
    fallback_backend: str = "heuristic",
) -> list[dict[str, Any]]:
    """Compare built-in token estimators on named text samples."""

    backends = ("heuristic", "model_aligned", "exact_tiktoken")
    results: list[dict[str, Any]] = []
    for sample in samples:
        text = str(sample.get("text", ""))
        if not text:
            continue
        item = {
            "name": str(sample.get("name", "sample")),
            "chars": len(text),
            "estimators": [],
        }
        path = str(sample.get("path", "")).strip()
        if path:
            item["path"] = path
        for backend in backends:
            report = describe_builtin_token_estimator(
                backend=backend,
                model=model,
                encoding=encoding,
                fallback_backend=fallback_backend,
            )
            estimator_entry = {
                "backend": backend,
                "effective_backend": report.effective_backend,
                "estimated_tokens": estimate_with_builtin_backend(
                    text,
                    backend=backend,
                    model=model,
                    encoding=encoding,
                    fallback_backend=fallback_backend,
                ),
                "uncertainty": report.uncertainty,
                "fallback_used": report.fallback_used,
            }
            if report.model:
                estimator_entry["model"] = report.model
            if report.encoding:
                estimator_entry["encoding"] = report.encoding
            if report.fallback_reason:
                estimator_entry["fallback_reason"] = report.fallback_reason
            item["estimators"].append(estimator_entry)
        results.append(item)
    return results


def builtin_token_estimator_plugin_name(backend: str) -> str:
    """Map a built-in backend selection to the registered plugin name."""

    normalized = _normalize_backend_name(backend)
    if normalized == "model_aligned":
        return "builtin.model_aligned"
    if normalized == "exact_tiktoken":
        return "builtin.exact_tiktoken"
    return "builtin.char4"


@lru_cache(maxsize=32)
def _resolve_builtin_token_estimator(
    *,
    backend: str,
    model: str,
    encoding: str,
    fallback_backend: str,
) -> _ResolvedBuiltinTokenEstimator:
    if backend == "model_aligned":
        chars_per_token = _model_chars_per_token(model)
        return _ResolvedBuiltinTokenEstimator(
            estimate=lambda text: _heuristic_estimate(text, chars_per_token),
            selected_backend="model_aligned",
            effective_backend="model_aligned",
            uncertainty="approximate",
            model=model,
            encoding="",
            available=True,
            fallback_used=False,
            fallback_reason="",
            notes=(f"Deterministic model-family profile: 1 token ~= {chars_per_token:.2f} chars.",),
        )

    if backend == "exact_tiktoken":
        return _resolve_exact_tiktoken_estimator(
            model=model,
            encoding=encoding,
            fallback_backend=fallback_backend,
        )

    return _ResolvedBuiltinTokenEstimator(
        estimate=estimate_tokens_heuristic,
        selected_backend="heuristic",
        effective_backend="heuristic",
        uncertainty="approximate",
        model="",
        encoding="",
        available=True,
        fallback_used=False,
        fallback_reason="",
        notes=("Deterministic baseline heuristic: 1 token ~= 4 chars.",),
    )


def _resolve_exact_tiktoken_estimator(
    *,
    model: str,
    encoding: str,
    fallback_backend: str,
) -> _ResolvedBuiltinTokenEstimator:
    tiktoken_module = _load_tiktoken()
    if tiktoken_module is None:
        return _fallback_exact_tiktoken_estimator(
            model=model,
            encoding=encoding,
            fallback_backend=fallback_backend,
            reason='Optional dependency "tiktoken" is not installed.',
        )

    encoder, resolved_encoding, failure_reason = _resolve_tiktoken_encoder(
        tiktoken_module=tiktoken_module,
        model=model,
        encoding=encoding,
    )
    if encoder is None:
        return _fallback_exact_tiktoken_estimator(
            model=model,
            encoding=encoding,
            fallback_backend=fallback_backend,
            reason=failure_reason,
        )

    return _ResolvedBuiltinTokenEstimator(
        estimate=lambda text: len(encoder.encode(text or "")),
        selected_backend="exact_tiktoken",
        effective_backend="exact_tiktoken",
        uncertainty="exact",
        model=model,
        encoding=resolved_encoding,
        available=True,
        fallback_used=False,
        fallback_reason="",
        notes=('Exact local tokenization via optional "tiktoken".',),
    )


def _fallback_exact_tiktoken_estimator(
    *,
    model: str,
    encoding: str,
    fallback_backend: str,
    reason: str,
) -> _ResolvedBuiltinTokenEstimator:
    normalized_fallback = fallback_backend if fallback_backend != "exact_tiktoken" else "heuristic"
    fallback = _resolve_builtin_token_estimator(
        backend=normalized_fallback,
        model=model,
        encoding=encoding,
        fallback_backend="heuristic",
    )
    return _ResolvedBuiltinTokenEstimator(
        estimate=fallback.estimate,
        selected_backend="exact_tiktoken",
        effective_backend=fallback.effective_backend,
        uncertainty=fallback.uncertainty,
        model=model,
        encoding=fallback.encoding,
        available=False,
        fallback_used=True,
        fallback_reason=reason,
        notes=(reason, f'Falling back to "{fallback.effective_backend}".'),
    )


def _load_tiktoken():
    try:
        return importlib.import_module("tiktoken")
    except ModuleNotFoundError:
        return None


def _resolve_tiktoken_encoder(*, tiktoken_module, model: str, encoding: str) -> tuple[Any | None, str, str]:
    if encoding:
        try:
            return tiktoken_module.get_encoding(encoding), encoding, ""
        except Exception:  # pragma: no cover - optional dependency failure shape
            return None, "", f'Tiktoken encoding "{encoding}" is unavailable.'

    try:
        encoder = tiktoken_module.encoding_for_model(model)
    except Exception:  # pragma: no cover - optional dependency failure shape
        return None, "", f'Tiktoken has no encoding mapping for model "{model}".'

    encoding_name = str(getattr(encoder, "name", "") or "")
    return encoder, encoding_name, ""
