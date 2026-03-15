from __future__ import annotations

from pathlib import Path
import subprocess


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def build_pr_audit_repo(root: Path) -> tuple[Path, str, str]:
    repo = root / "repo"
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.name", "Redcon Tests")
    run_git(repo, "config", "user.email", "tests@example.com")
    current_branch = run_git(repo, "branch", "--show-current")
    if current_branch != "main":
        run_git(repo, "checkout", "-b", "main")

    write_file(
        repo / "auth" / "service.py",
        """
def login(token: str) -> bool:
    return token.startswith("prod_")
""".strip()
        + "\n",
    )
    write_file(
        repo / "api" / "router.py",
        """
from auth.service import login


def route_login(token: str) -> bool:
    return login(token)
""".strip()
        + "\n",
    )
    write_file(repo / "requirements.txt", "fastapi==0.110.0\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "base")
    base_commit = run_git(repo, "rev-parse", "HEAD")

    run_git(repo, "checkout", "-b", "feature/pr-audit")
    write_file(
        repo / "auth" / "service.py",
        """
import httpx


def _normalize_token(token: str) -> str:
    return token.strip()


def _fetch_profile(token: str) -> dict[str, str]:
    client = httpx.Client()
    if token.startswith("prod_"):
        return {"scope": "prod", "client": client.__class__.__name__}
    if token.startswith("staging_"):
        return {"scope": "staging", "client": client.__class__.__name__}
    return {"scope": "unknown", "client": client.__class__.__name__}


def login(token: str) -> bool:
    normalized = _normalize_token(token)
    if not normalized:
        return False
    profile = _fetch_profile(normalized)
    if profile["scope"] == "unknown":
        return False
    return normalized.startswith("prod_")
""".strip()
        + "\n",
    )
    write_file(
        repo / "api" / "router.py",
        """
from auth.service import login


def route_login(token: str, *, admin: bool = False) -> bool:
    if admin and token.startswith("admin_"):
        return True
    if not token:
        return False
    if token.startswith("legacy_"):
        return False
    return login(token)


def route_refresh(token: str) -> bool:
    if token.endswith("_refresh"):
        return login(token)
    return False
""".strip()
        + "\n",
    )
    write_file(repo / "requirements.txt", "fastapi==0.110.0\nhttpx==0.27.0\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "feature")
    head_commit = run_git(repo, "rev-parse", "HEAD")

    return repo, base_commit, head_commit
