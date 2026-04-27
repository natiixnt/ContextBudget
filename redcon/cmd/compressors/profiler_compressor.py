"""
Profiler collapsed-stack output compressor (V70).

Targets py-spy / perf script collapsed-stack format:
  frame1;frame2;...;frameN <samples>

Each non-empty line is one stack, samples is a non-negative integer.
The compressor aggregates duplicates, sorts desc, emits top-K with
shared-prefix elision so adjacent stacks rooted in the same module
collapse to '... +next_frame'.

Detection: argv hits py-spy/perf, plus a literal `flamegraph.pl` form
for the rare standalone tool.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import (
    Compressor,
    CompressorContext,
    verify_must_preserve,
)
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    HotPath,
    ProfileResult,
)
from redcon.cmd._tokens_lite import estimate_tokens

_STACK_LINE = re.compile(r"^(?P<stack>\S(?:.*\S)?)\s+(?P<samples>\d+)$")
_COMPACT_TOP = 20
_ULTRA_TOP = 3
_VERBOSE_TOP = 50
_LEAF_CLIP = 80


class ProfilerCompressor:
    schema = "profiler"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        # Patterns extended at compress-time once we know the top-K leaf names.
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[0] == "py-spy":
            return len(argv) >= 2 and argv[1] in {"record", "dump", "top"}
        if argv[0] == "perf":
            return len(argv) >= 2 and argv[1] in {"record", "script", "report"}
        if argv[0] == "flamegraph.pl":
            return True
        return False

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text = raw_stdout.decode("utf-8", errors="replace")
        result = parse_profile(text)
        raw_tokens = estimate_tokens(text)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed_tokens = estimate_tokens(formatted)
        # Patterns: top-K leaf names + the sample-count head.
        top_for_preserve = _select_top(result, level)
        preserve_set: list[str] = []
        seen: set[str] = set()
        for hp in top_for_preserve:
            leaf = hp.leaf[:_LEAF_CLIP]
            if leaf and leaf not in seen:
                seen.add(leaf)
                preserve_set.append(re.escape(leaf))
            if len(preserve_set) >= 30:
                break
        patterns = tuple(preserve_set)
        preserved = verify_must_preserve(formatted, patterns, text)
        return CompressedOutput(
            text=formatted,
            level=level,
            schema=self.schema,
            original_tokens=raw_tokens,
            compressed_tokens=compressed_tokens,
            must_preserve_ok=preserved,
            truncated=False,
            notes=ctx.notes,
        )


def parse_profile(text: str) -> ProfileResult:
    """Aggregate duplicate stacks, sort desc by samples."""
    bucket: dict[tuple[str, ...], int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _STACK_LINE.match(line)
        if not match:
            continue
        stack_str = match.group("stack")
        try:
            samples = int(match.group("samples"))
        except ValueError:
            continue
        if samples <= 0:
            continue
        stack = tuple(part.strip() for part in stack_str.split(";") if part.strip())
        if not stack:
            continue
        bucket[stack] = bucket.get(stack, 0) + samples

    paths = tuple(
        HotPath(stack=stack, samples=count)
        for stack, count in sorted(
            bucket.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )
    )
    total = sum(p.samples for p in paths)
    return ProfileResult(
        paths=paths, total_samples=total, distinct_stacks=len(paths)
    )


def _select_top(result: ProfileResult, level: CompressionLevel) -> list[HotPath]:
    if level == CompressionLevel.ULTRA:
        return list(result.paths[:_ULTRA_TOP])
    if level == CompressionLevel.COMPACT:
        return list(result.paths[:_COMPACT_TOP])
    return list(result.paths[:_VERBOSE_TOP])


def _format(result: ProfileResult, level: CompressionLevel) -> str:
    if not result.paths:
        return f"profile: 0 samples, 0 stacks"

    head = (
        f"profile: {result.total_samples} samples, "
        f"{result.distinct_stacks} distinct stacks"
    )
    if level == CompressionLevel.ULTRA:
        top = result.paths[:_ULTRA_TOP]
        return head + "; " + "; ".join(
            f"{hp.samples} {_clip(hp.leaf, _LEAF_CLIP)}" for hp in top
        )

    top = _select_top(result, level)
    lines = [head]
    prev_stack: tuple[str, ...] = ()
    for hp in top:
        stack = hp.stack
        # Shared-prefix elision: when the head of this stack matches the
        # previous emitted stack, replace the shared prefix with '...'.
        common = 0
        for a, b in zip(prev_stack, stack):
            if a != b:
                break
            common += 1
        if common >= 2:
            tail = stack[common:]
            shown = "..." + ";" + ";".join(_clip(f, _LEAF_CLIP) for f in tail)
        else:
            shown = ";".join(_clip(f, _LEAF_CLIP) for f in stack)
        lines.append(f"{hp.samples}  {shown}")
        prev_stack = stack
    if len(result.paths) > len(top):
        lines.append(f"... +{len(result.paths) - len(top)} more stacks")
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
