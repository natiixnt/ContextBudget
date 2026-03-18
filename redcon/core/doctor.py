from __future__ import annotations

"""Diagnostics for verifying Redcon environment health."""

import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from redcon.config import RedconConfig, load_config, validate_config


@dataclass(slots=True)
class CheckResult:
    """Outcome of a single diagnostic check."""

    name: str
    status: str  # "ok", "warn", "fail"
    message: str
    detail: str = ""


@dataclass(slots=True)
class DoctorReport:
    """Aggregated diagnostics report."""

    python_version: str
    platform: str
    redcon_version: str
    checks: list[CheckResult] = field(default_factory=list)
    passed: int = 0
    warnings: int = 0
    failures: int = 0


def _check_python_version() -> CheckResult:
    version = sys.version_info
    if version >= (3, 10):
        return CheckResult(
            name="python_version",
            status="ok",
            message=f"Python {version.major}.{version.minor}.{version.micro}",
        )
    return CheckResult(
        name="python_version",
        status="fail",
        message=f"Python {version.major}.{version.minor}.{version.micro} - requires >= 3.10",
    )


def _check_toml_parser() -> CheckResult:
    try:
        import tomllib  # noqa: F401
        return CheckResult(name="toml_parser", status="ok", message="tomllib (stdlib)")
    except ModuleNotFoundError:
        pass
    try:
        import tomli  # noqa: F401
        return CheckResult(name="toml_parser", status="ok", message="tomli (backport)")
    except ModuleNotFoundError:
        return CheckResult(
            name="toml_parser",
            status="fail",
            message="No TOML parser available - install tomli for Python < 3.11",
        )


def _check_optional_dep(name: str, package: str, extra: str) -> CheckResult:
    try:
        mod = __import__(package)
        version = getattr(mod, "__version__", getattr(mod, "VERSION", "unknown"))
        return CheckResult(
            name=name,
            status="ok",
            message=f"{package} {version}",
        )
    except ImportError:
        return CheckResult(
            name=name,
            status="warn",
            message=f"Not installed - install with: pip install 'redcon[{extra}]'",
        )


def _check_config(repo: Path) -> CheckResult:
    config_path = repo / "redcon.toml"
    if not config_path.exists():
        return CheckResult(
            name="config",
            status="warn",
            message="No redcon.toml found - using defaults. Run 'redcon init' to create one.",
        )
    try:
        # load_config raises ConfigValidationError on invalid values, so
        # we parse manually to separate parse vs validation errors.
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        from redcon.config import load_config_from_mapping
        cfg = load_config_from_mapping(data)
        errors = validate_config(cfg)
        if errors:
            return CheckResult(
                name="config",
                status="fail",
                message=f"redcon.toml has {len(errors)} validation error(s)",
                detail="; ".join(errors),
            )
        return CheckResult(name="config", status="ok", message="redcon.toml is valid")
    except Exception as exc:
        return CheckResult(
            name="config",
            status="fail",
            message=f"Failed to parse redcon.toml: {exc}",
        )


def _check_cache_dir(repo: Path) -> CheckResult:
    cache_dir = repo / ".redcon"
    if not cache_dir.exists():
        return CheckResult(
            name="cache_dir",
            status="warn",
            message=".redcon/ directory does not exist - will be created on first run",
        )
    if not cache_dir.is_dir():
        return CheckResult(
            name="cache_dir",
            status="fail",
            message=".redcon exists but is not a directory",
        )
    return CheckResult(name="cache_dir", status="ok", message=".redcon/ directory exists")


def _check_git_repo(repo: Path) -> CheckResult:
    git_dir = repo / ".git"
    if git_dir.exists():
        return CheckResult(name="git_repo", status="ok", message="Git repository detected")
    return CheckResult(
        name="git_repo",
        status="warn",
        message="Not a git repository - git-aware features (dirty file boost, PR audit) will be unavailable",
    )


def run_doctor(repo: Path) -> DoctorReport:
    """Run all diagnostic checks and return a report."""
    try:
        from redcon import __version__
    except (ImportError, AttributeError):
        __version__ = "unknown"

    report = DoctorReport(
        python_version=platform.python_version(),
        platform=platform.platform(),
        redcon_version=__version__,
    )

    checks = [
        _check_python_version(),
        _check_toml_parser(),
        _check_optional_dep("tiktoken", "tiktoken", "tokenizers"),
        _check_optional_dep("redis", "redis", "redis"),
        _check_optional_dep("fastapi", "fastapi", "gateway"),
        _check_optional_dep("uvicorn", "uvicorn", "gateway"),
        _check_config(repo),
        _check_cache_dir(repo),
        _check_git_repo(repo),
    ]

    for check in checks:
        report.checks.append(check)
        if check.status == "ok":
            report.passed += 1
        elif check.status == "warn":
            report.warnings += 1
        else:
            report.failures += 1

    return report


def doctor_as_dict(report: DoctorReport) -> dict[str, Any]:
    """Convert a DoctorReport to a JSON-serializable dict."""
    return {
        "command": "doctor",
        "python_version": report.python_version,
        "platform": report.platform,
        "redcon_version": report.redcon_version,
        "checks": [
            {
                "name": c.name,
                "status": c.status,
                "message": c.message,
                **({"detail": c.detail} if c.detail else {}),
            }
            for c in report.checks
        ],
        "summary": {
            "passed": report.passed,
            "warnings": report.warnings,
            "failures": report.failures,
            "total": len(report.checks),
        },
    }
