from __future__ import annotations

"""Typed plugin interfaces for scorer, compressor, and token-estimator extensions."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from redcon.cache.summary_cache import SummaryCacheBackend
from redcon.compressors.context_compressor import CompressionResult
from redcon.config import CompressionSettings, ScoreSettings, SummarizationSettings
from redcon.schemas.models import FileRecord, RankedFile, TokenEstimatorReport


class TokenEstimatorCallable(Protocol):
    """Callable token-estimator plugin interface."""

    def __call__(
        self,
        *,
        text: str,
        options: Mapping[str, Any],
    ) -> int: ...


class TokenEstimatorDescribeCallable(Protocol):
    """Optional callable for reporting token-estimator backend metadata."""

    def __call__(
        self,
        *,
        options: Mapping[str, Any],
    ) -> TokenEstimatorReport: ...


class ScorerCallable(Protocol):
    """Callable scorer plugin interface."""

    def __call__(
        self,
        *,
        task: str,
        files: list[FileRecord],
        settings: ScoreSettings,
        options: Mapping[str, Any],
        estimate_tokens: Callable[[str], int],
    ) -> list[RankedFile]: ...


class CompressorCallable(Protocol):
    """Callable compressor plugin interface."""

    def __call__(
        self,
        *,
        task: str,
        repo: Path,
        ranked_files: list[RankedFile],
        max_tokens: int,
        cache: SummaryCacheBackend,
        settings: CompressionSettings,
        summarization_settings: SummarizationSettings,
        options: Mapping[str, Any],
        estimate_tokens: Callable[[str], int],
        duplicate_hash_cache_enabled: bool,
    ) -> CompressionResult: ...


@dataclass(frozen=True, slots=True)
class TokenEstimatorPlugin:
    """Registered token-estimator plugin definition."""

    name: str
    estimate: TokenEstimatorCallable
    description: str = ""
    describe: TokenEstimatorDescribeCallable | None = None


@dataclass(frozen=True, slots=True)
class ScorerPlugin:
    """Registered scorer plugin definition."""

    name: str
    score: ScorerCallable
    description: str = ""


@dataclass(frozen=True, slots=True)
class CompressorPlugin:
    """Registered compressor plugin definition."""

    name: str
    compress: CompressorCallable
    description: str = ""
