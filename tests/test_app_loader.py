"""Tests for AppLoader and ModuleLoader.load_all() production-safety contract.

Covers:
1. AppLoadError on missing app.json
2. AppLoadError on malformed JSON
3. AppLoadError on invalid app.json schema
4. Successful load with no modules
5. Env-var resolution in app.json
6. Successful load with a valid module → loaded_modules populated
7. Partial module failure → failed_module_names populated, result still usable
8. All-module failure → failed_module_names has all names, modules empty
9. ModuleLoader.load_all() returns (loaded, failed) tuple
10. load_all() collects both successes and failures in one pass
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError, AppLoadResult
from mozaiksai.core.runtime.app.module_loader import ModuleLoader


@pytest.fixture(autouse=True)
def _restore_import_state():
    """Restore sys.path and sys.modules after every test in this file.

    AppLoader.load() adds temporary directories to sys.path so generated module
    handlers can import app-local packages.  Without cleanup, stale path entries
    from one test remain in sys.path for subsequent tests.  When a later test's
    temp dir contains a package with a different set of modules (e.g. the replay
    test needs mozaikspay_client.py but the previous test's package only has
    billing_client.py), Python resolves the package from the earlier stale entry
    and the import fails.
    """
    path_snapshot = list(sys.path)
    modules_snapshot = set(sys.modules)
    yield
    sys.path[:] = path_snapshot
    for key in list(sys.modules):
        if key not in modules_snapshot:
            sys.modules.pop(key, None)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_app_json(root: Path, content: str = '{"appName": "Test App"}') -> None:
    (root / "app.json").write_text(content, encoding="utf-8")


def _write_valid_module(root: Path, module_id: str = "tasks") -> Path:
    """Write a minimal valid module with a simple handler."""
    module_dir = root / "modules" / module_id
    backend_dir = module_dir / "backend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "contracts").mkdir(exist_ok=True)

    module_dir.joinpath("module.yaml").write_text(
        f"""
schema_version: mozaiks.module.v1
module:
  id: {module_id}
  display_name: Tasks
  version: 1.0.0
  handler: backend.handler:{module_id.capitalize()}Handler
actions:
  - id: list
    description: List items
    handler_method: list_items
""".lstrip(),
        encoding="utf-8",
    )
    backend_dir.joinpath("handler.py").write_text(
        f"""
class {module_id.capitalize()}Handler:
    async def list_items(self, ctx, payload):
        return {{"items": []}}
""".lstrip(),
        encoding="utf-8",
    )
    return module_dir


def _write_broken_module(root: Path, module_id: str = "broken") -> Path:
    """Write a module with an invalid module.yaml (missing required fields)."""
    module_dir = root / "modules" / module_id
    (module_dir / "backend").mkdir(parents=True, exist_ok=True)

    module_dir.joinpath("module.yaml").write_text(
        "schema_version: mozaiks.module.v1\n",  # missing module: section
        encoding="utf-8",
    )
    return module_dir


def _write_module_using_app_services(root: Path, module_id: str = "billing") -> Path:
    services_dir = root / "services" / "integrations"
    services_dir.mkdir(parents=True, exist_ok=True)
    (root / "services" / "__init__.py").write_text("", encoding="utf-8")
    (services_dir / "__init__.py").write_text("", encoding="utf-8")
    (services_dir / "billing_client.py").write_text(
        "class BillingClient:\n    async def status(self):\n        return {'success': True}\n",
        encoding="utf-8",
    )

    module_dir = root / "modules" / module_id
    backend_dir = module_dir / "backend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    (backend_dir / "__init__.py").write_text("", encoding="utf-8")
    module_dir.joinpath("module.yaml").write_text(
        f"""
schema_version: mozaiks.module.v1
module:
  id: {module_id}
  display_name: Billing
  version: 1.0.0
  handler: backend.handler:BillingHandler
actions:
  - id: status
    description: Return billing status.
    handler_method: status
