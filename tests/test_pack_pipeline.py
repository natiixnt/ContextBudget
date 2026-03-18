from __future__ import annotations

import json
from pathlib import Path

from redcon.cache import load_run_history
from redcon.core.pipeline import as_json_dict, run_pack


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


def test_run_pack_records_history_entry(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def auth() -> bool:\n    return True\n")

    data = as_json_dict(run_pack("tighten auth checks", repo=tmp_path, max_tokens=300))
    history = load_run_history(tmp_path)

    assert history
    entry = history[-1]
    assert entry.task == "tighten auth checks"
    assert entry.selected_files == data["files_included"]
    assert entry.token_usage["estimated_input_tokens"] == data["budget"]["estimated_input_tokens"]
    assert entry.result_artifacts == {"run_json": "", "run_markdown": ""}


def test_duplicate_reads_prevented_on_same_content(tmp_path: Path) -> None:
    content = "def same():\n    return 1\n" * 20
    _write(tmp_path / "src" / "a.py", content)
    _write(tmp_path / "src" / "b.py", content)

    report = run_pack("change same function", repo=tmp_path, max_tokens=1000)
    data = as_json_dict(report)

    assert data["budget"]["duplicate_reads_prevented"] >= 1


def test_duplicate_hash_cache_can_be_disabled(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
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
    assert second["cache"]["fragment_hits"] >= 1


def test_fragment_cache_reuses_reference_on_second_run(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1000
snippet_score_threshold = 999
""".strip(),
    )
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")

    first = as_json_dict(run_pack("update auth flow", repo=tmp_path, max_tokens=1000))
    second = as_json_dict(run_pack("update auth flow", repo=tmp_path, max_tokens=1000))

    cache_file = json.loads((tmp_path / ".redcon_cache.json").read_text(encoding="utf-8"))
    first_entry = next(item for item in first["compressed_context"] if item["path"] == "src/auth.py")
    second_entry = next(item for item in second["compressed_context"] if item["path"] == "src/auth.py")

    assert cache_file["fragments"]
    assert first_entry["cache_status"] == "stored"
    assert first_entry["cache_reference"]
    assert second_entry["cache_status"] == "reused"
    assert second_entry["cache_reference"] == first_entry["cache_reference"]
    # Text must always contain real content, never an opaque cache marker.
    assert not second_entry["text"].startswith("@cached-summary:")
    assert second_entry["text"] == first_entry["text"]
    assert second["cache"]["fragment_hits"] >= 1


def test_fragment_cache_reuses_reference_across_repeated_tasks(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1000
snippet_score_threshold = 999
""".strip(),
    )
    _write(tmp_path / "src" / "auth.py", "def login(token: str) -> bool:\n    return token.startswith('prod_')\n")

    first = as_json_dict(run_pack("update auth flow", repo=tmp_path, max_tokens=1000))
    second = as_json_dict(run_pack("refactor auth flow", repo=tmp_path, max_tokens=1000))

    first_entry = next(item for item in first["compressed_context"] if item["path"] == "src/auth.py")
    second_entry = next(item for item in second["compressed_context"] if item["path"] == "src/auth.py")

    assert first_entry["cache_reference"]
    assert second_entry["cache_status"] == "reused"
    assert second_entry["cache_reference"] == first_entry["cache_reference"]


def test_fragment_cache_reports_token_savings(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1000
snippet_score_threshold = 999
""".strip(),
    )
    _write(
        tmp_path / "src" / "auth.py",
        "def login(token: str) -> bool:\n    return token.startswith('prod_')\n" * 8,
    )

    first = as_json_dict(run_pack("update auth flow", repo=tmp_path, max_tokens=1000))
    second = as_json_dict(run_pack("update auth flow", repo=tmp_path, max_tokens=1000))

    first_entry = next(item for item in first["compressed_context"] if item["path"] == "src/auth.py")
    second_entry = next(item for item in second["compressed_context"] if item["path"] == "src/auth.py")

    # With self-contained cache, real text is always kept - no fake token
    # savings from marker replacement.  Token counts match across runs.
    assert second_entry["compressed_tokens"] == first_entry["compressed_tokens"]
    assert second_entry["text"] == first_entry["text"]
    assert not second_entry["text"].startswith("@cached-summary:")
    assert second["budget"]["estimated_input_tokens"] == first["budget"]["estimated_input_tokens"]


def test_warm_cache_produces_self_contained_prompt(tmp_path: Path) -> None:
    """Every compressed entry must contain real text, never a cache marker."""
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
""".strip(),
    )
    _write(
        tmp_path / "src" / "auth.py",
        "def login(token: str) -> bool:\n    return token.startswith('prod_')\n" * 10,
    )
    _write(
        tmp_path / "src" / "middleware.py",
        "def auth_middleware(req):\n    return check(req.token)\n" * 10,
    )

    # First run populates cache, second run hits it.
    run_pack("update auth middleware", repo=tmp_path, max_tokens=2000)
    second = as_json_dict(run_pack("update auth middleware", repo=tmp_path, max_tokens=2000))

    for entry in second["compressed_context"]:
        assert not entry["text"].startswith("@cached-summary:"), (
            f"Cache marker leaked into prompt for {entry['path']}"
        )
        assert len(entry["text"]) > 10, (
            f"Suspiciously short text for {entry['path']}: {entry['text']!r}"
        )


def test_runtime_build_prompt_skips_cache_markers() -> None:
    """The runtime guard must filter out any stale cache markers."""
    from redcon.runtime.runtime import _build_prompt_text

    artifact = {
        "compressed_context": [
            {"path": "src/good.py", "text": "def good(): pass"},
            {"path": "src/bad.py", "text": "@cached-summary:cb-frag:abc123"},
            {"path": "src/also_good.py", "text": "def also_good(): pass"},
        ]
    }
    prompt = _build_prompt_text(artifact)
    assert "good" in prompt
    assert "also_good" in prompt
    assert "@cached-summary" not in prompt
    assert "src/bad.py" not in prompt


def test_shared_stub_cache_backend_records_misses_without_persistence(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
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
    assert first["cache"]["fragment_writes"] == 0
    assert second["cache"]["backend"] == "shared_stub"
    assert second["cache"]["hits"] == 0
    assert second["cache"]["misses"] >= 1
    assert second["cache"]["tokens_saved"] == 0


def test_python_language_aware_chunk_selection(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
symbol_extraction_enabled = false
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

    assert entry["strategy"] == "slice"
    assert entry["chunk_strategy"] == "language-aware-python"
    assert entry["selected_ranges"]
    assert any(r["kind"] in {"import", "class", "function"} for r in entry["selected_ranges"])
    assert all("reason" in item for item in entry["selected_ranges"])
    assert any("symbol extraction" in str(item["reason"]) for item in entry["selected_ranges"])


def test_typescript_language_aware_chunk_selection(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
symbol_extraction_enabled = false
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

    assert entry["strategy"] == "slice"
    assert entry["chunk_strategy"] == "language-aware-typescript"
    assert entry["selected_ranges"]
    assert any(r["kind"] in {"import", "export", "function", "class"} for r in entry["selected_ranges"])
    assert all("reason" in item for item in entry["selected_ranges"])


def test_unknown_extension_falls_back_to_keyword_window(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
symbol_extraction_enabled = false
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
    assert entry["selected_ranges"]
    assert all(item["kind"] == "keyword-window" for item in entry["selected_ranges"])
    assert all(str(item["reason"]).startswith("keyword proximity:") for item in entry["selected_ranges"])


def test_smart_slicing_uses_import_relationships_and_skips_unrelated_code(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
symbol_extraction_enabled = false
snippet_context_lines = 1
snippet_total_line_limit = 10
""".strip(),
    )
    _write(
        tmp_path / "src" / "app.py",
        """
from .auth import login


def run(token: str) -> bool:
    return login(token)
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "src" / "token_store.py",
        """
def verify_token(token: str) -> bool:
    return token.startswith("prod_")
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "src" / "auth.py",
        """
from .token_store import verify_token


def login(token: str) -> bool:
    normalized = token.strip()
    if not normalized:
        return False
    return verify_token(normalized)


def audit_everything(token: str) -> bool:
    entries = []
    for _ in range(20):
        entries.append(token)
    return bool(entries)
""".strip()
        + "\n",
    )

    data = as_json_dict(run_pack("tighten auth login flow", repo=tmp_path, max_tokens=1000))
    entry = next(item for item in data["compressed_context"] if item["path"] == "src/auth.py")

    assert entry["strategy"] == "slice"
    assert entry["chunk_strategy"] == "language-aware-python"
    assert "audit_everything" not in entry["text"]
    reasons = [str(item["reason"]) for item in entry["selected_ranges"]]
    assert any(
        "import relationship:" in reason or "imported by related file:" in reason
        for reason in reasons
    )
    assert any("imported by related file:" in reason for reason in reasons)


def test_run_pack_can_emit_delta_context_package(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 1
snippet_score_threshold = 0
snippet_total_line_limit = 40
""".strip(),
    )
    _write(
        tmp_path / "src" / "auth.py",
        """
def login(token: str) -> bool:
    return token.startswith("prod_")
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "src" / "middleware.py",
        """
def auth_middleware(token: str) -> bool:
    return login(token)
""".strip()
        + "\n",
    )

    first = as_json_dict(run_pack("update auth middleware", repo=tmp_path, max_tokens=1000))

    (tmp_path / "src" / "middleware.py").unlink()
    _write(
        tmp_path / "src" / "auth.py",
        """
class AuthService:
    def login_user(self, token: str) -> bool:
        return token.startswith("prod_")
""".strip()
        + "\n",
    )
    _write(
        tmp_path / "src" / "permissions.py",
        """
def allow_auth(token: str) -> bool:
    return token.startswith("prod_")
""".strip()
        + "\n",
    )

    second = as_json_dict(run_pack("update auth middleware", repo=tmp_path, max_tokens=1000, delta_from=first))
    delta = second["delta"]

    assert second["files_included"] == ["src/auth.py", "src/permissions.py"]
    assert delta["files_added"] == ["src/permissions.py"]
    assert delta["files_removed"] == ["src/middleware.py"]
    assert delta["changed_files"] == ["src/auth.py"]
    assert delta["changed_slices"]
    assert "changed_symbols" in delta
    assert delta["package"]["files_included"] == ["src/permissions.py", "src/auth.py"]
    assert delta["budget"]["original_tokens"] > 0
    assert delta["budget"]["delta_tokens"] > 0
    assert delta["budget"]["tokens_saved"] >= 0


def test_model_profile_expands_budget_and_records_assumptions(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        'model_profile = "gpt-4.1"\n',
    )
    _write(tmp_path / "src" / "notes.py", ("value = 'x' * 80\n" * 120))

    data = as_json_dict(run_pack("rename widget", repo=tmp_path, max_tokens=None))

    assert data["max_tokens"] == 1_014_808
    assert data["compressed_context"][0]["strategy"] == "full"
    assert data["model_profile"]["selected_profile"] == "gpt-4.1"
    assert data["model_profile"]["resolved_profile"] == "gpt-4.1"
    assert data["model_profile"]["family"] == "gpt"
    assert data["model_profile"]["tokenizer"] == "tiktoken"
    assert data["model_profile"]["context_window"] == 1_047_576
    assert data["model_profile"]["recommended_compression_strategy"] == "expanded"
    assert data["model_profile"]["effective_max_tokens"] == data["max_tokens"]
    assert data["token_estimator"]["selected_backend"] == "exact_tiktoken"


def test_local_model_profile_uses_aggressive_compression_and_custom_overrides(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        """
model_profile = "local-llm"

[model]
tokenizer = "llama-bpe"
context_window = 65536
output_reserve_tokens = 8192
recommended_compression_strategy = "aggressive"
""".strip(),
    )
    _write(tmp_path / "src" / "notes.py", ("value = 'x' * 80\n" * 120))

    data = as_json_dict(run_pack("rename widget", repo=tmp_path, max_tokens=None))

    assert data["compressed_context"][0]["strategy"] == "summary"
    assert data["model_profile"]["selected_profile"] == "local-llm"
    assert data["model_profile"]["family"] == "local"
    assert data["model_profile"]["tokenizer"] == "llama-bpe"
    assert data["model_profile"]["context_window"] == 65536
    assert data["model_profile"]["reserved_output_tokens"] == 8192
    assert data["model_profile"]["effective_max_tokens"] == 57344


def test_model_profile_clamps_explicit_budget_to_context_window(tmp_path: Path) -> None:
    _write(
        tmp_path / "redcon.toml",
        'model_profile = "claude-sonnet-4"\n',
    )
    _write(tmp_path / "src" / "auth.py", "def login() -> bool:\n    return True\n")

    data = as_json_dict(run_pack("update auth flow", repo=tmp_path, max_tokens=500000))

    assert data["max_tokens"] == 183616
    assert data["model_profile"]["selected_profile"] == "claude-sonnet-4"
    assert data["model_profile"]["budget_source"] == "cli"
    assert data["model_profile"]["budget_clamped"] is True
    assert any("Clamped max_tokens" in note for note in data["model_profile"]["notes"])


def test_progressive_packer_degrades_before_dropping(tmp_path: Path) -> None:
    """Under a tight budget, the progressive packer should degrade a low-score
    file to make room for a higher-score file that would otherwise be skipped."""
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 5000
snippet_score_threshold = 0
progressive_packer_enabled = true
""".strip(),
    )
    # Large file that matches the task well - should always be included.
    _write(
        tmp_path / "src" / "auth.py",
        "def login(token: str) -> bool:\n    return token.startswith('prod_')\n" * 30,
    )
    # Second file - also matches, will compete for budget.
    _write(
        tmp_path / "src" / "middleware.py",
        "def auth_middleware(req):\n    return check_auth(req.token)\n" * 30,
    )

    # Use a tight budget that cannot fit both files at full detail.
    # First get baseline token counts.
    big_budget = as_json_dict(run_pack("update auth middleware", repo=tmp_path, max_tokens=50000))
    total_full = sum(e["compressed_tokens"] for e in big_budget["compressed_context"])
    # Set budget to ~60% of total - enough for one full + one degraded.
    tight_budget = int(total_full * 0.6)
    if tight_budget < 50:
        tight_budget = 50

    data = as_json_dict(run_pack("update auth middleware", repo=tmp_path, max_tokens=tight_budget))

    # Both files should be included (neither dropped).
    assert len(data["files_included"]) == 2
    assert len(data["files_skipped"]) == 0
    # At least one file should have been degraded from its original strategy.
    strategies = [e["strategy"] for e in data["compressed_context"]]
    assert any(s != "full" for s in strategies), f"Expected degradation, got {strategies}"
    assert data["budget"]["estimated_input_tokens"] <= tight_budget


def test_progressive_packer_reports_degradation_metrics(tmp_path: Path) -> None:
    """Degradation metrics should appear in the run report."""
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
full_file_threshold_tokens = 5000
snippet_score_threshold = 0
progressive_packer_enabled = true
""".strip(),
    )
    _write(
        tmp_path / "src" / "auth.py",
        "def login(token: str) -> bool:\n    return token.startswith('prod_')\n" * 30,
    )
    _write(
        tmp_path / "src" / "middleware.py",
        "def auth_middleware(req):\n    return check_auth(req.token)\n" * 30,
    )

    data = as_json_dict(run_pack("update auth middleware", repo=tmp_path, max_tokens=50000))
    # With a large budget no degradation should occur.
    assert data.get("degraded_files", []) == []
    assert data.get("degradation_savings", 0) == 0


def test_greedy_fallback_when_progressive_disabled(tmp_path: Path) -> None:
    """When progressive_packer_enabled is false, the old greedy behavior is used."""
    _write(
        tmp_path / "redcon.toml",
        """
[compression]
progressive_packer_enabled = false
""".strip(),
    )
    _write(tmp_path / "src" / "auth.py", "def login():\n    return True\n")

    data = as_json_dict(run_pack("update auth", repo=tmp_path, max_tokens=1000))
    assert data["files_included"] == ["src/auth.py"]
    assert data.get("degraded_files", []) == []
