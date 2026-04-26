"""
Tree-sitter signature extractor.

Returns class/function/method/type definitions from a source file as
``Signature`` records (name, kind, line, end_line, snippet). Used by the
``redcon repo-map`` subcommand and the underlying MCP tool to render
Aider-style maps with grounded code structure.

Languages supported (when ``redcon[symbols]`` is installed): Python,
TypeScript, JavaScript, TSX, JSX, Rust, Go, Java, Ruby, C, C++, Kotlin,
Swift, Bash, PHP. Anything else returns an empty list.

Architecture:
- The heavy imports (``tree_sitter``, ``tree_sitter_language_pack``) are
  resolved lazily on first use so simply importing this module costs
  nothing when the extra is missing.
- Per-language node-kind mappings live in ``_NODE_KINDS``. Adding a
  language is one tuple plus a query-free walk over the parse tree.
- The walk is deterministic: depth-first, sibling order preserved,
  no LLM calls. Same source -> same signatures every time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Signature:
    """One symbol's signature header."""

    name: str
    kind: str  # "function" | "class" | "method" | "type" | "interface" | "struct" | "enum"
    line: int
    end_line: int
    snippet: str


# Map (language, tree-sitter-node-kind) -> our canonical Signature.kind
# plus a child-node-name to use as the symbol name. Entries are tried in
# insertion order; the first match wins.
_NODE_KINDS: dict[str, dict[str, str]] = {
    "python": {
        "class_definition": "class",
        "function_definition": "function",
        "decorated_definition": "function",  # treated as wrapper of fn/cls
    },
    "typescript": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "function_declaration": "function",
        "method_definition": "method",
        "abstract_class_declaration": "class",
    },
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "method_definition": "method",
    },
    "tsx": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "function_declaration": "function",
        "method_definition": "method",
    },
    "rust": {
        "function_item": "function",
        "struct_item": "struct",
        "enum_item": "enum",
        "trait_item": "interface",
        "impl_item": "class",
        "type_item": "type",
        "mod_item": "module",
    },
    "go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_declaration": "type",
        "struct_type": "struct",
        "interface_type": "interface",
    },
    "java": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "method_declaration": "method",
        "constructor_declaration": "method",
        "record_declaration": "class",
    },
    "ruby": {
        "class": "class",
        "module": "module",
        "method": "method",
        "singleton_method": "method",
    },
    "c": {
        "function_definition": "function",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
        "type_definition": "type",
    },
    "cpp": {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "struct",
        "enum_specifier": "enum",
        "namespace_definition": "module",
        "template_declaration": "template",
    },
    "kotlin": {
        "class_declaration": "class",
        "object_declaration": "class",
        "function_declaration": "function",
        "property_declaration": "type",
    },
    "swift": {
        "class_declaration": "class",
        "function_declaration": "function",
        "protocol_declaration": "interface",
        "enum_declaration": "enum",
        "struct_declaration": "struct",
    },
    "bash": {
        "function_definition": "function",
    },
    "php": {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "trait_declaration": "interface",
        "function_definition": "function",
        "method_declaration": "method",
    },
}


SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(sorted(_NODE_KINDS.keys()))


_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "tsx",  # tsx grammar handles jsx too
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".php": "php",
}


def detect_language(path: str | Path) -> str | None:
    """Return the tree-sitter language id for a path, or None if unsupported."""
    suffix = Path(str(path)).suffix.lower()
    return _EXT_TO_LANG.get(suffix)


_PARSER_CACHE: dict[str, Any] = {}
_AVAILABILITY: bool | None = None


def is_available() -> bool:
    """True iff a usable tree-sitter installation can produce parsers.

    We accept any of:
      - ``tree-sitter-language-pack`` (preferred, single wheel)
      - ``tree-sitter-languages`` (legacy popular bundle)
      - per-language wheels like ``tree_sitter_python``,
        ``tree_sitter_typescript`` resolved on demand.
    """
    global _AVAILABILITY
    if _AVAILABILITY is not None:
        return _AVAILABILITY
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        _AVAILABILITY = False
        return _AVAILABILITY
    # If any provider succeeds for the simplest language, we are usable.
    test_parser = _try_get_parser("python")
    _AVAILABILITY = test_parser is not None
    return _AVAILABILITY


def _try_get_parser(language: str):
    """Try every supported provider until one returns a parser."""
    # 1. tree-sitter-language-pack
    try:
        from tree_sitter_language_pack import get_parser

        return get_parser(language)
    except Exception:
        pass
    # 2. tree-sitter-languages (legacy)
    try:
        from tree_sitter_languages import get_parser

        return get_parser(language)
    except Exception:
        pass
    # 3. per-language wheel: tree_sitter_<language>
    return _per_language_parser(language)


