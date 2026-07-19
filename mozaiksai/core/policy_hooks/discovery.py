# ==============================================================================
# FILE: mozaiksai/core/policy_hooks/discovery.py
# DESCRIPTION: Discovers module-declared policy hook providers from
#   modules/{module}/contracts/policy_hooks.yaml.
# ==============================================================================
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from logs.logging_config import get_core_logger
from mozaiksai.core.runtime.app.module_loader import ModulePolicyHooksManifest

logger = get_core_logger("policy_hooks.discovery")


def load_policy_hook_providers(app_root: Path) -> list[dict[str, Any]]:
    """Return validated policy hook provider declarations for an app bundle.

    The runtime does not execute these hooks directly. It exposes a typed,
    discoverable manifest so app-owned modules and hosts can route policy
    questions through declared module actions instead of hardcoded imports.
    Invalid manifests are logged and skipped, matching relationship provider
    discovery behavior.
    """
    modules_dir = app_root / "modules"
    if not modules_dir.is_dir():
        return []

    providers: list[dict[str, Any]] = []

    for module_dir in sorted(modules_dir.iterdir(), key=lambda d: d.name.lower()):
        if not module_dir.is_dir():
            continue
        hooks_path = module_dir / "contracts" / "policy_hooks.yaml"
        if not hooks_path.exists():
            continue

        try:
            raw = yaml.safe_load(hooks_path.read_text(encoding="utf-8")) or {}
            manifest = ModulePolicyHooksManifest.model_validate(raw)
        except Exception as exc:
            logger.warning("[policy_hooks] failed to read %s: %s", hooks_path, exc)
            continue

        for hook in manifest.hooks:
            entry = hook.model_dump(mode="python")
            entry["module_id"] = module_dir.name
            providers.append(entry)

    providers.sort(key=lambda p: (p.get("order", 100), p.get("module_id", ""), p.get("id", "")))
    return providers
