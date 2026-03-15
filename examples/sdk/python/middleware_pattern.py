"""Explicit agent → Redcon → model middleware pipeline.

Shows the full prepare → enforce → record flow in two styles:

    Option A:  module-level helper functions  (lowest ceremony)
    Option B:  RedconMiddleware class  (reusable, composable)
    Option C:  RedconSDK.middleware()  (SDK-owned engine)

The middleware layer sits between agent and model without touching the
transport, authentication, or inference code.

Run from the repository root:
    python examples/sdk/python/middleware_pattern.py
"""

from redcon import (
    AgentTaskRequest,
    BudgetPolicyViolationError,
    RedconEngine,
    RedconMiddleware,
    enforce_budget,
    prepare_context,
    record_run,
)
from redcon.sdk import RedconSDK

TASK = "refactor auth middleware token validation"
REPO = "examples/risky-auth-change/repo"

# -----------------------------------------------------------------------
# Option A — module-level helpers (quickest path)
# -----------------------------------------------------------------------
print("=== Option A: module-level helpers ===")

result = prepare_context(
    TASK,
    repo=REPO,
    max_tokens=28_000,
    metadata={"agent": "my-agent", "session": "sess-001"},
)

policy = RedconEngine.make_policy(
    max_estimated_input_tokens=28_000,
    max_quality_risk_level="medium",
)

try:
    checked = enforce_budget(result, policy=policy, strict=True)
except BudgetPolicyViolationError as err:
    print(f"  policy violation: {err}")
    checked = result  # proceed without strict enforcement in the demo

output_path = record_run(checked, "run-option-a.json")
print(f"  recorded to:    {output_path}")

# Build the prompt the agent would forward to the model
prompt = "\n\n".join(
    f"# File: {entry['path']}\n{entry['text']}"
    for entry in checked.run_artifact.get("compressed_context", [])
)
meta = checked.metadata
print(f"  tokens:         {meta['estimated_input_tokens']} used, {meta['estimated_saved_tokens']} saved")
print(f"  files included: {meta['files_included_count']}")
print(f"  prompt length:  {len(prompt)} chars")
print()

# -----------------------------------------------------------------------
# Option B — reusable RedconMiddleware instance
# -----------------------------------------------------------------------
print("=== Option B: RedconMiddleware ===")

middleware = RedconMiddleware()

request = AgentTaskRequest(
    task=TASK,
    repo=REPO,
    max_tokens=28_000,
    metadata={"agent": "my-agent"},
)

# handle() = prepare_context + enforce_budget in one call
result2 = middleware.handle(request, policy=policy, strict=False)

if result2.policy_result:
    passed = result2.policy_result.get("passed", False)
    violations = result2.policy_result.get("violations", [])
    print(f"  policy passed:  {passed}")
    if not passed:
        for v in violations:
            print(f"  violation:      {v}")

print(f"  files included: {result2.metadata['files_included_count']}")
print(f"  tokens:         {result2.metadata['estimated_input_tokens']}")
print()

# -----------------------------------------------------------------------
# Option C — SDK-owned middleware (shares engine state)
# -----------------------------------------------------------------------
print("=== Option C: RedconSDK.middleware() ===")

sdk = RedconSDK(max_tokens=28_000)
mw = sdk.middleware()

request3 = AgentTaskRequest(task=TASK, repo=REPO, max_tokens=28_000)
result3 = mw.prepare_request(request3)

print(f"  tokens:         {result3.metadata['estimated_input_tokens']}")
print(f"  quality risk:   {result3.metadata['quality_risk_estimate']}")
print(f"  delta enabled:  {result3.metadata['delta_enabled']}")

# The prompt the agent would send to the model
prompt3 = "\n\n".join(
    f"# File: {entry['path']}\n{entry['text']}"
    for entry in result3.run_artifact.get("compressed_context", [])
)
print(f"  prompt length:  {len(prompt3)} chars")
