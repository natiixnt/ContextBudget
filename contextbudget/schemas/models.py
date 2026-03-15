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
    workspace: str = ""
    scanned_repos: list[dict] = field(default_factory=list)
    selected_repos: list[str] = field(default_factory=list)
    implementations: dict[str, str] = field(default_factory=dict)


CACHE_FILE = ".contextbudget_cache.json"
SCAN_INDEX_FILE = ".contextbudget/scan-index.json"
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
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
}


def normalize_repo(repo: str | Path) -> Path:
    """Normalize repository path to absolute path."""

    return Path(repo).resolve()
