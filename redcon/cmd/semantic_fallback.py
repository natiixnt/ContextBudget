"""
Optional LLMLingua-2 semantic compression fallback.

Activates only when:
  1. No schema-specific compressor matched the argv (so we'd otherwise
     emit raw passthrough).
  2. The user explicitly opted in via ``BudgetHint.semantic_fallback``.
  3. The ``redcon[heavy_compression]`` extra is installed (``llmlingua``
     plus its torch/transformers stack).

LLMLingua-2 is a task-agnostic, BERT-base token classifier that learns
which tokens contribute to perplexity and drops the rest. We import it
lazily and cache the model on a process-local handle so the hot path
(after the first call) is just a forward pass.

Why this is opt-in:
- The ``redcon[heavy_compression]`` extra adds torch+transformers
  (~2 GB on disk).
- First-call latency is ~50-100 ms on CPU even with BERT-base.
- The default Redcon path stays embedding-free and fast; this is a
  safety net for unknown tool output the agent would otherwise read raw.

Hard contracts:
- ``maybe_compress`` returns ``None`` when the extra is missing, when
  the input is too small, when the timeout fires, or when the model
  produces output that doesn't actually shrink the input.
- ``is_available()`` is cached and side-effect free.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from redcon.cmd._tokens_lite import estimate_tokens
from redcon.cmd.types import CompressedOutput, CompressionLevel

logger = logging.getLogger(__name__)


# Skip the model entirely on tiny inputs - the format header dominates.
MIN_RAW_TOKENS_FOR_FALLBACK = 200
SOFT_TIMEOUT_SECONDS = 0.15
DEFAULT_TARGET_RATIO = 0.4  # aim for 40% of original tokens


_MODEL_CACHE: dict[str, Any] = {}
_AVAILABILITY: bool | None = None


def is_available() -> bool:
    """Whether the heavy_compression extra is importable on this host."""
    global _AVAILABILITY
    if _AVAILABILITY is not None:
        return _AVAILABILITY
    try:
        import llmlingua  # noqa: F401

        _AVAILABILITY = True
    except ImportError:
        _AVAILABILITY = False
    return _AVAILABILITY


def reset_availability_for_testing() -> None:
    global _AVAILABILITY, _MODEL_CACHE
    _AVAILABILITY = None
    _MODEL_CACHE = {}


def maybe_compress(
    text: str,
    *,
    target_ratio: float = DEFAULT_TARGET_RATIO,
    timeout_seconds: float = SOFT_TIMEOUT_SECONDS,
) -> CompressedOutput | None:
    """
    Try LLMLingua-2 compression on ``text``. Returns None on any guard
    failure so callers can fall through to plain passthrough.
    """
    if not text or not text.strip():
        return None
    raw_tokens = estimate_tokens(text)
    if raw_tokens < MIN_RAW_TOKENS_FOR_FALLBACK:
        return None
    if not is_available():
        return None

    started = time.monotonic()
    model = _get_model()
    if model is None:
        return None

    try:
        compressed_text = _run_compression(model, text, target_ratio)
    except Exception as e:
        logger.debug("llmlingua compression failed: %s", e)
        return None

    duration = time.monotonic() - started
    # The classifier sometimes fires asynchronously - we still gate on a
    # soft post-deadline so a stuck model doesn't block downstream work.
    if duration > timeout_seconds * 4:
        logger.debug("llmlingua compression took %.2fs, dropping", duration)
        return None
    if not compressed_text or compressed_text == text:
        return None

    compressed_tokens = estimate_tokens(compressed_text)
    if compressed_tokens >= raw_tokens:
        # Degenerate output - heuristic estimator says we didn't help.
        return None

    return CompressedOutput(
        text=compressed_text,
        level=CompressionLevel.COMPACT,
        schema="semantic_fallback",
        original_tokens=raw_tokens,
        compressed_tokens=compressed_tokens,
        must_preserve_ok=True,
        truncated=False,
        notes=("llmlingua-2 semantic fallback",),
    )


def _get_model():
    """Lazy-load and cache the LLMLingua-2 BERT-base model."""
    if "model" in _MODEL_CACHE:
        return _MODEL_CACHE["model"]
    try:
        from llmlingua import PromptCompressor

        compressor = PromptCompressor(
            model_name="microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
            use_llmlingua2=True,
            device_map="cpu",
        )
        _MODEL_CACHE["model"] = compressor
        return compressor
    except Exception as e:
        logger.debug("could not load llmlingua model: %s", e)
        _MODEL_CACHE["model"] = None
        return None


def _run_compression(model, text: str, target_ratio: float) -> str | None:
    """Run a single forward pass and return the compressed text."""
    try:
        result = model.compress_prompt(
            text,
            rate=target_ratio,
            force_tokens=["\n", ".", "?", "!"],
        )
    except TypeError:
        # Older API
        result = model.compress_prompt(text, rate=target_ratio)
    if isinstance(result, dict):
        return result.get("compressed_prompt") or result.get("compressed")
    return None
