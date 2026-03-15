#!/usr/bin/env python3
"""Example: agent integration with the Redcon Runtime Gateway.

Demonstrates the agent → Redcon Gateway → LLM architecture using only
Python stdlib (no extra dependencies required).

Architecture
------------
    agent (this script)
        │
        ▼  HTTP JSON
    Redcon Gateway  (POST /prepare-context, /run-agent-step, /report-run)
        │
        ▼  optimized prompt
    LLM API  (not called in this example — swap in your own client)

Usage
-----
    # Terminal 1 — start the gateway
    python -m redcon.gateway
    # or with a custom port:
    RC_GATEWAY_PORT=9000 python -m redcon.gateway

    # Terminal 2 — run this example against a local repo
    python examples/gateway_agent.py [/path/to/repo]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

GATEWAY_URL = "http://127.0.0.1:8787"


# ── Low-level HTTP helpers ─────────────────────────────────────────────────────


def _post(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
    """Send a POST request to the gateway and return the parsed JSON response."""
    url = f"{GATEWAY_URL}{endpoint}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())  # type: ignore[return-value]
    except urllib.error.HTTPError as exc:
        error_body = json.loads(exc.read())
        print(
            f"[gateway] HTTP {exc.code}: {error_body.get('error', exc.reason)}",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(
            f"[gateway] Cannot reach gateway at {GATEWAY_URL}: {exc.reason}\n"
            "Start the gateway first:  python -m redcon.gateway",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Gateway client helpers ─────────────────────────────────────────────────────


def prepare_context(
    task: str,
    repo: str = ".",
    *,
    max_tokens: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    """Call ``POST /prepare-context`` — stateless, no session created."""
    body: dict[str, Any] = {"task": task, "repo": repo}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if max_files is not None:
        body["max_files"] = max_files
    return _post("/prepare-context", body)


def run_agent_step(
    task: str,
    repo: str = ".",
    *,
    session_id: str | None = None,
    max_tokens: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    """Call ``POST /run-agent-step`` — stateful, reuses delta context across turns."""
    body: dict[str, Any] = {"task": task, "repo": repo}
    if session_id is not None:
        body["session_id"] = session_id
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if max_files is not None:
        body["max_files"] = max_files
    return _post("/run-agent-step", body)


def report_run(
    session_id: str,
    run_id: str,
    *,
    status: str = "success",
    tokens_used: int | None = None,
) -> dict[str, Any]:
    """Call ``POST /report-run`` to acknowledge an LLM call completion."""
    body: dict[str, Any] = {
        "session_id": session_id,
        "run_id": run_id,
        "status": status,
    }
    if tokens_used is not None:
        body["tokens_used"] = tokens_used
    return _post("/report-run", body)


# ── Display helpers ────────────────────────────────────────────────────────────


def _print_result(label: str, result: dict[str, Any]) -> None:
    print(f"\n{'─' * 64}")
    print(f"  {label}")
    print(f"{'─' * 64}")
    print(f"  run_id         : {result.get('run_id', 'n/a')}")
    print(f"  session_id     : {result.get('session_id', 'n/a')}")
    print(f"  token_estimate : {result.get('token_estimate', 0):,}")
    print(f"  tokens_saved   : {result.get('tokens_saved', 0):,}")
    print(f"  cache_hits     : {result.get('cache_hits', 0)}")
    print(f"  quality_risk   : {result.get('quality_risk', 'n/a')}")

    policy = result.get("policy_status", {})
    status_str = "PASS" if policy.get("passed") else "FAIL"
    violations = policy.get("violations", [])
    print(f"  policy_status  : {status_str}", end="")
    if violations:
        print(f"  violations={violations}", end="")
    print()

    if "turn" in result:
        print(f"  turn           : {result['turn']}")
    if "session_tokens" in result:
        print(f"  session_tokens : {result['session_tokens']:,}")

    ctx = result.get("optimized_context", {})
    files = ctx.get("files_included", [])
    print(f"  files_included : {len(files)} file(s)")
    for path in files[:5]:
        print(f"    • {path}")
    if len(files) > 5:
        print(f"    … and {len(files) - 5} more")


# ── Main demo ─────────────────────────────────────────────────────────────────


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else "."

    print("Redcon Gateway — Agent Integration Example")
    print(f"  repo    : {repo}")
    print(f"  gateway : {GATEWAY_URL}")

    # ── 1. Stateless context preparation ──────────────────────────────────────
    print("\n[1/4] POST /prepare-context (stateless) …")
    result = prepare_context(
        task="add Redis caching to the session store",
        repo=repo,
        max_tokens=32_000,
        max_files=50,
    )
    _print_result("POST /prepare-context", result)

    # Consume the optimized context — in a real agent you would forward
    # result["optimized_context"]["prompt_text"] to your LLM client here.

    # ── 2. Start a multi-turn agent session ───────────────────────────────────
    print("\n[2/4] POST /run-agent-step — turn 1 (new session) …")
    turn1 = run_agent_step(
        task="identify files that need caching support",
        repo=repo,
        max_tokens=32_000,
        max_files=50,
    )
    _print_result("Turn 1  POST /run-agent-step", turn1)

    session_id = turn1["session_id"]

    # ── 3. Continue the session — delta context is applied automatically ──────
    print("\n[3/4] POST /run-agent-step — turn 2 (delta context) …")
    turn2 = run_agent_step(
        task="implement in-memory LRU cache for scan results",
        repo=repo,
        session_id=session_id,
        max_tokens=32_000,
    )
    _print_result("Turn 2  POST /run-agent-step", turn2)

    # ── 4. Report completion ──────────────────────────────────────────────────
    print("\n[4/4] POST /report-run …")
    ack = report_run(
        session_id=session_id,
        run_id=turn2["run_id"],
        status="success",
        tokens_used=turn2["session_tokens"],
    )
    print(f"  acknowledged : {ack.get('acknowledged')}")
    print(f"  session_id   : {ack.get('session_id')}")
    print(f"  run_id       : {ack.get('run_id')}")
    print("\nDone.")


if __name__ == "__main__":
    main()
