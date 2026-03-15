from __future__ import annotations

from pathlib import Path

from contextbudget import (
    ContextBudgetEngine,
    ExternalSummaryAdapter,
    register_external_summarizer_adapter,
    unregister_external_summarizer_adapter,
)
from contextbudget.cli import main
from contextbudget.core.pipeline import as_json_dict, run_pack
from contextbudget.core.render import render_report_markdown


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _EchoExternalSummarizer(ExternalSummaryAdapter):
    name = "echo"

    def summarize(self, request) -> str:
        return f"external summary for {request.path}"


class _BrokenExternalSummarizer(ExternalSummaryAdapter):
    name = "broken"

    def summarize(self, request) -> str:
        raise RuntimeError("adapter boom")


def test_deterministic_summarizer_is_default(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "large.py", "\n".join(f"line {index}" for index in range(2000)) + "\n")

    data = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))

    entry = next(item for item in data["compressed_context"] if item["path"] == "src/large.py")
    assert data["summarizer"]["selected_backend"] == "deterministic"
    assert data["summarizer"]["effective_backend"] == "deterministic"
    assert data["summarizer"]["fallback_used"] is False
    assert entry["chunk_strategy"] == "summary-preview"
    assert "deterministic summary" in entry["chunk_reason"]


def test_external_summarizer_is_used_when_configured(tmp_path: Path) -> None:
    register_external_summarizer_adapter("echo", _EchoExternalSummarizer())
    try:
        _write(
            tmp_path / "contextbudget.toml",
            """
[summarization]
backend = "external"
adapter = "echo"
""".strip(),
        )
        _write(tmp_path / "src" / "large.py", "\n".join(f"line {index}" for index in range(2000)) + "\n")

        data = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))
    finally:
        unregister_external_summarizer_adapter("echo")

    entry = next(item for item in data["compressed_context"] if item["path"] == "src/large.py")
    assert data["summarizer"]["selected_backend"] == "external"
    assert data["summarizer"]["external_adapter"] == "echo"
    assert data["summarizer"]["external_resolved"] is True
    assert data["summarizer"]["effective_backend"] == "external"
    assert data["summarizer"]["fallback_used"] is False
    assert entry["chunk_strategy"] == "summary-external"
    assert "external summary via adapter 'echo'" == entry["chunk_reason"]


def test_external_summarizer_failure_falls_back_to_deterministic(tmp_path: Path) -> None:
    register_external_summarizer_adapter("broken", _BrokenExternalSummarizer())
    try:
        _write(
            tmp_path / "contextbudget.toml",
            """
[summarization]
backend = "external"
adapter = "broken"
""".strip(),
        )
        _write(tmp_path / "src" / "large.py", "\n".join(f"line {index}" for index in range(2000)) + "\n")

        data = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))
    finally:
        unregister_external_summarizer_adapter("broken")

    entry = next(item for item in data["compressed_context"] if item["path"] == "src/large.py")
    assert data["summarizer"]["selected_backend"] == "external"
    assert data["summarizer"]["external_adapter"] == "broken"
    assert data["summarizer"]["external_resolved"] is True
    assert data["summarizer"]["effective_backend"] == "deterministic"
    assert data["summarizer"]["fallback_used"] is True
    assert data["summarizer"]["fallback_count"] >= 1
    assert data["summarizer"]["logs"]
    assert "failed" in data["summarizer"]["logs"][0]
    assert entry["chunk_strategy"] == "summary-preview"
    assert "fallback summary" in entry["chunk_reason"]

    summary = ContextBudgetEngine().report(data)
    markdown = render_report_markdown(summary)
    assert "- Summarizer fallback used: True" in markdown
    assert "adapter boom" in markdown


def test_cli_pack_prints_summarizer_fallback_logs(tmp_path: Path, monkeypatch, capsys) -> None:
    register_external_summarizer_adapter("broken", _BrokenExternalSummarizer())
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(
            repo / "contextbudget.toml",
            """
[summarization]
backend = "external"
adapter = "broken"
""".strip(),
        )
        _write(repo / "src" / "large.py", "\n".join(f"line {index}" for index in range(2000)) + "\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "sys.argv",
            ["contextbudget", "pack", "touch unrelated", "--repo", str(repo), "--out-prefix", "summ-run"],
        )
        assert main() == 0
    finally:
        unregister_external_summarizer_adapter("broken")

    output = capsys.readouterr().out
    assert "Summarizer: selected=external" in output
    assert "fallback=True" in output
    assert "Summarizer log:" in output
