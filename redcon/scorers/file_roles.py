from __future__ import annotations

"""Classify files by role for scoring adjustments.

Roles:
    prod      - production source code (default)
    test      - test files and test fixtures
    docs      - documentation (markdown, rst, txt in docs/)
    example   - example/demo/sample code
    config    - configuration files in root or config dirs
    generated - auto-generated artifacts
"""

import os

_TEST_MARKERS = ("test", "tests", "spec", "__tests__", "specs")
_EXAMPLE_MARKERS = ("example", "examples", "demo", "demos", "sample", "samples")
_DOCS_MARKERS = ("docs", "doc", "documentation")
_GENERATED_MARKERS = ("generated", "__pycache__", "_pb2", ".g.")
_DOC_EXTENSIONS = frozenset((".md", ".rst", ".txt", ".adoc"))
_CONFIG_EXTENSIONS = frozenset((".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".env"))
_CONFIG_DIRS = frozenset(("config", "configs", "conf", ".github", ".circleci"))


def classify_file_role(path: str) -> str:
    """Return the role of a file based on path heuristics."""
    parts = path.lower().replace("\\", "/").split("/")
    _, ext = os.path.splitext(parts[-1])

    # Generated artifacts - check before other roles.
    for marker in _GENERATED_MARKERS:
        if any(marker in part for part in parts):
            return "generated"

    # Test files.
    for marker in _TEST_MARKERS:
        if any(part == marker or part.startswith(f"{marker}_") or part.endswith(f"_{marker}") for part in parts):
            return "test"
        if any(marker in part for part in parts):
            return "test"

    # Example/demo code.
    for marker in _EXAMPLE_MARKERS:
        if any(part == marker or marker in part for part in parts):
            return "example"

    # Documentation.
    if ext in _DOC_EXTENSIONS:
        return "docs"
    for marker in _DOCS_MARKERS:
        if any(part == marker for part in parts):
            return "docs"

    # Config files - only in root or known config directories.
    if ext in _CONFIG_EXTENSIONS:
        if len(parts) <= 2 or any(part in _CONFIG_DIRS for part in parts[:-1]):
            return "config"

    return "prod"
