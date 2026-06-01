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
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ext(kind: str, entrypoint: str, prefix: Optional[str] = None) -> Any:
    ext = SimpleNamespace(kind=kind, entrypoint=entrypoint, prefix=prefix)
    return ext


def _make_loaded_module(
    name: str,
    extensions: Optional[List[Any]] = None,
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
            # Verify router was included
            routes_prefixes = [str(r.path) for r in app.routes]
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
    async def _start(self, loaded_modules):
        from mozaiksai.core.runtime.composition.extensions import start_module_services
        return await start_module_services(loaded_modules)

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
