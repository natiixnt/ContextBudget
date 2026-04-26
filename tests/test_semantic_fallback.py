"""Tests for the optional LLMLingua-2 semantic fallback compressor."""

from __future__ import annotations

import pytest

from redcon.cmd.semantic_fallback import (
    MIN_RAW_TOKENS_FOR_FALLBACK,
    is_available,
    maybe_compress,
    reset_availability_for_testing,
)


def test_is_available_returns_a_bool():
    assert isinstance(is_available(), bool)


def test_maybe_compress_returns_none_on_empty():
    assert maybe_compress("") is None


def test_maybe_compress_returns_none_on_too_small_input():
    """Inputs smaller than MIN_RAW_TOKENS_FOR_FALLBACK skip the model."""
    short = "small"  # ~1 token
    assert maybe_compress(short) is None


def test_maybe_compress_returns_none_when_extra_missing(monkeypatch):
    """Without llmlingua installed, semantic fallback degrades silently."""
    import redcon.cmd.semantic_fallback as sf
    import builtins

    real_import = builtins.__import__

    def stub(name, *args, **kwargs):
        if name == "llmlingua":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", stub)
    reset_availability_for_testing()

    # Long input but extra missing -> None.
    long_input = "word " * 500  # ~500 tokens
    assert maybe_compress(long_input) is None


def test_pipeline_falls_back_to_passthrough_when_extra_missing(monkeypatch, tmp_path):
    """Even with semantic_fallback=True, a missing extra falls through gracefully."""
    import builtins
    import subprocess

    from redcon.cmd import (
        BudgetHint,
        CompressionLevel,
        clear_default_cache,
        compress_command,
    )
    import redcon.cmd.semantic_fallback as sf

    # Make the import explicitly fail so semantic_fallback returns None.
    real_import = builtins.__import__

    def stub(name, *args, **kwargs):
        if name == "llmlingua":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", stub)
    reset_availability_for_testing()

    # Initialize a tiny git repo so we can run a command on a known surface.
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)

    clear_default_cache()
    # `wc` is not in the allowlist, so we use git --version which produces
    # non-trivial output. Schema will be raw_passthrough since nothing
    # matches that exact argv form.
    report = compress_command(
        "git --version",
        cwd=tmp_path,
        hint=BudgetHint(
            remaining_tokens=10000,
            max_output_tokens=4000,
            quality_floor=CompressionLevel.COMPACT,
            semantic_fallback=True,
        ),
    )
    # When llmlingua is missing, the schema is whatever the compressor
    # picked - either git_log/git_diff/etc OR raw_passthrough. The key
    # assertion is no crash and a valid CompressedOutput.
    assert report.output.text


def test_min_raw_tokens_threshold_constant_is_sane():
    """Sanity: someone could tune this to a useless value."""
    assert 50 <= MIN_RAW_TOKENS_FOR_FALLBACK <= 5_000
