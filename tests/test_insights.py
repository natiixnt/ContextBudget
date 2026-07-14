"""Prompt-optimization insights (deterministic, local-only Pro feature)."""

from __future__ import annotations

from redcon.cache.run_history import RunHistoryEntry
from redcon.core.insights import (
    MIN_RUNS,
    build_insights,
    insights_as_dict,
)


def _entry(task: str, input_tokens: int, *, selected: int = 1, scanned: int = 0) -> RunHistoryEntry:
    return RunHistoryEntry(
        generated_at="2026-01-01T00:00:00+00:00",
        task=task,
        selected_files=[f"f{i}.py" for i in range(selected)],
        token_usage={"estimated_input_tokens": input_tokens, "files_scanned": scanned},
    )


def test_not_enough_history_is_reported_gently():
    report = build_insights([_entry("do a thing", 5000) for _ in range(MIN_RUNS - 1)])
    assert report.opportunities == []
    assert "at least" in report.note
    assert report.total_potential_savings_tokens == 0


def test_recurring_prompt_is_flagged():
    # One heavy prompt repeated, plus lighter distinct runs to set a low median.
    entries = [_entry("refactor the whole auth system", 12000) for _ in range(3)]
    entries += [_entry(f"tiny task {i}", 800) for i in range(4)]

    report = build_insights(entries)
    kinds = [o.kind for o in report.opportunities]
    assert "recurring_prompt" in kinds
    recurring = next(o for o in report.opportunities if o.kind == "recurring_prompt")
    assert recurring.run_count == 3
    assert recurring.tokens_total == 36000
    # Each of the 3 heavy runs is well above the ~800 median, so there is real
    # excess to recover.
    assert recurring.potential_savings_tokens > 0
    assert report.total_potential_savings_tokens > 0


def test_recurring_normalizes_whitespace_and_case():
    entries = [
        _entry("Fix   the LOGIN bug", 9000),
        _entry("fix the login bug", 9000),
        _entry("fix the login   bug  ", 9000),
    ]
    entries += [_entry(f"small {i}", 500) for i in range(4)]
    report = build_insights(entries)
    recurring = [o for o in report.opportunities if o.kind == "recurring_prompt"]
    assert len(recurring) == 1
    assert recurring[0].run_count == 3


def test_broad_context_flagged_when_selection_is_wide():
    # A distinct heavy run that kept most of the files it scanned.
    entries = [_entry(f"small task {i}", 700) for i in range(6)]
    entries.append(_entry("do everything everywhere", 20000, selected=45, scanned=50))

    report = build_insights(entries)
    broad = [o for o in report.opportunities if o.kind == "broad_context"]
    assert len(broad) == 1
    assert "45 of 50 files" in broad[0].detail
    assert broad[0].potential_savings_tokens > 0


def test_heavy_but_well_scoped_run_is_not_flagged_as_broad():
    # Heavy, but it kept only a small fraction of what it scanned - already
    # scoped, so scoping advice would be noise.
    entries = [_entry(f"small task {i}", 700) for i in range(6)]
    entries.append(_entry("targeted heavy change", 20000, selected=3, scanned=200))

    report = build_insights(entries)
    assert not any(o.kind == "broad_context" for o in report.opportunities)


def test_lean_history_reports_no_opportunities():
    entries = [_entry(f"distinct small task {i}", 900) for i in range(MIN_RUNS + 2)]
    report = build_insights(entries)
    assert report.opportunities == []
    assert "lean" in report.note.lower()


def test_a_run_is_not_double_counted_across_items():
    # A recurring heavy prompt that is ALSO broad must contribute its excess
    # to the total only once.
    entries = [_entry("massive sweeping change", 15000, selected=60, scanned=70) for _ in range(3)]
    entries += [_entry(f"lil {i}", 600) for i in range(4)]

    report = build_insights(entries)
    per_run_excess = sum(
        max(0, o.potential_savings_tokens)
        for o in report.opportunities
        if o.kind == "broad_context"
    )
    # The recurring group already claimed those runs, so no separate broad item.
    assert per_run_excess == 0
    # Total equals the recurring group's excess, not double.
    recurring = next(o for o in report.opportunities if o.kind == "recurring_prompt")
    assert report.total_potential_savings_tokens == recurring.potential_savings_tokens


def test_opportunities_sorted_by_potential_desc():
    entries = [_entry("heavy recurring prompt", 18000) for _ in range(3)]
    entries += [_entry(f"other {i}", 500) for i in range(4)]
    entries.append(_entry("one broad distinct prompt", 9000, selected=30, scanned=40))

    report = build_insights(entries)
    potentials = [o.potential_savings_tokens for o in report.opportunities]
    assert potentials == sorted(potentials, reverse=True)


def test_insights_as_dict_shape():
    entries = [_entry("recurring heavy", 11000) for _ in range(3)]
    entries += [_entry(f"x {i}", 700) for i in range(4)]
    data = insights_as_dict(build_insights(entries))
    assert data["command"] == "insights"
    assert data["runs_analyzed"] == 7
    assert isinstance(data["opportunities"], list)
    assert data["opportunities"]
    first = data["opportunities"][0]
    assert {"kind", "title", "detail", "suggestion", "potential_savings_tokens"} <= set(first)


def test_runs_with_zero_input_are_ignored():
    entries = [_entry("real", 8000) for _ in range(3)]
    entries += [_entry(f"real small {i}", 600) for i in range(3)]
    entries.append(_entry("ghost run", 0))  # dropped
    report = build_insights(entries)
    assert report.runs_analyzed == 6
