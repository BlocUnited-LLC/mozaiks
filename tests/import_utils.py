from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def import_module_directly(module_name: str):
    """Import a leaf module by path without executing heavy parent __init__ files.

    Parent packages are fabricated only for the duration of the load and then
    removed again. A fabricated parent never executes its real ``__init__.py``,
    so leaving one cached in ``sys.modules`` would poison every later real
    import of that package: ``from mozaiksai.core.events import
    get_event_dispatcher`` then fails with "cannot import name ... (unknown
    location)" because the cached stub has no such attribute. Removing exactly
    the entries this call added keeps the helper non-polluting and makes the
    result independent of which test ran first.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]

    parts = module_name.split(".")
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(workspace, *parts) + ".py"
    if not os.path.exists(file_path):
        file_path = os.path.join(workspace, *parts, "__init__.py")
    if not os.path.exists(file_path):
        raise ImportError(f"Cannot find module file for {module_name}")

    fabricated_parents: list[str] = []
    for i in range(1, len(parts)):
        parent_name = ".".join(parts[:i])
        if parent_name in sys.modules:
            continue
        parent_path = os.path.join(workspace, *parts[:i])
        if not os.path.isdir(parent_path):
            continue
        import types

        pkg = types.ModuleType(parent_name)
        pkg.__path__ = [parent_path]
        pkg.__package__ = parent_name
        sys.modules[parent_name] = pkg
        fabricated_parents.append(parent_name)

    loaded = False
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load module spec for {module_name}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        loaded = True
    finally:
        # A module whose body raised is half-initialized; leaving it cached
        # would hand that broken object to the next importer.
        if not loaded:
            sys.modules.pop(module_name, None)
        _upgrade_fabricated_parents(fabricated_parents)
    return mod


def _upgrade_fabricated_parents(fabricated_parents: list[str]) -> None:
    """Replace each stub this call created with the real package.

    Deleting the stubs instead would orphan every real submodule that was
    imported through them: the submodule stays in ``sys.modules`` but a later
    real import of the parent builds a fresh module object without that
    attribute, so ``monkeypatch.setattr("pkg.sub.name", ...)`` fails. Leaving
    the stubs in place is what poisoned later imports in the first place (a
    stub never runs the real ``__init__``, so
    ``from mozaiksai.core.events import get_event_dispatcher`` raised "unknown
    location"). Upgrading gives both: real parents with their real contents,
    and submodules still reachable by dotted path.

    Shallowest first, so each real import binds to an already-real parent. A
    parent that cannot be imported keeps its stub, leaving behavior no worse
    than before.
    """
    for parent_name in fabricated_parents:
        stub = sys.modules.get(parent_name)
        if stub is None or getattr(stub, "__file__", None):
            continue
        del sys.modules[parent_name]
        try:
            real_parent = importlib.import_module(parent_name)
        except Exception:
            sys.modules[parent_name] = stub
            continue
        # Re-attach submodules that were bound onto the stub during the load
        # and that the real package does not import itself.
        prefix = f"{parent_name}."
        for name, module in list(sys.modules.items()):
            if not name.startswith(prefix):
                continue
            child = name[len(prefix):]
            if "." in child or getattr(real_parent, child, None) is not None:
                continue
            setattr(real_parent, child, module)


def active_app_root() -> Path:
    """Return the active app workspace root. Skips the calling test if not configured.

    Env vars win when set; the repo's first-party ``factory_app/app`` bundle is
    the deterministic fallback so resolution never depends on whether an
    earlier test imported a host module.
    """
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

    factory_override = os.environ.get("MOZAIKS_FACTORY_APP_PATH", "").strip()
    repo_factory_bundle = (
        (Path(factory_override) / "app")
        if factory_override
        else (Path(__file__).resolve().parents[1] / "factory_app" / "app")
    ).resolve()
    if (repo_factory_bundle / "app.json").exists():
        return repo_factory_bundle

    pytest.skip(
        "No active app workspace configured. "
        "Set MOZAIKS_APP_WORKSPACE_PATH or PLATFORM_PATH to run this test."
    )


__all__ = ["import_module_directly", "active_app_root"]


