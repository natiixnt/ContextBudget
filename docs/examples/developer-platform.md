# Developer Platform Examples

ContextBudget exposes a full developer platform for analyzing agent context and token usage.
These examples show how to use the six analytics commands end-to-end.

## Quick-start: pack, then analyze

```bash
# 1. Pack context and record a run artifact
contextbudget pack "add Redis caching to the search API" \
  --repo . \
  --max-tokens 32000 \
  --out-prefix runs/caching-run

# 2. Observe the run: extract metrics and persist to history
contextbudget observe runs/caching-run.json

# 3. Visualize the dependency graph annotated with this run's data
contextbudget visualize --repo . --history runs/caching-run.json --html

# 4. Get architecture advice using the run for frequency signals
contextbudget advise --repo . --history runs/caching-run.json

# 5. Simulate cost of running an agent on this task
contextbudget simulate-agent --run-artifact runs/caching-run.json \
  --model claude-sonnet-4-6 \
  --context-mode rolling
```

---

## observe — extract run metrics

```bash
# Human-readable metrics report
contextbudget observe run.json

# Machine-readable: pipe a single field
contextbudget observe run.json --json | jq '.tokens_saved'

# Persist metrics and export the full history store
contextbudget observe run.json --export-history

# Skip persisting to history (dry run)
contextbudget observe run.json --no-store --json
```

**Output fields (JSON):**

```json
{
  "total_tokens": 18400,
  "tokens_saved": 6200,
  "baseline_tokens": 24600,
  "files_read": 12,
  "unique_files_read": 11,
  "duplicate_reads": 1,
  "cache_hits": 3,
  "run_duration_ms": 420
}
```

---

## simulate-agent — pre-flight cost estimate

```bash
# Fresh simulation from a task description
contextbudget simulate-agent "implement OAuth2 login" \
  --repo . \
  --model claude-sonnet-4-6 \
  --context-mode rolling \
  --output-tokens 800

# Load task and repo from an existing pack artifact
contextbudget simulate-agent \
  --run-artifact runs/caching-run.json \
  --model gpt-4o \
  --context-mode full

# Compare context modes for the same task
for mode in isolated rolling full; do
  echo "=== $mode ==="
  contextbudget simulate-agent "add caching" --repo . \
    --context-mode $mode --json | jq '.cost_estimate.total_cost_usd'
done

# List all known model pricing
contextbudget simulate-agent --list-models
```

---

## drift — detect token growth

```bash
# Alert when token usage grew > 10% over last 20 runs
contextbudget drift --repo .

# Tighter threshold for a release-critical repo
contextbudget drift --repo . --threshold 5 --window 10

# Filter to a specific feature area
contextbudget drift --repo . --task "auth"

# Detect drift from explicit run files (no history.json required)
contextbudget drift --runs runs/run-1.json runs/run-2.json runs/run-3.json

# CI gate: fail the build if context is drifting
contextbudget drift --repo . --threshold 15 || exit 1

# JSON output for a custom dashboard
contextbudget drift --repo . --json | jq '{
  alert: .drift.alert,
  verdict: .drift.verdict,
  token_drift_pct: .drift.token_drift_pct
}'
```

**Exit codes:** `0` = no drift, `2` = drift alert triggered.

---

## advise — architecture suggestions

```bash
# Basic import graph analysis
contextbudget advise --repo .

# Use pack runs to weight suggestions by inclusion frequency
contextbudget advise --repo . --history runs/

# Tune detection thresholds
contextbudget advise --repo . \
  --large-file-tokens 800 \
  --high-fanin 3 \
  --high-fanout 8 \
  --top 10

# Extract only split-file suggestions
contextbudget advise --repo . --json \
  | jq '[.suggestions[] | select(.suggestion == "split_file") | {path, impact: .estimated_token_impact}]'

# Pipe into a review checklist
contextbudget advise --repo . --json \
  | jq -r '.suggestions[:5][] | "[ ] \(.suggestion): \(.path) (saves ~\(.estimated_token_impact) tokens)"'
```

