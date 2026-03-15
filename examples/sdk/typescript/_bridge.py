#!/usr/bin/env python3
"""stdin → stdout JSON bridge for the ContextBudget TypeScript SDK.

The TypeScript SDK spawns this script, writes a JSON request to stdin, and
reads a JSON response from stdout.  All errors are written to stderr and
signalled via a non-zero exit code.

Request envelope:
    {
        "method":  "prepareContext" | "simulateAgent" | "profileRun",
        "params":  { ... }
    }

Response:
    The raw dict returned by the corresponding Python SDK function, serialised
    as JSON.  Never contains newlines inside the JSON body — the TypeScript
    side reads until EOF.
"""

import json
import sys

# ---------------------------------------------------------------------------
# Path bootstrap — ensures contextbudget is importable in both pip-installed
# and editable-install (development) environments.
#
# When contextbudget is installed via `pip install contextbudget` the package
# is in site-packages and needs no extra path manipulation.
#
# During development (pip install -e .) Python may not find the package when
# the script is run directly (sys.path[0] is the script directory, not CWD).
# We walk up from this file's location to find the contextbudget package root
# and add it to sys.path.
# ---------------------------------------------------------------------------
try:
    import contextbudget  # noqa: F401 — fast path: package already importable
except ModuleNotFoundError:
    from pathlib import Path as _Path

    _here = _Path(__file__).resolve().parent
    for _candidate in [_here, *_here.parents]:
        if (_candidate / "contextbudget" / "__init__.py").exists():
            sys.path.insert(0, str(_candidate))
            break


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON input: {exc}"}), file=sys.stderr)
        sys.exit(1)

    method = payload.get("method", "")
    params: dict = payload.get("params", {})

    try:
        # Import here so startup cost is paid only when the bridge is invoked.
        from contextbudget.sdk import prepare_context, profile_run, simulate_agent

        if method == "prepareContext":
            result = prepare_context(
                task=params["task"],
                repo=params.get("repo", "."),
                workspace=params.get("workspace"),
                max_tokens=params.get("maxTokens"),
                top_files=params.get("topFiles"),
                delta_from=params.get("deltaFrom"),
                metadata=params.get("metadata"),
                config_path=params.get("configPath"),
            )
            output = result.as_record()

        elif method == "simulateAgent":
            output = simulate_agent(
                task=params["task"],
                repo=params.get("repo", "."),
                workspace=params.get("workspace"),
                model=params.get("model", "claude-sonnet-4-6"),
                top_files=params.get("topFiles"),
                price_per_1m_input=params.get("pricePerMillionInput"),
                price_per_1m_output=params.get("pricePerMillionOutput"),
                config_path=params.get("configPath"),
            )

        elif method == "profileRun":
            output = profile_run(
                task=params["task"],
                repo=params.get("repo", "."),
                workspace=params.get("workspace"),
                max_tokens=params.get("maxTokens"),
                top_files=params.get("topFiles"),
                config_path=params.get("configPath"),
            )

        else:
            print(json.dumps({"error": f"unknown method: {method!r}"}), file=sys.stderr)
            sys.exit(1)

    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(2)

    json.dump(output, sys.stdout)


if __name__ == "__main__":
    main()
