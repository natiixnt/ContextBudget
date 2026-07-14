"""Dollar translation of the selection saving in pack output."""

from __future__ import annotations

from pathlib import Path

from redcon.core.pipeline import as_json_dict, run_pack
from redcon.core.render import _cost_savings_md_lines, render_pack_markdown
from redcon.telemetry.pricing import DEFAULT_MODEL, get_pricing


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _repo(root: Path, count: int = 20) -> None:
    for i in range(count):
        body = (
            f"def feature_{i}(payload):\n"
            + "    # gateway auth billing rate-limit logic line\n" * 25
            + f"    return process_{i}(payload)\n"
        )
        _write(root / "src" / f"mod_{i:02d}.py", body)


def test_run_pack_prices_out_the_selection_saving(tmp_path: Path) -> None:
    _repo(tmp_path)
    data = as_json_dict(run_pack("refactor feature_01 auth", repo=tmp_path, max_tokens=500))

    cost = data["cost"]
    assert cost["model"] == DEFAULT_MODEL
    # Priced at the default model's input rate against the same baseline the
    # Context line uses.
    baseline = data["context_baseline_tokens"]
    sent = data["budget"]["estimated_input_tokens"]
    assert cost["baseline_tokens"] == baseline
    assert cost["optimized_tokens"] == sent
    assert cost["tokens_saved"] == baseline - sent
    rate = get_pricing(DEFAULT_MODEL)["input_per_million"]
    assert cost["savings_usd"] > 0
    assert abs(cost["savings_usd"] - (baseline - sent) / 1_000_000 * rate) < 1e-9


def test_cost_line_in_markdown(tmp_path: Path) -> None:
    _repo(tmp_path)
    data = as_json_dict(run_pack("refactor feature_01 auth", repo=tmp_path, max_tokens=500))
    markdown = render_pack_markdown(data)
    assert "Saved if paying per token:" in markdown


def test_cost_md_line_present_when_savings_positive() -> None:
    lines = _cost_savings_md_lines({"cost": {"savings_usd": 0.0123, "display_name": "GPT-4o"}})
    assert len(lines) == 1
    assert "$0.0123" in lines[0]
    assert "GPT-4o" in lines[0]


def test_cost_md_line_absent_when_no_savings() -> None:
    assert _cost_savings_md_lines({"cost": {"savings_usd": 0.0}}) == []
    assert _cost_savings_md_lines({"cost": {}}) == []
    assert _cost_savings_md_lines({}) == []  # older run.json without the field


def test_cost_empty_when_no_baseline(tmp_path: Path) -> None:
    # An empty repo has no scanned universe, so there is nothing to price out.
    (tmp_path / "src").mkdir(parents=True)
    data = as_json_dict(run_pack("do a thing", repo=tmp_path, max_tokens=500))
    assert data.get("cost", {}) == {}
