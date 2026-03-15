# Audit Logs

The audit log is an append-only record of significant events within an org: policy creation, policy activation, and (optionally) gateway request outcomes.

## Querying

```http
GET /orgs/{org_id}/audit-log?limit=100&offset=0
Authorization: Bearer cbk_...
```

Response:

```json
{
  "entries": [
    {
      "id": 42,
      "org_id": 1,
      "repository_id": "a3f4b2...",
      "run_id": "run-abc123",
      "task_hash": "sha256:f1e2...",
      "endpoint": "POST /orgs/{org_id}/policies",
      "policy_version": "v2",
      "tokens_used": null,
      "tokens_saved": null,
      "violation_count": 0,
      "policy_passed": null,
      "status_code": 201,
      "created_at": "2026-03-15T12:00:00Z"
    }
  ],
  "total": 1
}
```

## Entry fields

| Field | Description |
|---|---|
| `org_id` | Org that owns this entry |
| `repository_id` | SHA-256 digest of repo path (never raw path) |
| `run_id` | Run identifier from the gateway or runtime |
| `task_hash` | SHA-256 digest of task text (never plaintext) |
| `endpoint` | API path that triggered this entry |
| `policy_version` | Active policy version at time of event |
| `tokens_used` | Estimated input tokens |
| `tokens_saved` | Tokens eliminated by compression |
| `violation_count` | Number of policy violations detected |
| `policy_passed` | `true` if all policy checks passed |
| `status_code` | HTTP status code of the triggering request |
| `created_at` | UTC timestamp |

## When entries are written

Entries are appended automatically for:

| Event | Triggered by |
|---|---|
| Policy version created | `POST /orgs/{org_id}/policies` |
| Policy version activated | `PUT /orgs/{org_id}/policies/{id}/activate` |

To record gateway activity (token usage, policy enforcement results per request), call `cp_store.append_audit_entry` from your application code after each gateway call completes.

## Privacy

- `repository_id` and `task_hash` store SHA-256 digests. Raw paths and task text are never stored.
- Audit entries are immutable — there are no update or delete endpoints.

## Pagination

Use `limit` and `offset` query parameters. Results are ordered `created_at DESC` (newest first).

```http
GET /orgs/1/audit-log?limit=25&offset=50
```

## Programmatic access

```python
from app import cp_store

entries = await cp_store.list_audit_log(pool, org_id=1, limit=50, offset=0)
for e in entries:
    print(e["endpoint"], e["policy_version"], e["created_at"])
```

## Manual appending

```python
await cp_store.append_audit_entry(
    pool,
    org_id=1,
    repository_id="sha256:a3f4b2...",
    run_id="run-abc123",
    endpoint="/prepare-context",
    tokens_used=4200,
    tokens_saved=8800,
    violation_count=0,
    policy_passed=True,
    status_code=200,
)
```
