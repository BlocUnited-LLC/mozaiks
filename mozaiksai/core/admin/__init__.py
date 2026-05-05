from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["router"]


def __getattr__(name: str) -> Any:
    if name == "router":
        return import_module("mozaiksai.core.admin.router").router
    raise AttributeError(name)
