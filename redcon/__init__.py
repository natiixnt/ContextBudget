"""Redcon - deterministic context packing for LLM agents."""

from redcon.sdk import RedconSDK
from redcon.agents import (
    AgentAdapter,
    AgentAdapterRun,
    AgentMiddlewareResult,
    AgentTaskRequest,
    RedconMiddleware,
    LocalDemoAgentAdapter,
    enforce_budget,
    prepare_context,
    record_run,
)
from redcon.runtime import (
    AgentRuntime,
    PreparedContext,
    RuntimeResult,
    RuntimeSession,
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
from redcon.telemetry import (
    JsonlFileTelemetrySink,
    NoOpTelemetrySink,
    TelemetryEvent,
    TelemetrySession,
    TelemetrySink,
)

__version__ = "1.1.0"

# Explicit public API - sorted alphabetically for easy auditing.
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
