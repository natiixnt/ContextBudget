# API Keys

API keys authenticate requests to the ContextBudget Cloud control plane.

## Format

```
cbk_<64 hex chars>
```

Example: `cbk_a3f4b2c1d0e9f8...` (68 characters total)

Keys use `cbk_` as a scannable prefix so they can be detected in secret scanning tools (GitHub, GitLab, pre-commit hooks, etc.).

## Issuance

```http
POST /orgs/{org_id}/api-keys
Content-Type: application/json

{"label": "ci-pipeline"}
```

Response (returned **once** — store it immediately):

```json
{
  "id": 5,
  "org_id": 1,
  "key_prefix": "cbk_a3f4b2",
  "label": "ci-pipeline",
  "raw_key": "cbk_a3f4b2c1d0e9f8...",
  "created_at": "2026-03-15T12:00:00Z"
}
```

**The `raw_key` is not stored and cannot be recovered.** If you lose it, revoke and reissue.

## Storage

Only a SHA-256 hash of the raw key is stored in the database. The hash cannot be reversed.

```
stored:  SHA-256("cbk_a3f4b2c1d0e9f8...") → "8f3ab12..."
```

## Using a key

Include the key as a Bearer token in all authenticated requests:

```bash
curl -H "Authorization: Bearer cbk_a3f4b2c1d0e9f8..." \
     https://cloud.example.com/orgs/1/projects
```

Python:

```python
import urllib.request, json

req = urllib.request.Request(
    "https://cloud.example.com/analytics/cost",
    headers={"Authorization": "Bearer cbk_..."},
)
with urllib.request.urlopen(req) as resp:
    print(json.loads(resp.read()))
```

## Listing keys

```http
GET /orgs/{org_id}/api-keys
Authorization: Bearer cbk_...
```

Response lists active and revoked keys. The `raw_key` field is never returned in list responses — only `key_prefix` (the first 12 characters) is shown for identification.

```json
[
  {
    "id": 5,
    "org_id": 1,
    "key_prefix": "cbk_a3f4b2",
    "label": "ci-pipeline",
    "revoked": false,
    "created_at": "2026-03-15T12:00:00Z",
    "revoked_at": null
  }
]
```

## Revoking a key

```http
DELETE /orgs/{org_id}/api-keys/{key_id}
Authorization: Bearer cbk_...
```

Returns `204 No Content` on success. The row is marked `revoked = true` and `revoked_at` is set. Revoked keys are rejected immediately on the next request — no propagation delay.

## Rotation

Rotate keys by issuing a new key before revoking the old one:

```bash
# 1. Issue new key
NEW_KEY=$(curl -s -X POST .../orgs/1/api-keys -d '{"label": "ci-v2"}' | jq -r .raw_key)
# 2. Update your secret store / CI environment
# 3. Revoke old key
curl -X DELETE .../orgs/1/api-keys/5 -H "Authorization: Bearer $NEW_KEY"
```

## Unauthenticated endpoints

The following endpoints do not require an API key:

- `GET /health`
- `POST /events`
- `POST /orgs` (operator bootstrap)
- `POST /orgs/{id}/api-keys` (operator bootstrap)

In production, restrict these paths to internal networks using a reverse proxy or firewall rule.
