"""Real agent integration layers for ContextBudget.

Provides first-party wrappers for OpenAI and Anthropic, plus a generic runner
that accepts any callable LLM backend.

Quick-start
-----------
::

    from contextbudget.integrations import OpenAIAgentWrapper

    agent = OpenAIAgentWrapper(model="gpt-4.1", repo=".")
    result = agent.run_task("add caching to API")
    print(result.llm_response)

Available wrappers
------------------
- :class:`OpenAIAgentWrapper`  — OpenAI Chat Completions API
- :class:`AnthropicAgentWrapper` — Anthropic Messages API
- :class:`GenericAgentRunner` — any ``(prompt: str) -> str`` callable
"""

from contextbudget.integrations.anthropic_wrapper import AnthropicAgentWrapper
from contextbudget.integrations.generic_runner import GenericAgentRunner
from contextbudget.integrations.openai_wrapper import OpenAIAgentWrapper

__all__ = [
    "AnthropicAgentWrapper",
    "GenericAgentRunner",
    "OpenAIAgentWrapper",
]