# Per-language wheels usually expose ``language()`` but a few (notably
# tree-sitter-typescript which ships two grammars) expose dialect-suffixed
# entry points. Try each candidate in order.
_PER_LANGUAGE_ENTRYPOINTS: dict[str, tuple[str, ...]] = {
    "typescript": ("language_typescript", "language"),
    "tsx": ("language_tsx", "language"),
    "cpp": ("language", "language_cpp"),
}


def _per_language_parser(language: str):
    """Build a parser from a stand-alone ``tree-sitter-<lang>`` wheel."""
    import importlib

    try:
        import tree_sitter
    except ImportError:
        return None
    module_name = f"tree_sitter_{language.replace('-', '_')}"
    # `tree-sitter-tsx` is unusual: it lives inside `tree_sitter_typescript`.
    fallback_module: str | None = None
    if language == "tsx":
        fallback_module = "tree_sitter_typescript"
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        if fallback_module is None:
            return None
        try:
            module = importlib.import_module(fallback_module)
        except ImportError:
            return None
    candidates = _PER_LANGUAGE_ENTRYPOINTS.get(language, ("language",))
    language_fn = None
    for name in candidates:
        language_fn = getattr(module, name, None)
        if callable(language_fn):
            break
    if language_fn is None:
        return None
    try:
        ts_language = tree_sitter.Language(language_fn())
        parser = tree_sitter.Parser(ts_language)
    except Exception as e:
        logger.debug("could not build per-language parser for %s: %s", language, e)
        return None
    return parser


def _get_parser(language: str):
    if language in _PARSER_CACHE:
        return _PARSER_CACHE[language]
    if not is_available():
        return None
    parser = _try_get_parser(language)
    if parser is None:
        logger.debug("no tree-sitter parser provider could load %s", language)
        return None
    _PARSER_CACHE[language] = parser
    return parser


def extract_signatures(
    source: str,
    *,
    language: str | None = None,
    path: str | Path | None = None,
    max_signatures: int = 500,
) -> list[Signature]:
    """
    Walk the AST and collect ``Signature`` entries for top-level and nested
    definitions.

    Supply either a ``language`` id or a ``path`` from which to derive one.
    Returns an empty list when:
      - tree-sitter is not installed
      - the language has no entry in ``_NODE_KINDS``
      - the parse fails or the source is empty
    """
    if not source:
        return []
    lang = language or (detect_language(path) if path is not None else None)
    if lang is None or lang not in _NODE_KINDS:
        return []
    parser = _get_parser(lang)
    if parser is None:
        return []

    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception as e:
        logger.debug("tree-sitter parse failed for %s: %s", lang, e)
        return []

    kind_map = _NODE_KINDS[lang]
    source_lines = source.splitlines()
    out: list[Signature] = []

    stack: list = [tree.root_node]
    while stack and len(out) < max_signatures:
        node = stack.pop()
        kind = kind_map.get(node.type)
        if kind is not None:
            sig = _build_signature(node, kind, source_lines, lang)
            if sig is not None:
                out.append(sig)
        # DFS but preserve sibling order: push reversed so we pop them in
        # original document order.
        for child in reversed(node.children):
            stack.append(child)
    return out


def _build_signature(
    node, kind: str, source_lines: list[str], language: str
) -> Signature | None:
    name = _node_name(node, language)
    if not name:
        return None
    line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    # Snippet: just the header line(s) up to the body open. For simple
    # nodes we take the start line; for multiline signatures we walk
    # forward until we hit a balanced paren / open brace / colon.
    snippet = _signature_snippet(node, source_lines)
    return Signature(
        name=name,
        kind=kind,
        line=line,
        end_line=end_line,
        snippet=snippet,
    )


def _node_name(node, language: str) -> str:
    """Find the identifier child of a definition node."""
    # Tree-sitter nodes expose named children by field name on most grammars.
    for field in ("name", "alias"):
        try:
            child = node.child_by_field_name(field)
        except Exception:
            child = None
        if child is not None:
            return child.text.decode("utf-8", errors="replace")
    # Fallback: scan immediate children for an identifier-like type.
    for child in node.children:
        if "identifier" in child.type or child.type == "name":
            try:
                return child.text.decode("utf-8", errors="replace")
            except Exception:
                continue
    # Some impl_item style nodes have no name; use the type signature instead.
    if language == "rust" and node.type == "impl_item":
        for child in node.children:
            if child.type in {"type_identifier", "scoped_type_identifier"}:
                return child.text.decode("utf-8", errors="replace")
    return ""


_SNIPPET_END_CHARS = frozenset({":", "{", "{"})


def _signature_snippet(node, source_lines: list[str]) -> str:
    """Take just the signature header (up to the body start) for the snippet."""
    start = node.start_point[0]
    end = min(node.end_point[0], start + 5)  # cap at 6 lines
    lines = source_lines[start : end + 1]
    cleaned: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        cleaned.append(stripped)
        # Stop after the first line that ends in a body-opener.
        if stripped and stripped[-1] in _SNIPPET_END_CHARS:
            break
    return "\n".join(cleaned).strip()
