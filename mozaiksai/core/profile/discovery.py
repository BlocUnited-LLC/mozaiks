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


def _iter_profile_manifests(app_root: Path):
    """Yield (module_id, manifest) pairs for all modules with profile.yaml."""
    modules_dir = app_root / "modules"
    if not modules_dir.is_dir():
        return
    for module_dir in sorted(modules_dir.iterdir(), key=lambda d: d.name.lower()):
        if not module_dir.is_dir():
            continue
        profile_path = module_dir / "contracts" / "profile.yaml"
        if not profile_path.exists():
            continue
        try:
            raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
            manifest = ModuleProfileManifest.model_validate(raw)
            yield module_dir.name, manifest
        except Exception as exc:
            logger.warning("[profile] failed to read %s: %s", profile_path, exc)


def load_profile_panels(app_root: Path) -> list[dict[str, Any]]:
    """Walk modules/{module}/contracts/profile.yaml and return validated panel dicts.

    Each returned dict includes a ``module_id`` key so the platform endpoint
    can route action calls to the correct module without additional lookups.
    Panels are returned sorted globally by ``order`` ascending.

    Errors in individual panel files are logged and skipped so a bad module
    cannot break the profile page for all users.
    """
    panels: list[dict[str, Any]] = []
    for module_id, manifest in _iter_profile_manifests(app_root):
        for panel in manifest.panels:
            entry = panel.model_dump(mode="python")
            entry["module_id"] = module_id
            panels.append(entry)
    panels.sort(key=lambda p: (p.get("order", 100), p.get("module_id", ""), p.get("id", "")))
    return panels


def load_profile_tabs(app_root: Path) -> list[dict[str, Any]]:
    """Walk modules/{module}/contracts/profile.yaml and return validated tab dicts.

    Each returned dict includes a ``module_id`` key.
    Tabs are sorted globally by ``order`` ascending.
    """
    tabs: list[dict[str, Any]] = []
    for module_id, manifest in _iter_profile_manifests(app_root):
        for tab in manifest.tabs:
            entry = tab.model_dump(mode="python")
            entry["module_id"] = module_id
            tabs.append(entry)
    tabs.sort(key=lambda t: (t.get("order", 100), t.get("module_id", ""), t.get("id", "")))
    return tabs
