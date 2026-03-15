from __future__ import annotations

from pathlib import Path

from contextbudget.config import load_workspace
from contextbudget.config import ScoreSettings
from contextbudget.scanners.repository import scan_repository
from contextbudget.scanners.workspace import scan_workspace
from contextbudget.scorers.relevance import score_files


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_repository_finds_text_files(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth.py", "def check_token():\n    return True\n")
    _write(tmp_path / "README.md", "Authentication module")
    _write(tmp_path / ".contextbudget_cache.json", "{\"summaries\": {}}")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    records = scan_repository(tmp_path)
    paths = [r.path for r in records]

    assert "src/auth.py" in paths
    assert "README.md" in paths
    assert "image.png" not in paths
    assert ".contextbudget_cache.json" not in paths
    assert ".contextbudget/scan-index.json" not in paths


def test_score_files_ranks_keyword_matches(tmp_path: Path) -> None:
    _write(tmp_path / "api" / "search.py", "def cache_search():\n    pass\n")
    _write(tmp_path / "docs" / "notes.md", "misc text")

    records = scan_repository(tmp_path)
    ranked = score_files("add caching to search API", records)

    assert ranked
    assert ranked[0].file.path == "api/search.py"
    assert ranked[0].score > 0


def test_scan_repository_respects_include_and_ignore_globs(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "a.py", "print('a')\n")
    _write(tmp_path / "src" / "b_test.py", "print('b')\n")
    _write(tmp_path / "docs" / "readme.md", "hello\n")

    records = scan_repository(
        tmp_path,
        include_globs=["src/*.py"],
        ignore_globs=["*test.py"],
    )
    paths = [r.path for r in records]

    assert "src/a.py" in paths
    assert "src/b_test.py" not in paths
    assert "docs/readme.md" not in paths


def test_score_files_supports_critical_path_keywords(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "auth_middleware.py", "def noop():\n    pass\n")
    _write(tmp_path / "src" / "misc.py", "def noop():\n    pass\n")
    records = scan_repository(tmp_path)

    settings = ScoreSettings(critical_path_keywords=["auth"], critical_path_bonus=5.0)
    ranked = score_files("refactor middleware", records, settings=settings)

    assert ranked[0].file.path == "src/auth_middleware.py"


def test_python_import_graph_propagates_relevance(tmp_path: Path) -> None:
    _write(
        tmp_path / "src" / "app.py",
        "from .auth import login\n\n\ndef run() -> None:\n    login('x')\n",
    )
    _write(
        tmp_path / "src" / "auth.py",
        "from .token_store import verify_token\n\n\ndef login(token: str) -> bool:\n    return verify_token(token)\n",
    )
    _write(
        tmp_path / "src" / "token_store.py",
        "def verify_token(token: str) -> bool:\n    return token.startswith('prod_')\n",
    )
    _write(tmp_path / "src" / "math_utils.py", "def add(a: int, b: int) -> int:\n    return a + b\n")

    records = scan_repository(tmp_path)
    ranked = score_files("refactor auth login flow", records)
    by_path = {item.file.path: item for item in ranked}

    assert by_path["src/token_store.py"].score > by_path["src/math_utils.py"].score
    assert any("imported by relevant file" in reason for reason in by_path["src/token_store.py"].reasons)
    assert any("depends on relevant module" in reason for reason in by_path["src/app.py"].reasons)
    assert any("adjacent to entrypoint" in reason for reason in by_path["src/auth.py"].reasons)


def test_typescript_import_graph_propagates_relevance(tmp_path: Path) -> None:
    _write(
        tmp_path / "src" / "main.ts",
        "import { routeAuth } from './auth';\n\nrouteAuth();\n",
    )
    _write(
        tmp_path / "src" / "auth.ts",
        "import { createSession } from './session';\n\nexport function routeAuth(): string {\n  return createSession();\n}\n",
    )
    _write(
        tmp_path / "src" / "session.ts",
        "export function createSession(): string {\n  return 'ok';\n}\n",
    )
    _write(tmp_path / "src" / "ui.ts", "export const Button = 'button';\n")

    records = scan_repository(tmp_path)
    ranked = score_files("update auth route", records)
    by_path = {item.file.path: item for item in ranked}

    assert by_path["src/session.ts"].score > by_path["src/ui.ts"].score
    assert any("imported by relevant file" in reason for reason in by_path["src/session.ts"].reasons)
    assert any("adjacent to entrypoint" in reason for reason in by_path["src/auth.ts"].reasons)


def test_workspace_scan_namespaces_paths_and_applies_repo_rules(tmp_path: Path) -> None:
    _write(tmp_path / "service-a" / "src" / "auth.py", "def login() -> bool:\n    return True\n")
    _write(tmp_path / "service-a" / "tests" / "test_auth.py", "def test_login() -> None:\n    assert True\n")
    _write(tmp_path / "service-b" / "src" / "auth.py", "def verify() -> bool:\n    return True\n")
    _write(
        tmp_path / "workspace.toml",
        """
[scan]
include_globs = ["**/*.py"]

[[repos]]
label = "service-a"
path = "service-a"
ignore_globs = ["tests/**"]

[[repos]]
label = "service-b"
path = "service-b"
""".strip(),
    )

    workspace = load_workspace(tmp_path / "workspace.toml")
    records, scanned_repos = scan_workspace(workspace, workspace.config)
    paths = [record.path for record in records]

    assert "service-a:src/auth.py" in paths
    assert "service-b:src/auth.py" in paths
    assert "service-a:tests/test_auth.py" not in paths
    assert [item.label for item in scanned_repos] == ["service-a", "service-b"]


def test_workspace_score_files_rank_across_repo_boundaries(tmp_path: Path) -> None:
    _write(
        tmp_path / "api" / "src" / "main.py",
        "from .auth import login\n\n\ndef run() -> bool:\n    return login()\n",
    )
    _write(
        tmp_path / "api" / "src" / "auth.py",
        "def login() -> bool:\n    return True\n",
    )
    _write(
        tmp_path / "worker" / "src" / "main.py",
        "from .auth import sync_auth\n\n\ndef run() -> bool:\n    return sync_auth()\n",
    )
    _write(
        tmp_path / "worker" / "src" / "auth.py",
        "def sync_auth() -> bool:\n    return True\n",
    )
    _write(
        tmp_path / "workspace.toml",
        """
[scan]
include_globs = ["**/*.py"]

[[repos]]
label = "api"
path = "api"

[[repos]]
label = "worker"
path = "worker"
""".strip(),
    )

    workspace = load_workspace(tmp_path / "workspace.toml")
    records, _ = scan_workspace(workspace, workspace.config)
    ranked = score_files("update auth flow across services", records)
    by_path = {item.file.path: item for item in ranked}

    assert "api:src/auth.py" in by_path
    assert "worker:src/auth.py" in by_path
    assert any("adjacent to entrypoint" in reason for reason in by_path["api:src/auth.py"].reasons)
    assert any("adjacent to entrypoint" in reason for reason in by_path["worker:src/auth.py"].reasons)
