from __future__ import annotations

import importlib.util
import os
import sys


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


__all__ = ["import_module_directly"]

