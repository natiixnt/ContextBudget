"""Anthropic agent integration with Redcon middleware.

Architecture:
    agent task
        → Redcon (scan → rank → compress → cache → delta)
        → optimised prompt
        → Claude API
        → response

Redcon sits transparently between the agent loop and the model.
On every turn it intercepts the task, builds the smallest possible context
that fits under the token budget, and forwards the compressed prompt to Claude.
Delta mode ensures subsequent turns only resend files that changed.

Prerequisites:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

Run from the repository root:
    python examples/sdk/python/anthropic_agent.py
"""

import os

import anthropic

from redcon.runtime import AgentRuntime

# -----------------------------------------------------------------------
# LLM callable — receives the optimised prompt, returns Claude's response
# -----------------------------------------------------------------------

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


def call_claude(prompt: str) -> str:
    """Send the packed context prompt to Claude and return the response text."""
    message = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# -----------------------------------------------------------------------
# Wire Redcon into the agent loop via AgentRuntime
#
#   agent → AgentRuntime (Redcon middleware) → Claude
# -----------------------------------------------------------------------

runtime = AgentRuntime(
    max_tokens=32_000,
    llm_fn=call_claude,   # Redcon assembles the prompt, Claude handles inference
    delta=True,            # Only resend changed files on turns 2+
)

REPO = "examples/small-feature/repo"

# -----------------------------------------------------------------------
# Turn 1 — first agent task
# -----------------------------------------------------------------------
result1 = runtime.run("add Redis caching to the session store", repo=REPO)
ctx1 = result1.prepared_context

print(f"[turn {result1.turn_number}] task: {ctx1.task}")
print(f"  tokens:      {ctx1.estimated_tokens} used, {ctx1.tokens_saved} saved")
print(f"  files:       {', '.join(ctx1.files_included)}")
print(f"  delta:       {ctx1.delta_enabled}")
print(f"  quality:     {ctx1.quality_risk}")
print(f"  response:    {result1.llm_response[:120]}...")
print(f"  session:     {result1.session_tokens} cumulative tokens")
print()

# -----------------------------------------------------------------------
# Turn 2 — follow-up task in the same session
#          Delta mode sends only the files that changed since turn 1.
# -----------------------------------------------------------------------
result2 = runtime.run("add unit tests for the Redis cache layer", repo=REPO)
ctx2 = result2.prepared_context

print(f"[turn {result2.turn_number}] task: {ctx2.task}")
print(f"  tokens:      {ctx2.estimated_tokens} used, {ctx2.tokens_saved} saved")
print(f"  delta:       {ctx2.delta_enabled}")
print(f"  response:    {result2.llm_response[:120]}...")
print(f"  session:     {result2.session_tokens} cumulative tokens")
print()

# Session summary
summary = runtime.session_summary()
print("=== session summary ===")
print(f"  turns:           {summary['turn_number']}")
print(f"  total tokens:    {summary['cumulative_tokens']}")
print(f"  total saved:     {summary['cumulative_tokens_saved']}")
