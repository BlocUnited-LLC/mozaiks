from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        if (parent / "factory_app").is_dir() and (parent / "mozaiksai").is_dir():
            return parent
    return here.parents[-1]


def load_default_ai_config() -> dict[str, Any]:
    path = _repo_root() / "factory_app" / "app" / "config" / "ai.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Factory ai.json must contain a JSON object: {path}")
    return data


def load_default_control_plane_runtime_config() -> dict[str, Any]:
    path = _repo_root() / "factory_app" / "app" / "config" / "llm.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Factory llm.yaml must contain a YAML object: {path}")
    return data


__all__ = [
    "load_default_ai_config",
    "load_default_control_plane_runtime_config",
]
