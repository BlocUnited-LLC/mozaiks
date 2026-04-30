from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "router",
    "APP_BACKEND_ADMIN_SCHEMA_VERSION",
    "build_app_backend_admin_code_files",
    "build_app_backend_admin_router",
    "validate_app_backend_admin_config",
]


def __getattr__(name: str) -> Any:
    if name == "router":
        return import_module("mozaiksai.core.admin.router").router
    if name in {
        "APP_BACKEND_ADMIN_SCHEMA_VERSION",
        "validate_app_backend_admin_config",
    }:
        module = import_module("mozaiksai.core.admin.app_backend_contract")
        return getattr(module, name)
    if name == "build_app_backend_admin_code_files":
        module = import_module("mozaiksai.core.admin.app_backend_codegen")
        return getattr(module, name)
    if name == "build_app_backend_admin_router":
        module = import_module("mozaiksai.core.admin.app_backend_router")
        return getattr(module, name)
    raise AttributeError(name)
