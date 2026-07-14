"""Trust-hardening guarantees: secret exclusion, MCP confinement, run gating."""

from __future__ import annotations

from pathlib import Path

import pytest

from redcon.core.pipeline import as_json_dict, run_pack


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- secret exclusion -------------------------------------------------------


def test_secrets_never_packed_by_default(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "db.py", "def connect():\n    return DATABASE_URL\n" * 10)
    _write(tmp_path / ".env", "DATABASE_URL=postgres://u:SECRETPASS@h/db\n")
    _write(tmp_path / "deploy" / "id_rsa", "-----BEGIN RSA PRIVATE KEY-----\nSECRETKEY\n")
    _write(tmp_path / "credentials.json", '{"aws_secret_access_key": "AKIASECRET"}')

    data = as_json_dict(run_pack("fix the database connection", repo=tmp_path, max_tokens=2000))
    packed = "\n".join(item.get("text", "") for item in data["compressed_context"])

    assert "SECRETPASS" not in packed
    assert "BEGIN RSA PRIVATE KEY" not in packed
    assert "AKIASECRET" not in packed
    assert not any(
        ".env" in f or "id_rsa" in f or "credentials" in f for f in data["files_included"]
    )


def test_secret_exclusion_can_be_disabled(tmp_path: Path) -> None:
    _write(tmp_path / ".env", "SECRET=leakme\n" * 5)
    _write(tmp_path / "redcon.toml", "[scan]\nexclude_secrets = false\n")

    data = as_json_dict(run_pack("read the env file", repo=tmp_path, max_tokens=2000))

    assert any(".env" in f for f in data["files_included"])


def test_doctor_flags_disabled_secret_exclusion(tmp_path: Path) -> None:
    from redcon.core.doctor import run_doctor

    _write(tmp_path / ".env", "X=1\n")
    _write(tmp_path / "redcon.toml", "[scan]\nexclude_secrets = false\n")

    report = run_doctor(tmp_path)
    check = next(c for c in report.checks if c.name == "secret_exposure")
    assert check.status == "fail"


# --- MCP path confinement ---------------------------------------------------


def test_mcp_tool_rejects_path_outside_root(tmp_path: Path, monkeypatch) -> None:
    from redcon.mcp import tools

    monkeypatch.setenv("REDCON_MCP_ROOT", str(tmp_path))
    _write(tmp_path / "a.py", "x = 1\n")

    ok = tools.tool_rank(task="find", repo=str(tmp_path))
    assert "error" not in ok

    escaped = tools.tool_search(pattern="KEY", task="x", repo="/etc", scope="all")
    assert escaped.get("kind") == "path_denied"


# --- redcon_run gating ------------------------------------------------------


def test_redcon_run_disabled_by_default(monkeypatch) -> None:
    from redcon.mcp import tools

    monkeypatch.delenv("REDCON_MCP_ENABLE_RUN", raising=False)
    result = tools.tool_run("git status")
    assert result.get("kind") == "disabled"


def test_redcon_run_rejects_dangerous_args(monkeypatch) -> None:
    from redcon.mcp import tools

    monkeypatch.setenv("REDCON_MCP_ENABLE_RUN", "1")
    result = tools.tool_run("python -c 'import os'", cwd=".")
    assert result.get("kind") == "not_allowed"


def test_runner_blocks_escape_flags() -> None:
    from redcon.cmd.runner import CommandNotAllowed, reject_dangerous_args

    for argv in (("find", ".", "-exec", "rm", "{}", ";"), ("npm", "run", "evil")):
        with pytest.raises(CommandNotAllowed):
            reject_dangerous_args(argv)


def test_runner_allows_benign_uppercase_flags() -> None:
    from redcon.cmd.runner import reject_dangerous_args

    # -C (git -C dir, grep -C 3) must not be confused with -c.
    reject_dangerous_args(("git", "-C", "sub", "status"))
    reject_dangerous_args(("grep", "-C", "3", "pattern"))


# --- gateway fail-closed ----------------------------------------------------


def test_gateway_refuses_public_bind_without_key() -> None:
    from redcon.gateway.config import GatewayConfig
    from redcon.gateway.server import GatewayServer

    with pytest.raises(RuntimeError, match="not loopback"):
        GatewayServer(GatewayConfig(host="0.0.0.0", port=8787, api_key=None)).start()


def test_gateway_allows_loopback_without_key() -> None:
    from redcon.gateway.config import GatewayConfig
    from redcon.gateway.server import GatewayServer

    # Guard must pass; we don't actually bind.
    GatewayServer(GatewayConfig(host="127.0.0.1", api_key=None))._guard_bind()
