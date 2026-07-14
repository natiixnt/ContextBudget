"""The release workflow must gate PyPI publish on the test suite.

A tag pushed to a red commit must never publish a broken wheel, so the publish
job depends on a test job that runs pytest. Parsing is kept dependency-free
(plain text assertions) so the guard runs in the default dev environment.
"""

from __future__ import annotations

from pathlib import Path

_RELEASE_YML = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def test_release_workflow_gates_publish_on_tests() -> None:
    text = _RELEASE_YML.read_text(encoding="utf-8")

    # A dedicated test job exists and the publish job depends on it.
    assert "\n  test:\n" in text, "release workflow must define a test job"
    assert "\n  publish:\n" in text, "release workflow must define a publish job"
    assert "needs: test" in text, "publish must not run unless the test job passes"

    # The gate must actually run the test suite, not just exist.
    assert "pytest" in text, "the release gate must run pytest"
