"""Shared test fixtures and helpers.

Tests that require an active app workspace resolve it explicitly:
``PLATFORM_PATH`` or ``MOZAIKS_APP_WORKSPACE_PATH`` win when set, and the
repo's first-party ``factory_app/app`` bundle is the deterministic fallback in
a repo checkout. The fallback keeps the resolution order-independent — it must
never depend on whether an earlier test happened to import a host module.
Only when neither an env var nor the repo bundle resolves (framework CI
without a bundled app) are workspace-dependent tests skipped.

To run workspace-dependent tests against another workspace locally:

    MOZAIKS_APP_WORKSPACE_PATH=/path/to/mozaiks-app pytest
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import FunctionType, ModuleType

import pytest

# Existing deterministic Factory tests feed source authored in this repository.
# They exercise repair/build contracts using the trusted fixture probes, while
# test_app_acceptance_isolation.py exercises the production Docker boundary.
_TRUSTED_GENERATED_APP_FIXTURE_TESTS = {
    "test_agentgenerator_generate_and_download_collection.py",
    "test_ai_research_workspace_golden_path.py",
    "test_app_auth_generation.py",
    "test_app_build_failure_routing.py",
    "test_app_runtime_smoke.py",
    "test_app_validation_readiness.py",
    "test_app_validation_strategy.py",
    "test_appgenerator_assembly_failures.py",
    "test_appgenerator_bounded_recovery.py",
    "test_appgenerator_download_admission.py",
    "test_appgenerator_export_snapshot.py",
    "test_appgenerator_generate_and_download_persistence.py",
    "test_appgenerator_recovery_resume.py",
    "test_appgenerator_recovery_routing.py",
    "test_appgenerator_revision_baseline.py",
    "test_appgenerator_task_integrity.py",
    "test_appgenerator_wiring_acceptance_boundary.py",
    "test_appplan_materialization_acceptance.py",
    "test_appschema_scoped_manifest.py",
    "test_ask_context_contract_closure.py",
    "test_brownfield_agentgenerator_acceptance.py",
    "test_continuous_deterministic_materialization.py",
    "test_deterministic_page_materialization.py",
    "test_e2e_deterministic_acceptance_gate.py",
    "test_factory_bundle_promotion_to_host_load.py",
    "test_factory_regression_suite.py",
    "test_generated_app_archetype_matrix.py",
    "test_generated_app_candidate_validation.py",
    "test_generated_app_functional_acceptance.py",
    "test_managed_capability_artifact_replay.py",
    "test_materialized_bundle_production_runtime.py",
    "test_offline_factory_build_sequence_smoke.py",
    "test_offline_generated_build_acceptance.py",
    "test_run_termination.py",
    "test_smoke_appgenerator_live_acceptance.py",
    "test_smoke_appgenerator_live_subscription.py",
}


@pytest.fixture(autouse=True)
def _trusted_generated_app_fixture_probe(monkeypatch, request):
    if request.node.path.name not in _TRUSTED_GENERATED_APP_FIXTURE_TESTS:
        return

    from factory_app.workflows.AppGenerator.tools import app_validation
    validation_name = app_validation.__name__
    validation_globals = {id(vars(app_validation)): vars(app_validation)}

    def include_validation_reference(value):
        if isinstance(value, ModuleType) and value.__name__ == validation_name:
            validation_globals[id(vars(value))] = vars(value)
        elif isinstance(value, FunctionType) and value.__module__ == validation_name:
            validation_globals[id(value.__globals__)] = value.__globals__

    test_namespace = vars(request.node.module)
    for value in test_namespace.values():
        include_validation_reference(value)
        # Workflow loading evicts cached tool modules. A test or script that
        # imported a gate before that reload still calls its original globals.
        if isinstance(value, FunctionType) and value.__module__.startswith(("tests.", "scripts.")):
            for imported in value.__globals__.values():
                include_validation_reference(imported)
        elif isinstance(value, ModuleType) and value.__name__.startswith(("tests.", "scripts.")):
            for imported in vars(value).values():
                include_validation_reference(imported)

    for globals_dict in validation_globals.values():
        smoke_module = globals_dict["app_runtime_smoke"]

        async def trusted_fixture_load(generated_files, *, _globals=globals_dict):
            from factory_app.workflows.AppGenerator.tools.app_runtime_load_probe import (
                probe_app_root,
            )

            with tempfile.TemporaryDirectory(prefix="mozaiks-test-runtime-load-") as temporary:
                app_root = Path(temporary) / "app"
                _globals["_write_files_to_dir"](app_root, generated_files)
                return await probe_app_root(app_root)

        async def trusted_fixture_smoke(generated_files, *, _globals=globals_dict, _smoke=smoke_module):
            with tempfile.TemporaryDirectory(prefix="mozaiks-test-runtime-smoke-") as temporary:
                app_root = Path(temporary) / "app"
                _globals["_write_files_to_dir"](app_root, generated_files)
                return await _smoke.run_app_runtime_smoke(
                    app_root, mongo_uri=_smoke.resolve_smoke_mongo_uri(),
                )

        monkeypatch.setitem(globals_dict, "_app_runtime_load_result", trusted_fixture_load)
        monkeypatch.setitem(globals_dict, "_app_runtime_smoke_result", trusted_fixture_smoke)
        monkeypatch.setattr(smoke_module, "resolve_smoke_mongo_uri", lambda: None)


def _repo_factory_app_bundle() -> Path:
    """The first-party factory app bundle, honoring MOZAIKS_FACTORY_APP_PATH.

    The host resolves the factory root through ``mozaiksai.resources``, which
    consults that env var; matching it here keeps tests and host agreeing about
    the active workspace in relocated or installed-package checkouts.
    """
    override = os.environ.get("MOZAIKS_FACTORY_APP_PATH", "").strip()
    if override:
        return (Path(override) / "app").resolve()
    return (Path(__file__).resolve().parents[1] / "factory_app" / "app").resolve()


def _resolve_active_app_root() -> Path | None:
    """Return the active app root: env vars first, then the repo factory bundle."""
    platform_path = os.environ.get("PLATFORM_PATH", "").strip()
    if platform_path:
        candidate = Path(platform_path)
        if (candidate / "app.json").exists():
            return candidate.resolve()
        nested = candidate / "app"
        if (nested / "app.json").exists():
            return nested.resolve()
        return candidate.resolve()

    workspace_path = os.environ.get("MOZAIKS_APP_WORKSPACE_PATH", "").strip()
    if workspace_path:
        candidate = Path(workspace_path)
        nested = candidate / "app"
        if (nested / "app.json").exists():
            return nested.resolve()
        if (candidate / "app.json").exists():
            return candidate.resolve()

    factory_bundle = _repo_factory_app_bundle()
    if (factory_bundle / "app.json").exists():
        return factory_bundle

    return None


def active_app_root() -> Path:
    """Return the active app root. Skips the test if not configured."""
    root = _resolve_active_app_root()
    if root is None:
        pytest.skip(
            "No active app workspace configured. "
            "Set MOZAIKS_APP_WORKSPACE_PATH or PLATFORM_PATH to run this test."
        )
    return root


@pytest.fixture
def app_root() -> Path:
    """Pytest fixture: active app workspace root. Skips if not configured."""
    return active_app_root()


@pytest.fixture(autouse=True)
def _development_access_by_default(monkeypatch):
    """Serve in-process test clients with development access unless a test says otherwise.

    With authentication off a host grants development access only to requests
    from this machine (``AUTH_ANON_ACCESS=local``), and Starlette's TestClient
    is not a network peer at all (its client is ``"testclient"``). Tests that
    do not configure auth therefore run as CI does (``AUTH_ENABLED=false``)
    with ``AUTH_ANON_ACCESS=open``. Values already in the environment win, and
    tests of auth behaviour set or delete these variables themselves.
    """
    if "AUTH_ENABLED" not in os.environ and "AUTH_PROVIDER" not in os.environ:
        monkeypatch.setenv("AUTH_ENABLED", "false")
    if "AUTH_ANON_ACCESS" not in os.environ:
        monkeypatch.setenv("AUTH_ANON_ACCESS", "open")
    yield


@pytest.fixture(autouse=True)
def _isolate_chat_lock_state():
    """Reset the chat execution lock's process-level state before each test.

    The lock module keeps process-global state (configured mode, held local
    locks, the lease registry). A test that runs host startup pins `required`
    mode, and an abandoned background task can leave a local lock held for a
    reused chat id — either would silently change the behavior of every later
    test. Entering each test with clean lock state keeps tests
    order-independent.
    """
    from mozaiksai.core.runtime.persistence.distributed_lock import reset_chat_lock_state

    reset_chat_lock_state()
    yield


def _unstart_shared_host_app() -> None:
    """Drop the built middleware stack of the process-wide host app, if any.

    The host is looked up, never imported, so this is inert for a process that
    has not loaded one.
    """
    runtime_host = sys.modules.get("mozaiksai.hosts.runtime")
    host_app = getattr(runtime_host, "app", None)
    if host_app is not None:
        host_app.middleware_stack = None


@pytest.fixture(autouse=True)
def _isolate_shared_host_app_start():
    """Enter and leave every test with the process-wide host app not started.

    ``mozaiksai.hosts.runtime`` owns one module-level FastAPI app, and the
    platform host composes itself onto that same object when it is first
    imported, registering an HTTP middleware on it. Starlette builds the
    middleware stack on the app's first ASGI call and refuses
    ``add_middleware`` from then on. A test that served a request through the
    runtime app before anything in the process had imported the platform host
    therefore made that import raise ``RuntimeError: Cannot add middleware
    after an application has started`` in every later test, and whether that
    happened depended only on which test files shared a CI shard.

    The built stack is a cache of the registered middleware, so dropping it is
    a complete un-start: the next ASGI call rebuilds it from whatever is
    registered by then. Dropping it on the way in as well covers a stack built
    outside a test body, such as a wider-scoped fixture's teardown.
    """
    _unstart_shared_host_app()
    yield
    _unstart_shared_host_app()



@pytest.fixture(autouse=True)
def _no_runtime_smoke_database(monkeypatch):
    """Acceptance-gate tests see no runtime smoke database unless they opt in.

    CI shards configure a real MONGO_URI, so without this every test that runs
    the app-bundle acceptance gate would boot its fixture bundle against that
    Mongo. The gate then reports ``skipped: no database configured``. Tests of
    the runtime smoke itself pass the URI explicitly or patch
    ``resolve_smoke_mongo_uri`` back (see tests/test_app_runtime_smoke.py).
    """
    # app_runtime_smoke imports no host configuration, so this stays env-inert.
    from factory_app.workflows.AppGenerator.tools import app_runtime_smoke

    monkeypatch.setattr(app_runtime_smoke, "resolve_smoke_mongo_uri", lambda: None)
    yield
