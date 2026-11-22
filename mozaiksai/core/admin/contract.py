"""Admin shell contract: the standard page catalog and the framework panels.

An app's admin portal has only the pages its ``app/admin/admin_registry.yaml``
declares. This module is the single catalog of the *standard* pages a registry
may declare, so the generator, the app-backend panel contract, the frontend, and
the docs all name the same ids. A registry may also declare its own page ids
(a capability pack's ``commerce`` page, for example); those render the panels
that reference them.

The /api/admin/config response shape:
  {
    "pages": [...],          # from admin_registry.yaml
    "runtime_panels": [...], # framework panels, each bound to a standard page
    "module_panels": [...],  # discovered from modules/{module}/contracts/admin.yaml
  }
"""
from __future__ import annotations

from typing import Any

# Standard admin pages, in navigation order. ``path`` is the generated-app route
# (the ``/admin`` family); a hosted product may mount the same id elsewhere.
STANDARD_ADMIN_PAGES: tuple[dict[str, Any], ...] = (
    {"id": "overview", "label": "Overview", "path": "/admin", "icon": "dashboard", "order": 0},
    {"id": "access", "label": "Access", "path": "/admin/access", "icon": "access", "order": 10},
    {"id": "usage", "label": "Usage", "path": "/admin/usage", "icon": "chart", "order": 20},
    {"id": "billing", "label": "Billing", "path": "/admin/billing", "icon": "billing", "order": 30},
    {"id": "activity", "label": "Activity", "path": "/admin/activity", "icon": "history", "order": 40},
    {"id": "operations", "label": "Operations", "path": "/admin/operations", "icon": "server", "order": 50},
    {"id": "settings", "label": "Settings", "path": "/admin/settings", "icon": "settings", "order": 60},
    {"id": "integrations", "label": "Integrations", "path": "/admin/integrations", "icon": "plug", "order": 70},
    {"id": "support", "label": "Support", "path": "/admin/support", "icon": "support", "order": 80},
)

STANDARD_ADMIN_PAGE_IDS: tuple[str, ...] = tuple(page["id"] for page in STANDARD_ADMIN_PAGES)

# Framework panels. Each renders data the runtime already records and appears
# only when the registry declares the page it is bound to.
DEFAULT_RUNTIME_PANELS: list[dict[str, Any]] = [
    {"id": "stats", "label": "AI Usage", "page": "usage"},
    {"id": "runs", "label": "Recent Runs", "page": "usage"},
    {"id": "sessions", "label": "Recent Sessions", "page": "operations"},
]
