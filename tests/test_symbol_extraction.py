from __future__ import annotations

from pathlib import Path

import pytest

from redcon.compressors import context_compressor
from redcon.compressors.symbols import select_symbol_aware_chunks
from redcon.config import CompressionSettings, RedconConfig
from redcon.core.pipeline import as_json_dict, run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_python_symbol_extraction_detects_symbols_and_strips_docstrings() -> None:
    text = """
# Handles auth checks.
class AuthService:
    \"\"\"Auth service docs.\"\"\"

    def login(self, token: str) -> bool:
        return token.startswith("prod_")


def helper() -> None:
    pass
""".strip()

    chunk = select_symbol_aware_chunks(
        file_path="src/auth_service.py",
        text=text,
        keywords=["auth", "login"],
        line_budget=40,
    )

    assert chunk is not None
    assert chunk.chunk_strategy == "symbol-extract-python"
    assert {item["name"] for item in chunk.symbols} >= {"AuthService"}
    assert any(item["symbol_type"] == "class" for item in chunk.symbols)
    assert "# Handles auth checks." in chunk.text
    assert "class AuthService" in chunk.text
    # Docstring is stripped for compression.
    assert '"""Auth service docs."""' not in chunk.text


def test_typescript_symbol_extraction_detects_exports_and_interfaces() -> None:
    text = """
// Exported auth contract.
export interface AuthConfig {
  issuer: string;
}

export class AuthClient {
  login(token: string): boolean {
    return token.length > 3;
  }
}

export function validate(token: string): boolean {
  return token.startsWith("prod_");
}
""".strip()

    chunk = select_symbol_aware_chunks(
        file_path="src/auth.ts",
        text=text,
        keywords=["auth", "validate"],
        line_budget=60,
    )

    assert chunk is not None
    assert chunk.chunk_strategy == "symbol-extract-typescript"
    assert {item["name"] for item in chunk.symbols} >= {"AuthConfig", "AuthClient", "validate"}
    assert any(item["symbol_type"] == "interface" for item in chunk.symbols)
    assert "export interface AuthConfig" in chunk.text


def test_go_symbol_extraction_detects_exported_symbols() -> None:
    text = """
// AuthChecker validates tokens.
type AuthChecker interface {
    Login(token string) bool
}

// Login verifies production auth.
func Login(token string) bool {
    return len(token) > 3
}

func helper() bool {
    return true
}
""".strip()

    chunk = select_symbol_aware_chunks(
        file_path="auth.go",
        text=text,
        keywords=["auth", "login"],
        line_budget=40,
    )

    assert chunk is not None
    assert chunk.chunk_strategy == "symbol-extract-go"
    assert {item["name"] for item in chunk.symbols} >= {"AuthChecker", "Login"}
    assert any(item["symbol_type"] == "interface" for item in chunk.symbols)
    assert "type AuthChecker interface" in chunk.text


def test_pack_records_symbol_metadata_when_extraction_succeeds(tmp_path: Path) -> None:
    _write(
        tmp_path / "src" / "auth_service.py",
        """
# Handles auth checks.
class AuthService:
    \"\"\"Auth service docs.\"\"\"

    def login(self, token: str) -> bool:
        return token.startswith("prod_")


def helper() -> None:
    pass
""".strip()
        + "\n",
    )

    cfg = RedconConfig(
        compression=CompressionSettings(
            full_file_threshold_tokens=1,
            snippet_score_threshold=0,
            snippet_total_line_limit=40,
            symbol_extraction_enabled=True,
        )
    )

    data = as_json_dict(run_pack("refactor auth login", repo=tmp_path, max_tokens=1000, config=cfg))
    entry = next(item for item in data["compressed_context"] if item["path"] == "src/auth_service.py")

    assert entry["strategy"] == "symbol"
    assert entry["chunk_strategy"] == "symbol-extract-python"
    assert entry["symbols"]
    assert {"name", "symbol_type", "path", "start_line", "end_line", "exported"} <= set(entry["symbols"][0])
    assert any(item["name"] == "AuthService" for item in entry["symbols"])
    assert any(r["kind"] in {"class", "function"} for r in entry["selected_ranges"])


def test_pack_falls_back_to_language_aware_snippet_when_symbol_extraction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(
        tmp_path / "src" / "auth_service.py",
        """
import os

# Handles auth checks.
class AuthService:
    def login(self, token: str) -> bool:
        return token.startswith("prod_")


def helper() -> None:
    pass
""".strip()
        + "\n",
    )

    def _raise_symbol_error(**_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(context_compressor, "select_symbol_aware_chunks", _raise_symbol_error)

    cfg = RedconConfig(
        compression=CompressionSettings(
            full_file_threshold_tokens=1,
            snippet_score_threshold=0,
            snippet_total_line_limit=40,
            symbol_extraction_enabled=True,
        )
    )

    data = as_json_dict(run_pack("refactor auth login", repo=tmp_path, max_tokens=1000, config=cfg))
    entry = next(item for item in data["compressed_context"] if item["path"] == "src/auth_service.py")

    assert entry["strategy"] == "snippet"
    assert entry["chunk_strategy"] == "language-aware-python"
    assert "symbol extraction failed" in entry["chunk_reason"]
    assert "symbols" not in entry


def test_symbol_level_packing_reduces_tokens_against_full_file_pack(tmp_path: Path) -> None:
    repeated_helpers = "\n\n".join(
        f"def helper_{index}() -> str:\n    return 'helper-{index}'"
        for index in range(25)
    )
    _write(
        tmp_path / "src" / "auth_service.py",
        (
            """
# Critical auth entrypoint.
class AuthService:
    def login(self, token: str) -> bool:
        return token.startswith("prod_")

"""
            + repeated_helpers
            + "\n"
        ),
    )

    full_cfg = RedconConfig(
        compression=CompressionSettings(
            full_file_threshold_tokens=100_000,
            snippet_score_threshold=0,
            symbol_extraction_enabled=True,
        )
    )
    symbol_cfg = RedconConfig(
        compression=CompressionSettings(
            full_file_threshold_tokens=1,
            snippet_score_threshold=0,
            snippet_total_line_limit=24,
            symbol_extraction_enabled=True,
        )
    )

    full_data = as_json_dict(run_pack("refactor auth login", repo=tmp_path, max_tokens=5000, config=full_cfg))
    symbol_data = as_json_dict(run_pack("refactor auth login", repo=tmp_path, max_tokens=5000, config=symbol_cfg))

    full_entry = next(item for item in full_data["compressed_context"] if item["path"] == "src/auth_service.py")
    symbol_entry = next(item for item in symbol_data["compressed_context"] if item["path"] == "src/auth_service.py")

    assert full_entry["strategy"] == "full"
    assert symbol_entry["strategy"] == "symbol"
    assert symbol_entry["compressed_tokens"] < full_entry["compressed_tokens"]
    assert symbol_data["budget"]["estimated_input_tokens"] < full_data["budget"]["estimated_input_tokens"]
