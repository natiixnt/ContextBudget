# Cost Analytics

The cost analytics API compares the token cost of running ContextBudget (optimized) against what a naive full-context approach would cost (baseline). All queries are against the `events` table populated by telemetry from the runtime.

## Concepts

| Term | Source field | Meaning |
|---|---|---|
| Baseline tokens | `baseline_full_context_tokens` | Tokens that would be sent with no compression |
| Optimized tokens | `estimated_input_tokens` | Tokens actually sent after compression |
| Tokens saved | `estimated_saved_tokens` | Baseline − Optimized |
| Savings rate | Computed | `tokens_saved / (optimized + tokens_saved)` |

All counts are per `pack_completed` event.

## Endpoints

### `GET /analytics/cost`

Overall cost summary with optional filtering.

**Query parameters:**

| Parameter | Type | Description |
|---|---|---|
| `repository_id` | `string` | Filter to a specific SHA-256 repo digest |
| `from_date` | `datetime` | Inclusive start (ISO 8601) |
| `to_date` | `datetime` | Exclusive end (ISO 8601) |

**Request:**

```bash
curl "https://cloud.example.com/analytics/cost?from_date=2026-03-01T00:00:00Z" \
     -H "Authorization: Bearer cbk_..."
```

**Response:**

```json
{
  "baseline_tokens": 850000,
  "optimized_tokens": 310000,
  "tokens_saved": 540000,
  "savings_rate": 0.6352,
  "run_count": 142
}
```

---

### `GET /analytics/cost/by-repo`

Cost breakdown grouped by repository.

```bash
curl "https://cloud.example.com/analytics/cost/by-repo" \
     -H "Authorization: Bearer cbk_..."
```

Response:

```json
{
  "repositories": [
    {
      "repository_id": "a3f4b2...",
      "baseline_tokens": 420000,
      "optimized_tokens": 150000,
      "tokens_saved": 270000,
      "savings_rate": 0.6429,
      "run_count": 74
    }
  ]
}
```

Results are ordered by `optimized_tokens` descending (highest usage first).

---

### `GET /analytics/cost/by-date`

Daily cost breakdown, newest days first.

```bash
curl "https://cloud.example.com/analytics/cost/by-date?from_date=2026-03-01T00:00:00Z" \
     -H "Authorization: Bearer cbk_..."
```

Response:

```json
{
  "days": [
    {
      "date": "2026-03-15",
      "baseline_tokens": 52000,
      "optimized_tokens": 19000,
      "tokens_saved": 33000,
      "savings_rate": 0.6346,
      "run_count": 9
    }
  ]
}
```

---

## Converting tokens to USD

Use the pricing module to estimate dollar costs from token counts:

```python
from contextbudget.telemetry.pricing import tokens_to_usd, get_pricing

pricing = get_pricing("claude-sonnet-4-6")

baseline_cost = tokens_to_usd(baseline_tokens, pricing, token_type="input")
optimized_cost = tokens_to_usd(optimized_tokens, pricing, token_type="input")
saved_usd = baseline_cost - optimized_cost

print(f"Saved ${saved_usd:.4f} on this run")
```

Or use the high-level helper:

```python
from contextbudget.telemetry.pricing import compute_run_costs

costs = compute_run_costs(
    model="claude-sonnet-4-6",
    baseline_tokens=850000,
    optimized_tokens=310000,
)
print(costs)
# {"baseline_cost_usd": 2.55, "optimized_cost_usd": 0.93, "saved_usd": 1.62}
```

---

## Notes on data freshness

- The `events` table is populated in real time as agents push events via `POST /events`.
- There is no aggregation lag — analytics queries run directly on the `events` table.
- The `baseline_full_context_tokens` field is populated only when the runtime computes a full-context baseline estimate. It may be `null` for some events; those rows contribute `0` to the `baseline_tokens` aggregate.
