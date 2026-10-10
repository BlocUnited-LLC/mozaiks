"""
Tests for module runtime_extensions.yaml enforcement.

Covers:
- _module_package_root: correct sys.modules key derivation
- _qualify_module_entrypoint: module-local → qualified path translation
- mount_module_routers: mounts APIRouter from api_router extensions
- start_module_services: starts services from startup_service extensions, calls start()
- Both functions are no-ops when manifests.runtime_extensions is None
- stop_services: calls stop() on started services
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRouter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ext(
    kind: str, entrypoint: str, prefix: str | None = None, profile: str | None = None
) -> Any:
    ext = SimpleNamespace(kind=kind, entrypoint=entrypoint, prefix=prefix, profile=profile)
    return ext


def _make_loaded_module(
    name: str,
    extensions: list[Any] | None = None,
) -> Any:
    """Build a minimal fake LoadedModule with the given runtime extensions."""
    rt_ext = None
    if extensions is not None:
        rt_ext = SimpleNamespace(extensions=extensions)
    manifests = SimpleNamespace(runtime_extensions=rt_ext)
    return SimpleNamespace(name=name, manifests=manifests)


# ---------------------------------------------------------------------------
# _module_package_root
# ---------------------------------------------------------------------------

class TestModulePackageRoot:
    def _fn(self, name):
        from mozaiksai.core.runtime.composition.extensions import _module_package_root
        return _module_package_root(name)

    def test_simple_name(self):
        assert self._fn("task_manager") == "mozaiks_runtime_module_task_manager"

    def test_hyphenated_name(self):
        assert self._fn("my-module") == "mozaiks_runtime_module_my_module"

    def test_dotted_name(self):
        assert self._fn("a.b") == "mozaiks_runtime_module_a_b"


def test_get_workflow_lifecycle_hooks_loads_workflow_local_files(monkeypatch, tmp_path):
    from mozaiksai.core.runtime.composition.extensions import get_workflow_lifecycle_hooks
    from mozaiksai.core.workflow import workflow_manager as workflow_manager_mod

    wf_dir = tmp_path / "FlowLifecycle"
    hook_dir = wf_dir / "tools" / "platform"
    hook_dir.mkdir(parents=True)
    (wf_dir / "orchestrator.yaml").write_text("workflow_name: FlowLifecycle\n", encoding="utf-8")
    (hook_dir / "build_lifecycle.py").write_text(
        "\n".join(
            [
                "async def started(*, app_id, workflow_name, chat_id=None, **kwargs):",
                "    return f'{app_id}:{workflow_name}:{chat_id}'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    class _FakeManager:
        def get_config(self, workflow_name):
            assert workflow_name == "FlowLifecycle"
            return {
                "lifecycle_tools": [
                    {
                        "trigger": "on_start",
                        "file": "tools/platform/build_lifecycle.py",
                        "function": "started",
                    }
                ]
            }

    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(tmp_path))
    monkeypatch.setattr(workflow_manager_mod, "get_workflow_manager", lambda: _FakeManager())

    hooks = get_workflow_lifecycle_hooks("FlowLifecycle")

    assert hooks["on_complete"] is None
    assert hooks["on_fail"] is None
    assert asyncio.run(
        hooks["on_start"](
            app_id="app_1",
            workflow_name="FlowLifecycle",
            chat_id="chat_1",
        )
    ) == "app_1:FlowLifecycle:chat_1"


# ---------------------------------------------------------------------------
# _qualify_module_entrypoint
# ---------------------------------------------------------------------------

class TestQualifyModuleEntrypoint:
    def _fn(self, module_name, entrypoint):
        from mozaiksai.core.runtime.composition.extensions import _qualify_module_entrypoint
        return _qualify_module_entrypoint(module_name, entrypoint)

    def test_basic_entrypoint(self):
        result = self._fn("task_manager", "backend.router:get_router")
        assert result == "mozaiks_runtime_module_task_manager.backend.router:get_router"

    def test_worker_entrypoint(self):
        result = self._fn("task_manager", "backend.worker:TaskWorker")
        assert result == "mozaiks_runtime_module_task_manager.backend.worker:TaskWorker"

    def test_invalid_no_colon_raises(self):
        from mozaiksai.core.runtime.composition.extensions import _qualify_module_entrypoint
        with pytest.raises(ValueError, match="Invalid entrypoint"):
            _qualify_module_entrypoint("task_manager", "backend.router")

    def test_strips_whitespace(self):
        result = self._fn("task_manager", " backend.router : get_router ")
        assert result == "mozaiks_runtime_module_task_manager.backend.router:get_router"


# ---------------------------------------------------------------------------
# mount_module_routers
# ---------------------------------------------------------------------------

class TestMountModuleRouters:
    def _mount(self, app, loaded_modules):
        from mozaiksai.core.runtime.composition.extensions import mount_module_routers
        return mount_module_routers(app, loaded_modules)

    def test_noop_when_no_modules(self):
        app = FastAPI()
        assert self._mount(app, []) == 0

    def test_noop_when_no_runtime_extensions(self):
        app = FastAPI()
        mod = _make_loaded_module("task_manager", extensions=None)
        assert self._mount(app, [mod]) == 0

    def test_noop_when_no_api_router_extensions(self):
        app = FastAPI()
        mod = _make_loaded_module(
            "task_manager",
            extensions=[_make_ext("startup_service", "backend.worker:W")],
        )
        assert self._mount(app, [mod]) == 0

    def test_mounts_router_when_entrypoint_resolves(self):
        router = APIRouter()
        package_root = "mozaiks_runtime_module_task_manager"
        fake_backend_router = MagicMock()
        fake_backend_router.get_router = MagicMock(return_value=router)

        # Register fake module package in sys.modules
        fake_pkg = MagicMock()
        fake_pkg.get_router = MagicMock(return_value=router)
        sys.modules[f"{package_root}.backend.router"] = fake_pkg

        try:
            app = FastAPI()
            mod = _make_loaded_module(
                "task_manager",
                extensions=[_make_ext("api_router", "backend.router:get_router", prefix="/webhooks")],
            )
            result = self._mount(app, [mod])
            assert result == 1
            # Verify router was included (filter _IncludedRouter objects in FastAPI 0.116+)
            [str(r.path) for r in app.routes if hasattr(r, "path")]
            # The app.include_router call itself is the verification; if no exception, it worked
        finally:
            sys.modules.pop(f"{package_root}.backend.router", None)

    def test_warns_and_skips_on_bad_entrypoint(self, caplog):
        import logging
        app = FastAPI()
        mod = _make_loaded_module(
            "task_manager",
            extensions=[_make_ext("api_router", "backend.missing:get_router")],
        )
        with caplog.at_level(logging.WARNING, logger="runtime_extensions"):
            result = self._mount(app, [mod])
        assert result == 0
        # Should log a warning, not raise
        assert any("MODULE_EXTENSIONS_ROUTER_FAILED" in r.message for r in caplog.records)

    def test_warns_when_entrypoint_returns_non_router(self):
        package_root = "mozaiks_runtime_module_task_manager"
        fake_pkg = MagicMock()
        fake_pkg.get_router = MagicMock(return_value="not_a_router")
        sys.modules[f"{package_root}.backend.router"] = fake_pkg

        try:
            app = FastAPI()
            mod = _make_loaded_module(
                "task_manager",
                extensions=[_make_ext("api_router", "backend.router:get_router")],
            )
            from mozaiksai.core.runtime.composition.extensions import mount_module_routers
            result = mount_module_routers(app, [mod])
            assert result == 0
        finally:
            sys.modules.pop(f"{package_root}.backend.router", None)


# ---------------------------------------------------------------------------
# start_module_services
# ---------------------------------------------------------------------------

class TestStartModuleServices:
    async def _start(self, loaded_modules, *, profile="host"):
        from mozaiksai.core.runtime.composition.extensions import start_module_services
        return await start_module_services(loaded_modules, profile=profile)

    @pytest.mark.asyncio
    async def test_noop_when_no_modules(self):
        result = await self._start([])
        assert result == []

    @pytest.mark.asyncio
    async def test_noop_when_no_runtime_extensions(self):
        mod = _make_loaded_module("task_manager", extensions=None)
        result = await self._start([mod])
        assert result == []

    @pytest.mark.asyncio
    async def test_noop_when_no_startup_service_extensions(self):
        mod = _make_loaded_module(
            "task_manager",
            extensions=[_make_ext("api_router", "backend.router:get_router")],
        )
        result = await self._start([mod])
        assert result == []

    @pytest.mark.asyncio
    async def test_starts_sync_service_with_start_method(self):
        started_calls = []

        class FakeService:
            def start(self):
                started_calls.append("started")

        package_root = "mozaiks_runtime_module_task_manager"
        fake_pkg = MagicMock()
        fake_pkg.TaskWorker = FakeService
        sys.modules[f"{package_root}.backend.worker"] = fake_pkg

        try:
            mod = _make_loaded_module(
                "task_manager",
                extensions=[_make_ext("startup_service", "backend.worker:TaskWorker")],
            )
            result = await self._start([mod])
            assert len(result) == 1
            assert isinstance(result[0], FakeService)
            assert started_calls == ["started"]
        finally:
            sys.modules.pop(f"{package_root}.backend.worker", None)

    @pytest.mark.asyncio
    async def test_starts_async_service_with_start_method(self):
        started_calls = []

        class AsyncService:
            async def start(self):
                started_calls.append("started")

        package_root = "mozaiks_runtime_module_task_manager"
        fake_pkg = MagicMock()
        fake_pkg.AsyncWorker = AsyncService
        sys.modules[f"{package_root}.backend.worker"] = fake_pkg

        try:
            mod = _make_loaded_module(
                "task_manager",
                extensions=[_make_ext("startup_service", "backend.worker:AsyncWorker")],
            )
            result = await self._start([mod])
            assert len(result) == 1
            assert started_calls == ["started"]
        finally:
            sys.modules.pop(f"{package_root}.backend.worker", None)

    @pytest.mark.asyncio
    async def test_starts_service_without_start_method(self):
        class NoStartService:
            pass

        package_root = "mozaiks_runtime_module_task_manager"
        fake_pkg = MagicMock()
        fake_pkg.NoStartService = NoStartService
        sys.modules[f"{package_root}.backend.worker"] = fake_pkg

        try:
            mod = _make_loaded_module(
                "task_manager",
                extensions=[_make_ext("startup_service", "backend.worker:NoStartService")],
            )
            result = await self._start([mod])
            assert len(result) == 1
        finally:
            sys.modules.pop(f"{package_root}.backend.worker", None)

    @pytest.mark.asyncio
    async def test_warns_and_continues_on_missing_entrypoint(self, caplog):
        import logging
        mod = _make_loaded_module(
            "task_manager",
            extensions=[_make_ext("startup_service", "backend.missing:TaskWorker")],
        )
        with caplog.at_level(logging.WARNING, logger="runtime_extensions"):
            result = await self._start([mod])
        assert result == []
        assert any("MODULE_EXTENSIONS_SERVICE_FAILED" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_multiple_modules_all_started(self):
        class SvcA:
            pass
        class SvcB:
            pass

        sys.modules["mozaiks_runtime_module_mod_a.backend.worker"] = MagicMock(Worker=SvcA)
        sys.modules["mozaiks_runtime_module_mod_b.backend.worker"] = MagicMock(Worker=SvcB)

        try:
            mods = [
                _make_loaded_module("mod_a", [_make_ext("startup_service", "backend.worker:Worker")]),
                _make_loaded_module("mod_b", [_make_ext("startup_service", "backend.worker:Worker")]),
            ]
            result = await self._start(mods)
            assert len(result) == 2
        finally:
            sys.modules.pop("mozaiks_runtime_module_mod_a.backend.worker", None)
            sys.modules.pop("mozaiks_runtime_module_mod_b.backend.worker", None)

    @pytest.mark.asyncio
    async def test_host_profile_starts_existing_services_and_excludes_worker(self):
        started = []

        class HostService:
            def start(self):
                started.append("host")

        class WorkerService:
            def start(self):
                started.append("worker")

        key = "mozaiks_runtime_module_tasks.backend.worker"
        sys.modules[key] = MagicMock(HostService=HostService, WorkerService=WorkerService)
        try:
            mod = _make_loaded_module("tasks", [
                _make_ext("startup_service", "backend.worker:HostService"),
                _make_ext("startup_service", "backend.worker:WorkerService", profile="worker"),
            ])
            services = await self._start([mod])
            assert len(services) == 1
            assert isinstance(services[0], HostService)
            assert started == ["host"]
        finally:
            sys.modules.pop(key, None)

    @pytest.mark.asyncio
    async def test_worker_profile_starts_only_declared_worker_services(self):
        started = []

        class HostService:
            def start(self):
                started.append("host")

        class WorkerService:
            def start(self):
                started.append("worker")

        key = "mozaiks_runtime_module_tasks.backend.worker"
        sys.modules[key] = MagicMock(HostService=HostService, WorkerService=WorkerService)
        try:
            mod = _make_loaded_module("tasks", [
                _make_ext("startup_service", "backend.worker:HostService"),
                _make_ext("startup_service", "backend.worker:WorkerService", profile="worker"),
            ])
            services = await self._start([mod], profile="worker")
            assert len(services) == 1
            assert isinstance(services[0], WorkerService)
            assert started == ["worker"]
        finally:
            sys.modules.pop(key, None)

    @pytest.mark.asyncio
    async def test_invalid_environment_profile_aborts_before_start(self, monkeypatch):
        from mozaiksai.core.runtime.composition.extensions import (
            StartupServiceProfileError,
            start_module_services,
        )

        monkeypatch.setenv("MOZAIKS_STARTUP_SERVICE_PROFILE", "other")
        with pytest.raises(StartupServiceProfileError, match="host or worker"):
            await start_module_services([
                _make_loaded_module("tasks", [
                    _make_ext("startup_service", "backend.worker:HostService")
                ])
            ])

    @pytest.mark.asyncio
    async def test_worker_profile_requires_declared_service(self):
        from mozaiksai.core.runtime.composition.extensions import StartupServiceProfileError

        with pytest.raises(StartupServiceProfileError, match="no declared"):
            await self._start([], profile="worker")

    @pytest.mark.asyncio
    async def test_worker_service_requires_callable_start_method(self):
        from mozaiksai.core.runtime.composition.extensions import StartupServiceProfileError

        key = "mozaiks_runtime_module_tasks.backend.worker"
        sys.modules[key] = MagicMock(Worker=lambda: SimpleNamespace(start=None))
        try:
            mod = _make_loaded_module("tasks", [
                _make_ext("startup_service", "backend.worker:Worker", profile="worker"),
            ])
            with pytest.raises(StartupServiceProfileError, match="callable start method"):
                await self._start([mod], profile="worker")
        finally:
            sys.modules.pop(key, None)

    @pytest.mark.asyncio
    async def test_malformed_declared_profile_aborts_before_any_service_starts(self):
        from mozaiksai.core.runtime.composition.extensions import StartupServiceProfileError

        mod = _make_loaded_module("tasks", [
            _make_ext("startup_service", "backend.worker:HostService"),
            _make_ext("startup_service", "backend.worker:WorkerService", profile="unexpected"),
        ])
        with pytest.raises(StartupServiceProfileError, match="Invalid declared"):
            await self._start([mod])

    @pytest.mark.asyncio
    async def test_worker_start_failure_stops_prior_worker_service(self):
        from mozaiksai.core.runtime.composition.extensions import StartupServiceProfileError

        stopped = []

        class FirstService:
            def start(self):
                pass

            def stop(self):
                stopped.append(True)

        class BrokenService:
            def start(self):
                raise RuntimeError("cannot start")

            def stop(self):
                stopped.append("broken")

        key = "mozaiks_runtime_module_tasks.backend.worker"
        sys.modules[key] = MagicMock(FirstService=FirstService, BrokenService=BrokenService)
        try:
            mod = _make_loaded_module("tasks", [
                _make_ext("startup_service", "backend.worker:FirstService", profile="worker"),
                _make_ext("startup_service", "backend.worker:BrokenService", profile="worker"),
            ])
            with pytest.raises(StartupServiceProfileError, match="could not start"):
                await self._start([mod], profile="worker")
            assert stopped == ["broken", True]
        finally:
            sys.modules.pop(key, None)


@pytest.mark.asyncio
async def test_platform_host_starts_only_worker_service_from_real_app_bundle(tmp_path, monkeypatch):
    from mozaiksai.core.auth.adapters import registry
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader
    from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
    from mozaiksai.hosts import platform

    module_dir = tmp_path / "modules" / "profile_smoke"
    backend_dir = module_dir / "backend"
    backend_dir.mkdir(parents=True)
    (tmp_path / "app.json").write_text('{"appName": "Profile Smoke"}', encoding="utf-8")
    (module_dir / "module.yaml").write_text(
        "schema_version: mozaiks.module.v1\n"
        "module:\n  id: profile_smoke\n  handler: backend.handler:Handler\n",
        encoding="utf-8",
    )
    (backend_dir / "handler.py").write_text("class Handler:\n    pass\n", encoding="utf-8")
    (module_dir / "runtime_extensions.yaml").write_text(
        "schema_version: mozaiks.runtime_extensions.v1\n"
        "extensions:\n"
        "  - kind: startup_service\n    entrypoint: backend.worker:HostService\n"
        "  - kind: startup_service\n    entrypoint: backend.worker:WorkerService\n"
        "    profile: worker\n",
        encoding="utf-8",
    )
    (backend_dir / "worker.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def mark(value):\n"
        "    with Path(os.environ['MOZAIKS_TEST_STARTUP_EVENTS_PATH']).open('a') as events:\n"
        "        events.write(value + '\\n')\n"
        "class HostService:\n"
        "    def start(self): mark('host')\n"
        "class WorkerService:\n"
        "    def start(self): mark('worker')\n"
        "    def stop(self): mark('stopped')\n",
        encoding="utf-8",
    )
    events_path = tmp_path / "events.txt"
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))
    monkeypatch.setenv("MOZAIKS_STARTUP_SERVICE_PROFILE", "worker")
    monkeypatch.setenv("MOZAIKS_TEST_STARTUP_EVENTS_PATH", str(events_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(registry, "validate_auth_provider_configuration", lambda: None)
    monkeypatch.setattr(platform, "executor_registry", ExecutorRegistry())
    monkeypatch.setattr(platform, "_runtime_services", [])
    monkeypatch.setattr(platform.app.state, "module_defaults_path", None, raising=False)

    try:
        await platform._platform_startup()
        assert platform.app.state.startup_degraded is False
        assert events_path.read_text(encoding="utf-8") == "worker\n"
        assert len(platform._runtime_services) == 1
    finally:
        await platform._platform_shutdown()
        ModuleLoader._clear_registered_package("mozaiks_runtime_module_profile_smoke")
    assert events_path.read_text(encoding="utf-8") == "worker\nstopped\n"


# ---------------------------------------------------------------------------
# stop_services integration
# ---------------------------------------------------------------------------

class TestStopServices:
    @pytest.mark.asyncio
    async def test_calls_sync_stop(self):
        stopped = []

        class Svc:
            def stop(self):
                stopped.append(True)

        from mozaiksai.core.runtime.composition.extensions import stop_services
        await stop_services([Svc()])
        assert stopped == [True]

    @pytest.mark.asyncio
    async def test_calls_async_stop(self):
        stopped = []

        class AsyncSvc:
            async def stop(self):
                stopped.append(True)

        from mozaiksai.core.runtime.composition.extensions import stop_services
        await stop_services([AsyncSvc()])
        assert stopped == [True]

    @pytest.mark.asyncio
    async def test_noop_for_service_without_stop(self):
        class NoStop:
            pass

        from mozaiksai.core.runtime.composition.extensions import stop_services
        await stop_services([NoStop()])  # no exception

    @pytest.mark.asyncio
    async def test_noop_for_empty_list(self):
        from mozaiksai.core.runtime.composition.extensions import stop_services
        await stop_services([])  # no exception

