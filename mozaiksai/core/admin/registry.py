"""Admin registry — loads app/admin/admin_registry.yaml.

admin_registry.yaml is the canonical declaration of an app's admin portal
shape: what pages exist, their paths, display names, and scope.

Modules declare panels in contracts/admin.yaml and reference a page id here.
The runtime joins panels to pages at /api/admin/config serving time.

Schema version: mozaiks.admin.registry.v1
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

ADMIN_REGISTRY_SCHEMA_VERSION = "mozaiks.admin.registry.v1"


class AdminRegistryPage(BaseModel):
    """A single page in the admin portal."""

    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    path: str
    icon: Optional[str] = None
    order: int = 0
    enabled: bool = True
    scope: Literal["workspace", "app"] = "app"

    @field_validator("id", "label", "path", mode="before")
    @classmethod
    def _required(cls, v: Any, info: Any) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"admin_registry page '{info.field_name}' must be a non-empty string")
        return v.strip()

    @field_validator("scope", mode="before")
    @classmethod
    def _scope(cls, v: Any) -> str:
        val = str(v or "app").lower().strip()
        if val not in ("workspace", "app"):
            raise ValueError("scope must be 'workspace' or 'app'")
        return val


class AdminRegistry(BaseModel):
    """Validated admin_registry.yaml contract."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = ADMIN_REGISTRY_SCHEMA_VERSION
    pages: List[AdminRegistryPage] = Field(default_factory=list)

    def enabled_pages(self, scope: Optional[str] = None) -> List[AdminRegistryPage]:
        pages = [p for p in self.pages if p.enabled]
        if scope:
            pages = [p for p in pages if p.scope == scope]
        return sorted(pages, key=lambda p: (p.order, p.id))

    def page_ids(self) -> set[str]:
        return {p.id for p in self.pages if p.enabled}


def load_admin_registry(app_root: Path) -> AdminRegistry:
    """Load admin/admin_registry.yaml from the active app root.

    Returns an empty registry (no pages) when the file is absent or invalid.
    """
    registry_path = app_root / "admin" / "admin_registry.yaml"
    if not registry_path.exists():
        return AdminRegistry()
    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        return AdminRegistry.model_validate(raw)
    except Exception:
        return AdminRegistry()


def build_admin_shell_routes(registry: AdminRegistry) -> list[dict[str, Any]]:
    """Convert registry pages into shell route dicts for platform.py injection.

    Each enabled page becomes a React shell route bound to the AdminPortal
    component. The 'adminPage' meta field tells the frontend which page is active.
    """
    routes: list[dict[str, Any]] = []
    for page in registry.enabled_pages():
        routes.append(
            {
                "path": page.path,
                "label": page.label,
                "order": page.order,
                "title": page.label,
                "admin_page": page.id,
                "scope": page.scope,
            }
        )
    return routes
