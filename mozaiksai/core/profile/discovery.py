# ==============================================================================
# FILE: mozaiksai/core/profile/discovery.py
# DESCRIPTION: Discovers module-declared profile panels from
#   modules/{module}/contracts/profile.yaml, mirroring the admin panel
#   discovery pattern in mozaiksai/core/admin/router.py.
# ==============================================================================
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from logs.logging_config import get_core_logger
from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

logger = get_core_logger("profile.discovery")


def load_profile_panels(app_root: Path) -> list[dict[str, Any]]:
    """Walk modules/{module}/contracts/profile.yaml and return validated panel dicts.

    Each returned dict includes a ``module_id`` key so the platform endpoint
    can route action calls to the correct module without additional lookups.
    Panels are returned in document order within each module, sorted globally
    by ``order`` ascending.

    Errors in individual panel files are logged and skipped so a bad module
    cannot break the profile page for all users.
    """
    modules_dir = app_root / "modules"
    if not modules_dir.is_dir():
        return []

    panels: list[dict[str, Any]] = []

    for module_dir in sorted(modules_dir.iterdir(), key=lambda d: d.name.lower()):
        if not module_dir.is_dir():
            continue
        profile_path = module_dir / "contracts" / "profile.yaml"
        if not profile_path.exists():
            continue

        try:
            raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
            manifest = ModuleProfileManifest.model_validate(raw)
        except Exception as exc:
            logger.warning("[profile] failed to read %s: %s", profile_path, exc)
            continue

        for panel in manifest.panels:
            entry = panel.model_dump(mode="python")
            entry["module_id"] = module_dir.name
            panels.append(entry)

    panels.sort(key=lambda p: (p.get("order", 100), p.get("module_id", ""), p.get("id", "")))
    return panels
