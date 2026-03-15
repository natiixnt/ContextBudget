"""Basic Redcon SDK usage.

Demonstrates all three primary SDK entry points:

    prepareContext  — pack repository context under a token budget
    simulateAgent   — estimate token use and API cost before packing
    profileRun      — pack and return compression profiling metrics

Run from the repository root:
    python examples/sdk/python/basic.py
"""

from redcon.sdk import RedconSDK

TASK = "add Redis caching to the session store"
REPO = "examples/small-feature/repo"

sdk = RedconSDK(max_tokens=30_000)

# -----------------------------------------------------------------------
# 1. simulate_agent — check token and cost estimates before packing
# -----------------------------------------------------------------------
plan = sdk.simulate_agent(TASK, repo=REPO, model="claude-sonnet-4-6")

print("=== simulate_agent ===")
print(f"Model:          {plan.get('model', '?')}")
print(f"Total tokens:   {plan.get('total_tokens', '?')}")
cost = plan.get("cost_estimate", {})
print(f"Estimated cost: ${cost.get('total_cost_usd', 0):.4f}")
print()
for step in plan.get("steps", []):
    print(f"  {step['id']:14}  {step['step_total_tokens']:6} tokens")
print()

# -----------------------------------------------------------------------
# 2. prepare_context — pack context and get the middleware result
# -----------------------------------------------------------------------
result = sdk.prepare_context(TASK, repo=REPO)

print("=== prepare_context ===")
meta = result.metadata
print(f"Tokens used:    {meta['estimated_input_tokens']}")
print(f"Tokens saved:   {meta['estimated_saved_tokens']}")
print(f"Files included: {meta['files_included_count']}")
print(f"Quality risk:   {meta['quality_risk_estimate']}")
print(f"Cache hits:     {meta['cache']['hits']}")
print()

# Build a prompt string from the compressed context entries
prompt_parts = [
    f"# File: {entry['path']}\n{entry['text']}"
    for entry in result.run_artifact.get("compressed_context", [])
]
prompt = "\n\n".join(prompt_parts)
print(f"Prompt length:  {len(prompt)} chars")
print()

# -----------------------------------------------------------------------
# 3. profile_run — pack and return timing + compression metrics
# -----------------------------------------------------------------------
prof = sdk.profile_run(TASK, repo=REPO)

print("=== profile_run ===")
p = prof["profile"]
print(f"Elapsed:            {p['elapsed_ms']} ms")
print(f"Compression ratio:  {p['compression_ratio']:.1%}")
print(f"Files included:     {p['files_included_count']}")
print(f"Files skipped:      {p['files_skipped_count']}")
print(f"Quality risk:       {p['quality_risk_estimate']}")
