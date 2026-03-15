from __future__ import annotations

from contextbudget.core.diffing import diff_run_artifacts


def _sample_run(
    *,
    task: str,
    files_included: list[str],
    ranked_files: list[dict],
    input_tokens: int,
    saved_tokens: int,
    quality_risk: str,
    cache_hits: int,
) -> dict:
    return {
        "task": task,
        "ranked_files": ranked_files,
        "files_included": files_included,
        "budget": {
            "estimated_input_tokens": input_tokens,
            "estimated_saved_tokens": saved_tokens,
            "quality_risk_estimate": quality_risk,
        },
        "cache_hits": cache_hits,
    }


def test_diff_run_artifacts_reports_expected_deltas() -> None:
    old = _sample_run(
        task="refactor auth middleware",
        files_included=["src/auth.py", "src/middleware.py"],
        ranked_files=[
            {"path": "src/auth.py", "score": 3.0},
            {"path": "src/middleware.py", "score": 2.5},
        ],
        input_tokens=1200,
        saved_tokens=300,
        quality_risk="medium",
        cache_hits=1,
    )
    new = _sample_run(
        task="refactor auth middleware v2",
        files_included=["src/auth.py", "src/permissions.py"],
        ranked_files=[
            {"path": "src/auth.py", "score": 3.4},
            {"path": "src/permissions.py", "score": 1.8},
        ],
        input_tokens=900,
        saved_tokens=550,
        quality_risk="low",
        cache_hits=4,
    )

    diff = diff_run_artifacts(old, new, old_label="old.json", new_label="new.json")

    assert diff["task_diff"]["changed"] is True
    assert diff["context_diff"]["files_added"] == ["src/permissions.py"]
    assert diff["context_diff"]["files_removed"] == ["src/middleware.py"]

    by_path = {item["path"]: item for item in diff["ranked_score_changes"]}
    assert by_path["src/auth.py"]["delta"] == 0.4
    assert by_path["src/permissions.py"]["change_type"] == "added"
    assert by_path["src/middleware.py"]["change_type"] == "removed"

    budget = diff["budget_delta"]
    assert budget["estimated_input_tokens"]["delta"] == -300
    assert budget["estimated_saved_tokens"]["delta"] == 250
    assert budget["quality_risk"]["delta_level"] == -1
    assert budget["cache_hits"]["delta"] == 3


def test_diff_run_artifacts_handles_stable_runs() -> None:
    old = _sample_run(
        task="same",
        files_included=["a.py"],
        ranked_files=[{"path": "a.py", "score": 1.0}],
        input_tokens=10,
        saved_tokens=2,
        quality_risk="low",
        cache_hits=0,
    )
    new = _sample_run(
        task="same",
        files_included=["a.py"],
        ranked_files=[{"path": "a.py", "score": 1.0}],
        input_tokens=10,
        saved_tokens=2,
        quality_risk="low",
        cache_hits=0,
    )

    diff = diff_run_artifacts(old, new)
    assert diff["task_diff"]["changed"] is False
    assert diff["context_diff"]["files_added"] == []
    assert diff["context_diff"]["files_removed"] == []
    assert diff["ranked_score_changes"] == []
    assert diff["budget_delta"]["estimated_input_tokens"]["delta"] == 0
    assert diff["budget_delta"]["estimated_saved_tokens"]["delta"] == 0
    assert diff["budget_delta"]["quality_risk"]["delta_level"] == 0
    assert diff["budget_delta"]["cache_hits"]["delta"] == 0


def test_diff_run_artifacts_reads_cache_hits_from_cache_metadata() -> None:
    old = _sample_run(
        task="same",
        files_included=["a.py"],
        ranked_files=[{"path": "a.py", "score": 1.0}],
        input_tokens=10,
        saved_tokens=2,
        quality_risk="low",
        cache_hits=1,
    )
    new = _sample_run(
        task="same",
        files_included=["a.py"],
        ranked_files=[{"path": "a.py", "score": 1.0}],
        input_tokens=10,
        saved_tokens=2,
        quality_risk="low",
        cache_hits=4,
    )
    old.pop("cache_hits")
    new.pop("cache_hits")
    old["cache"] = {"backend": "local_file", "enabled": True, "hits": 1, "misses": 2, "writes": 1}
    new["cache"] = {"backend": "shared_stub", "enabled": True, "hits": 4, "misses": 6, "writes": 0}

    diff = diff_run_artifacts(old, new)

    assert diff["budget_delta"]["cache_hits"]["delta"] == 3
