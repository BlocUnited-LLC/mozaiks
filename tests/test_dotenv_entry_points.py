"""A ``.env`` is read only at process entry points, never by importing Mozaiks.

Issue #778: ``mozaiksai.core.core_config`` called ``load_dotenv()`` at import.
With no path, python-dotenv searches upward from the calling file (or from the
working directory under a REPL, debugger, or coverage tracer) and sets every
variable missing from the process. Installed under a project, that is the
project's ``.env``. A release driver unset ``MOZAIKS_FACTORY_APP_PATH`` on
purpose, a lazy ``core_config`` import put it back, and the run built from a
stale checkout.

These tests pin the replacement contract:

- importing the runtime, the hosts, and the CLI never changes ``os.environ``,
  whichever discovery route a parent ``.env`` would have been found by;
- CLI commands load the ``.env`` of the workspace they operate on, once, and
  never override a variable the process already has.

Every test builds its own ``.env`` and workspace under ``tmp_path``, so none
depends on (or can read) a developer's ``.env``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_SENTINELS = {
    "MOZAIKS_DOTENV_PROBE_SENTINEL": "from-parent-dotenv",
    "MOZAIKS_FACTORY_APP_PATH": "",  # filled with a stale directory per test
}

_IMPORT_PROBE = r"""
import json
import os
import sys

before = dict(os.environ)

import mozaiksai  # noqa: E402,F401
import mozaiksai.core.core_config  # noqa: E402,F401
import mozaiksai.hosts.studio  # noqa: E402,F401
import mozaiks_cli.main  # noqa: E402,F401

after = dict(os.environ)
print(json.dumps({
    "added": sorted(set(after) - set(before)),
    "removed": sorted(set(before) - set(after)),
    "changed": sorted(k for k in set(before) & set(after) if before[k] != after[k]),
    "origins": {
        name: sys.modules[name].__file__
        for name in ("mozaiksai", "mozaiksai.core.core_config", "mozaiks_cli", "logs")
    },
}))
"""


def _restore_after(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Remove ``names`` now and restore their original state at teardown.

    ``monkeypatch.delenv(name, raising=False)`` records nothing for an absent
    key, so a value a loader writes afterwards would leak past teardown.
    Setting the key first forces a restore entry.
    """
    for name in names:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def _write_dotenv(directory: Path, values: dict[str, str]) -> Path:
    env_file = directory / ".env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return env_file


