from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def import_module_directly(module_name: str):
    """Import a leaf module by path without executing heavy parent __init__ files."""
    if module_name in sys.modules:
        return sys.modules[module_name]

    parts = module_name.split(".")
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(workspace, *parts) + ".py"
    if not os.path.exists(file_path):
        file_path = os.path.join(workspace, *parts, "__init__.py")
    if not os.path.exists(file_path):
        raise ImportError(f"Cannot find module file for {module_name}")

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

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {module_name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def active_app_root() -> Path:
    """Return the active app workspace root. Skips the calling test if not configured."""
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

    pytest.skip(
        "No active app workspace configured. "
        "Set MOZAIKS_APP_WORKSPACE_PATH or PLATFORM_PATH to run this test."
    )


__all__ = ["import_module_directly", "active_app_root"]


