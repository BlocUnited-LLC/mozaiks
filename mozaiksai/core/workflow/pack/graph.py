from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def _find_repo_root() -> Path:
    try:
        here = Path(__file__).resolve()
    except Exception:  # pragma: no cover
        return Path.cwd().resolve()

    for parent in [here] + list(here.parents):
        try:
            if (parent / "workflows").is_dir() and (parent / "core").is_dir():
                return parent
        except Exception:
            continue

    return Path.cwd().resolve()


def load_pack_graph(workflow_name: str) -> Optional[Dict[str, Any]]:
    wf = str(workflow_name or "").strip()
    if not wf:
        return None
    root = _find_repo_root()
    path = root / "workflows" / wf / "_pack" / "workflow_graph.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def workflow_has_nested_chats(workflow_name: str) -> bool:
    cfg = load_pack_graph(workflow_name)
    if not cfg or not isinstance(cfg, dict):
        return False
    nested = cfg.get("nested_chats")
    return isinstance(nested, list) and bool(nested)


__all__ = ["load_pack_graph", "workflow_has_nested_chats"]