def test_importing_mozaiks_never_reads_a_parent_dotenv(tmp_path: Path) -> None:
    """Install Mozaiks under a project with a ``.env`` and import it from below.

    The packages are copied into ``project/site-packages`` so the frame-based
    search starts inside the project, and the probe runs with its working
    directory inside the project too, so the working-directory search would
    also reach the ``.env``. The import must not see it either way.
    """
    project = tmp_path / "project"
    site_packages = project / "site-packages"
    work_dir = project / "work"
    stale_factory = tmp_path / "stale-checkout" / "factory_app"
    for directory in (site_packages, work_dir, stale_factory):
        directory.mkdir(parents=True)
    for package in ("mozaiksai", "logs", "mozaiks_cli"):
        shutil.copytree(
            REPO_ROOT / package,
            site_packages / package,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    sentinels = {**_SENTINELS, "MOZAIKS_FACTORY_APP_PATH": str(stale_factory)}
    _write_dotenv(project, sentinels)
    probe = work_dir / "probe.py"
    probe.write_text(_IMPORT_PROBE, encoding="utf-8")

    env = {
        key: value
        for key, value in os.environ.items()
        # A disabled dotenv would make this test vacuous; coverage's subprocess
        # hook would switch python-dotenv to the working-directory search only.
        if key not in sentinels and key != "PYTHON_DOTENV_DISABLED" and not key.startswith("COV_CORE_")
    }
    # The copies come first; factory_app and the other top-level packages the
    # host imports still resolve from the repository.
    env["PYTHONPATH"] = os.pathsep.join([str(site_packages), str(REPO_ROOT)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(work_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report = json.loads(result.stdout.strip().splitlines()[-1])

    for name, origin in report["origins"].items():
        assert Path(origin).resolve().is_relative_to(site_packages.resolve()), (
            f"{name} was imported from {origin}, not the copy under the project; "
            "the probe would not exercise the installed-package search"
        )
    assert report["added"] == [], (
        f"importing Mozaiks loaded {report['added']} from the project's .env"
    )
    assert report["removed"] == [], report
    assert report["changed"] == [], report


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------


def test_load_workspace_dotenv_reads_only_the_workspace_file_without_overriding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mozaiks_cli.workspace import load_workspace_dotenv

    workspace = tmp_path / "parent" / "workspace"
    workspace.mkdir(parents=True)
    _write_dotenv(tmp_path / "parent", {"MOZAIKS_DOTENV_PARENT_ONLY": "from-parent"})
    env_file = _write_dotenv(
        workspace,
        {"MOZAIKS_DOTENV_WORKSPACE": "from-workspace", "MOZAIKS_DOTENV_PRESET": "from-workspace"},
    )
    _restore_after(
        monkeypatch, "MOZAIKS_DOTENV_PARENT_ONLY", "MOZAIKS_DOTENV_WORKSPACE", "MOZAIKS_DOTENV_PRESET"
    )
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setenv("MOZAIKS_DOTENV_PRESET", "from-process")

    assert load_workspace_dotenv(workspace) == env_file.resolve()

    assert os.environ["MOZAIKS_DOTENV_WORKSPACE"] == "from-workspace"
    assert os.environ["MOZAIKS_DOTENV_PRESET"] == "from-process", "the workspace .env overrode the process"
    assert "MOZAIKS_DOTENV_PARENT_ONLY" not in os.environ, "a parent directory's .env was read"


def test_load_workspace_dotenv_without_a_file_changes_nothing(tmp_path: Path) -> None:
    from mozaiks_cli.workspace import load_workspace_dotenv

    _write_dotenv(tmp_path, {"MOZAIKS_DOTENV_PARENT_ONLY": "from-parent"})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = dict(os.environ)

    assert load_workspace_dotenv(workspace) is None
    assert dict(os.environ) == before


def test_serve_hands_the_workspace_dotenv_to_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mozaiks serve`` loads the workspace .env before the host is imported."""
    from mozaiks_cli.commands import serve

    workspace = tmp_path / "workspace"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "app.json").write_text('{"appName": "Serve"}', encoding="utf-8")
    _write_dotenv(
        workspace,
        {"MOZAIKS_DOTENV_WORKSPACE": "from-workspace", "MOZAIKS_DOTENV_PRESET": "from-workspace"},
    )
    _restore_after(
        monkeypatch,
        "MOZAIKS_DOTENV_WORKSPACE",
        "MOZAIKS_DOTENV_PRESET",
        "PLATFORM_PATH",
        "MOZAIKS_APP_WORKSPACE_PATH",
        "MOZAIKS_HOST",
    )
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setenv("MOZAIKS_DOTENV_PRESET", "from-process")

    seen_by_host: dict[str, str | None] = {}
    fake_uvicorn = ModuleType("uvicorn")

    def _fake_run(app_module, **_kwargs):
        seen_by_host["app_module"] = app_module
        for name in ("MOZAIKS_DOTENV_WORKSPACE", "MOZAIKS_DOTENV_PRESET"):
            seen_by_host[name] = os.environ.get(name)

    fake_uvicorn.run = _fake_run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(
        "mozaiks_cli.commands.sync_agent_guidance.auto_sync_agent_guidance", lambda _workspace: None
    )

    serve.run(Namespace(workspace=str(workspace), host="platform", port=8000, listen="127.0.0.1", reload=False))

    assert seen_by_host == {
        "app_module": "mozaiksai.hosts.platform:app",
        "MOZAIKS_DOTENV_WORKSPACE": "from-workspace",
        "MOZAIKS_DOTENV_PRESET": "from-process",
    }


def test_gen_reads_provider_keys_from_the_current_workspace_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A key that exists only in ./.env passes gen's API-key check."""
    from mozaiks_cli.commands import gen

    keys = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_OPENAI_API_KEY", "MONGO_URI")
    _restore_after(monkeypatch, *keys)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    _write_dotenv(tmp_path, {"OPENAI_API_KEY": "sk-from-workspace-dotenv", "MONGO_URI": "mongodb://dotenv-only"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gen, "_init_logging", lambda: None)
    monkeypatch.setattr(gen, "_find_generator_source", lambda: None)
    monkeypatch.setattr(gen, "RICH_AVAILABLE", False)
    monkeypatch.setattr(gen, "console", None)

    rc = gen.run(
        Namespace(
            mode="workflow",
            prompt="Build a customer support triage workflow with escalation rules",
            output=str(tmp_path / "generated"),
            validation_strategy="skip",
            allow_interactive=False,
        )
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "No LLM API key found" not in out
    assert "MONGO_URI is not set" not in out
    assert "Could not find AgentGenerator workflow" in out
    assert os.environ["OPENAI_API_KEY"] == "sk-from-workspace-dotenv"


def test_migrations_status_reads_the_current_workspace_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mozaiks_cli.commands import migrations
    from mozaiks_cli.main import create_parser

    _restore_after(monkeypatch, "MONGO_URI")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    _write_dotenv(tmp_path, {"MONGO_URI": "mongodb://dotenv-only/migrations"})
    monkeypatch.chdir(tmp_path)
    seen: dict[str, str | None] = {}

    async def _fake_report(**_kwargs):
        seen["MONGO_URI"] = os.environ.get("MONGO_URI")
        return {"summary": {}, "items": [], "has_blockers": False, "has_unknown_statuses": False}

    monkeypatch.setattr(migrations, "get_migration_health_report", _fake_report)

    assert migrations.run(create_parser().parse_args(["migrations", "status", "--json"])) == 0
    assert seen == {"MONGO_URI": "mongodb://dotenv-only/migrations"}


def test_context_index_reads_the_indexed_workspace_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mozaiks_cli.commands import context

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _restore_after(monkeypatch, "MONGO_URI")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    _write_dotenv(workspace, {"MONGO_URI": "mongodb://dotenv-only/context"})
    seen: dict[str, str | None] = {}

    class _Stop(Exception):
        pass

    async def _fake_index(**_kwargs):
        seen["MONGO_URI"] = os.environ.get("MONGO_URI")
        raise _Stop

    monkeypatch.setattr(context, "index_workspace_app_intelligence", _fake_index)

    with pytest.raises(_Stop):
        context.run(Namespace(context_action="index", app_id="app_1", workspace=str(workspace)))
    assert seen == {"MONGO_URI": "mongodb://dotenv-only/context"}
