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
