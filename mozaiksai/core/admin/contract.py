"""Admin shell config contract.

Runtime panels are built-in observability surfaces (stats, runs, sessions).
Module panels come from modules/{module}/contracts/admin.yaml discovery.
Pages come from app/admin/admin_registry.yaml.

The /api/admin/config response shape:
  {
    "pages": [...],          # from admin_registry.yaml
    "runtime_panels": [...], # built-in observability
    "module_panels": [...],  # discovered from admin.yaml files
  }
"""
from __future__ import annotations

from typing import Any

DEFAULT_RUNTIME_PANELS: list[dict[str, Any]] = [
    {"id": "stats", "label": "Usage Stats", "page": "usage"},
    {"id": "runs", "label": "Active Runs", "page": "usage"},
    {"id": "sessions", "label": "Recent Sessions", "page": "operations"},
]


def build_default_admin_config() -> dict[str, Any]:
    return {
        "pages": [],
        "runtime_panels": list(DEFAULT_RUNTIME_PANELS),
        "module_panels": [],
    }