""".lstrip(),
        encoding="utf-8",
    )
    backend_dir.joinpath("handler.py").write_text(
        """
from .service import BillingService


class BillingHandler:
    def __init__(self):
        self.service = BillingService()

    async def status(self, ctx, **params):
        return await self.service.status(ctx, **params)
""".lstrip(),
        encoding="utf-8",
    )
    backend_dir.joinpath("service.py").write_text(
        """
from services.integrations.billing_client import BillingClient


class BillingService:
    async def status(self, ctx, **params):
        return await BillingClient().status()
""".lstrip(),
        encoding="utf-8",
    )
    return module_dir


# ---------------------------------------------------------------------------
# AppLoader.load() error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_loader_raises_when_app_json_missing(tmp_path: Path) -> None:
    with pytest.raises(AppLoadError, match="app.json not found"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_app_loader_raises_on_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(AppLoadError, match="Failed to read app.json"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_app_loader_raises_on_non_object_json(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(AppLoadError, match="must be a JSON object"):
        await AppLoader.load(str(tmp_path))


# ---------------------------------------------------------------------------
# AppLoader.load() success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_loader_succeeds_with_no_modules(tmp_path: Path) -> None:
    _write_app_json(tmp_path, '{"appName": "Minimal App", "version": "2.0"}')

    result = await AppLoader.load(str(tmp_path))

    assert isinstance(result, AppLoadResult)
    assert result.definition.name == "Minimal App"
    assert result.definition.version == "2.0"
    assert result.modules == []
    assert result.failed_module_names == []


@pytest.mark.asyncio
async def test_app_loader_uses_directory_name_when_app_name_absent(tmp_path: Path) -> None:
    named_dir = tmp_path / "my-product"
    named_dir.mkdir()
    _write_app_json(named_dir, '{"version": "1.0"}')

    result = await AppLoader.load(str(named_dir))

    assert result.definition.name == "my-product"


@pytest.mark.asyncio
async def test_app_loader_resolves_env_vars_in_app_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME_VAR", "Resolved App Name")
    _write_app_json(tmp_path, '{"appName": "${APP_NAME_VAR}"}')

    result = await AppLoader.load(str(tmp_path))

    assert result.definition.name == "Resolved App Name"


@pytest.mark.asyncio
async def test_app_loader_resolves_missing_env_var_to_directory_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NONEXISTENT_VAR_12345", raising=False)
    _write_app_json(tmp_path, '{"appName": "${NONEXISTENT_VAR_12345}"}')

    result = await AppLoader.load(str(tmp_path))

    # Empty string is falsy → falls back to the directory name
    assert result.definition.name == tmp_path.name


@pytest.mark.asyncio
async def test_app_loader_loads_valid_module(tmp_path: Path) -> None:
    _write_app_json(tmp_path)
    _write_valid_module(tmp_path, "tasks")

    result = await AppLoader.load(str(tmp_path))

    assert len(result.modules) == 1
    assert result.modules[0].name == "tasks"
    assert result.failed_module_names == []


@pytest.mark.asyncio
async def test_app_loader_app_root_named_app_can_import_app_services(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    _write_app_json(app_root)
    _write_module_using_app_services(app_root)

    result = await AppLoader.load(str(app_root))

    assert [module.name for module in result.modules] == ["billing"]
    assert result.failed_module_names == []


@pytest.mark.asyncio
async def test_app_loader_loads_multiple_valid_modules(tmp_path: Path) -> None:
    _write_app_json(tmp_path)
    _write_valid_module(tmp_path, "alpha")
    _write_valid_module(tmp_path, "beta")

    result = await AppLoader.load(str(tmp_path))

    loaded_names = {m.name for m in result.modules}
    assert loaded_names == {"alpha", "beta"}
    assert result.failed_module_names == []


# ---------------------------------------------------------------------------
# Partial module failure → failed_module_names
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_loader_partial_failure_populates_failed_module_names(tmp_path: Path) -> None:
    _write_app_json(tmp_path)
    _write_valid_module(tmp_path, "ok_module")
    _write_broken_module(tmp_path, "bad_module")

    result = await AppLoader.load(str(tmp_path))

    assert len(result.modules) == 1
    assert result.modules[0].name == "ok_module"
    assert result.failed_module_names == ["bad_module"]


@pytest.mark.asyncio
async def test_app_loader_all_modules_fail_returns_empty_loaded_list(tmp_path: Path) -> None:
    _write_app_json(tmp_path)
    _write_broken_module(tmp_path, "broken_a")
    _write_broken_module(tmp_path, "broken_b")

    result = await AppLoader.load(str(tmp_path))

    assert result.modules == []
    assert sorted(result.failed_module_names) == ["broken_a", "broken_b"]


@pytest.mark.asyncio
async def test_app_loader_failed_module_does_not_affect_valid_modules(tmp_path: Path) -> None:
    _write_app_json(tmp_path)
    _write_valid_module(tmp_path, "alpha")
    _write_broken_module(tmp_path, "bad")
    _write_valid_module(tmp_path, "gamma")

    result = await AppLoader.load(str(tmp_path))

    loaded_names = {m.name for m in result.modules}
    assert loaded_names == {"alpha", "gamma"}
    assert result.failed_module_names == ["bad"]


# ---------------------------------------------------------------------------
# ModuleLoader.load_all() returns (loaded, failed) tuple
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_module_loader_load_all_returns_tuple(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    _write_app_json(app_root)
    _write_valid_module(app_root, "tasks")

    loader = ModuleLoader(base_path=str(app_root))
    result = await loader.load_all(["tasks"])

    assert isinstance(result, tuple)
    loaded, failed = result
    assert len(loaded) == 1
    assert loaded[0].name == "tasks"
    assert failed == []


@pytest.mark.asyncio
async def test_module_loader_load_all_tracks_failed_names(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()
    _write_app_json(app_root)
    _write_valid_module(app_root, "good")
    _write_broken_module(app_root, "bad")

    loader = ModuleLoader(base_path=str(app_root))
    loaded, failed = await loader.load_all(["good", "bad"])

    assert [m.name for m in loaded] == ["good"]
    assert failed == ["bad"]


@pytest.mark.asyncio
async def test_module_loader_load_all_empty_names_returns_empty_tuple(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    app_root.mkdir()

    loader = ModuleLoader(base_path=str(app_root))
    loaded, failed = await loader.load_all([])

    assert loaded == []
    assert failed == []


# ---------------------------------------------------------------------------
# Handler path containment: symlink escape guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_module_loader_rejects_symlinked_handler_outside_module_dir(
    tmp_path: Path,
) -> None:
    """A handler symlinked to a file outside the module directory must be rejected."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    _write_app_json(app_root)

    # Create a target file outside the module directory
    secret_file = tmp_path / "secret.py"
    secret_file.write_text(
        "class TasksHandler:\n    async def list_items(self, ctx, payload): return {}\n",
        encoding="utf-8",
    )

    module_dir = app_root / "modules" / "tasks"
    backend_dir = module_dir / "backend"
    backend_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "contracts").mkdir(exist_ok=True)
    module_dir.joinpath("module.yaml").write_text(
        """
schema_version: mozaiks.module.v1
module:
  id: tasks
  display_name: Tasks
  version: 1.0.0
  handler: backend.handler:TasksHandler
actions:
  - id: list
    description: List items
    handler_method: list_items
""".lstrip(),
        encoding="utf-8",
    )

    # Symlink handler.py → outside module directory
    handler_link = backend_dir / "handler.py"
    try:
        handler_link.symlink_to(secret_file)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported on this platform")

    loader = ModuleLoader(base_path=str(app_root))
    loaded, failed = await loader.load_all(["tasks"])

    assert "tasks" in failed, (
        "Module loader must reject a handler symlinked outside the module directory"
    )
    assert loaded == []
