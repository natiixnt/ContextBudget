"""
Compressor protocol.

Every compressor takes raw command output, parses it into a canonical type,
and formats that type at the chosen CompressionLevel. Each compressor declares
`must_preserve_patterns` - regexes that must match the formatted output for it
to be considered information-preserving. The quality harness in M8 verifies
this on a corpus of real outputs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol, runtime_checkable

from redcon.cmd.budget import BudgetHint
from redcon.cmd.types import CompressedOutput, CompressionLevel


@lru_cache(maxsize=2048)
def _compile_preserve(pattern: str, flags: int) -> re.Pattern[str]:
    return re.compile(pattern, flags)


@dataclass(frozen=True, slots=True)
class CompressorContext:
    """Per-call inputs that aren't part of the raw output itself."""

    argv: tuple[str, ...]
    cwd: str
    returncode: int
    hint: BudgetHint
    notes: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class Compressor(Protocol):
    """A compressor turns raw output bytes into a CompressedOutput."""

    @property
    def schema(self) -> str:
        """Canonical type name, e.g. 'git_diff'. Used for telemetry."""
        ...

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        """Regex strings that must match the compressed output."""
        ...

    def matches(self, argv: tuple[str, ...]) -> bool:
        """Return True if this compressor should handle the given argv."""
        ...

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        """Parse and compress. Returns CompressedOutput ready for the agent."""
        ...


def verify_must_preserve(
    text: str, patterns: tuple[str, ...], original: str
) -> bool:
    """
    Verify that every required pattern that appeared in the raw output also
    appears in the compressed output. Patterns that did not match the raw
    text in the first place are ignored - we only enforce preservation of
    facts that were actually present.
    """
    for pat in patterns:
        regex = _compile_preserve(pat, re.MULTILINE)
        if regex.search(original) and not regex.search(text):
            return False
    return True


def select_level_for_output(
    raw_text: str, hint: BudgetHint, token_estimator
) -> tuple[CompressionLevel, int]:
    """Helper: estimate raw token count and choose a level. Returns (level, raw_tokens)."""
    from redcon.cmd.budget import select_level

    raw_tokens = token_estimator(raw_text)
    return select_level(raw_tokens, hint), raw_tokens
