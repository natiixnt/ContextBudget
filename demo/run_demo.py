#!/usr/bin/env python3
"""Redcon end-to-end demo.

Exercises all major components against this repository itself:
  1. Pack        - scan, rank, and compress repository context
  2. Policy      - evaluate a token-budget policy against the run artifact
  3. Cost        - translate token savings into estimated USD savings
  4. Benchmark   - compare two packing strategies side-by-side
  5. Gateway     - start the FastAPI gateway and send real HTTP requests
  6. Adapters    - instantiate the OpenAI and Anthropic wrappers (dry-run)

Run from the repo root:
    python demo/run_demo.py

No network calls are made.  The gateway demo sends requests to a local
server started in-process.  The adapter demo uses a stub LLM function
so no real API keys are required.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

# Resolve repo root relative to this file so the demo works from any cwd
REPO = Path(__file__).parent.parent.resolve()

# Ensure the repo root is on sys.path so `import redcon` works when the
# package is not installed (e.g. running from a cloned repo with `python
# demo/run_demo.py` instead of from inside the package).
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SEPARATOR = "-" * 64


def section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


# ---------------------------------------------------------------------------
# 1. Pack
# ---------------------------------------------------------------------------


def demo_pack() -> dict:
    section("1. Pack - scan, rank, and compress context")
    from redcon.engine import RedconEngine

    engine = RedconEngine()
    result = engine.pack(
        task="add Redis caching to the gateway session store",
        repo=REPO,
        max_tokens=32_000,
        top_files=30,
    )

    budget = result.get("budget", {})
    estimated = int(budget.get("estimated_input_tokens", 0))
    saved = int(budget.get("estimated_saved_tokens", 0))
    files = result.get("files_included", [])

    print(f"  files ranked   : {len(result.get('files_ranked', []))}")
    print(f"  files included : {len(files)}")
    print(f"  token estimate : {estimated:,}")
    print(f"  tokens saved   : {saved:,}")
    print(f"  quality risk   : {budget.get('quality_risk_estimate', 'n/a')}")
    return result


# ---------------------------------------------------------------------------
# 2. Policy
# ---------------------------------------------------------------------------


def demo_policy(run_artifact: dict) -> None:
    section("2. Policy - evaluate budget constraints")
    from redcon.core.policy import PolicySpec, evaluate_policy, policy_result_to_dict

    policy = PolicySpec(
        max_estimated_input_tokens=80_000,
        max_files_included=50,
        max_quality_risk_level="medium",
        min_estimated_savings_percentage=0.0,
    )
    result = evaluate_policy(run_artifact, policy)
    d = policy_result_to_dict(result)

    status = "PASS" if d["passed"] else "FAIL"
    print(f"  policy result  : {status}")
    for check_name, check in d["checks"].items():
        mark = "ok" if check["passed"] else "FAIL"
        print(f"  [{mark}] {check_name}: {check['actual']} (limit: {check['limit']})")
    if d["violations"]:
        for v in d["violations"]:
            print(f"  violation: {v}")


# ---------------------------------------------------------------------------
# 3. Cost analytics
# ---------------------------------------------------------------------------


def demo_cost(run_artifact: dict) -> None:
    section("3. Cost analytics - translate savings to USD")
    from redcon.core.cost_analysis import compute_cost_analysis

    analysis = compute_cost_analysis(run_artifact, model="gpt-4o")
    print(f"  model          : {analysis['model']} ({analysis['provider']})")
    print(f"  baseline       : {analysis['baseline_tokens']:,} tokens  (${analysis['baseline_cost_usd']:.4f})")
    print(f"  optimized      : {analysis['optimized_tokens']:,} tokens  (${analysis['optimized_cost_usd']:.4f})")
    print(f"  saved          : {analysis['saved_tokens']:,} tokens  (${analysis['saved_cost_usd']:.4f})")
    print(f"  savings rate   : {analysis['savings_pct']:.1f}%")
    if analysis["notes"]:
        for note in analysis["notes"]:
            print(f"  note: {note}")


# ---------------------------------------------------------------------------
# 4. Benchmark
# ---------------------------------------------------------------------------


def demo_benchmark() -> None:
    section("4. Benchmark - compare packing strategies")
    from redcon.core.benchmark import run_benchmark

    result = run_benchmark(
        task="add caching to the session store",
        repo=REPO,
        max_tokens=32_000,
        top_files=20,
    )

    strategies = result.get("strategies", [])
    print(f"  strategies compared : {len(strategies)}")
    for s in strategies:
        name = s.get("strategy", "?")
        tokens = s.get("estimated_input_tokens", 0)
        saved = s.get("estimated_saved_tokens", 0)
        ms = s.get("runtime_ms", 0)
        print(f"  {name:30s}  tokens={tokens:>7,}  saved={saved:>7,}  {ms}ms")


# ---------------------------------------------------------------------------
# 5. Gateway (FastAPI, in-process)
# ---------------------------------------------------------------------------


def demo_gateway() -> None:
    section("5. Gateway - FastAPI server (in-process)")
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        print("  fastapi/uvicorn not installed — skipping gateway demo")
        print("  Install: pip install 'redcon[gateway]'")
        return

    from redcon.gateway import GatewayConfig, GatewayServer

    config = GatewayConfig(host="127.0.0.1", port=18787, log_requests=False)
    server = GatewayServer(config)
    server.start(block=False)
    time.sleep(0.4)

    try:
        # Health check
        with urllib.request.urlopen("http://127.0.0.1:18787/health", timeout=5) as r:
            health = json.loads(r.read())
        print(f"  /health        : {health}")

        # Metrics
        with urllib.request.urlopen("http://127.0.0.1:18787/metrics", timeout=5) as r:
            metrics = json.loads(r.read())
        print(f"  /metrics       : uptime={metrics['gateway']['uptime_seconds']}s")

        # POST /prepare-context
        body = json.dumps({"task": "add caching", "repo": str(REPO), "max_tokens": 32000}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:18787/prepare-context",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        print(f"  /prepare-context:")
        print(f"    run_id         : {resp.get('run_id', 'n/a')}")
        print(f"    token_estimate : {resp.get('token_estimate', 0):,}")
        print(f"    tokens_saved   : {resp.get('tokens_saved', 0):,}")
        ctx = resp.get("optimized_context", {})
        print(f"    files_included : {len(ctx.get('files_included', []))}")
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# 6. Adapters (dry-run, no real API calls)
# ---------------------------------------------------------------------------


def demo_adapters() -> None:
    section("6. Adapters - OpenAI and Anthropic wrappers (dry-run)")
    from redcon.integrations import AnthropicAgentWrapper, OpenAIAgentWrapper

    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "src").mkdir()
        (repo / "src" / "cache.py").write_text(
            "def cache_get(key: str) -> str | None:\n    return None\n"
        )

        # OpenAI adapter — stub LLM function
        openai_agent = OpenAIAgentWrapper(model="gpt-4.1", repo=repo)
        openai_agent._runtime._llm_fn = lambda prompt: "[stub] gpt-4.1 response"

        result = openai_agent.run_task("add caching", repo=repo)
        print(f"  OpenAI adapter:")
        print(f"    turn           : {result.turn_number}")
        print(f"    session_id     : {result.session_id}")
        print(f"    llm_response   : {result.llm_response}")
        print(f"    token_estimate : {result.prepared_context.estimated_tokens:,}")

        # Anthropic adapter — stub LLM function
        anthropic_agent = AnthropicAgentWrapper(model="claude-sonnet-4-6", repo=repo)
        anthropic_agent._runtime._llm_fn = lambda prompt: "[stub] claude response"

        result2 = anthropic_agent.run_task("add caching", repo=repo)
        print(f"  Anthropic adapter:")
        print(f"    turn           : {result2.turn_number}")
        print(f"    session_id     : {result2.session_id}")
        print(f"    llm_response   : {result2.llm_response}")
        print(f"    token_estimate : {result2.prepared_context.estimated_tokens:,}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("Redcon - production-ready alpha demo")
    print(f"repo: {REPO}")

    run_artifact = demo_pack()
    demo_policy(run_artifact)
    demo_cost(run_artifact)
    demo_benchmark()
    demo_gateway()
    demo_adapters()

    print(f"\n{SEPARATOR}")
    print("  All demos complete.")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
