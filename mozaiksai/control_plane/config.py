from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

from mozaiksai.core.workflow.paths import resolve_active_app_root
from mozaiksai.resources import resolve_factory_app_root

ControlPlaneLLMProfileId = Literal[
    "classifier",
    "impact_analyzer",
    "architecture",
    "planner_replanner",
    "codegen",
    "reviewer_validator",
]

ALLOWED_CONTROL_PLANE_LLM_PROFILE_IDS: tuple[str, ...] = (
    "classifier",
    "impact_analyzer",
    "architecture",
    "planner_replanner",
    "codegen",
    "reviewer_validator",
)


class ControlPlaneLLMProfileConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    purpose: str = ""
    default_temperature: float | None = None
    expected_behavior: str = ""
    llm_config: dict[str, Any] | None = None


class ControlPlaneCapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    llm_profile: ControlPlaneLLMProfileId | None = None
    llm_config: dict[str, Any] | None = None


class ControlPlaneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.refinement.policy.v1"] = "mozaiks.refinement.policy.v1"
    enabled: bool = False
    profile: str | None = None
    llm_profiles: dict[ControlPlaneLLMProfileId, ControlPlaneLLMProfileConfig] = Field(default_factory=dict)
    classifier: ControlPlaneCapabilityConfig = Field(default_factory=ControlPlaneCapabilityConfig)
    coding: ControlPlaneCapabilityConfig = Field(default_factory=ControlPlaneCapabilityConfig)
    contract_surface: ControlPlaneCapabilityConfig = Field(default_factory=ControlPlaneCapabilityConfig)
    scope: ControlPlaneCapabilityConfig = Field(default_factory=ControlPlaneCapabilityConfig)

    @field_validator("llm_profiles", mode="before")
    @classmethod
    def _validate_llm_profile_ids(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("refinement_policy.llm_profiles must be an object keyed by profile id")
        unknown = sorted(str(key) for key in value if str(key) not in ALLOWED_CONTROL_PLANE_LLM_PROFILE_IDS)
        if unknown:
            allowed = ", ".join(ALLOWED_CONTROL_PLANE_LLM_PROFILE_IDS)
            raise ValueError(f"Unknown refinement policy LLM profile id(s): {', '.join(unknown)}. Allowed: {allowed}.")
        return value

    def classifier_enabled(self) -> bool:
        return bool(self.enabled and self.classifier.enabled)

    def coding_enabled(self) -> bool:
        return bool(self.enabled and self.coding.enabled)

    def resolve_capability_llm_config(self, capability: str) -> dict[str, Any] | None:
        capability_config = getattr(self, capability, None)
        if not isinstance(capability_config, ControlPlaneCapabilityConfig):
            raise ValueError(f"Unknown refinement capability '{capability}'")

        if capability_config.llm_profile:
            profile = self.llm_profiles.get(capability_config.llm_profile)
            if profile is None:
                raise ValueError(
                    f"Refinement capability '{capability}' references unknown LLM profile "
                    f"'{capability_config.llm_profile}'."
                )
            if profile.llm_config is None:
                raise ValueError(
                    f"Refinement policy LLM profile '{capability_config.llm_profile}' does not declare llm_config."
                )
            return dict(profile.llm_config)

        if capability_config.llm_config is not None:
            return dict(capability_config.llm_config)
        return None


def resolve_ai_config_path(app_root: Path | None = None) -> Path:
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


def load_ai_config_json(app_root: Path | None = None) -> dict[str, Any]:
    ai_path = resolve_ai_config_path(app_root)
    if not ai_path.exists():
        return {}

    try:
        data = json.loads(ai_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("AI_CONFIG_LOAD_FAILED %s: %s", ai_path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def resolve_refinement_policy_config_path(app_root: Path | None = None) -> Path:
    """Return the path to the workspace LLM profile config (``app/config/refinement_policy.yaml``).

    The canonical location is ``{workspace}/app/config/refinement_policy.yaml`` — co-located
    with ``ai.json`` in the operator-facing config directory.
    """
    if app_root is not None:
        return (app_root / "config" / "refinement_policy.yaml").resolve()

    active_root = resolve_active_app_root()
    active_path = (active_root / "config" / "refinement_policy.yaml").resolve()
    if active_path.exists():
        return active_path

    factory_root = resolve_factory_app_root()
    if factory_root is not None:
        factory_path = (factory_root / "app" / "config" / "refinement_policy.yaml").resolve()
        if factory_path.exists():
            return factory_path

    return active_path


def load_control_plane_config_yaml(app_root: Path | None = None) -> dict[str, Any]:
    config_path = resolve_refinement_policy_config_path(app_root)
    if not config_path.exists():
        return {}

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("REFINEMENT_POLICY_LOAD_FAILED %s: %s", config_path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_control_plane_config(app_root: Path | None = None) -> ControlPlaneConfig:
    raw = load_control_plane_config_yaml(app_root)
    if not isinstance(raw, dict) or not raw:
        config_path = resolve_refinement_policy_config_path(app_root)
        if config_path.exists():
            # Config file exists but failed to parse — already logged at WARNING by
            # load_control_plane_config_yaml. Surface the disabled state here too.
            logger.warning(
                "REFINEMENT_POLICY_DISABLED: config at %s failed to parse; "
                "refinement engine running with all app-local features disabled",
                config_path,
            )
        return ControlPlaneConfig()
    cfg = ControlPlaneConfig.model_validate(raw)
    logger.info(
        "REFINEMENT_POLICY_LOADED: enabled=%s path=%s",
        cfg.enabled,
        resolve_refinement_policy_config_path(app_root),
    )
    return cfg


def load_refinement_policy_config_yaml(app_root: Path | None = None) -> dict[str, Any]:
    return load_control_plane_config_yaml(app_root)


def load_refinement_policy_config(app_root: Path | None = None) -> ControlPlaneConfig:
    return load_control_plane_config(app_root)


resolve_control_plane_runtime_config_path = resolve_refinement_policy_config_path
