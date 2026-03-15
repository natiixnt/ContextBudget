# Policy and CI

## Policy Checks

ContextBudget can gate on context quality metrics before returning a result. Supported checks:

| Check | Parameter |
|-------|-----------|
| Maximum estimated input tokens | `max_estimated_input_tokens` |
| Maximum files included | `max_files_included` |
| Maximum quality risk level | `max_quality_risk_level` (`"low"`, `"medium"`, `"high"`) |
| Minimum estimated savings percentage | `min_estimated_savings_percentage` |

---

## Policy TOML

Create a `policy.toml` file:

```toml
max_estimated_input_tokens = 28000
max_files_included = 15
max_quality_risk_level = "medium"
min_estimated_savings_percentage = 20.0
```

---

## Strict Mode CLI

```bash
contextbudget pack "tighten auth middleware token validation" \
  --repo . \
  --strict \
  --policy examples/policy.toml
```

Returns non-zero when `--strict` is set and a policy violation is detected. When `--delta` is used, policy evaluation applies to the effective delta package size instead of the full current baseline.

---

## Strict Mode Python API

```python
from contextbudget import BudgetGuard, BudgetPolicyViolationError

guard = BudgetGuard(
    max_tokens=30000,
    strict=True,
    max_files_included=15,
    max_quality_risk_level="medium",
)

try:
    result = guard.pack_context(task="large refactor", repo=".")
except BudgetPolicyViolationError as err:
    for v in err.policy_result["violations"]:
        print(f"violation: {v}")
    # err.run_artifact — the pack result that triggered the violation
```

### Evaluate policy without raising

```python
guard = BudgetGuard(max_tokens=30000, max_files_included=5)
result = guard.pack_context(task="large refactor", repo=".")

policy_result = guard.evaluate_policy(result)
if not policy_result["passed"]:
    for v in policy_result["violations"]:
        print(v)
```

---

## GitHub Actions Integration

### Example Workflow

```yaml
name: ContextBudget

on:
  pull_request:
  workflow_dispatch:

jobs:
  context-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - run: pip install -e .[dev]

      - name: Pack context
        run: |
          contextbudget pack "review changed code" \
            --repo . \
            --strict \
            --policy .github/contextbudget-policy.toml

      - name: PR audit
        run: |
          contextbudget pr-audit \
            --repo . \
            --base "${{ github.event.pull_request.base.sha }}" \
            --head "${{ github.event.pull_request.head.sha }}" \
            --out-prefix contextbudget-pr

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: contextbudget-artifacts
          path: |
            run.json
            run.md
            contextbudget-pr.json
            contextbudget-pr.md
            contextbudget-pr.comment.md
```

### Policy File (`.github/contextbudget-policy.toml`)

```toml
max_estimated_input_tokens = 28000
max_files_included = 20
max_quality_risk_level = "medium"
```

---

## PR Audit Gate

`contextbudget pr-audit` analyzes the PR diff directly from git, estimates changed-file token cost before and after the PR, flags files that grew, and detects newly introduced dependencies.

```bash
contextbudget pr-audit \
  --repo . \
  --base "${{ github.event.pull_request.base.sha }}" \
  --head "${{ github.event.pull_request.head.sha }}" \
  --max-token-increase 5000 \
  --max-token-increase-pct 20
```

The `--max-token-increase` and `--max-token-increase-pct` flags gate the PR if context growth exceeds the threshold. The audit also writes a `*.comment.md` artifact ready to post as a PR comment.
