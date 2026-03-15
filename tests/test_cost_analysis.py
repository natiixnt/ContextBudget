from __future__ import annotations

"""Tests for cost analytics engine and pricing table.

Covers:
  - redcon.core.agent_cost: resolve_model_pricing, list_known_models, _tokens_to_usd
  - redcon.core.cost_analysis: load_run_data, compute_cost_analysis
  - CLI: redcon cost-analysis
"""

import json
from pathlib import Path

import pytest

from redcon.core.agent_cost import (
    BUILTIN_MODEL_PRICING,
    ModelPricing,
    _tokens_to_usd,
    list_known_models,
    resolve_model_pricing,
)
from redcon.core.cost_analysis import (
    compute_cost_analysis,
    load_run_data,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_run(
    *,
    input_tokens: int = 18340,
    saved_tokens: int = 31660,
    compressed_context: list[dict] | None = None,
    task: str = "refactor auth middleware",
    repo: str = "repo/path",
) -> dict:
    """Build a minimal run artifact dict."""
    if compressed_context is None:
        compressed_context = [
            {
                "path": "src/auth.py",
                "strategy": "symbols",
                "original_tokens": 9000,
                "compressed_tokens": 3000,
            },
            {
                "path": "src/middleware.py",
                "strategy": "full",
                "original_tokens": 2000,
                "compressed_tokens": 2000,
            },
        ]
    return {
        "command": "pack",
        "task": task,
        "repo": repo,
        "generated_at": "2026-03-15T10:00:00+00:00",
        "max_tokens": 64000,
        "files_included": ["src/auth.py", "src/middleware.py"],
        "files_skipped": [],
        "budget": {
            "estimated_input_tokens": input_tokens,
            "estimated_saved_tokens": saved_tokens,
            "quality_risk_estimate": "low",
        },
        "compressed_context": compressed_context,
    }


# ---------------------------------------------------------------------------
# agent_cost: resolve_model_pricing
# ---------------------------------------------------------------------------

class TestResolveModelPricing:
    def test_exact_match_openai(self) -> None:
        p = resolve_model_pricing("gpt-4o")
        assert p.provider == "openai"
        assert p.input_per_1m == 2.50
        assert p.output_per_1m == 10.00

    def test_exact_match_anthropic(self) -> None:
        p = resolve_model_pricing("claude-sonnet-4-6")
        assert p.provider == "anthropic"
        assert p.input_per_1m == 3.00

    def test_exact_match_open_source(self) -> None:
        p = resolve_model_pricing("llama-3.3-70b")
        assert p.provider == "meta"
        assert p.input_per_1m == 0.59

    def test_alias_opus(self) -> None:
        p = resolve_model_pricing("opus")
        assert p.provider == "anthropic"
        assert p.input_per_1m == 15.00

    def test_alias_sonnet(self) -> None:
        p = resolve_model_pricing("sonnet")
        assert p.provider == "anthropic"

    def test_alias_llama(self) -> None:
        p = resolve_model_pricing("llama")
        assert p.provider == "meta"

    def test_alias_deepseek(self) -> None:
        p = resolve_model_pricing("deepseek")
        assert p.provider == "deepseek"

    def test_prefix_match(self) -> None:
        # "gpt-4o-mini-2024" should match "gpt-4o-mini" via substring
        p = resolve_model_pricing("gpt-4o-mini-2024")
        assert p.input_per_1m == 0.15

    def test_unknown_model_defaults_to_gpt4o(self) -> None:
        p = resolve_model_pricing("totally-unknown-model-xyz")
        assert p.input_per_1m == 2.50
        assert "not in pricing table" in p.notes

    def test_custom_price_override_both(self) -> None:
        p = resolve_model_pricing("gpt-4o", price_per_1m_input=1.00, price_per_1m_output=4.00)
        assert p.provider == "custom"
        assert p.input_per_1m == 1.00
        assert p.output_per_1m == 4.00

    def test_custom_price_override_input_only(self) -> None:
        p = resolve_model_pricing("gpt-4o", price_per_1m_input=0.50)
        assert p.input_per_1m == 0.50
        assert p.output_per_1m == 10.00  # original gpt-4o output price

    def test_case_insensitive(self) -> None:
        p = resolve_model_pricing("GPT-4O")
        assert p.provider == "openai"

    def test_all_providers_covered(self) -> None:
        providers = {p.provider for p in BUILTIN_MODEL_PRICING.values()}
        assert "openai" in providers
        assert "anthropic" in providers
        assert "meta" in providers
        assert "deepseek" in providers
        assert "google" in providers


# ---------------------------------------------------------------------------
# agent_cost: _tokens_to_usd
# ---------------------------------------------------------------------------

class TestTokensToUsd:
    def test_zero_tokens(self) -> None:
        assert _tokens_to_usd(0, 2.50) == 0.0

    def test_one_million_tokens(self) -> None:
        assert _tokens_to_usd(1_000_000, 2.50) == pytest.approx(2.50)

    def test_fractional(self) -> None:
        assert _tokens_to_usd(500_000, 2.50) == pytest.approx(1.25)

    def test_free_model(self) -> None:
        assert _tokens_to_usd(10_000_000, 0.0) == 0.0


# ---------------------------------------------------------------------------
# agent_cost: list_known_models
# ---------------------------------------------------------------------------

class TestListKnownModels:
    def test_returns_list(self) -> None:
        models = list_known_models()
        assert isinstance(models, list)
        assert len(models) > 10

    def test_each_row_has_required_keys(self) -> None:
        for row in list_known_models():
            assert {"model", "provider", "input_per_1m_usd", "output_per_1m_usd"}.issubset(row)

    def test_sorted_by_model_name(self) -> None:
        models = list_known_models()
        names = [m["model"] for m in models]
        assert names == sorted(names)

    def test_openai_present(self) -> None:
        providers = {m["provider"] for m in list_known_models()}
        assert "openai" in providers

    def test_anthropic_present(self) -> None:
        providers = {m["provider"] for m in list_known_models()}
        assert "anthropic" in providers


# ---------------------------------------------------------------------------
# cost_analysis: load_run_data
# ---------------------------------------------------------------------------

class TestLoadRunData:
    def test_loads_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "run.json"
        p.write_text(json.dumps({"command": "pack", "budget": {}}), encoding="utf-8")
        data = load_run_data(p)
        assert data["command"] == "pack"

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_run_data(tmp_path / "nonexistent.json")

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        p = tmp_path / "run.json"
        p.write_text(json.dumps({"x": 1}), encoding="utf-8")
        data = load_run_data(str(p))
        assert data["x"] == 1


# ---------------------------------------------------------------------------
# cost_analysis: compute_cost_analysis
# ---------------------------------------------------------------------------

class TestComputeCostAnalysis:
    def test_baseline_equals_input_plus_saved(self) -> None:
        run = _make_run(input_tokens=18340, saved_tokens=31660)
        result = compute_cost_analysis(run, model="gpt-4o")
        assert result["baseline_tokens"] == 50000
        assert result["optimized_tokens"] == 18340
        assert result["saved_tokens"] == 31660

    def test_savings_pct_correct(self) -> None:
        run = _make_run(input_tokens=50000, saved_tokens=50000)
        result = compute_cost_analysis(run, model="gpt-4o")
        assert result["savings_pct"] == pytest.approx(50.0)

    def test_cost_maths_gpt4o(self) -> None:
        # gpt-4o: $2.50 / MTok input
        run = _make_run(input_tokens=1_000_000, saved_tokens=0)
        result = compute_cost_analysis(run, model="gpt-4o")
        assert result["baseline_cost_usd"] == pytest.approx(2.50, rel=1e-4)
        assert result["optimized_cost_usd"] == pytest.approx(2.50, rel=1e-4)
        assert result["saved_cost_usd"] == pytest.approx(0.0, abs=1e-6)

    def test_savings_reduce_cost(self) -> None:
        run = _make_run(input_tokens=18340, saved_tokens=31660)
        result = compute_cost_analysis(run, model="gpt-4o")
        assert result["optimized_cost_usd"] < result["baseline_cost_usd"]
        assert result["saved_cost_usd"] > 0

    def test_anthropic_model_pricing(self) -> None:
        run = _make_run(input_tokens=1_000_000, saved_tokens=0)
        result = compute_cost_analysis(run, model="claude-sonnet-4-6")
        assert result["provider"] == "anthropic"
        assert result["baseline_cost_usd"] == pytest.approx(3.00, rel=1e-4)

    def test_custom_price_override(self) -> None:
        run = _make_run(input_tokens=1_000_000, saved_tokens=0)
        result = compute_cost_analysis(run, model="gpt-4o", price_per_1m_input=1.00)
        assert result["baseline_cost_usd"] == pytest.approx(1.00, rel=1e-4)

    def test_per_file_breakdown_present(self) -> None:
        run = _make_run()
        result = compute_cost_analysis(run, model="gpt-4o")
        per_file = result["per_file"]
        assert len(per_file) == 2
        assert per_file[0]["path"] == "src/auth.py"
        assert per_file[0]["saved_tokens"] == 6000

    def test_per_file_costs_non_negative(self) -> None:
        run = _make_run()
        result = compute_cost_analysis(run, model="gpt-4o")
        for row in result["per_file"]:
            assert row["baseline_cost_usd"] >= 0
            assert row["optimized_cost_usd"] >= 0
            assert row["saved_cost_usd"] >= 0

    def test_run_meta_populated(self) -> None:
        run = _make_run(task="test task", repo="my/repo")
        result = compute_cost_analysis(run)
        meta = result["run_meta"]
        assert meta["task"] == "test task"
        assert meta["repo"] == "my/repo"
        assert meta["files_included"] == 2

    def test_zero_tokens_produces_zero_costs(self) -> None:
        run = _make_run(input_tokens=0, saved_tokens=0, compressed_context=[])
        result = compute_cost_analysis(run, model="gpt-4o")
        assert result["baseline_cost_usd"] == 0.0
        assert result["saved_cost_usd"] == 0.0
        assert "No token data found" in "\n".join(result["notes"])

    def test_no_savings_note(self) -> None:
        run = _make_run(input_tokens=5000, saved_tokens=0)
        run["compressed_context"] = [
            {"path": "a.py", "strategy": "full", "original_tokens": 5000, "compressed_tokens": 5000}
        ]
        result = compute_cost_analysis(run)
        assert any("No savings" in n for n in result["notes"])

    def test_result_has_all_required_keys(self) -> None:
        run = _make_run()
        result = compute_cost_analysis(run)
        required = {
            "model", "provider", "input_per_1m_usd",
            "baseline_tokens", "optimized_tokens", "saved_tokens", "savings_pct",
            "baseline_cost_usd", "optimized_cost_usd", "saved_cost_usd",
            "per_file", "run_meta", "notes",
        }
        assert required.issubset(result)

    def test_savings_pct_zero_when_no_baseline(self) -> None:
        run = _make_run(input_tokens=0, saved_tokens=0, compressed_context=[])
        result = compute_cost_analysis(run)
        assert result["savings_pct"] == 0.0


# ---------------------------------------------------------------------------
# CLI: redcon cost-analysis
# ---------------------------------------------------------------------------

class TestCostAnalysisCli:
    def _write_run(self, tmp_path: Path, run: dict) -> Path:
        p = tmp_path / "run.json"
        p.write_text(json.dumps(run), encoding="utf-8")
        return p

    def test_human_output_shows_costs(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from redcon.cli import main

        run = _make_run(input_tokens=18340, saved_tokens=31660)
        run_path = self._write_run(tmp_path, run)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["redcon", "cost-analysis", str(run_path), "--model", "gpt-4o"],
        )
        rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "Baseline cost" in out
        assert "Optimized cost" in out
        assert "Savings" in out

    def test_json_output_parseable(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from redcon.cli import main

        run = _make_run(input_tokens=18340, saved_tokens=31660)
        run_path = self._write_run(tmp_path, run)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            [
                "redcon", "cost-analysis", str(run_path),
                "--model", "gpt-4o",
                "--format", "json",
            ],
        )
        rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["baseline_tokens"] == 50000
        assert data["saved_cost_usd"] > 0

    def test_list_models_flag(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from redcon.cli import main

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["redcon", "cost-analysis", "--list-models"],
        )
        rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "gpt-4o" in out
        assert "anthropic" in out

    def test_list_models_json(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from redcon.cli import main

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["redcon", "cost-analysis", "--list-models", "--format", "json"],
        )
        rc = main()
        assert rc == 0
        models = json.loads(capsys.readouterr().out)
        assert isinstance(models, list)
        assert any(m["model"] == "gpt-4o" for m in models)

    def test_missing_file_exits_nonzero(self, tmp_path: Path, monkeypatch) -> None:
        from redcon.cli import main

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["redcon", "cost-analysis", "nonexistent.json"],
        )
        rc = main()
        assert rc != 0

    def test_writes_json_output_file(self, tmp_path: Path, monkeypatch) -> None:
        from redcon.cli import main

        run = _make_run()
        run_path = self._write_run(tmp_path, run)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["redcon", "cost-analysis", str(run_path), "--model", "claude-sonnet-4-6"],
        )
        rc = main()
        assert rc == 0
        out_json = tmp_path / "run-cost-analysis.json"
        assert out_json.exists()
        data = json.loads(out_json.read_text())
        assert data["provider"] == "anthropic"

    def test_writes_markdown_output_file(self, tmp_path: Path, monkeypatch) -> None:
        from redcon.cli import main

        run = _make_run()
        run_path = self._write_run(tmp_path, run)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["redcon", "cost-analysis", str(run_path)],
        )
        rc = main()
        assert rc == 0
        out_md = tmp_path / "run-cost-analysis.md"
        assert out_md.exists()
        md = out_md.read_text()
        assert "Cost Analysis" in md

    def test_custom_out_flag(self, tmp_path: Path, monkeypatch) -> None:
        from redcon.cli import main

        run = _make_run()
        run_path = self._write_run(tmp_path, run)
        custom_out = tmp_path / "my-report.json"
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["redcon", "cost-analysis", str(run_path), "--out", str(custom_out)],
        )
        rc = main()
        assert rc == 0
        assert custom_out.exists()
