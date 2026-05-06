from __future__ import annotations

from pathlib import Path

from mozaiksai.core.workflow.paths import resolve_active_app_root

def resolve_admin_app_root() -> Path:
    """Resolve the active app root for admin config/module loading."""
    return resolve_active_app_root()


def resolve_platform_root() -> Path:
    """Backward-compatible alias for resolve_admin_app_root()."""
    return resolve_admin_app_root()
