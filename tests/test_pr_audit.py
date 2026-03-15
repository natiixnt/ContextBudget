from __future__ import annotations

import json
from pathlib import Path

from contextbudget import ContextBudgetEngine

from tests.support_git import build_pr_audit_repo


def test_pr_audit_reports_token_growth_and_drivers(tmp_path: Path) -> None:
    repo, base_commit, head_commit = build_pr_audit_repo(tmp_path)

    engine = ContextBudgetEngine()
    data = engine.pr_audit(repo=repo, base_ref=base_commit, head_ref=head_commit)

    assert data["command"] == "pr-audit"
    assert data["summary"]["estimated_tokens_after"] > data["summary"]["estimated_tokens_before"]
    assert data["summary"]["estimated_token_delta"] > 0
    assert "auth/service.py" in data["larger_files"]
    assert "api/router.py" in data["increased_complexity"]
    assert "auth/service.py" in data["files_causing_increase"]
    assert "api/router.py" in data["files_causing_increase"]
    assert any(item["name"] == "httpx" for item in data["new_dependencies"])
    assert "## ContextBudget Analysis" in data["comment_markdown"]
    assert data["suggestions"]


def test_pr_audit_resolves_refs_from_github_event_payload(tmp_path: Path, monkeypatch) -> None:
    repo, base_commit, head_commit = build_pr_audit_repo(tmp_path)
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "base": {"sha": base_commit},
                    "head": {"sha": head_commit},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    engine = ContextBudgetEngine()
    data = engine.pr_audit(repo=repo)

    assert data["base_commit"] == base_commit
    assert data["head_commit"] == head_commit
    assert data["merge_base"] == base_commit
