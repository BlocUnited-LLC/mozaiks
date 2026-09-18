import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.build_e2b_preview_template import _build_log, build_template


def test_e2b_template_build_defaults_to_a_safe_dry_run(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile.preview"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")

    result = build_template(
        dockerfile=dockerfile,
        template_name="mozaiks-preview",
        cpu_count=2,
        memory_mb=2048,
        confirm_paid_build=False,
    )

    assert result["status"] == "dry_run"
    assert "--confirm-paid-build" in result["message"]


def test_e2b_template_name_is_closed_and_lowercase(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile.preview"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")

    try:
        build_template(
            dockerfile=dockerfile,
            template_name="Mozaiks Preview",
            cpu_count=2,
            memory_mb=2048,
            confirm_paid_build=False,
        )
    except ValueError as exc:
        assert "lowercase" in str(exc)
    else:
        raise AssertionError("invalid E2B template name was accepted")


def test_e2b_template_build_uses_stable_minimal_copy_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dockerfile = tmp_path / "Dockerfile.preview"
    dockerfile.write_text("FROM python:3.12-slim\nCOPY pyproject.toml /app/\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeTemplate:
        def __init__(self, *, file_context_path):
            captured["file_context_path"] = file_context_path

        def from_dockerfile(self, path):
            captured["dockerfile"] = path
            return self

        @staticmethod
        def build(template, **kwargs):
            context_root = Path(captured["file_context_path"])
            captured["has_dockerfile"] = (context_root / "Dockerfile.preview").is_file()
            captured["has_pyproject"] = (context_root / "pyproject.toml").is_file()
            captured["has_git"] = (context_root / ".git").exists()
            return SimpleNamespace(template_id="template-id", build_id="build-id")

    monkeypatch.setitem(sys.modules, "e2b", SimpleNamespace(Template=FakeTemplate))
    monkeypatch.setenv("E2B_API_KEY", "configured")

    result = build_template(
        dockerfile=dockerfile,
        template_name="mozaiks-preview",
        cpu_count=2,
        memory_mb=2048,
        confirm_paid_build=True,
    )

    assert result["status"] == "built"
    context_root = Path(captured["file_context_path"])
    assert context_root != tmp_path
    assert captured["has_dockerfile"] is True
    assert captured["has_pyproject"] is True
    assert captured["has_git"] is False


def test_e2b_build_log_replaces_symbols_unsupported_by_windows_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Console:
        encoding = "cp1252"

        def write(self, value: str) -> int:
            return len(value)

        def flush(self) -> None:
            return None

    output: list[str] = []
    console = Console()
    monkeypatch.setattr("scripts.build_e2b_preview_template.sys.stdout", console)
    monkeypatch.setattr("builtins.print", lambda value, **_: output.append(value))

    _build_log(SimpleNamespace(message="Build finished → ready"))

    assert output == ["Build finished ? ready"]
