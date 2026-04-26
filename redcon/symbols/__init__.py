"""
Optional tree-sitter symbol extraction.

Importing this package never raises - the heavy ``tree-sitter`` and
``tree-sitter-language-pack`` deps are optional via the ``redcon[symbols]``
extra. Use ``is_available()`` before calling extraction APIs; callers
can branch on it to fall back to the existing regex-based extractor in
``redcon.compressors.symbols``.
"""

from redcon.symbols.tree_sitter import (
    Signature,
    SUPPORTED_LANGUAGES,
    detect_language,
    extract_imports,
    extract_signatures,
    is_available,
)

__all__ = [
    "Signature",
    "SUPPORTED_LANGUAGES",
    "detect_language",
    "extract_imports",
    "extract_signatures",
    "is_available",
]
