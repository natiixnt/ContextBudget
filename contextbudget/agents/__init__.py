"""Agent middleware and adapter interfaces."""

from contextbudget.agents.adapters import AgentAdapter, AgentAdapterRun, LocalDemoAgentAdapter
from contextbudget.agents.middleware import (
    AgentMiddlewareResult,
    AgentTaskRequest,
    ContextBudgetMiddleware,
    enforce_budget,
    prepare_context,
    record_run,
)

__all__ = [
    "AgentAdapter",
    "AgentAdapterRun",
    "AgentMiddlewareResult",
    "AgentTaskRequest",
    "ContextBudgetMiddleware",
    "LocalDemoAgentAdapter",
    "enforce_budget",
    "prepare_context",
    "record_run",
]
