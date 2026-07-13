"""Agent middleware, adapter, and runtime interfaces."""

from typing import TYPE_CHECKING

from redcon.agents.adapters import AgentAdapter, AgentAdapterRun, LocalDemoAgentAdapter
from redcon.agents.middleware import (
    AgentMiddlewareResult,
    AgentTaskRequest,
    RedconMiddleware,
    enforce_budget,
    prepare_context,
    record_run,
)

if TYPE_CHECKING:
    from redcon.runtime import AgentRuntime, PreparedContext, RuntimeResult, RuntimeSession

# The runtime re-exports must stay lazy: redcon.runtime.runtime imports
# redcon.agents.middleware, so an eager import here closes a cycle that
# breaks `import redcon.runtime.runtime` when agents loads first.
_RUNTIME_EXPORTS = {"AgentRuntime", "PreparedContext", "RuntimeResult", "RuntimeSession"}


def __getattr__(name: str):
    if name in _RUNTIME_EXPORTS:
        import redcon.runtime as _runtime

        value = getattr(_runtime, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'redcon.agents' has no attribute {name!r}")


__all__ = [
    "AgentAdapter",
    "AgentAdapterRun",
    "AgentMiddlewareResult",
    "AgentRuntime",
    "AgentTaskRequest",
    "RedconMiddleware",
    "LocalDemoAgentAdapter",
    "PreparedContext",
    "RuntimeResult",
    "RuntimeSession",
    "enforce_budget",
    "prepare_context",
    "record_run",
]
