from __future__ import annotations

import os
from pathlib import Path


def resolve_platform_root() -> Path:
    """Resolve the active app root for admin config/module loading."""
    platform_path = os.environ.get("PLATFORM_PATH", "")
    if platform_path:
        candidate = Path(platform_path)
        if not candidate.is_absolute():
            candidate = (Path(__file__).parents[3] / candidate).resolve()
        if (candidate / "app.json").exists():
            return candidate.resolve()
        nested = candidate / "app"
        if (nested / "app.json").exists():
            return nested.resolve()
        return candidate.resolve()

    # No PLATFORM_PATH set — return CWD-relative default; callers handle missing paths gracefully
    return (Path.cwd() / "app").resolve()


def resolve_admin_config_path() -> Path:
    """Resolve admin.json relative to the active platform root."""
    return resolve_platform_root() / "config" / "admin.json"
