"""ContextBudget package."""

from contextbudget.agents import (
    AgentAdapter,
    AgentAdapterRun,
    AgentMiddlewareResult,
    AgentTaskRequest,
    ContextBudgetMiddleware,
    LocalDemoAgentAdapter,
    enforce_budget,
    prepare_context,
    record_run,
)
from contextbudget.compressors import (
    DeterministicSummaryAdapter,
    ExternalSummaryAdapter,
    SummaryAdapter,
    get_external_summarizer_adapter,
    register_external_summarizer_adapter,
    unregister_external_summarizer_adapter,
)
from contextbudget.engine import BudgetGuard, BudgetPolicyViolationError, ContextBudgetEngine
from contextbudget.telemetry import (
    JsonlFileTelemetrySink,
    NoOpTelemetrySink,
    TelemetryEvent,
    TelemetrySession,
    TelemetrySink,
)

__all__ = [
    "__version__",
    "AgentAdapter",
    "AgentAdapterRun",
    "AgentMiddlewareResult",
    "AgentTaskRequest",
    "BudgetGuard",
    "BudgetPolicyViolationError",
    "ContextBudgetMiddleware",
    "ContextBudgetEngine",
    "DeterministicSummaryAdapter",
    "ExternalSummaryAdapter",
    "JsonlFileTelemetrySink",
    "LocalDemoAgentAdapter",
    "NoOpTelemetrySink",
    "SummaryAdapter",
    "TelemetryEvent",
    "TelemetrySession",
    "TelemetrySink",
    "enforce_budget",
    "get_external_summarizer_adapter",
    "prepare_context",
    "register_external_summarizer_adapter",
    "record_run",
    "unregister_external_summarizer_adapter",
]
__version__ = "0.2.0"
