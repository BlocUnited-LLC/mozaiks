from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.core.workflow.paths import resolve_active_app_root

def resolve_runtime_ai_config(ai_config: object, *, app_root: Path | None = None) -> dict[str, Any]:
    _ = app_root
    return dict(ai_config) if isinstance(ai_config, dict) else {}


def load_runtime_ai_config(app_root: Path | None = None) -> dict[str, Any]:
    resolved_app_root = (app_root or resolve_active_app_root()).resolve()
    ai_path = resolved_app_root / "config" / "ai.json"
    if not ai_path.exists():
        return {}

    try:
        data = json.loads(ai_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return resolve_runtime_ai_config(data, app_root=resolved_app_root)