"""
Docker / Podman output compressor.

Handles ``docker ps``, ``docker build`` (legacy and BuildKit), and
``docker image ls`` output. Build output is dominated by step lines plus
verbose layer/cache details that the agent rarely needs once the build
either succeeds or fails - so we keep the step list compact, capture
the final image id / tags, surface errors and warnings, and drop the
rest.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    ContainerInfo,
    ContainerListResult,
    ImageBuildResult,
    ImageBuildStep,
)
from redcon.cmd._tokens_lite import estimate_tokens


_PS_HEADER_RE = re.compile(r"^CONTAINER ID\s+IMAGE")
# Legacy build markers
_LEGACY_STEP_RE = re.compile(r"^Step (?P<num>\d+)/\d+\s*:\s*(?P<inst>.*)$")
_LEGACY_CACHED_RE = re.compile(r"---> Using cache")
_LEGACY_SUCCESS_RE = re.compile(
    r"Successfully built\s+(?P<id>[0-9a-f]{6,64})"
)
_LEGACY_TAGGED_RE = re.compile(r"Successfully tagged\s+(?P<tag>\S+)")
# BuildKit markers
_BK_STEP_RE = re.compile(
    r"^#?\s*(?:=>\s+)?(?:CACHED\s+)?\[?(?P<idx>\d+/\d+)?\]?\s*"
    r"(?P<inst>.*?)\s+(?P<dur>\d+\.\d+)s\s*$"
)
_BK_FINISHED_RE = re.compile(
    r"\[\+\]\s+Building\s+(?P<dur>\d+\.\d+)s\s+\(\d+/\d+\)\s+FINISHED"
)
_BK_FAILED_RE = re.compile(r"\[\+\]\s+Building.*\bFAILED\b", re.IGNORECASE)
_BK_IMAGE_ID_RE = re.compile(r"writing image\s+sha256:(?P<id>[0-9a-f]{12,64})")
_BK_IMAGE_TAG_RE = re.compile(r"naming to\s+(?P<tag>\S+)")
_ERROR_RE = re.compile(r"^(?:ERROR|error):\s*(?P<msg>.+)$", re.IGNORECASE)
_WARN_RE = re.compile(r"^(?:WARNING|warn):\s*(?P<msg>.+)$", re.IGNORECASE)


class DockerCompressor:
    """Top-level docker compressor; dispatches to ps / build by argv."""

    schema = "docker"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if len(argv) < 2:
            return False
        return argv[0] in {"docker", "podman"} and argv[1] in {"ps", "build", "image"}

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text_out = raw_stdout.decode("utf-8", errors="replace")
        text_err = raw_stderr.decode("utf-8", errors="replace")

        if len(ctx.argv) >= 2 and ctx.argv[1] == "ps":
            return _compress_ps(text_out, ctx)
        if len(ctx.argv) >= 2 and ctx.argv[1] == "build":
            return _compress_build(text_out, text_err, ctx)
        if len(ctx.argv) >= 3 and ctx.argv[1] == "image" and ctx.argv[2] == "ls":
            return _compress_ps(text_out, ctx, image_ls=True)
        # Default: passthrough through compact format.
        return _compress_build(text_out, text_err, ctx)


# --- docker ps ---


def parse_ps(text: str) -> ContainerListResult:
    containers: list[ContainerInfo] = []
    running = 0
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ContainerListResult(containers=(), running_count=0)
    if not _PS_HEADER_RE.match(lines[0]):
        return ContainerListResult(containers=(), running_count=0)
    # Determine column boundaries from the header positions.
    header = lines[0]
    columns = _column_offsets(header)
    for raw in lines[1:]:
        cells = _split_by_offsets(raw, columns)
        if len(cells) < 6:
            continue
        cid = cells[0]
        image = cells[1]
        # ports column may be empty; names is last
        status = cells[4] if len(cells) > 4 else ""
        ports = tuple(p.strip() for p in cells[5].split(",")) if len(cells) > 5 and cells[5] else ()
        name = cells[-1]
        if status.startswith("Up"):
            running += 1
        containers.append(
            ContainerInfo(
                container_id=cid[:12],
                image=image,
                status=status,
                name=name,
                ports=tuple(p for p in ports if p),
                age=cells[3] if len(cells) > 3 else None,
            )
        )
    return ContainerListResult(
        containers=tuple(containers), running_count=running
    )


def _column_offsets(header: str) -> list[int]:
    """Return the start offset of each column in a fixed-width header.

    Columns are separated by **2+ spaces**. Single spaces inside a column
    name (``CONTAINER ID``) keep it as one column. This matches both
    docker's and kubectl's default table formatting.
    """
    offsets: list[int] = []
    i = 0
    n = len(header)
    while i < n:
        if header[i] != " ":
            offsets.append(i)
            # Walk until we hit two or more consecutive spaces (or EOL).
            while i < n:
                if header[i] == " " and i + 1 < n and header[i + 1] == " ":
                    break
                i += 1
            while i < n and header[i] == " ":
                i += 1
        else:
            i += 1
    return offsets


def _split_by_offsets(line: str, offsets: list[int]) -> list[str]:
    parts: list[str] = []
    for idx, start in enumerate(offsets):
        end = offsets[idx + 1] if idx + 1 < len(offsets) else len(line)
        parts.append(line[start:end].strip())
    return parts


def _compress_ps(
    text: str,
    ctx: CompressorContext,
    image_ls: bool = False,
) -> CompressedOutput:
    result = parse_ps(text)
    raw_tokens = estimate_tokens(text)
    level = select_level(raw_tokens, ctx.hint)
    formatted = _format_ps(result, level, image_ls=image_ls)
    compressed = estimate_tokens(formatted)
    names = tuple(re.escape(c.name) for c in result.containers)
    preserved = verify_must_preserve(formatted, names, text)
    return CompressedOutput(
        text=formatted,
        level=level,
        schema="docker_ps" if not image_ls else "docker_image_ls",
        original_tokens=raw_tokens,
        compressed_tokens=compressed,
        must_preserve_ok=preserved,
        truncated=False,
        notes=ctx.notes,
    )


def _format_ps(
    result: ContainerListResult, level: CompressionLevel, image_ls: bool = False
) -> str:
    head = f"docker ps: {len(result.containers)} containers, {result.running_count} running"
    if image_ls:
        head = f"docker image: {len(result.containers)} entries"
    if not result.containers:
        return head + " (none)"
    if level == CompressionLevel.ULTRA:
        return head
    lines = [head]
    limit = len(result.containers) if level == CompressionLevel.VERBOSE else 30
    for c in result.containers[:limit]:
        ports = (",".join(c.ports)) if c.ports else "-"
        lines.append(
            f"{c.name} ({c.image}) {c.status}; id={c.container_id} ports={ports}"
        )
    if len(result.containers) > limit:
        lines.append(f"+{len(result.containers) - limit} more")
    return "\n".join(lines)


# --- docker build ---


def parse_build(stdout: str, stderr: str = "") -> ImageBuildResult:
    text = stdout + ("\n" + stderr if stderr.strip() else "")
    steps: list[ImageBuildStep] = []
    final_id: str | None = None
    final_tags: list[str] = []
    success = False
    failed = False
    errors: list[str] = []
    warnings: list[str] = []

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        # Legacy build
        m = _LEGACY_STEP_RE.match(line)
        if m:
            steps.append(
                ImageBuildStep(
                    instruction=m.group("inst").strip(),
                    cached=False,
                    duration_seconds=None,
                )
            )
            continue
        if _LEGACY_CACHED_RE.search(line) and steps:
            last = steps.pop()
            steps.append(
                ImageBuildStep(
                    instruction=last.instruction,
                    cached=True,
                    duration_seconds=last.duration_seconds,
                )
            )
            continue
        m = _LEGACY_SUCCESS_RE.search(line)
        if m:
            final_id = m.group("id")
            success = True
            continue
        m = _LEGACY_TAGGED_RE.search(line)
        if m:
            final_tags.append(m.group("tag"))
            continue
        # BuildKit
        if _BK_FINISHED_RE.search(line):
            success = True
            continue
        if _BK_FAILED_RE.search(line):
            failed = True
            continue
        m = _BK_IMAGE_ID_RE.search(line)
        if m and final_id is None:
            final_id = m.group("id")
        m = _BK_IMAGE_TAG_RE.search(line)
        if m:
            final_tags.append(m.group("tag"))
        # Common step pattern: `=> [3/5] COPY package.json ./   0.1s`
        m = _BK_STEP_RE.match(line.lstrip())
        if m and m.group("inst").strip() and "Building" not in m.group("inst"):
            cached = "CACHED" in line
            try:
                dur = float(m.group("dur"))
            except (TypeError, ValueError):
                dur = None
            inst = m.group("inst").strip()
            if not _is_step_noise(inst):
                steps.append(
                    ImageBuildStep(
                        instruction=inst,
                        cached=cached,
                        duration_seconds=dur,
                    )
                )
            continue
        em = _ERROR_RE.match(line)
        if em:
            errors.append(em.group("msg").strip())
            continue
        wm = _WARN_RE.match(line)
        if wm:
            warnings.append(wm.group("msg").strip())
    if failed:
        success = False

    return ImageBuildResult(
        steps=tuple(steps),
        final_image_id=final_id[:12] if final_id else None,
        final_tags=tuple(final_tags),
        success=success,
        errors=tuple(errors),
        warnings=tuple(warnings[:10]),
    )


_STEP_NOISE_PREFIXES = (
    "exporting",
    "transferring",
    "load metadata",
    "load build definition",
    "load .dockerignore",
)


def _is_step_noise(inst: str) -> bool:
    low = inst.lower().lstrip()
    return any(low.startswith(p) for p in _STEP_NOISE_PREFIXES)


def _compress_build(
    stdout: str, stderr: str, ctx: CompressorContext
) -> CompressedOutput:
    result = parse_build(stdout, stderr)
    raw_tokens = estimate_tokens(stdout) + estimate_tokens(stderr)
    level = select_level(raw_tokens, ctx.hint)
    formatted = _format_build(result, level)
    compressed = estimate_tokens(formatted)
    must_preserve = tuple(
        re.escape(e) for e in result.errors[:5]
    ) + tuple(re.escape(t) for t in result.final_tags)
    preserved = verify_must_preserve(formatted, must_preserve, stdout + stderr)
    return CompressedOutput(
        text=formatted,
        level=level,
        schema="docker_build",
        original_tokens=raw_tokens,
        compressed_tokens=compressed,
        must_preserve_ok=preserved,
        truncated=False,
        notes=ctx.notes,
    )


def _format_build(result: ImageBuildResult, level: CompressionLevel) -> str:
    status = "succeeded" if result.success else (
        "failed" if result.errors or not result.success else "unknown"
    )
    head = (
        f"docker build: {status}, {len(result.steps)} steps, "
        f"{sum(1 for s in result.steps if s.cached)} cached"
    )
    if result.final_image_id:
        head += f", image={result.final_image_id}"
    if result.final_tags:
        head += f", tags={','.join(result.final_tags[:3])}"
    if level == CompressionLevel.ULTRA:
        return head
    lines = [head]
    if result.errors:
        lines.append("")
        lines.append("errors:")
        for err in result.errors[:5]:
            lines.append(f"- {err}")
        if len(result.errors) > 5:
            lines.append(f"+{len(result.errors) - 5} more")
    if level == CompressionLevel.COMPACT:
        # Show the first 6 + last 4 step instructions.
        steps = result.steps
        if len(steps) > 12:
            window = list(steps[:6]) + [None] + list(steps[-4:])
        else:
            window = list(steps)
        for step in window:
            if step is None:
                lines.append(f"... ({len(steps) - 10} steps)")
            else:
                tag = "CACHED" if step.cached else "RAN"
                dur = (
                    f" {step.duration_seconds:.1f}s"
                    if step.duration_seconds is not None
                    else ""
                )
                lines.append(f"{tag}{dur} {step.instruction[:120]}")
    else:  # VERBOSE
        for step in result.steps:
            tag = "CACHED" if step.cached else "RAN"
            dur = (
                f" {step.duration_seconds:.1f}s"
                if step.duration_seconds is not None
                else ""
            )
            lines.append(f"{tag}{dur} {step.instruction}")
    if result.warnings:
        lines.append("")
        lines.append("warnings:")
        for warn in result.warnings[:5]:
            lines.append(f"- {warn}")
    return "\n".join(lines)
