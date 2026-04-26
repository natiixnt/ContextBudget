"""Tests for the optional tree-sitter symbol extractor.

Tests gracefully skip when ``redcon[symbols]`` is not installed so
the suite stays green on a default ``pip install redcon``. The
``is_available`` -> graceful fallback contract is exercised whether
the extra is present or not.
"""

from __future__ import annotations

import pytest

from redcon.symbols import (
    SUPPORTED_LANGUAGES,
    Signature,
    detect_language,
    extract_signatures,
    is_available,
)


def test_detect_language_for_common_extensions():
    assert detect_language("foo.py") == "python"
    assert detect_language("bar.ts") == "typescript"
    assert detect_language("baz.tsx") == "tsx"
    assert detect_language("a.rs") == "rust"
    assert detect_language("a.go") == "go"
    assert detect_language("a.java") == "java"
    assert detect_language("a.rb") == "ruby"
    assert detect_language("a.kt") == "kotlin"
    assert detect_language("a.swift") == "swift"
    assert detect_language("a.unknown") is None


def test_supported_languages_covers_all_documented():
    for lang in (
        "python", "typescript", "javascript", "tsx", "rust", "go", "java",
        "ruby", "c", "cpp", "kotlin", "swift", "bash", "php",
    ):
        assert lang in SUPPORTED_LANGUAGES


def test_extract_returns_empty_for_unknown_language():
    """Unknown language -> empty list, no exception."""
    result = extract_signatures("anything", language="cobol")
    assert result == []


def test_extract_returns_empty_for_empty_source():
    result = extract_signatures("", language="python")
    assert result == []


def test_extract_gracefully_handles_no_path_and_no_language():
    result = extract_signatures("def foo(): pass")
    assert result == []


def test_is_available_returns_a_bool():
    assert isinstance(is_available(), bool)


@pytest.mark.skipif(
    not is_available(),
    reason="redcon[symbols] extra not installed (tree-sitter-language-pack)",
)
class TestWithTreeSitter:
    def test_python_classes_and_functions(self):
        source = (
            "import os\n"
            "\n"
            "class MyClass:\n"
            "    \"\"\"docstring\"\"\"\n"
            "    def method(self, x: int) -> str:\n"
            "        return str(x)\n"
            "\n"
            "def top_level(a, b):\n"
            "    return a + b\n"
        )
        sigs = extract_signatures(source, language="python")
        kinds = {s.kind for s in sigs}
        names = {s.name for s in sigs}
        assert "class" in kinds
        assert "function" in kinds
        assert "MyClass" in names
        assert "method" in names
        assert "top_level" in names

    def test_typescript_class_method_function(self):
        source = (
            "export class Widget {\n"
            "  height: number;\n"
            "  render(): string {\n"
            "    return '';\n"
            "  }\n"
            "}\n"
            "\n"
            "export function helper(): void {}\n"
            "\n"
            "interface Foo {\n"
            "  bar(): void;\n"
            "}\n"
        )
        sigs = extract_signatures(source, language="typescript")
        names = {s.name for s in sigs}
        assert "Widget" in names
        assert "render" in names
        assert "helper" in names
        assert "Foo" in names

    def test_rust_struct_function(self):
        source = (
            "struct Point { x: i32, y: i32 }\n"
            "\n"
            "fn distance(p: Point) -> f64 { 0.0 }\n"
            "\n"
            "impl Point {\n"
            "    fn new() -> Self { Self { x: 0, y: 0 } }\n"
            "}\n"
        )
        sigs = extract_signatures(source, language="rust")
        names = {s.name for s in sigs}
        assert "Point" in names
        assert "distance" in names
        assert "new" in names

    def test_signatures_include_line_numbers(self):
        source = "def first():\n    pass\n\ndef second():\n    pass\n"
        sigs = extract_signatures(source, language="python")
        # Two functions, lines 1 and 4
        lines = sorted(s.line for s in sigs if s.kind == "function")
        assert lines == [1, 4]

    def test_signature_snippet_is_header_only(self):
        source = "def calculate_total(items: list[int]) -> int:\n    return sum(items)\n"
        sigs = extract_signatures(source, language="python")
        fn = next(s for s in sigs if s.kind == "function")
        assert "calculate_total" in fn.snippet
        assert "items: list" in fn.snippet
        # The body line should NOT be in the snippet.
        assert "return sum" not in fn.snippet

    def test_path_based_detection(self):
        source = "def foo():\n    pass\n"
        sigs = extract_signatures(source, path="example.py")
        assert any(s.name == "foo" for s in sigs)

    def test_extract_imports_python(self):
        from redcon.symbols import extract_imports

        source = (
            "import os\n"
            "from typing import List\n"
            "import pandas as pd\n"
            "from .relative import foo\n"
        )
        imps = extract_imports(source, language="python")
        assert "os" in imps
        assert "typing" in imps
        # Alias must be stripped.
        assert "pandas" in imps
        assert "pandas as pd" not in imps
        # Relative imports preserved.
        assert ".relative" in imps

    def test_extract_imports_typescript(self):
        from redcon.symbols import extract_imports

        source = (
            "import { foo } from './auth';\n"
            "import bar from 'react';\n"
            "import * as utils from '../utils';\n"
        )
        imps = extract_imports(source, language="typescript")
        assert "./auth" in imps
        assert "react" in imps
        assert "../utils" in imps

    def test_extract_imports_rust(self):
        from redcon.symbols import extract_imports

        source = "use std::collections::HashMap;\nuse super::utils;\n"
        imps = extract_imports(source, language="rust")
        assert "std.collections.HashMap" in imps
        assert "super.utils" in imps

    def test_extract_imports_go_multi(self):
        from redcon.symbols import extract_imports

        source = 'import (\n  "fmt"\n  "net/http"\n  "strings"\n)\n'
        imps = extract_imports(source, language="go")
        assert imps == ["fmt", "net/http", "strings"]

    def test_extract_imports_returns_empty_for_unknown(self):
        from redcon.symbols import extract_imports

        # Even with valid Python source, an unsupported language id returns [].
        assert extract_imports("import os", language="cobol") == []
