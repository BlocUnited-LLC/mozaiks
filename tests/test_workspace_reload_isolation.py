from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.runtime.app.module_loader import ModuleLoader


@pytest.fixture
def import_state(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    names = [key for key in sys.modules if key == "services" or key.startswith("services.")]
    saved = {key: sys.modules[key] for key in names}
    yield
    ModuleLoader._clear_registered_package("services")
    sys.modules.update(saved)


def test_reloading_workspace_prioritizes_its_own_optional_service_package(tmp_path, import_state):
    first = tmp_path / "first" / "app"
    second = tmp_path / "second" / "app"
    for root in (first, second):
        (root / "services" / "integrations").mkdir(parents=True)
    (second / "services" / "__init__.py").write_text("", encoding="utf-8")
    (second / "services" / "integrations" / "__init__.py").write_text("", encoding="utf-8")
    (first / "services" / "integrations" / "client.py").write_text('WORKSPACE = "first"\n', encoding="utf-8")
    (second / "services" / "integrations" / "client.py").write_text('WORKSPACE = "second"\n', encoding="utf-8")

    for root, expected in ((first, "first"), (second, "second"), (first, "first")):
        ModuleLoader(str(root))
        client = importlib.import_module("services.integrations.client")
        assert client.WORKSPACE == expected
        assert sys.path[0] == str(root.resolve())
        assert sys.path.count(str(root.resolve())) == 1


def test_workspace_without_services_cannot_import_another_apps_clients(tmp_path, import_state):
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "services").mkdir(parents=True)
    second.mkdir()
    (first / "services" / "__init__.py").write_text("", encoding="utf-8")
    (first / "services" / "private_client.py").write_text("VALUE = 1\n", encoding="utf-8")
    ModuleLoader(str(first))
    assert importlib.import_module("services.private_client").VALUE == 1
    ModuleLoader(str(second))
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("services.private_client")


@pytest.mark.asyncio
async def test_platform_startup_health_does_not_retain_an_earlier_failure(monkeypatch):
    from mozaiksai.core.auth.adapters import registry
    from mozaiksai.hosts import platform

    monkeypatch.setattr(registry, "validate_auth_provider_configuration", lambda: None)
    monkeypatch.setattr(platform, "get_platform_hooks", lambda: SimpleNamespace(run_startup=AsyncMock()))
    monkeypatch.setattr(platform.app.state, "startup_degraded", False)
    monkeypatch.setattr(platform.app.state, "startup_degraded_reason", None)
    monkeypatch.setattr(platform.app.state, "failed_module_names", [], raising=False)
    monkeypatch.setattr(platform.AppLoader, "load", AsyncMock(side_effect=[
        platform.AppLoadError("invalid app contract"),
        platform.AppLoadError("app.json not found"),
    ]))
    await platform._platform_startup()
    assert platform.app.state.startup_degraded is True
    assert platform.app.state.startup_degraded_reason == "APP_LOAD_ERROR"
    await platform._platform_startup()
    assert platform.app.state.startup_degraded is False
    assert platform.app.state.startup_degraded_reason is None
    assert platform.app.state.failed_module_names == []
