"""Redcon - deterministic context packing for LLM agents.

Top-level public API uses PEP 562 lazy loading. Importing this package is
~150 ms cheaper than eager loading because submodules like \`redcon.gateway\`
(which transitively pulls in asyncio) only load when something actually
references them. Submodule imports like \`from redcon.cmd import X\` no longer
pay for the full SDK chain.

Public symbol behaviour is unchanged: \`from redcon import RedconEngine\`
still works, it just defers the SDK + agents + runtime imports until the
first attribute access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "1.1.0"

# Mapping from public symbol name to the submodule it lives in. The first
# attribute access triggers an import of that module and caches the symbol
# back on this module so subsequent accesses are free.
_LAZY_IMPORTS: dict[str, str] = {
    # SDK and engine
    "BudgetGuard": "redcon.engine",
    "BudgetPolicyViolationError": "redcon.engine",
    "RedconEngine": "redcon.engine",
    "RedconSDK": "redcon.sdk",
    # agent layer
    "AgentAdapter": "redcon.agents",
    "AgentAdapterRun": "redcon.agents",
    "AgentMiddlewareResult": "redcon.agents",
    "AgentRuntime": "redcon.runtime",
    "AgentTaskRequest": "redcon.agents",
    "LocalDemoAgentAdapter": "redcon.agents",
    "PreparedContext": "redcon.runtime",
    "RedconMiddleware": "redcon.agents",
    "RuntimeResult": "redcon.runtime",
    "RuntimeSession": "redcon.runtime",
    # compressors (file-context summarisers)
    "DeterministicSummaryAdapter": "redcon.compressors",
    "ExternalSummaryAdapter": "redcon.compressors",
    "SummaryAdapter": "redcon.compressors",
    # telemetry
    "JsonlFileTelemetrySink": "redcon.telemetry",
    "NoOpTelemetrySink": "redcon.telemetry",
    "TelemetryEvent": "redcon.telemetry",
    "TelemetrySession": "redcon.telemetry",
    "TelemetrySink": "redcon.telemetry",
    # public functions
    "enforce_budget": "redcon.agents",
    "get_external_summarizer_adapter": "redcon.compressors",
    "prepare_context": "redcon.agents",
    "record_run": "redcon.agents",
    "register_external_summarizer_adapter": "redcon.compressors",
    "unregister_external_summarizer_adapter": "redcon.compressors",
}


def __getattr__(name: str):
    """PEP 562 lazy attribute lookup. Imports the submodule on first access."""
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'redcon' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    # Cache on this module so subsequent accesses bypass __getattr__.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_IMPORTS))


# Static type checkers don't run __getattr__ so they need explicit imports
# to see the public API. These imports are skipped at runtime - tools like
# mypy / pyright walk this branch but the interpreter doesn't.
if TYPE_CHECKING:
    from redcon.agents import (
        AgentAdapter,
        AgentAdapterRun,
        AgentMiddlewareResult,
        AgentTaskRequest,
        LocalDemoAgentAdapter,
        RedconMiddleware,
        enforce_budget,
        prepare_context,
        record_run,
    )
    from redcon.compressors import (
        DeterministicSummaryAdapter,
        ExternalSummaryAdapter,
        SummaryAdapter,
        get_external_summarizer_adapter,
        register_external_summarizer_adapter,
        unregister_external_summarizer_adapter,
    )
    from redcon.engine import BudgetGuard, BudgetPolicyViolationError, RedconEngine
    from redcon.runtime import (
        AgentRuntime,
        PreparedContext,
        RuntimeResult,
        RuntimeSession,
    )
    from redcon.sdk import RedconSDK
    from redcon.telemetry import (
        JsonlFileTelemetrySink,
        NoOpTelemetrySink,
        TelemetryEvent,
        TelemetrySession,
        TelemetrySink,
    )


__all__ = [
    # metadata
    "__version__",
    # SDK and engine
    "BudgetGuard",
    "BudgetPolicyViolationError",
    "RedconEngine",
    "RedconSDK",
    # agent layer
    "AgentAdapter",
    "AgentAdapterRun",
    "AgentMiddlewareResult",
    "AgentRuntime",
    "AgentTaskRequest",
    "LocalDemoAgentAdapter",
    "PreparedContext",
    "RedconMiddleware",
    "RuntimeResult",
    "RuntimeSession",
    # compressors
    "DeterministicSummaryAdapter",
    "ExternalSummaryAdapter",
    "SummaryAdapter",
    # telemetry
    "JsonlFileTelemetrySink",
    "NoOpTelemetrySink",
    "TelemetryEvent",
    "TelemetrySession",
    "TelemetrySink",
    # public functions
    "enforce_budget",
    "get_external_summarizer_adapter",
    "prepare_context",
    "record_run",
    "register_external_summarizer_adapter",
    "unregister_external_summarizer_adapter",
]
