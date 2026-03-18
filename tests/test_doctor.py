from __future__ import annotations

from pathlib import Path

from redcon.core.doctor import run_doctor, doctor_as_dict


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_doctor_passes_with_default_setup(tmp_path: Path) -> None:
    report = run_doctor(tmp_path)
    assert report.failures == 0
    assert report.passed > 0
    # Python and TOML parser should always pass
    names = [c.name for c in report.checks]
    assert "python_version" in names
    assert "toml_parser" in names


def test_doctor_warns_no_config_file(tmp_path: Path) -> None:
    report = run_doctor(tmp_path)
    config_check = next(c for c in report.checks if c.name == "config")
    assert config_check.status == "warn"
    assert "No redcon.toml found" in config_check.message


def test_doctor_validates_config(tmp_path: Path) -> None:
    _write(tmp_path / "redcon.toml", "[budget]\nmax_tokens = -1\n")
    report = run_doctor(tmp_path)
    config_check = next(c for c in report.checks if c.name == "config")
    assert config_check.status == "fail"
    assert "validation error" in config_check.message


def test_doctor_detects_valid_config(tmp_path: Path) -> None:
    _write(tmp_path / "redcon.toml", "[budget]\nmax_tokens = 5000\n")
    report = run_doctor(tmp_path)
    config_check = next(c for c in report.checks if c.name == "config")
    assert config_check.status == "ok"


def test_doctor_warns_no_cache_dir(tmp_path: Path) -> None:
    report = run_doctor(tmp_path)
    cache_check = next(c for c in report.checks if c.name == "cache_dir")
    assert cache_check.status == "warn"


def test_doctor_ok_cache_dir_exists(tmp_path: Path) -> None:
    (tmp_path / ".redcon").mkdir()
    report = run_doctor(tmp_path)
    cache_check = next(c for c in report.checks if c.name == "cache_dir")
    assert cache_check.status == "ok"


def test_doctor_warns_no_git(tmp_path: Path) -> None:
    report = run_doctor(tmp_path)
    git_check = next(c for c in report.checks if c.name == "git_repo")
    assert git_check.status == "warn"


def test_doctor_ok_git_exists(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    report = run_doctor(tmp_path)
    git_check = next(c for c in report.checks if c.name == "git_repo")
    assert git_check.status == "ok"


def test_doctor_as_dict_structure(tmp_path: Path) -> None:
    report = run_doctor(tmp_path)
    data = doctor_as_dict(report)
    assert data["command"] == "doctor"
    assert "python_version" in data
    assert "platform" in data
    assert "redcon_version" in data
    assert isinstance(data["checks"], list)
    assert "summary" in data
    assert data["summary"]["total"] == len(report.checks)


def test_doctor_reports_nonzero_on_failure(tmp_path: Path) -> None:
    _write(tmp_path / "redcon.toml", "[budget]\nmax_tokens = -1\n")
    report = run_doctor(tmp_path)
    assert report.failures > 0
