"""Shared schema dataclasses and legacy constants."""

from __future__ import annotations

import json
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
    symbol_names: str = ""
    # JSON list of raw import specifiers extracted at scan time (empty string =
    # not extracted, so the import graph falls back to reading the file).
    import_specs: str = ""
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
    score_breakdown: dict[str, float] = field(default_factory=dict)


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
    ranked_files: list[dict[str, str | float | int]]
    steps: list[AgentPlanStep]
    shared_context: list[AgentPlanContextFile]
    total_estimated_tokens: int
    unique_context_tokens: int
    reused_context_tokens: int
    generated_at: str
    workspace: str = ""
    scanned_repos: list[dict[str, str | int]] = field(default_factory=list)
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
    """Top-level run report persisted to ``run.json``.

    Captures every metric and artifact produced by a single redcon context
    assembly run, including ranked/compressed file lists, budget accounting,
    cache statistics, and optional delta comparisons.
    """

    command: str
    task: str
    repo: str
    max_tokens: int
    ranked_files: list[dict[str, str | float | int]]
    compressed_context: list[dict[str, str | int]]
    files_included: list[str]
    files_skipped: list[str]
    budget: dict[str, int | str]
    cache: CacheReport
    summarizer: SummarizerReport
    token_estimator: TokenEstimatorReport
    cache_hits: int
    generated_at: str
    model_profile: ModelProfileReport = field(default_factory=ModelProfileReport)
    workspace: str = ""
    scanned_repos: list[dict[str, str | int]] = field(default_factory=list)
    selected_repos: list[str] = field(default_factory=list)
    implementations: dict[str, str] = field(default_factory=dict)
    delta: dict[str, str | int | float] = field(default_factory=dict)
    degraded_files: list[str] = field(default_factory=list)
    degradation_savings: int = 0
    # Scan-level facts for this run. Populated with file_count_capped /
    # file_count_limit / files_seen when the walk hit the file cap, so a
    # truncated monorepo scan is visible in run.json instead of silent.
    scan: dict[str, int | bool] = field(default_factory=dict)
    # Selection savings. context_baseline_tokens is what the whole scanned file
    # universe would cost if dumped into context (char/4 heuristic);
    # files_scanned is how many files that universe held. The pack sends only a
    # ranked subset, so the honest saving is this baseline minus the tokens
    # actually sent - reported so a run that does no in-file compression
    # (estimated_saved_tokens == 0) still shows the value redcon delivered by
    # picking the right files.
    context_baseline_tokens: int = 0
    files_scanned: int = 0
    # Dollar translation of the selection saving, for the "if you pay per token"
    # framing (redcon.telemetry.pricing.compute_run_costs). Empty when there is
    # no baseline to compare against. On a flat plan the value delivered is
    # runway (see context_baseline_tokens); this is that same saving priced out
    # at a model's input rate, labeled with the model assumed. Read by the
    # editor nudge and the savings dashboard.
    cost: dict[str, str | int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")

    def __repr__(self) -> str:
        used = self.budget.get("estimated_input_tokens", "?")
        return f"RunReport(task={self.task!r}, tokens={used}/{self.max_tokens})"

    def to_summary(self) -> str:
        """Return a single-line human-readable summary of this run."""
        included = len(self.files_included)
        saved = self.budget.get("estimated_saved_tokens", 0)
        used = self.budget.get("estimated_input_tokens", 0)
        return f"[{self.repo}] {self.task!r} - {included} files, {used} tokens used, {saved} saved"

    @property
    def compression_ratio(self) -> float:
        """Percentage of tokens saved relative to total before compression.

        Returns 0.0 when the total is zero or when saved-token data is
        unavailable.
        """
        saved = self.budget.get("estimated_saved_tokens", 0)
        used = self.budget.get("estimated_input_tokens", 0)
        if not isinstance(saved, (int, float)) or not isinstance(used, (int, float)):
            return 0.0
        total = used + saved
        if total == 0:
            return 0.0
        return (saved / total) * 100.0

    @property
    def is_over_budget(self) -> bool:
        """True when estimated input tokens exceed the configured max."""
        used = self.budget.get("estimated_input_tokens", 0)
        if not isinstance(used, (int, float)):
            return False
        return used > self.max_tokens

    @classmethod
    def from_json(cls, raw: str) -> RunReport:
        """Deserialize a RunReport from a JSON string.

        Validates that required top-level keys are present and reconstructs
        nested report dataclasses.

        Raises ``ValueError`` on missing keys or malformed data, and
        ``json.JSONDecodeError`` on invalid JSON.
        """
        data = json.loads(raw)
        required_keys = {
            "command",
            "task",
            "repo",
            "max_tokens",
            "ranked_files",
            "compressed_context",
            "files_included",
            "files_skipped",
            "budget",
            "cache",
            "summarizer",
            "token_estimator",
            "cache_hits",
            "generated_at",
        }
        missing = required_keys - set(data)
        if missing:
            raise ValueError(f"Missing required keys: {sorted(missing)}")

        data["cache"] = CacheReport(**data["cache"])
        data["summarizer"] = SummarizerReport(**data["summarizer"])
        data["token_estimator"] = TokenEstimatorReport(**data["token_estimator"])
        if "model_profile" in data:
            data["model_profile"] = ModelProfileReport(**data["model_profile"])
        return cls(**data)


CACHE_FILE = ".redcon_cache.json"
SCAN_INDEX_FILE = ".redcon/scan-index.json"
RUN_HISTORY_FILE = ".redcon/history.json"
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
    ".redcon",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
}

# Files that commonly hold credentials. These are excluded from the scan
# universe by default so a pack can never send secrets to an LLM (or write
# them into run.json / .redcon/runs/). The worst case is task-correlated:
# packing "fix the database connection" would otherwise surface the .env whose
# DATABASE_URL matches the task. Users can still force-include a path via
# explicit include_globs. Kept as glob patterns matched against the POSIX
# relative path, so both "*.pem" and nested ".aws/credentials" work.
DEFAULT_SECRET_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    "env.*.local",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "*.keystore",
    "*.jks",
    "id_rsa",
    "id_rsa.*",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_ed25519.*",
    "*.ppk",
    "*.tfvars",
    "*.tfvars.json",
    "secrets.*",
    "*secrets.y*ml",
    "credentials",
    "credentials.*",
    "*credentials.json",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".htpasswd",
    ".pgpass",
    ".aws/credentials",
    ".aws/config",
    ".ssh/*",
    "gcp-*.json",
    "service-account*.json",
    "serviceaccount*.json",
)


def normalize_repo(repo: str | Path) -> Path:
    """Normalize repository path to absolute path."""

    return Path(repo).resolve()
