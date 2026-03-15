from __future__ import annotations

from pathlib import Path

from contextbudget.core.pipeline import as_json_dict, run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_run_pack_builds_budget_report(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def auth():\n    return 'ok'\n" * 20)
    _write(tmp_path / "src" / "middleware.py", "def auth_middleware():\n    return auth()\n" * 20)

    report = run_pack("refactor auth middleware", repo=tmp_path, max_tokens=1000)
    data = as_json_dict(report)

    assert data["budget"]["estimated_input_tokens"] <= 1000
    assert "files_included" in data
    assert "files_skipped" in data
    assert data["budget"]["quality_risk_estimate"] in {"low", "medium", "high"}
    first = data["compressed_context"][0]
    assert "chunk_strategy" in first
    assert "chunk_reason" in first
    assert "selected_ranges" in first


def test_duplicate_reads_prevented_on_same_content(tmp_path: Path) -> None:
    content = "def same():\n    return 1\n" * 20
    _write(tmp_path / "src" / "a.py", content)
    _write(tmp_path / "src" / "b.py", content)

    report = run_pack("change same function", repo=tmp_path, max_tokens=1000)
    data = as_json_dict(report)

    assert data["budget"]["duplicate_reads_prevented"] >= 1


def test_duplicate_hash_cache_can_be_disabled(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[cache]
duplicate_hash_cache_enabled = false
""".strip(),
    )
    content = "def same():\n    return 1\n" * 20
    _write(tmp_path / "src" / "a.py", content)
    _write(tmp_path / "src" / "b.py", content)

    report = run_pack("change same function", repo=tmp_path, max_tokens=1000)
    data = as_json_dict(report)

    assert data["budget"]["duplicate_reads_prevented"] == 0


def test_summary_cache_hits_on_second_run(tmp_path: Path) -> None:
    long_text = "\n".join([f"line {i}" for i in range(2000)])
    _write(tmp_path / "src" / "large.py", long_text)

    first = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))
    second = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))

    assert first["cache"]["backend"] == "local_file"
    assert first["cache"]["hits"] == first["cache_hits"]
    assert first["cache_hits"] == 0
    assert first["cache"]["misses"] >= 1
    assert second["cache_hits"] >= 1
    assert second["cache"]["hits"] == second["cache_hits"]


def test_shared_stub_cache_backend_records_misses_without_persistence(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[cache]
backend = "shared_stub"
""".strip(),
    )
    _write(tmp_path / "src" / "large.py", "\n".join([f"line {i}" for i in range(2000)]) + "\n")

    first = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))
    second = as_json_dict(run_pack("touch unrelated", repo=tmp_path, max_tokens=500))

    assert first["cache"]["backend"] == "shared_stub"
    assert first["cache"]["hits"] == 0
    assert first["cache"]["misses"] >= 1
    assert first["cache"]["writes"] == 0
    assert second["cache"]["backend"] == "shared_stub"
    assert second["cache"]["hits"] == 0
    assert second["cache"]["misses"] >= 1


def test_python_language_aware_chunk_selection(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
snippet_total_line_limit = 40
""".strip(),
    )
    _write(
        tmp_path / "src" / "auth_service.py",
        """
import os
import hashlib

# Handles auth checks.
class AuthService:
    \"\"\"Auth service docs.\"\"\"

    def login(self, token: str) -> bool:
        return token.startswith(\"prod_\")


def helper() -> None:
    pass
""".strip()
        + "\n",
    )

    data = as_json_dict(run_pack("refactor auth login", repo=tmp_path, max_tokens=1000))
    entry = next(item for item in data["compressed_context"] if item["path"] == "src/auth_service.py")

    assert entry["strategy"] == "snippet"
    assert entry["chunk_strategy"] == "language-aware-python"
    assert entry["selected_ranges"]
    assert any(r["kind"] in {"import", "class", "function"} for r in entry["selected_ranges"])


def test_typescript_language_aware_chunk_selection(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
snippet_total_line_limit = 40
""".strip(),
    )
    _write(
        tmp_path / "src" / "auth.ts",
        """
import { createHash } from \"node:crypto\";

// Exported auth class.
export class AuthClient {
  login(token: string): boolean {
    return token.startsWith(\"prod_\");
  }
}

export function validate(token: string): boolean {
  return token.length > 3;
}
""".strip()
        + "\n",
    )

    data = as_json_dict(run_pack("update auth exports", repo=tmp_path, max_tokens=1000))
    entry = next(item for item in data["compressed_context"] if item["path"] == "src/auth.ts")

    assert entry["strategy"] == "snippet"
    assert entry["chunk_strategy"] == "language-aware-typescript"
    assert entry["selected_ranges"]
    assert any(r["kind"] in {"import", "export", "function", "class"} for r in entry["selected_ranges"])


def test_unknown_extension_falls_back_to_keyword_window(tmp_path: Path) -> None:
    _write(
        tmp_path / "contextbudget.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
snippet_total_line_limit = 20
""".strip(),
    )
    _write(
        tmp_path / "src" / "auth_notes.txt",
        "\n".join(["auth middleware note" for _ in range(40)]) + "\n",
    )

    data = as_json_dict(run_pack("auth middleware", repo=tmp_path, max_tokens=1000))
    entry = next(item for item in data["compressed_context"] if item["path"] == "src/auth_notes.txt")

    assert entry["strategy"] == "snippet"
    assert entry["chunk_strategy"] == "keyword-window"
    assert entry["selected_ranges"] == []
