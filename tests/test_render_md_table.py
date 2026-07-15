"""The shared md_table helper and the render functions that now use it."""

from __future__ import annotations

from redcon.core.render import md_table, render_benchmark_markdown, render_pipeline_markdown


def test_md_table_default_left_align() -> None:
    assert md_table(["A", "B"], [["1", "2"]]) == [
        "| A | B |",
        "| --- | --- |",
        "| 1 | 2 |",
    ]


def test_md_table_alignment_markers() -> None:
    assert md_table(["A", "B", "C"], [], align="lrc") == [
        "| A | B | C |",
        "| --- | ---: | :-: |",
    ]


def test_md_table_align_shorter_than_headers_defaults_left() -> None:
    assert md_table(["A", "B"], [], align="r") == ["| A | B |", "| ---: | --- |"]


def test_md_table_stringifies_cells() -> None:
    assert md_table(["N"], [[42], [3.5]]) == ["| N |", "| --- |", "| 42 |", "| 3.5 |"]


def test_pipeline_summary_and_stage_tables_render() -> None:
    out = render_pipeline_markdown({"scanned_files": 5, "stages": []})
    assert "| Metric | Value |" in out
    assert "| --- | --- |" in out
    assert "| Scanned files | 5 |" in out
    # Stage breakdown header + right-aligned separator emitted even with no stages.
    assert "| Stage | Files | Tokens In | Tokens Out | Saved | Reduction |" in out
    assert "| --- | ---: | ---: | ---: | ---: | ---: |" in out


def test_benchmark_comparison_table_renders() -> None:
    out = render_benchmark_markdown(
        {
            "strategies": [
                {
                    "strategy": "greedy",
                    "estimated_input_tokens": 7000,
                    "estimated_saved_tokens": 3000,
                    "files_included": ["a.py"],
                    "duplicate_reads_prevented": 1,
                    "quality_risk_estimate": "low",
                    "cache_hits": 2,
                    "runtime_ms": 9,
                }
            ]
        }
    )
    assert "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |" in out
    assert "| greedy | 7000 | 3000 | 1 | 1 | low | 2 | 9 |" in out
