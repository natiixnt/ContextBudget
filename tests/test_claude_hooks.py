"""Tests for the Claude Code UserPromptSubmit hook integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from redcon.hooks.claude_code import (
    HOOK_COMMAND,
    build_prompt_context,
    hook_status,
    install_hook,
    run_user_prompt_submit,
    uninstall_hook,
)


def _settings(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".claude" / "settings.json").read_text())


def _write_settings(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_repo(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "auth.py").write_text("def login(token):\n    return token.startswith('prod_')\n" * 5)
    (src / "billing.py").write_text("def charge(amount):\n    return amount\n" * 5)


# --- install / uninstall / status ---


def test_install_creates_settings_with_hook(tmp_path: Path):
    result = install_hook(tmp_path)
    assert result["status"] == "installed"

    data = _settings(tmp_path)
    entries = data["hooks"]["UserPromptSubmit"]
    assert len(entries) == 1
    hook = entries[0]["hooks"][0]
    assert hook["type"] == "command"
    assert hook["command"] == HOOK_COMMAND
    assert hook["timeout"] > 0


def test_install_is_idempotent(tmp_path: Path):
    install_hook(tmp_path)
    result = install_hook(tmp_path)
    assert result["status"] == "up_to_date"
    assert len(_settings(tmp_path)["hooks"]["UserPromptSubmit"]) == 1


def test_install_preserves_existing_settings_and_hooks(tmp_path: Path):
    _write_settings(
        tmp_path,
        {
            "permissions": {"allow": ["Bash(npm test)"]},
            "hooks": {
                "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-tool run"}]}],
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "guard"}]}
                ],
            },
        },
    )

    assert install_hook(tmp_path)["status"] == "installed"

    data = _settings(tmp_path)
    assert data["permissions"] == {"allow": ["Bash(npm test)"]}
    assert len(data["hooks"]["PreToolUse"]) == 1
    submit = data["hooks"]["UserPromptSubmit"]
    assert len(submit) == 2
    assert submit[0]["hooks"][0]["command"] == "other-tool run"


def test_install_refuses_to_touch_unparseable_settings(tmp_path: Path):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    result = install_hook(tmp_path)
    assert result["status"] == "error"
    # The broken file is left exactly as it was.
    assert path.read_text() == "{not json"


def test_uninstall_removes_only_redcon_entries(tmp_path: Path):
    _write_settings(
        tmp_path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "other-tool run"}]},
                    {"hooks": [{"type": "command", "command": HOOK_COMMAND}]},
                ]
            }
        },
    )

    assert uninstall_hook(tmp_path)["status"] == "removed"
    submit = _settings(tmp_path)["hooks"]["UserPromptSubmit"]
    assert len(submit) == 1
    assert submit[0]["hooks"][0]["command"] == "other-tool run"


def test_uninstall_cleans_empty_structures(tmp_path: Path):
    install_hook(tmp_path)
    assert uninstall_hook(tmp_path)["status"] == "removed"
    assert "hooks" not in _settings(tmp_path)


def test_uninstall_when_not_installed(tmp_path: Path):
    assert uninstall_hook(tmp_path)["status"] == "not_installed"


def test_status_reflects_registration(tmp_path: Path):
    assert hook_status(tmp_path) is None
    install_hook(tmp_path)
    assert hook_status(tmp_path) == tmp_path / ".claude" / "settings.json"


# --- context building ---


def test_context_block_ranks_relevant_files(tmp_path: Path):
    _write_repo(tmp_path)
    block = build_prompt_context("fix the login token validation in auth", tmp_path)
    assert block is not None
    assert block.startswith("<redcon-context>")
    assert block.endswith("</redcon-context>")
    assert "src/auth.py" in block
    assert "redcon_compress" in block


def test_short_and_slash_prompts_are_skipped(tmp_path: Path):
    _write_repo(tmp_path)
    assert build_prompt_context("ok", tmp_path) is None
    assert build_prompt_context("retry", tmp_path) is None
    assert build_prompt_context("/compact keep the current task list", tmp_path) is None


def test_context_fails_open_on_broken_repo(tmp_path: Path):
    assert build_prompt_context("fix the login token validation", tmp_path / "nope") is None


# --- hook entry point ---


def test_run_hook_prints_context_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture):
    _write_repo(tmp_path)
    payload = json.dumps({"prompt": "fix the login token validation in auth", "cwd": str(tmp_path)})
    assert run_user_prompt_submit(payload) == 0
    out = capsys.readouterr().out
    assert "<redcon-context>" in out
    assert "src/auth.py" in out


def test_run_hook_is_silent_on_garbage_input(capsys: pytest.CaptureFixture):
    assert run_user_prompt_submit("definitely not json") == 0
    assert run_user_prompt_submit("") == 0
    assert capsys.readouterr().out == ""


def test_run_hook_respects_disable_env(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    _write_repo(tmp_path)
    monkeypatch.setenv("REDCON_HOOK_DISABLE", "1")
    payload = json.dumps({"prompt": "fix the login token validation in auth", "cwd": str(tmp_path)})
    assert run_user_prompt_submit(payload) == 0
    assert capsys.readouterr().out == ""
