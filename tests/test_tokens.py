from __future__ import annotations

from pathlib import Path

from contextbudget.core.benchmark import run_benchmark
from contextbudget.core.pipeline import as_json_dict, run_pack
from contextbudget.core import tokens as token_module
from contextbudget.core.tokens import (
    describe_builtin_token_estimator,
    estimate_tokens_model_aligned,
    estimate_with_builtin_backend,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_exact_backend_falls_back_deterministically_when_tiktoken_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("contextbudget.core.tokens._load_tiktoken", lambda: None)
    token_module._resolve_builtin_token_estimator.cache_clear()
    try:
        text = "def login(token: str) -> bool:\n    return token.startswith('prod_')\n"

        report = describe_builtin_token_estimator(
            backend="exact_tiktoken",
            model="gpt-4o-mini",
            fallback_backend="model_aligned",
        )
        estimate = estimate_with_builtin_backend(
            text,
            backend="exact_tiktoken",
            model="gpt-4o-mini",
            fallback_backend="model_aligned",
        )

        assert report.selected_backend == "exact_tiktoken"
        assert report.effective_backend == "model_aligned"
        assert report.available is False
        assert report.fallback_used is True
        assert "tiktoken" in report.fallback_reason.lower()
        assert estimate == estimate_tokens_model_aligned(text, model="gpt-4o-mini")
    finally:
        token_module._resolve_builtin_token_estimator.cache_clear()


def test_pack_records_selected_token_estimator_backend(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[tokens]
backend = "model_aligned"
model = "gpt-4o-mini"
""".strip(),
    )
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    data = as_json_dict(run_pack("update auth flow", repo=tmp_path, max_tokens=200))

    assert data["implementations"]["token_estimator"] == "builtin.model_aligned"
    assert data["token_estimator"]["selected_backend"] == "model_aligned"
    assert data["token_estimator"]["effective_backend"] == "model_aligned"
    assert data["token_estimator"]["model"] == "gpt-4o-mini"
    assert data["token_estimator"]["fallback_used"] is False


def test_benchmark_includes_estimator_samples_and_fallback_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("contextbudget.core.tokens._load_tiktoken", lambda: None)
    token_module._resolve_builtin_token_estimator.cache_clear()
    try:
        _write(
            tmp_path / "contextbudget.toml",
            """
[tokens]
backend = "exact"
model = "gpt-4o-mini"
fallback_backend = "heuristic"
""".strip(),
        )
        _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n" * 20)

        data = run_benchmark("update auth flow", repo=tmp_path)

        assert data["implementations"]["token_estimator"] == "builtin.exact_tiktoken"
        assert data["token_estimator"]["selected_backend"] == "exact_tiktoken"
        assert data["token_estimator"]["effective_backend"] == "heuristic"
        assert data["token_estimator"]["fallback_used"] is True

        samples = {item["name"]: item for item in data["estimator_samples"]}
        assert {"task", "top_ranked_file", "packed_context"}.issubset(samples)
        exact_entry = next(
            estimator
            for estimator in samples["task"]["estimators"]
            if estimator["backend"] == "exact_tiktoken"
        )
        assert exact_entry["effective_backend"] == "heuristic"
        assert exact_entry["fallback_used"] is True
    finally:
        token_module._resolve_builtin_token_estimator.cache_clear()
