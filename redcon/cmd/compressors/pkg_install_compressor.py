"""
Package install / uninstall output compressor.

Covers ``pip install``, ``pip uninstall``, ``npm install``, ``pnpm
install``, ``yarn add``. The default output of these tools is dominated
by progress noise (Collecting X, Downloading Y, idealTree:..., done). The
agent only needs:
  - what was installed / removed / updated
  - vulnerability summary
  - deprecation warnings
  - errors

Compact level returns those four buckets. Ultra returns counts only.
"""

from __future__ import annotations

import re

from redcon.cmd.budget import select_level
from redcon.cmd.compressors.base import CompressorContext, verify_must_preserve
from redcon.cmd.types import (
    CompressedOutput,
    CompressionLevel,
    PackageInstallResult,
    PackageOp,
)
from redcon.cmd._tokens_lite import estimate_tokens


# pip patterns
_PIP_INSTALLED = re.compile(r"^Successfully installed\s+(?P<list>.+)$")
_PIP_UNINSTALLED = re.compile(r"^Successfully uninstalled\s+(?P<name>\S+)$")
_PIP_DEPRECATION = re.compile(r"^DEPRECATION:\s*(?P<msg>.+)$")
_PIP_ERROR = re.compile(r"^ERROR:\s*(?P<msg>.+)$")
_PIP_WARNING = re.compile(r"^WARNING:\s*(?P<msg>.+)$")
_PKG_VER_RE = re.compile(r"^(?P<name>[A-Za-z0-9._\-]+)-(?P<ver>[\w.\-+!]+)$")
# npm / pnpm / yarn patterns
_NPM_ADDED = re.compile(
    r"^added\s+(?P<n>\d+)\s+packages?(?:,\s*"
    r"changed\s+(?P<changed>\d+))?"
    r"(?:.*?in\s+(?P<dur>[\d.]+)s)?",
    re.IGNORECASE,
)
_NPM_REMOVED = re.compile(r"^removed\s+(?P<n>\d+)\s+packages?", re.IGNORECASE)
_NPM_UPDATED = re.compile(r"^updated\s+(?P<n>\d+)\s+packages?", re.IGNORECASE)
_NPM_VULN = re.compile(
    r"^(?P<n>\d+)\s+vulnerabilit(?:y|ies)\s*(?:\((?P<details>[^)]+)\))?",
    re.IGNORECASE,
)
_NPM_DEPRECATED = re.compile(
    r"^npm warn deprecated\s+(?P<spec>\S+)\s*:?\s*(?P<msg>.*)$",
    re.IGNORECASE,
)
_PNPM_PROGRESS = re.compile(
    r"^Progress:\s+resolved\s+\d+,\s+reused\s+\d+,\s+downloaded\s+\d+",
    re.IGNORECASE,
)
_YARN_SUCCESS = re.compile(
    r"^success\s+Saved\s+(?P<n>\d+)\s+new\s+dependencies",
    re.IGNORECASE,
)


class PackageInstallCompressor:
    schema = "pkg_install"

    @property
    def must_preserve_patterns(self) -> tuple[str, ...]:
        return ()

    def matches(self, argv: tuple[str, ...]) -> bool:
        if not argv:
            return False
        if argv[0] == "pip" and len(argv) >= 2 and argv[1] in {"install", "uninstall"}:
            return True
        if argv[0] in {"python", "python3"} and "-m" in argv and "pip" in argv:
            return "install" in argv or "uninstall" in argv
        if argv[0] in {"npm", "pnpm"} and len(argv) >= 2 and argv[1] in {
            "install", "i", "ci", "uninstall", "remove", "rm",
        }:
            return True
        if argv[0] == "yarn" and len(argv) >= 2 and argv[1] in {
            "add", "remove", "install"
        }:
            return True
        return False

    def compress(
        self,
        raw_stdout: bytes,
        raw_stderr: bytes,
        ctx: CompressorContext,
    ) -> CompressedOutput:
        text_out = raw_stdout.decode("utf-8", errors="replace")
        text_err = raw_stderr.decode("utf-8", errors="replace")
        tool = _detect_tool(ctx.argv)
        result = parse_pkg_install(text_out + "\n" + text_err, tool=tool)
        raw_tokens = estimate_tokens(text_out) + estimate_tokens(text_err)
        level = select_level(raw_tokens, ctx.hint)
        formatted = _format(result, level)
        compressed = estimate_tokens(formatted)
        # Names of installed/uninstalled packages must survive.
        names = tuple(re.escape(op.name) for op in result.operations[:30])
        preserved = verify_must_preserve(formatted, names, text_out + text_err)
        return CompressedOutput(
            text=formatted,
            level=level,
            schema=self.schema,
            original_tokens=raw_tokens,
            compressed_tokens=compressed,
            must_preserve_ok=preserved,
            truncated=False,
            notes=ctx.notes,
        )


def _detect_tool(argv: tuple[str, ...]) -> str:
    if not argv:
        return "pkg"
    if argv[0] in {"pip", "npm", "pnpm", "yarn"}:
        return argv[0]
    if argv[0] in {"python", "python3"} and "pip" in argv:
        return "pip"
    return "pkg"