---

## visualize — dependency graph

```bash
# Export graph as JSON and Markdown
contextbudget visualize --repo .

# Add historical inclusion frequency annotations
contextbudget visualize --repo . --history runs/

# Generate interactive HTML for browser review
contextbudget visualize --repo . --history runs/ --html

# Find the most token-heavy files
contextbudget visualize --repo . --json \
  | jq '.stats.top_token_files'

# Find the most-imported (high fan-in) files
contextbudget visualize --repo . --json \
  | jq '.nodes | sort_by(-.in_degree) | .[:5] | map({path, in_degree, estimated_tokens})'
```

---

## dataset — reproducible benchmarks

```bash
# Build a dataset from a TOML task list (runs fresh benchmarks)
contextbudget dataset tasks.toml --repo . --max-tokens 32000

# Build a dataset from pre-existing run artifacts (no re-running)
contextbudget dataset --runs runs/run-*.json

# Extract the aggregate reduction percentage
contextbudget dataset tasks.toml --repo . --json \
  | jq '.aggregate.avg_reduction_pct'

# Compare multiple repos
for repo in service-a service-b service-c; do
  echo "$repo:"
  contextbudget dataset tasks.toml --repo ../$repo --json \
    | jq '.aggregate | {avg_reduction_pct, avg_optimized_tokens}'
done

# Built-in task suite (no TOML required)
contextbudget build-dataset --repo . --out-prefix reports/baseline
```

**TOML task list format:**

```toml
[[tasks]]
name = "Add caching"
description = "add Redis caching to the search API"

[[tasks]]
name = "Add authentication"
description = "add JWT authentication middleware to protect API routes"

[[tasks]]
name = "Refactor database layer"
description = "refactor the database layer to use the repository pattern"
```

---

## Full analysis pipeline

Combine all six commands into a single analysis workflow:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="."
TASK="add Redis caching to the search API"
OUT="runs/$(date +%Y%m%d-%H%M%S)"

# 1. Pack
contextbudget pack "$TASK" --repo "$REPO" --max-tokens 32000 --out-prefix "$OUT"

# 2. Observe
contextbudget observe "${OUT}.json" --base-dir "$REPO"

# 3. Drift check (non-zero if context is growing)
contextbudget drift --repo "$REPO" --threshold 10 --json | tee drift-report.json
if [ "$(jq '.drift.alert' drift-report.json)" = "true" ]; then
  echo "WARNING: context drift detected"
fi

# 4. Advise
contextbudget advise --repo "$REPO" --history "${OUT}.json" \
  --json | jq '.summary'

# 5. Visualize
contextbudget visualize --repo "$REPO" --history "${OUT}.json" --html

# 6. Simulate cost for CI reporting
contextbudget simulate-agent --run-artifact "${OUT}.json" \
  --model claude-sonnet-4-6 --context-mode rolling --json \
  | jq '{total_cost_usd: .cost_estimate.total_cost_usd, total_tokens: .total_tokens}'
```

---

## CI integration

### GitHub Actions drift gate

```yaml
- name: Context drift check
  run: |
    contextbudget drift --repo . --threshold 15 --json \
      | tee drift.json
    if [ "$(jq '.drift.alert' drift.json)" = "true" ]; then
      echo "::warning::Context drift detected: $(jq '.drift.token_drift_pct' drift.json)%"
    fi
```

### Cost estimate on pull requests

```yaml
- name: Simulate agent cost
  run: |
    contextbudget simulate-agent "${{ github.event.pull_request.title }}" \
      --repo . \
      --model claude-sonnet-4-6 \
      --context-mode rolling \
      --json | jq '.cost_estimate.total_cost_usd'
```

### Weekly dataset benchmark

```yaml
- name: Run benchmark dataset
  run: |
    contextbudget build-dataset --repo . \
      --out-prefix reports/weekly-$(date +%Y-W%V)
    contextbudget observe reports/weekly-$(date +%Y-W%V).json
```
