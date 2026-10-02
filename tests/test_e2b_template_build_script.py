import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import build_e2b_preview_template as builder
from scripts.build_e2b_preview_template import _build_log, build_template


@pytest.fixture
def preview_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in builder._PREVIEW_CONTEXT_FILES:
        (root / name).write_text("source\n", encoding="utf-8")
    for name in builder._PREVIEW_CONTEXT_DIRECTORIES:
        (root / name).mkdir()
    monkeypatch.setattr(builder, "REPO_ROOT", root)
    return root


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


def test_e2b_template_build_uses_stable_minimal_copy_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preview_repo: Path,
) -> None:
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
    assert not context_root.exists()


def test_staging_excludes_local_data_and_preserves_source_assets(preview_repo: Path) -> None:
    source_files = {
        "logs/__init__.py": b"",
        "logs/logging_config.py": b"# logging source\n",
        "chat-ui/src/components/App.jsx": b"export default () => <main />;\n",
        "web_shell/.env.example": b"VITE_API_URL=\n",
        "web_shell/package-lock.json": b'{"lockfileVersion": 3}',
        "factory_app/app/brand/fonts/BungeeInline-Regular.ttf": b"\x00\x01font\x80\xff",
        "factory_app/app/brand/fonts/BungeeInline-OFL.txt": b"SIL OPEN FONT LICENSE\n",
        "factory_app/app/brand/theme_config.json": b'{"theme": {"font": "Bungee Inline"}}',
    }
    local_files = [
        "web_shell/node_modules/dependency/index.js",
        "chat-ui/node_modules/dependency/.env",
        "factory_app/app/.env",
        "factory_app/app/.env.production",
        "web_shell/.env.local",
        "logs/logs/session.jsonl",
        "logs/agent_outputs/private.json",
        "logs/workflow_converter/private.yaml",
        "factory_app/runtime.log",
        "web_shell/dist/assets/bundle.js",
        "chat-ui/build/bundle.js",
        "web_shell/.vite/deps/react.js",
        "web_shell/.mozaiks-tailwind-sources/platform-ui/private.jsx",
        "web_shell/playwright-report/index.html",
        "web_shell/playwright-report-visual/index.html",
        "web_shell/test-results/screenshot.png",
        "web_shell/coverage/coverage.json",
        "mozaiksai/.venv/Lib/private.py",
        "mozaiksai/__pycache__/runtime.pyc",
        "mozaiksai/.pytest_cache/state",
        "mozaiksai/.mypy_cache/state",
        "mozaiksai/.ruff_cache/state",
        "mozaiksai/package.egg-info/PKG-INFO",
        "factory_app/.local/artifacts/private.json",
        "factory_app/.git/config",
    ]
    for relative_path, content in {
        **source_files, **dict.fromkeys(local_files, b"local data must not be uploaded"),
    }.items():
        path = preview_repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    dockerfile = preview_repo / "Dockerfile.preview"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")

    with builder._stage_preview_context(dockerfile) as context:
        context_root = Path(context)
        for relative_path, content in source_files.items():
            assert (context_root / relative_path).read_bytes() == content
        for relative_path in local_files:
            assert not (context_root / relative_path).exists(), relative_path

    # Staging never deletes the operator's source files or local outputs.
    assert all((preview_repo / path).is_file() for path in local_files)


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