def parse_pkg_install(text: str, *, tool: str = "pkg") -> PackageInstallResult:
    operations: list[PackageOp] = []
    deprecated_count = 0
    vulnerabilities: list[str] = []
    errors: list[str] = []
    duration: float | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # pip
        m = _PIP_INSTALLED.match(line)
        if m:
            for spec in m.group("list").split():
                pkg = _PKG_VER_RE.match(spec)
                if pkg:
                    operations.append(
                        PackageOp(name=pkg.group("name"), version=pkg.group("ver"), op="added")
                    )
                else:
                    operations.append(PackageOp(name=spec, version=None, op="added"))
            continue
        m = _PIP_UNINSTALLED.match(line)
        if m:
            operations.append(PackageOp(name=m.group("name"), version=None, op="removed"))
            continue
        m = _PIP_DEPRECATION.match(line)
        if m:
            deprecated_count += 1
            continue
        m = _PIP_ERROR.match(line)
        if m:
            errors.append(m.group("msg").strip()[:200])
            continue

        # npm / pnpm / yarn
        m = _NPM_ADDED.match(line)
        if m:
            try:
                count = int(m.group("n"))
                # Synthesise per-count placeholder ops so counts round-trip.
                for _ in range(count):
                    operations.append(PackageOp(name="<bulk>", version=None, op="added"))
            except (TypeError, ValueError):
                pass
            try:
                if m.group("dur"):
                    duration = float(m.group("dur"))
            except (TypeError, ValueError):
                pass
            continue
        m = _NPM_REMOVED.match(line)
        if m:
            try:
                count = int(m.group("n"))
                for _ in range(count):
                    operations.append(PackageOp(name="<bulk>", version=None, op="removed"))
            except (TypeError, ValueError):
                pass
            continue
        m = _NPM_UPDATED.match(line)
        if m:
            try:
                count = int(m.group("n"))
                for _ in range(count):
                    operations.append(PackageOp(name="<bulk>", version=None, op="updated"))
            except (TypeError, ValueError):
                pass
            continue
        m = _NPM_VULN.match(line)
        if m:
            details = m.group("details") or m.group("n") + " total"
            vulnerabilities.append(details.strip())
            continue
        m = _NPM_DEPRECATED.match(line)
        if m:
            deprecated_count += 1
            operations.append(
                PackageOp(name=m.group("spec"), version=None, op="deprecated")
            )
            continue
        m = _YARN_SUCCESS.match(line)
        if m:
            try:
                count = int(m.group("n"))
                for _ in range(count):
                    operations.append(PackageOp(name="<bulk>", version=None, op="added"))
            except (TypeError, ValueError):
                pass
            continue

    added = sum(1 for o in operations if o.op == "added")
    removed = sum(1 for o in operations if o.op == "removed")
    updated = sum(1 for o in operations if o.op == "updated")

    return PackageInstallResult(
        tool=tool,
        operations=tuple(operations),
        added=added,
        removed=removed,
        updated=updated,
        deprecated_count=deprecated_count,
        vulnerabilities=tuple(vulnerabilities),
        duration_seconds=duration,
        errors=tuple(errors),
    )


def _format(result: PackageInstallResult, level: CompressionLevel) -> str:
    head_parts = [f"{result.tool}:"]
    if result.added:
        head_parts.append(f"+{result.added}")
    if result.removed:
        head_parts.append(f"-{result.removed}")
    if result.updated:
        head_parts.append(f"~{result.updated}")
    if result.deprecated_count:
        head_parts.append(f"deprecated:{result.deprecated_count}")
    if result.vulnerabilities:
        head_parts.append(f"vulns:{len(result.vulnerabilities)}")
    if result.errors:
        head_parts.append(f"errors:{len(result.errors)}")
    if result.duration_seconds is not None:
        head_parts.append(f"in {result.duration_seconds:.1f}s")
    if len(head_parts) == 1:
        head_parts.append("(no-op)")
    head = " ".join(head_parts)
    if level == CompressionLevel.ULTRA:
        return head

    lines = [head]
    named = [op for op in result.operations if op.name and op.name != "<bulk>"]
    if named and level != CompressionLevel.ULTRA:
        # pip-style: list package names with versions
        sample_limit = (
            len(named) if level == CompressionLevel.VERBOSE else 30
        )
        for op in named[:sample_limit]:
            ver = f"=={op.version}" if op.version else ""
            lines.append(f"{_op_marker(op.op)} {op.name}{ver}")
        if len(named) > sample_limit:
            lines.append(f"+{len(named) - sample_limit} more")
    if result.vulnerabilities:
        lines.append("vulnerabilities:")
        for vuln in result.vulnerabilities[:5]:
            lines.append(f"- {vuln}")
    if result.errors:
        lines.append("errors:")
        for err in result.errors[:5]:
            lines.append(f"- {err}")
    return "\n".join(lines)


def _op_marker(op: str) -> str:
    return {"added": "+", "removed": "-", "updated": "~", "deprecated": "!"}.get(op, "?")
