#!/usr/bin/env python3
"""context-eval CLI.

Usage:
    python context-eval/run.py --repo . --budget 16000 --max-tasks 20
    python context-eval/run.py --tools redcon,keyword-topk,random

Writes results/results.json and results/results.md next to this script.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
sys.path.insert(0, str(_here.parent))  # import redcon from the repo checkout

from contexteval.adapters import ADAPTERS  # noqa: E402
from contexteval.report import as_dict, render_markdown  # noqa: E402
from contexteval.runner import evaluate  # noqa: E402
from contexteval.tasks import extract_tasks  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the context-eval benchmark.")
    parser.add_argument("--repo", default=str(_here.parent), help="Repository to evaluate on")
    parser.add_argument("--rev", default="HEAD", help="History to draw tasks from")
    parser.add_argument("--budget", type=int, default=24_000, help="Token budget per task")
    parser.add_argument("--max-tasks", type=int, default=33)
    parser.add_argument(
        "--tools",
        default="all",
        help="Comma-separated adapter names (default: all of " + ",".join(ADAPTERS) + ")",
    )
    parser.add_argument("--out", default=str(_here / "results"))
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if args.tools == "all":
        adapters = dict(ADAPTERS)
    else:
        adapters = {name: ADAPTERS[name] for name in args.tools.split(",")}

    tasks = extract_tasks(repo, rev=args.rev, max_tasks=args.max_tasks)
    if not tasks:
        print("No usable tasks found in git history.", file=sys.stderr)
        return 1
    print(f"Extracted {len(tasks)} tasks from {repo}")

    results = evaluate(repo, tasks, adapters, budget=args.budget)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = as_dict(results, budget=args.budget, repo=repo.name)
    payload["generated"] = generated
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    markdown = render_markdown(results, budget=args.budget, repo=repo.name, generated=generated)
    (out_dir / "results.md").write_text(markdown, encoding="utf-8")

    print()
    print(markdown.split("## Per-task", 1)[0])
    print(f"Wrote {out_dir / 'results.json'} and {out_dir / 'results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
