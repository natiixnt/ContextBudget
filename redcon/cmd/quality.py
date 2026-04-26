"""
Quality validation harness for command-output compressors.

For each (compressor, raw input) pair this module runs the compressor at
all three levels and reports whether:
  - must-preserve patterns survived compression
  - reduction met the per-level floor
  - the same input produced byte-identical output twice (determinism)
  - the compressor handled empty / truncated input without crashing

Used by tests/test_cmd_quality.py and importable for ad-hoc benchmarking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from redcon.cmd.budget import BudgetHint
from redcon.cmd.compressors.base import Compressor, CompressorContext
from redcon.cmd.types import CompressedOutput, CompressionLevel

logger = logging.getLogger(__name__)


# Per-level reduction floors. A compressor must achieve at least this
# fraction of token reduction on a non-trivial input; anything weaker
# signals a regression. VERBOSE allows slight inflation for headers.
DEFAULT_THRESHOLDS: dict[CompressionLevel, float] = {
    CompressionLevel.VERBOSE: -0.10,
    CompressionLevel.COMPACT: 0.30,
    CompressionLevel.ULTRA: 0.70,
}

# Inputs smaller than this are exempt from reduction floor checks - tiny
# outputs can't be compressed meaningfully and the absolute overhead of
# the format header dominates.
MIN_RAW_TOKENS_FOR_REDUCTION_CHECK = 80


@dataclass(frozen=True, slots=True)
class LevelReport:
    level: CompressionLevel
    output: CompressedOutput
    threshold: float
    threshold_met: bool
    deterministic: bool


@dataclass(frozen=True, slots=True)
class QualityCheck:
    """Aggregated result of running a compressor through the harness."""

    schema: str
    levels: tuple[LevelReport, ...]
    robust: bool
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        if not self.robust:
            return False
        for level in self.levels:
            if not (level.threshold_met and level.deterministic):
                return False
            # ULTRA is exempt from must-preserve, see failures() for reasoning.
            if (
                level.level != CompressionLevel.ULTRA
                and not level.output.must_preserve_ok
            ):
                return False
        return True

    def failures(self) -> list[str]:
        out: list[str] = []
        if not self.robust:
            out.append(f"{self.schema}: not robust to adversarial input")
        for level in self.levels:
            if not level.threshold_met:
                out.append(
                    f"{self.schema}/{level.level.value}: "
                    f"reduction {level.output.reduction_pct:.1f}% "
                    f"below floor {level.threshold * 100:.0f}%"
                )
            # must-preserve is enforced at COMPACT and VERBOSE only.
            # ULTRA is by design a lossy summary - asking it to keep every
            # failure name or every path would defeat its purpose.
            if (
                level.level != CompressionLevel.ULTRA
                and not level.output.must_preserve_ok
            ):
                out.append(
                    f"{self.schema}/{level.level.value}: must-preserve patterns lost"
                )
            if not level.deterministic:
                out.append(f"{self.schema}/{level.level.value}: non-deterministic output")
        return out


def run_quality_check(
    compressor: Compressor,
    *,
    raw_stdout: bytes,
    raw_stderr: bytes = b"",
    argv: tuple[str, ...],
    thresholds: dict[CompressionLevel, float] | None = None,
) -> QualityCheck:
    """Run the compressor through the full harness and return a QualityCheck."""
    levels: list[LevelReport] = []
    for level in (CompressionLevel.VERBOSE, CompressionLevel.COMPACT, CompressionLevel.ULTRA):
        report = _check_level(compressor, raw_stdout, raw_stderr, argv, level, thresholds)
        levels.append(report)
    robust = _check_robustness(compressor, argv)
    return QualityCheck(
        schema=compressor.schema,
        levels=tuple(levels),
        robust=robust,
    )


def _check_level(
    compressor: Compressor,
    raw_stdout: bytes,
    raw_stderr: bytes,
    argv: tuple[str, ...],
    level: CompressionLevel,
    thresholds: dict[CompressionLevel, float] | None,
) -> LevelReport:
    hint = _force_level_hint(level)
    ctx = CompressorContext(
        argv=argv, cwd=".", returncode=0, hint=hint
    )
    first = compressor.compress(raw_stdout, raw_stderr, ctx)
    # Re-run to verify determinism. The cache in the pipeline isn't involved
    # here: we go straight through the compressor.
    second = compressor.compress(raw_stdout, raw_stderr, ctx)
    deterministic = first.text == second.text and first.level == second.level

    floor = (thresholds or DEFAULT_THRESHOLDS).get(level, 0.0)
    if first.original_tokens < MIN_RAW_TOKENS_FOR_REDUCTION_CHECK:
        threshold_met = True
    else:
        threshold_met = (first.reduction_pct / 100.0) >= floor

    return LevelReport(
        level=first.level,
        output=first,
        threshold=floor,
        threshold_met=threshold_met,
        deterministic=deterministic,
    )


def _force_level_hint(level: CompressionLevel) -> BudgetHint:
    """Build a BudgetHint that pushes select_level to the requested level."""
    if level == CompressionLevel.VERBOSE:
        return BudgetHint(remaining_tokens=10**6, max_output_tokens=10**6)
    if level == CompressionLevel.COMPACT:
        # Quality floor pulls the result up to COMPACT regardless of math.
        return BudgetHint(
            remaining_tokens=200,
            max_output_tokens=4_000,
            quality_floor=CompressionLevel.COMPACT,
        )
    return BudgetHint(remaining_tokens=10, max_output_tokens=2)


def _check_robustness(compressor: Compressor, argv: tuple[str, ...]) -> bool:
    """Run the compressor against pathological inputs; require no crash."""
    hint = _force_level_hint(CompressionLevel.COMPACT)
    ctx = CompressorContext(argv=argv, cwd=".", returncode=0, hint=hint)
    pathological = (
        b"",
        b"\x00\x01\x02 binary garbage \xff\xfe",
        b"truncated mid-stream because the buffer ran out at exactly thi",
        b"\n" * 5_000,
        b"random words " * 5_000,
    )
    for blob in pathological:
        try:
            compressor.compress(blob, b"", ctx)
        except Exception:
            logger.exception("compressor %s crashed on adversarial input", compressor.schema)
            return False
    return True
