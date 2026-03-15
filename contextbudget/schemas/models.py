from __future__ import annotations

"""Shared schema dataclasses and legacy constants."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class FileRecord:
    """Metadata for a scanned repository file."""

    path: str
    absolute_path: str
    extension: str
    size_bytes: int
    line_count: int
    content_hash: str
    content_preview: str
    relative_path: str = ""
    repo_label: str = ""
    repo_root: str = ""

    def __post_init__(self) -> None:
        if not self.relative_path:
            self.relative_path = self.path


@dataclass(slots=True)
class RankedFile:
    """File metadata paired with a relevance score and reasons."""

    file: FileRecord
    score: float
    heuristic_score: float = 0.0
    historical_score: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CompressedFile:
    """Packed output entry for a file included in context payload."""

    path: str
    strategy: str
    original_tokens: int
    compressed_tokens: int
    text: str
    chunk_strategy: str = "none"
    chunk_reason: str = ""
    selected_ranges: list[dict[str, int | str]] = field(default_factory=list)
    symbols: list[dict[str, int | str | bool]] = field(default_factory=list)
    cache_reference: str = ""
    cache_status: str = ""
    relative_path: str = ""
    repo_label: str = ""


@dataclass(slots=True)
class BudgetReport:
    """Budget metrics included in run reports."""

    max_tokens: int
    estimated_input_tokens: int
    estimated_saved_tokens: int
    duplicate_reads_prevented: int
    quality_risk_estimate: str


@dataclass(slots=True)
class CacheReport:
    """Cache backend metadata included in run reports."""

    backend: str
    enabled: bool
    hits: int
    misses: int
    writes: int
    tokens_saved: int = 0
    fragment_hits: int = 0
    fragment_misses: int = 0
    fragment_writes: int = 0
    slice_hits: int = 0
    slice_misses: int = 0
    slice_writes: int = 0


@dataclass(slots=True)
class SummarizerReport:
    """Summarizer metadata included in run reports."""

    selected_backend: str
    external_adapter: str = ""
    effective_backend: str = "unused"
    external_configured: bool = False
    external_resolved: bool = False
    fallback_used: bool = False
    fallback_count: int = 0
    summary_count: int = 0
    logs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TokenEstimatorReport:
    """Token-estimator metadata included in plan, run, and benchmark artifacts."""

    selected_backend: str
    effective_backend: str
    uncertainty: str = "approximate"
    model: str = ""
    encoding: str = ""
    available: bool = True
    fallback_used: bool = False
    fallback_reason: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ModelProfileReport:
    """Resolved model assumptions included in plan, run, and benchmark artifacts."""

    selected_profile: str = ""
    resolved_profile: str = ""
    family: str = ""
    tokenizer: str = ""
    context_window: int = 0
    recommended_compression_strategy: str = ""
    effective_max_tokens: int = 0
    reserved_output_tokens: int = 0
    budget_source: str = ""
    budget_clamped: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentPlanContextFile:
    """Context assignment entry for a workflow step or shared plan context."""

    path: str
    score: float
    estimated_tokens: int
    reasons: list[str] = field(default_factory=list)
    line_count: int = 0
    source: str = "step"
    relative_path: str = ""
    repo: str = ""
    reuse_count: int = 0
    step_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentPlanStep:
    """One planned workflow step with assigned context and token estimates."""

    id: str
    title: str
    objective: str
    planning_prompt: str
    context: list[AgentPlanContextFile] = field(default_factory=list)
    estimated_tokens: int = 0
    shared_context_tokens: int = 0
    step_context_tokens: int = 0


@dataclass(slots=True)
class AgentPlanReport:
    """Top-level workflow-planning artifact for multi-step agent runs."""

    command: str
    task: str
    repo: str
    scanned_files: int
    ranked_files: list[dict]
    steps: list[AgentPlanStep]
    shared_context: list[AgentPlanContextFile]
    total_estimated_tokens: int
    unique_context_tokens: int
    reused_context_tokens: int
    generated_at: str
    workspace: str = ""
    scanned_repos: list[dict] = field(default_factory=list)
    selected_repos: list[str] = field(default_factory=list)
    implementations: dict[str, str] = field(default_factory=dict)
    token_estimator: TokenEstimatorReport = field(
        default_factory=lambda: TokenEstimatorReport(
            selected_backend="heuristic",
            effective_backend="heuristic",
        )
    )
    model_profile: ModelProfileReport = field(default_factory=ModelProfileReport)


@dataclass(slots=True)
class PrAuditSnapshot:
    """Per-file snapshot metrics captured for PR context audits."""

    size_bytes: int = 0
    line_count: int = 0
    token_count: int = 0
    symbol_count: int = 0
    branch_count: int = 0
    import_count: int = 0
    complexity_score: float = 0.0


@dataclass(slots=True)
class PrAuditDependency:
    """Dependency introduced by a pull request."""

    name: str
    source: str
    file: str = ""


@dataclass(slots=True)
class PrAuditFile:
    """Per-file change analysis for PR context audits."""

    path: str
    change_type: str
    previous_path: str = ""
    analyzed: bool = True
    binary: bool = False
    skipped_reason: str = ""
    before: PrAuditSnapshot = field(default_factory=PrAuditSnapshot)
    after: PrAuditSnapshot = field(default_factory=PrAuditSnapshot)
    token_delta: int = 0
    size_delta: int = 0
    line_delta: int = 0
    complexity_delta: float = 0.0
    new_dependencies: list[str] = field(default_factory=list)
    removed_dependencies: list[str] = field(default_factory=list)
    growth_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PrAuditSummary:
    """Summary metrics for a pull-request context audit."""

    changed_files: int
    analyzed_files: int
    skipped_files: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    estimated_token_delta: int
    estimated_token_delta_pct: float
    larger_file_count: int
    new_dependency_count: int
    increased_complexity_count: int


@dataclass(slots=True)
class PrAuditReport:
    """Top-level PR context audit artifact."""

    command: str
    repo: str
    base_ref: str
    head_ref: str
    base_commit: str
    head_commit: str
    merge_base: str
    generated_at: str
    token_estimator: TokenEstimatorReport
    summary: PrAuditSummary
    files: list[PrAuditFile]
    files_causing_increase: list[str]
    larger_files: list[str]
    new_dependencies: list[PrAuditDependency]
    increased_complexity: list[str]
    suggestions: list[str]
    comment_markdown: str


@dataclass(slots=True)
class RunReport:
    """Top-level run report persisted to ``run.json``."""

    command: str
    task: str
    repo: str
    max_tokens: int
    ranked_files: list[dict]
    compressed_context: list[dict]
    files_included: list[str]
    files_skipped: list[str]
    budget: dict
    cache: CacheReport
    summarizer: SummarizerReport
    token_estimator: TokenEstimatorReport
    cache_hits: int
    generated_at: str
    model_profile: ModelProfileReport = field(default_factory=ModelProfileReport)
    workspace: str = ""
    scanned_repos: list[dict] = field(default_factory=list)
    selected_repos: list[str] = field(default_factory=list)
    implementations: dict[str, str] = field(default_factory=dict)
    delta: dict = field(default_factory=dict)


CACHE_FILE = ".contextbudget_cache.json"
SCAN_INDEX_FILE = ".contextbudget/scan-index.json"
RUN_HISTORY_FILE = ".contextbudget/history.json"
DEFAULT_MAX_TOKENS = 30_000
DEFAULT_TOP_FILES = 25
BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".so",
    ".dll",
    ".exe",
    ".class",
}
DEFAULT_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".contextbudget",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
}


def normalize_repo(repo: str | Path) -> Path:
    """Normalize repository path to absolute path."""

    return Path(repo).resolve()
