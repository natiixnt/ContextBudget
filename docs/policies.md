# Remote Policy Management

The control plane stores versioned `PolicySpec` objects. Gateways fetch the active policy at startup and enforce it server-side, so policy changes take effect without redeploying agents.

## PolicySpec fields

| Field | Type | Description |
|---|---|---|
| `max_estimated_input_tokens` | `int \| null` | Hard token budget per request |
| `max_files_included` | `int \| null` | Maximum files in packed context |
| `max_quality_risk_level` | `"low" \| "medium" \| "high" \| null` | Reject runs above this compression risk |
| `min_estimated_savings_percentage` | `float \| null` | Minimum compression ratio required (0–100) |
| `max_context_size_bytes` | `int \| null` | Maximum raw context size |

All fields are optional. An empty `spec` object `{}` creates a version that passes all checks.

## Creating a policy version

```http
POST /orgs/1/policies
Authorization: Bearer rck_...
Content-Type: application/json

{
  "version": "v2",
  "spec": {
    "max_estimated_input_tokens": 64000,
    "max_quality_risk_level": "medium"
  }
}
```

Response:

```json
{
  "id": 20,
  "org_id": 1,
  "project_id": null,
  "repo_id": null,
  "version": "v2",
  "spec": {"max_estimated_input_tokens": 64000, "max_quality_risk_level": "medium"},
  "is_active": false,
  "created_at": "...",
  "activated_at": null
}
```

Newly created versions are inactive until explicitly activated.

## Activating a policy version

```http
PUT /orgs/1/policies/20/activate
Authorization: Bearer rck_...
```

Activation is atomic: all other active versions at the same scope are deactivated before the requested version becomes active.

## Scope inheritance

Policies can be scoped at three levels. The gateway resolves the most specific active policy:

```
repo-level policy      (most specific)
  └── project-level policy
        └── org-level policy    (least specific, fallback)
```

To create a repo-scoped policy:

```json
{
  "version": "strict-v1",
  "spec": {"max_estimated_input_tokens": 32000},
  "repo_id": 100
}
```

## Listing policy versions

```http
GET /orgs/1/policies
Authorization: Bearer rck_...
```

Returns all versions (active and inactive) ordered by `created_at DESC`.

## Fetching active policy (gateway endpoint)

```http
GET /policies/active?org_id=1&repo_id=100
Authorization: Bearer rck_...
```

Returns the active `PolicyVersionResponse` for the given scope, or `null` if no active policy exists.

## Gateway integration

Set these environment variables on the runtime gateway to enable remote policy fetching:

```bash
export RC_GATEWAY_CLOUD_POLICY_URL=https://cloud.example.com
export RC_GATEWAY_CLOUD_API_KEY=rck_...
export RC_GATEWAY_CLOUD_ORG_ID=1
```

Or in Python:

```python
from redcon.gateway import GatewayConfig, GatewayServer

server = GatewayServer(GatewayConfig(
    cloud_policy_url="https://cloud.example.com",
    cloud_api_key="rck_...",
    cloud_policy_org_id=1,
))
server.start()
```

When configured, the gateway fetches the active policy from the control plane before each `prepare-context` or `run-agent-step` request. Fetch failures are logged at `WARNING` level and the gateway falls back to its locally-configured policy (if any). The fallback ensures the gateway never refuses requests due to a transient control plane outage.

## Policy in Python API

```python
from redcon.core.policy import PolicySpec
from redcon.engine import RedconEngine

policy = PolicySpec(
    max_estimated_input_tokens=64_000,
    max_quality_risk_level="medium",
)
engine = RedconEngine()
engine.make_policy(
    max_estimated_input_tokens=64_000,
    max_quality_risk_level="medium",
)
```

See [policy-and-ci.md](policy-and-ci.md) for local policy enforcement and CI integration.
