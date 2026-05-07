from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.resources import resolve_factory_app_root
from mozaiksai.core.workflow.paths import resolve_active_app_root


class ControlPlaneCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    llm_config: Optional[dict[str, Any]] = None


class ControlPlaneConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    classifier: ControlPlaneCapabilityConfig = Field(default_factory=ControlPlaneCapabilityConfig)
    coding: ControlPlaneCapabilityConfig = Field(default_factory=ControlPlaneCapabilityConfig)

    def classifier_enabled(self) -> bool:
        return bool(self.enabled and self.classifier.enabled)

    def coding_enabled(self) -> bool:
        return bool(self.enabled and self.coding.enabled)


def resolve_ai_config_path(app_root: Optional[Path] = None) -> Path:
    if app_root is not None:
        return (app_root / "config" / "ai.json").resolve()

    active_root = resolve_active_app_root()
    active_path = (active_root / "config" / "ai.json").resolve()
    if active_path.exists():
        return active_path

    factory_root = resolve_factory_app_root()
    if factory_root is not None:
        factory_path = (factory_root / "app" / "config" / "ai.json").resolve()
        if factory_path.exists():
            return factory_path

    return active_path


def load_ai_config_json(app_root: Optional[Path] = None) -> dict[str, Any]:
    ai_path = resolve_ai_config_path(app_root)
    if not ai_path.exists():
        return {}

    try:
        data = json.loads(ai_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_control_plane_config(app_root: Optional[Path] = None) -> ControlPlaneConfig:
    ai_config = load_ai_config_json(app_root)
    raw = ai_config.get("control_plane")
    if not isinstance(raw, dict):
        return ControlPlaneConfig()
    return ControlPlaneConfig.model_validate(raw)
