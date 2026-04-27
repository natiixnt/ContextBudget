"""
Session-scoped path aliasing for compressor outputs (V41).

A PathAliaser holds a path -> short-alias map across `compress_command`
invocations belonging to the same agent session. The first occurrence of
a path stays verbatim and is annotated `(=f001)`; subsequent occurrences
(in the same call OR later calls) collapse to the alias `f001`. Lazy
first-use guarantees the rewrite is non-regressive: a once-only path
keeps its full form plus a small 1-2 token annotation; repeated paths
save the difference between full path tokens and alias tokens
(typically 4-12 cl100k tokens per repeat).

Determinism: alias assignment is first-seen-first-numbered with a
lexicographic tie-break inside a single call. The PathAliaser is owned
by the caller (typically a session object) and passed into
`compress_command`. The default is None which disables aliasing entirely
so existing callers are byte-identical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Match conservative path-shaped tokens:
#   - at least one '/' or relative-current marker
#   - one of a known set of extensions (covers the bulk of code/config)
#   - bounded character set so we avoid eating arbitrary words
_PATH_RE = re.compile(
    r"\b(?:[\w.\-]+/)+[\w.\-]+\.(?:py|js|jsx|ts|tsx|go|rs|java|kt|rb|md|toml|yaml|yml|json|sh|cfg|ini|h|hpp|c|cpp|sql)\b"
)
_ALIAS_FMT = "f{:03d}"
_LEGEND_SUFFIX = " (={alias})"


@dataclass
class PathAliaser:
    """Holds the per-session path -> alias bindings.

    Caller creates one and passes it into compress_command for every call
    that should share the same alias namespace. Thread-safe under a single
    session (no cross-session sharing should happen).
    """

    next_index: int = 1
    by_path: dict[str, str] = field(default_factory=dict)

    def apply(self, text: str) -> str:
        """Rewrite text so paths use aliases (lazy first-use).

        First occurrence of a path stays full and is annotated. All later
        occurrences (this call or future calls sharing the aliaser) become
        bare alias tokens.
        """
        # Pre-scan: collect all distinct paths in this text in first-seen order.
        seen_in_call: list[str] = []
        seen_set: set[str] = set()
        for match in _PATH_RE.finditer(text):
            path = match.group(0)
            if path not in seen_set:
                seen_set.add(path)
                seen_in_call.append(path)

        # Assign aliases for new paths (deterministic order).
        new_aliases: list[tuple[str, str]] = []
        for path in seen_in_call:
            if path not in self.by_path:
                alias = _ALIAS_FMT.format(self.next_index)
                self.next_index += 1
                self.by_path[path] = alias
                new_aliases.append((path, alias))

        if not seen_in_call:
            return text

        # Walk paths-in-call. For paths already bound BEFORE this call,
        # every occurrence becomes the alias. For paths first-seen IN this
        # call, the FIRST occurrence stays full + annotation, the rest
        # become the alias.
        out_parts: list[str] = []
        cursor = 0
        first_use_done: set[str] = set()
        for match in _PATH_RE.finditer(text):
            path = match.group(0)
            alias = self.by_path[path]
            out_parts.append(text[cursor:match.start()])
            is_new = any(p == path for p, _ in new_aliases)
            if is_new and path not in first_use_done:
                out_parts.append(path + _LEGEND_SUFFIX.format(alias=alias))
                first_use_done.add(path)
            else:
                out_parts.append(alias)
            cursor = match.end()
        out_parts.append(text[cursor:])
        return "".join(out_parts)

    def reset(self) -> None:
        self.next_index = 1
        self.by_path.clear()


# --- V43 content reference ledger ---


import hashlib

# Block must exceed this many cl100k tokens to be eligible for ref'ing.
# Below the floor the '(=ref:NNN)' annotation cost beats the saving.
_REF_MIN_TOKENS = 6
# Cap per-block content length we'll consider; very long blocks still
# refable but we limit the dict footprint.
_REF_MAX_CHARS = 4096
_REF_MIN_CHARS = 24
_REF_FMT = "ref:{:03d}"


def _normalise_block(text: str) -> str:
    """Strip trailing whitespace + collapse internal runs so cosmetically-
    equal blocks share a fingerprint. Determinism-preserving."""
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def _fingerprint(text: str) -> str:
    return hashlib.sha1(_normalise_block(text).encode("utf-8")).hexdigest()


@dataclass
class RefLedger:
    """Session-scoped content -> numeric-ref map (V43).

    Caller passes one into compress_command per session; the pipeline
    rewrites repeat content blocks to '{ref:NNN}' on later calls.
    First-call output annotates blocks above the size floor with
    '(=ref:NNN)' so the agent learns the binding.

    Block scope: paragraph-shaped chunks separated by blank lines.
    A block must have at least _REF_MIN_TOKENS cl100k tokens (estimated
    via _tokens_lite) and no more than _REF_MAX_CHARS characters to be
    eligible.
    """

    next_index: int = 1
    by_fingerprint: dict[str, str] = field(default_factory=dict)

    def apply(self, text: str) -> str:
        from redcon.cmd._tokens_lite import estimate_tokens

        # Split on blank lines: each "block" is a paragraph separated by
        # one or more empty lines. Preserves the inter-block separator
        # so reassembly is exact.
        if not text:
            return text
        parts: list[str] = []
        for block in _split_blocks(text):
            if block.strip() == "":
                parts.append(block)
                continue
            if (
                len(block) < _REF_MIN_CHARS
                or len(block) > _REF_MAX_CHARS
                or estimate_tokens(block) < _REF_MIN_TOKENS
            ):
                parts.append(block)
                continue
            fp = _fingerprint(block)
            existing = self.by_fingerprint.get(fp)
            if existing is None:
                ref = _REF_FMT.format(self.next_index)
                self.next_index += 1
                self.by_fingerprint[fp] = ref
                parts.append(block + f"  (=ref:{self.next_index - 1:03d})")
            else:
                parts.append("{" + existing + "}")
        return "".join(parts)

    def reset(self) -> None:
        self.next_index = 1
        self.by_fingerprint.clear()


def _split_blocks(text: str) -> list[str]:
    """Split text into paragraph-shaped blocks, keeping the empty
    separators so reassembly is exact."""
    out: list[str] = []
    buf: list[str] = []
    in_blank = False
    for line in text.splitlines(keepends=True):
        is_blank = line.strip() == ""
        if is_blank and not in_blank and buf:
            out.append("".join(buf))
            buf = [line]
            in_blank = True
        elif is_blank:
            buf.append(line)
            in_blank = True
        else:
            if in_blank and buf:
                out.append("".join(buf))
                buf = [line]
            else:
                buf.append(line)
            in_blank = False
    if buf:
        out.append("".join(buf))
    return out
