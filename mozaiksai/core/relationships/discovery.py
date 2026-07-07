# ==============================================================================
# FILE: mozaiksai/core/relationships/discovery.py
# DESCRIPTION: Discovers module-declared current-user relationship providers from
#   modules/{module}/contracts/relationships.yaml.
# ==============================================================================
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from logs.logging_config import get_core_logger
from mozaiksai.core.runtime.app.module_loader import ModuleRelationshipsManifest

logger = get_core_logger("relationships.discovery")


def load_relationship_providers(app_root: Path) -> list[dict[str, Any]]:
    """Return validated relationship provider declarations for an app bundle.

    Each returned provider includes ``module_id`` so the host endpoint can route
    hydration through the owning module action. Invalid provider manifests are
    logged and skipped, matching profile panel discovery behavior.
    """
    modules_dir = app_root / "modules"
    if not modules_dir.is_dir():
        return []

    providers: list[dict[str, Any]] = []

    for module_dir in sorted(modules_dir.iterdir(), key=lambda d: d.name.lower()):
        if not module_dir.is_dir():
            continue
        relationships_path = module_dir / "contracts" / "relationships.yaml"
        if not relationships_path.exists():
            continue

        try:
            raw = yaml.safe_load(relationships_path.read_text(encoding="utf-8")) or {}
            manifest = ModuleRelationshipsManifest.model_validate(raw)
        except Exception as exc:
            logger.warning("[relationships] failed to read %s: %s", relationships_path, exc)
            continue

        for provider in manifest.providers:
            entry = provider.model_dump(mode="python")
            entry["module_id"] = module_dir.name
            providers.append(entry)

    providers.sort(key=lambda p: (p.get("order", 100), p.get("module_id", ""), p.get("id", "")))
    return providers
