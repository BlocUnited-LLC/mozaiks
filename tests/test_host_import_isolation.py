"""Host modules must be import-inert; only real startup applies repo defaults.

Importing ``mozaiksai.hosts.studio`` (or platform/runtime) previously ran
``configure_repo_host_defaults("studio")`` at module import time, writing
``PLATFORM_PATH`` and ``MOZAIKS_WORKFLOWS_PATH`` into ``os.environ``. That made
test outcomes depend on import order: any test that imported a host module
silently reconfigured the active app workspace for every later test in the
process. These tests pin the corrected contract:

- a plain host import leaves the process environment byte-identical and does
  not touch ``sys.path``, the working directory, or pre-existing
  ``sys.modules`` entries;
- explicit startup (the registered host bootstrap lifespan) still applies the
  repo defaults before lower-layer startup resolves app/workflow roots;
- caller-provided environment values keep precedence;
- repeated initialization is idempotent;
- isolated environments do not leak one workspace into another.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.hosts import bootstrap as host_bootstrap
from mozaiksai.hosts.bootstrap import (
    configure_repo_host_defaults,
    register_repo_host_bootstrap,
    resolve_repo_host_defaults,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_HOST_ENV_KEYS = (
    "PLATFORM_PATH",
    "MOZAIKS_WORKFLOWS_PATH",
    "MOZAIKS_APP_WORKSPACE_PATH",
    "MOZAIKS_FACTORY_APP_PATH",
)


def _delenv_restoring(monkeypatch, name: str) -> None:
    """Delete ``name`` and guarantee restoration even when it was absent.

    ``monkeypatch.delenv(name, raising=False)`` records nothing for an absent
    key, so a value written afterwards by production bootstrap code would leak
    past teardown (the defect fixed in PR #418). Setting the key first forces
    monkeypatch to record a restore entry.
    """
    monkeypatch.setenv(name, "")
    monkeypatch.delenv(name)


_IMPORT_PROBE = r"""
import json
import os
import sys

# Import the module whose import side effect is load_dotenv() FIRST, so the
# snapshot below isolates what importing the host itself does. Compensating for
# dotenv by pre-seeding the child environment instead would make this test
# vacuous for any developer whose .env pins PLATFORM_PATH or
# MOZAIKS_WORKFLOWS_PATH: the host's writes would land on identical values and
# the diff would stay clean even with the import-time bootstrap restored.
import mozaiksai.core.core_config  # noqa: F401

before_env = dict(os.environ)
before_path = list(sys.path)
before_cwd = os.getcwd()
before_modules = dict(sys.modules)

import mozaiksai.hosts.studio  # noqa: F401

after_env = dict(os.environ)
report = {
    "env_added": sorted(set(after_env) - set(before_env)),
    "env_removed": sorted(set(before_env) - set(after_env)),
    "env_changed": sorted(
        k for k in set(before_env) & set(after_env) if before_env[k] != after_env[k]
    ),
    "sys_path_changed": sys.path != before_path,
    "cwd_changed": os.getcwd() != before_cwd,
    "modules_replaced": sorted(
        name
        for name, module in before_modules.items()
        if sys.modules.get(name) is not module
    ),
    "bootstrap_hosts": sorted(
        getattr(mozaiksai.hosts.studio.app.state, "mozaiks_repo_host_bootstrap_hosts", ())
    ),
    "platform_path_after": os.environ.get("PLATFORM_PATH"),
}
print(json.dumps(report))
"""


def _clean_child_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV_KEYS}
    # pytest-cov's subprocess hook is driven by COV_CORE_* rather than by the
    # outer run's --no-cov, so an inherited value makes every nested
    # interpreter drop a stray .coverage.* file into the repo root that the
    # shard's own coverage run would then combine.
    for key in [k for k in env if k.startswith("COV_CORE_")]:
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return env


def _run_import_probe(extra_env: dict[str, str] | None = None) -> dict:
    env = _clean_child_env(extra_env)
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_plain_studio_import_leaves_process_state_byte_identical() -> None:
    """A plain host import must not mutate env, sys.path, cwd, or loaded modules.

    Runs in a subprocess so the assertion covers the true first import of the
    host stack in a clean interpreter, on both Windows and Linux env semantics.
    """
    report = _run_import_probe()

    assert report["env_added"] == [], report
    assert report["env_removed"] == [], report
    assert report["env_changed"] == [], report
    assert report["sys_path_changed"] is False
    assert report["cwd_changed"] is False
    assert report["modules_replaced"] == [], report


def test_studio_import_registers_startup_bootstrap_without_running_it() -> None:
    report = _run_import_probe()
    assert report["bootstrap_hosts"] == ["studio"]


def test_caller_env_survives_studio_import_unchanged(tmp_path) -> None:
    """A caller's own PLATFORM_PATH must come back out of the import verbatim.

    ``PLATFORM_PATH`` is deliberately set to the *workspace root* rather than
    the bundle directory: the bootstrap normalizes that to ``<workspace>/app``,
    so this asserts a value the import-time bootstrap would visibly rewrite.
    That keeps the check meaningful no matter what the developer's .env holds.
    """
    workspace = tmp_path / "caller-workspace"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "Caller App"}', encoding="utf-8")

    report = _run_import_probe({"PLATFORM_PATH": str(workspace)})
    assert report["env_added"] == [], report
    assert report["env_changed"] == [], report
    assert report["platform_path_after"] == str(workspace), (
        "the Studio import rewrote the caller's PLATFORM_PATH to the resolved "
        f"bundle dir: {report['platform_path_after']}"
    )


def test_resolve_repo_host_defaults_is_pure_and_reads_provided_environ(tmp_path) -> None:
    workspace = tmp_path / "workspace-a"
    app_root = workspace / "app"
    workflows = workspace / "workflows"
    app_root.mkdir(parents=True)
    workflows.mkdir()
    (app_root / "app.json").write_text('{"appName": "A"}', encoding="utf-8")

    env_before = dict(os.environ)
    updates = resolve_repo_host_defaults(
        "platform",
        environ={"MOZAIKS_APP_WORKSPACE_PATH": str(workspace)},
    )

    assert dict(os.environ) == env_before, "resolve_repo_host_defaults mutated os.environ"
    assert Path(updates["PLATFORM_PATH"]) == app_root.resolve()
    assert Path(updates["MOZAIKS_WORKFLOWS_PATH"]) == workflows.resolve()


def test_resolve_repo_host_defaults_caller_overrides_win(tmp_path) -> None:
    workspace = tmp_path / "workspace-b"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "B"}', encoding="utf-8")
    explicit_workflows = tmp_path / "explicit-workflows"
    explicit_workflows.mkdir()

    updates = resolve_repo_host_defaults(
        "studio",
        environ={
            "PLATFORM_PATH": str(workspace),
            "MOZAIKS_WORKFLOWS_PATH": str(explicit_workflows),
        },
    )

    assert Path(updates["PLATFORM_PATH"]) == app_root.resolve()
    assert "MOZAIKS_WORKFLOWS_PATH" not in updates, (
        "an explicit MOZAIKS_WORKFLOWS_PATH must never be overridden by defaults"
    )


def test_resolve_repo_host_defaults_isolated_workspaces_do_not_leak(tmp_path) -> None:
    def _make_workspace(name: str) -> Path:
        workspace = tmp_path / name
        app_root = workspace / "app"
        app_root.mkdir(parents=True)
        (workspace / "workflows").mkdir()
        (app_root / "app.json").write_text(f'{{"appName": "{name}"}}', encoding="utf-8")
        return workspace

    workspace_a = _make_workspace("workspace-a")
    workspace_b = _make_workspace("workspace-b")

    updates_a = resolve_repo_host_defaults("platform", environ={"PLATFORM_PATH": str(workspace_a)})
    updates_b = resolve_repo_host_defaults("platform", environ={"PLATFORM_PATH": str(workspace_b)})

    assert Path(updates_a["PLATFORM_PATH"]) == (workspace_a / "app").resolve()
    assert Path(updates_b["PLATFORM_PATH"]) == (workspace_b / "app").resolve()
    assert Path(updates_a["MOZAIKS_WORKFLOWS_PATH"]) == (workspace_a / "workflows").resolve()
    assert Path(updates_b["MOZAIKS_WORKFLOWS_PATH"]) == (workspace_b / "workflows").resolve()


def test_configure_repo_host_defaults_is_idempotent(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace-idempotent"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (workspace / "workflows").mkdir()
    (app_root / "app.json").write_text('{"appName": "Idempotent"}', encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace))
    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    configure_repo_host_defaults("platform")
    first_pass = {key: os.environ.get(key) for key in _HOST_ENV_KEYS}
    configure_repo_host_defaults("platform")
    second_pass = {key: os.environ.get(key) for key in _HOST_ENV_KEYS}

    assert first_pass == second_pass
    assert Path(first_pass["PLATFORM_PATH"]) == app_root.resolve()


_REAL_STUDIO_BOOT_PROBE = r"""
import json
from fastapi.testclient import TestClient

import mozaiksai.hosts.studio as studio
from mozaiksai.core.workflow.workflow_manager import workflow_manager

at_import = sorted(workflow_manager.get_all_workflow_names())
with TestClient(studio.app):
    after_first = sorted(workflow_manager.get_all_workflow_names())
after_shutdown = sorted(workflow_manager.get_all_workflow_names())
with TestClient(studio.app):
    after_second = sorted(workflow_manager.get_all_workflow_names())

print(json.dumps({
    "at_import": at_import,
    "after_first": after_first,
    "after_shutdown": after_shutdown,
    "after_second": after_second,
}))
"""


def test_real_studio_boot_binds_factory_workflow_catalog_for_external_workspace(tmp_path) -> None:
    """Booting the real Studio app must still load the factory workflow catalog.

    ``mozaiksai.core.workflow.workflow_manager`` builds its global catalog at
    module import, from whatever workflow root the environment named then.
    Studio's defaults deliberately prefer the shared factory workflow root,
    while bare root resolution prefers an app workspace's own ``workflows/``.
    So for ``mozaiks serve <workspace> --host studio`` the import-time catalog
    binds the (usually empty) workspace root, and only the startup bootstrap
    can rebind it to the factory catalog.

    This drives the real ASGI startup of the real Studio app rather than a
    probe, because the defect this guards against is invisible to any test
    that stubs the host or strips the workspace environment.
    """
    workspace = tmp_path / "external-workspace"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (workspace / "workflows").mkdir()
    (app_root / "app.json").write_text('{"appName": "External"}', encoding="utf-8")

    env = _clean_child_env(
        {
            "PLATFORM_PATH": str(app_root),
            "MOZAIKS_APP_WORKSPACE_PATH": str(app_root),
            "AUTH_ENABLED": "false",
            "RATE_LIMIT_ENABLED": "false",
            "MOZAIKS_DATABASE_STARTUP_POLICY": "best_effort",
            "ENV": "test",
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", _REAL_STUDIO_BOOT_PROBE],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report = json.loads(result.stdout.strip().splitlines()[-1])

    assert "AppGenerator" not in report["at_import"], (
        "importing the Studio host resolved the factory catalog at import time — "
        "the import is supposed to be inert"
    )
    assert "AppGenerator" in report["after_first"], (
        "real Studio startup did not bind the factory workflow catalog; "
        f"catalog was {report['after_first']}"
    )
    assert report["after_second"] == report["after_first"], (
        "repeated Studio startup changed the workflow catalog"
    )
    assert report["after_shutdown"] == report["at_import"], (
        "the startup workflow-catalog binding outlived the server; it must be "
        "released on shutdown so one host's root cannot leak into the next"
    )


def test_failed_otel_configuration_stays_retryable_then_settles() -> None:
    """A raising OTel configuration must not mark observability as configured.

    ``_configure_otel_once`` guards against rebuilding a ``TracerProvider`` on
    every ASGI startup. Setting its flag before the call would make a single
    transient failure (an exporter that is not up yet) permanent for the life
    of the process: the next startup would skip configuration and the host
    would run silently untraced with no retry.

    The monkeypatch context is exited inside the test so the restoration of
    both patched attributes is asserted here rather than assumed — this test
    mutates a module-global flag, and leaking it would disable or force OTel
    configuration for every later test in the process.
    """
    attempts: list[str] = []
    original_configure = host_bootstrap.configure_otel_from_env
    original_flag = host_bootstrap._otel_configured

    def _flaky_configure() -> bool:
        attempts.append("call")
        if len(attempts) == 1:
            raise RuntimeError("otel exporter unavailable")
        return True

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(host_bootstrap, "_otel_configured", False)
        patcher.setattr(host_bootstrap, "configure_otel_from_env", _flaky_configure)

        # 1. the first attempt raises, and leaves the flag false
        with pytest.raises(RuntimeError, match="otel exporter unavailable"):
            host_bootstrap._configure_otel_once()
        assert host_bootstrap._otel_configured is False, (
            "a failed configuration marked OTel configured, so no later startup can retry"
        )
        assert attempts == ["call"]

        # 2. a later startup retries and succeeds
        host_bootstrap._configure_otel_once()
        assert host_bootstrap._otel_configured is True
        assert attempts == ["call", "call"]

        # 3. further startups are no-ops
        host_bootstrap._configure_otel_once()
        host_bootstrap._configure_otel_once()
        assert attempts == ["call", "call"], "OTel was reconfigured after it had succeeded"

    # 4. nothing leaks out of the test
    assert host_bootstrap.configure_otel_from_env is original_configure
    assert host_bootstrap._otel_configured is original_flag


def test_registered_bootstrap_runs_before_inner_lifespans(monkeypatch, tmp_path) -> None:
    """The host bootstrap must apply defaults before runtime/platform startup."""
    from mozaiksai.hosts.runtime import register_app_lifespan

    workspace = tmp_path / "workspace-lifespan"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (workspace / "workflows").mkdir()
    (app_root / "app.json").write_text('{"appName": "Lifespan"}', encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace))
    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    seen_at_inner_startup: dict[str, str | None] = {}
    probe_app = FastAPI()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _inner_lifespan(_: FastAPI):
        seen_at_inner_startup["PLATFORM_PATH"] = os.environ.get("PLATFORM_PATH")
        seen_at_inner_startup["MOZAIKS_WORKFLOWS_PATH"] = os.environ.get("MOZAIKS_WORKFLOWS_PATH")
        yield

    register_app_lifespan(probe_app, _inner_lifespan)
    register_repo_host_bootstrap(probe_app, "platform")

    with TestClient(probe_app):
        pass

    assert seen_at_inner_startup["PLATFORM_PATH"] is not None
    assert Path(seen_at_inner_startup["PLATFORM_PATH"]) == app_root.resolve()
    assert seen_at_inner_startup["MOZAIKS_WORKFLOWS_PATH"] is not None
    assert Path(seen_at_inner_startup["MOZAIKS_WORKFLOWS_PATH"]) == (workspace / "workflows").resolve()


def test_register_repo_host_bootstrap_is_idempotent_per_host(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace-once"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "Once"}', encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace))
    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    calls: list[str] = []
    monkeypatch.setattr(
        host_bootstrap,
        "configure_repo_host_defaults",
        lambda host: calls.append(host),
    )

    probe_app = FastAPI()
    register_repo_host_bootstrap(probe_app, "studio")
    register_repo_host_bootstrap(probe_app, "studio")
    # Host names are normalized downstream, so registration must dedupe on the
    # normalized name too — otherwise "Studio" stacks a second bootstrap layer.
    register_repo_host_bootstrap(probe_app, "Studio")

    with TestClient(probe_app):
        pass

    assert calls == ["studio"], "duplicate registration must not stack bootstrap layers"


def test_repeated_startup_of_bootstrapped_app_is_deterministic(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace-repeat"
    app_root = workspace / "app"
    app_root.mkdir(parents=True)
    (workspace / "workflows").mkdir()
    (app_root / "app.json").write_text('{"appName": "Repeat"}', encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace))
    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    probe_app = FastAPI()
    register_repo_host_bootstrap(probe_app, "platform")

    with TestClient(probe_app):
        first = {key: os.environ.get(key) for key in _HOST_ENV_KEYS}
    with TestClient(probe_app):
        second = {key: os.environ.get(key) for key in _HOST_ENV_KEYS}

    assert first == second
    assert Path(first["PLATFORM_PATH"]) == app_root.resolve()
