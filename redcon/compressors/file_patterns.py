from __future__ import annotations

"""Shared file classification patterns for the compression pipeline."""

_TEST_FILE_PATTERNS = ("test_", "_test.", "/test/", "/tests/", "spec_", "_spec.")

_UTILITY_FILE_PATTERNS = (
    "/config.", "/config/", "helpers.", "utils.", "/utils/",
    "validators.", "constants.", "settings.", "/types.", "exceptions.", "errors.",
)


def _is_test_file(path: str) -> bool:
    """Return True if the file path looks like a test file."""
    p = path.lower().replace("\\", "/")
    return any(pat in p for pat in _TEST_FILE_PATTERNS)


def _is_utility_file(path: str) -> bool:
    """Return True if the file is a utility/config/helper module.

    Utility files rarely contain task-specific logic so their bodies are
    stubbed - only imports and signatures are retained, saving 40-60%.
    """
    p = path.lower().replace("\\", "/")
    return any(pat in p for pat in _UTILITY_FILE_PATTERNS)
