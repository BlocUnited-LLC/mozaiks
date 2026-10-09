from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.control_plane import (
    ALLOWED_CONTROL_PLANE_LLM_PROFILE_IDS,
    ControlPlaneConfig,
    load_control_plane_config,
)


def test_factory_app_refinement_policy_enables_refinement_engine() -> None:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    config = load_control_plane_config(app_root)

    assert config.enabled is True
    assert set(config.llm_profiles) == {
        "classifier", "impact_analyzer", "architecture", "codegen",
    }
    assert set(config.llm_profiles) <= set(ALLOWED_CONTROL_PLANE_LLM_PROFILE_IDS)
    assert config.classifier.enabled is True
    assert config.classifier.llm_profile == "classifier"
    classifier_cfg = config.resolve_capability_llm_config("classifier")
    assert classifier_cfg is not None
    assert classifier_cfg["model"]  # any non-empty model is valid
    assert config.coding.enabled is True
    assert config.coding.llm_profile == "codegen"
    coding_cfg = config.resolve_capability_llm_config("coding")
    assert coding_cfg is not None
    assert coding_cfg["model"]  # any non-empty model is valid
    assert config.scope.llm_profile == "impact_analyzer"
    assert config.contract_surface.llm_profile == "impact_analyzer"
    assert config.contract_surface.regeneration_llm_profile == "codegen"
    assert config.resolve_capability_llm_config("scope") == config.llm_profiles["impact_analyzer"].llm_config
    assert config.resolve_capability_llm_config("contract_surface") == config.llm_profiles["impact_analyzer"].llm_config
    assert config.resolve_contract_surface_regeneration_llm_config() == config.llm_profiles["codegen"].llm_config


def test_factory_refinement_policy_config_is_staged_under_app_config() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runtime_path = repo_root / "factory_app" / "app" / "config" / "refinement_policy.yaml"
    data = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))

    assert data["enabled"] is True
    assert "profile" not in data
    assert data["classifier"]["llm_profile"] == "classifier"
    assert data["coding"]["llm_profile"] == "codegen"
    assert data["contract_surface"]["regeneration_llm_profile"] == "codegen"


def test_refinement_policy_rejects_unknown_llm_profile_id() -> None:
    try:
        ControlPlaneConfig.model_validate(
            {
                "enabled": True,
                "llm_profiles": {
                    "unknown_profile": {
                        "purpose": "invalid",
                        "llm_config": {"model": "example"},
                    }
                },
            }
        )
    except ValueError as exc:
        assert "Unknown refinement policy LLM profile id" in str(exc)
    else:
        raise AssertionError("unknown profile id should fail validation")


def test_control_plane_accepts_runtime_profile_metadata() -> None:
    config = ControlPlaneConfig.model_validate(
        {
            "schema_version": "mozaiks.refinement.policy.v1",
            "enabled": True,
            "profile": "mozaiks_app",
        }
    )

    assert config.profile == "mozaiks_app"


def test_control_plane_rejects_unknown_capability_profile_reference() -> None:
    with pytest.raises(ValidationError, match="references unknown LLM profile 'classifier'"):
        ControlPlaneConfig.model_validate(
            {
                "enabled": True,
                "classifier": {"enabled": True, "llm_profile": "classifier"},
            }
        )


@pytest.mark.parametrize("capability", ["classifier", "scope", "coding", "contract_surface"])
def test_enabled_capability_requires_named_profile_with_model(capability: str) -> None:
    with pytest.raises(ValidationError, match=f"Refinement capability '{capability}' requires llm_profile"):
        ControlPlaneConfig.model_validate({"enabled": True, capability: {"enabled": True}})

    with pytest.raises(ValidationError, match="requires a non-empty model"):
        ControlPlaneConfig.model_validate({
            "enabled": True,
            "llm_profiles": {"codegen": {"llm_config": {"model": "  "}}},
            capability: {"enabled": True, "llm_profile": "codegen"},
        })


def test_enabled_surface_requires_separate_regeneration_profile() -> None:
    policy = {
        "enabled": True,
        "llm_profiles": {"impact_analyzer": {"llm_config": {"model": "planning-model"}}},
        "contract_surface": {"enabled": True, "llm_profile": "impact_analyzer"},
    }
    with pytest.raises(ValidationError, match="contract_surface.regeneration_llm_profile requires llm_profile"):
        ControlPlaneConfig.model_validate(policy)


@pytest.mark.parametrize("capability", ["classifier", "scope", "coding", "contract_surface"])
def test_enabled_capability_rejects_config_list_model_override(capability: str) -> None:
    with pytest.raises(ValidationError, match="config_list is not supported"):
        ControlPlaneConfig.model_validate({
            "enabled": True,
            "llm_profiles": {"codegen": {"llm_config": {
                "model": "declared-model",
                "config_list": [{"model": "different-runtime-model"}],
            }}},
            capability: {"enabled": True, "llm_profile": "codegen"},
        })


def test_enabled_surface_rejects_regeneration_config_list_model_override() -> None:
    with pytest.raises(ValidationError, match="contract_surface.regeneration_llm_profile.*config_list is not supported"):
        ControlPlaneConfig.model_validate({
            "enabled": True,
            "llm_profiles": {
                "impact_analyzer": {"llm_config": {"model": "planning-model"}},
                "codegen": {"llm_config": {
                    "model": "declared-model",
                    "config_list": [{"model": "different-runtime-model"}],
                }},
            },
            "contract_surface": {
                "enabled": True,
                "llm_profile": "impact_analyzer",
                "regeneration_llm_profile": "codegen",
            },
        })


def test_capability_rejects_inline_model_and_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="llm_config"):
        ControlPlaneConfig.model_validate({
            "enabled": True,
            "classifier": {"enabled": True, "llm_config": {"model": "model"}},
        })
    with pytest.raises(ValidationError, match="surprise"):
        ControlPlaneConfig.model_validate({"scope": {"surprise": True}})


def test_disabled_capability_needs_no_model_profile() -> None:
    assert ControlPlaneConfig.model_validate({"enabled": True, "coding": {"enabled": False}}).coding.enabled is False


def test_refinement_policy_rejects_unused_profile_temperature(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    config_dir = app_root / "config"
    config_dir.mkdir(parents=True)
    policy = {
        "enabled": True,
        "llm_profiles": {
            "classifier": {
                "default_temperature": 0.5,
                "llm_config": {"model": "test-model"},
            }
        },
        "classifier": {"enabled": True, "llm_profile": "classifier"},
    }
    policy_path = config_dir / "refinement_policy.yaml"
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")

    with pytest.raises(ValidationError, match="default_temperature") as exc_info:
        load_control_plane_config(app_root)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"

    profile = policy["llm_profiles"]["classifier"]
    profile.pop("default_temperature")
    profile["llm_config"]["temperature"] = 0.5
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")

    config = load_control_plane_config(app_root)
    assert config.resolve_capability_llm_config("classifier") == {
        "model": "test-model",
        "temperature": 0.5,
    }


def test_factory_workflows_do_not_reference_undeclared_llm_profiles() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    app_root = repo_root / "factory_app" / "app"
    config = load_control_plane_config(app_root)
    declared = set(config.llm_profiles)
    references: list[str] = []

    control_plane = yaml.safe_load(
        (repo_root / "factory_app" / "app" / "config" / "refinement_policy.yaml").read_text(encoding="utf-8")
    )
    for value in control_plane.values():
        if isinstance(value, dict):
            for field in ("llm_profile", "regeneration_llm_profile"):
                if isinstance(value.get(field), str):
                    references.append(value[field])

    for path in (repo_root / "factory_app" / "workflows").rglob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        references.extend(_collect_llm_profile_refs(data))

    undeclared = sorted({ref for ref in references if ref not in declared})
    assert undeclared == []


def _collect_llm_profile_refs(value) -> list[str]:  # noqa: ANN001
    if isinstance(value, dict):
        refs = [str(value["llm_profile"])] if isinstance(value.get("llm_profile"), str) else []
        for child in value.values():
            refs.extend(_collect_llm_profile_refs(child))
        return refs
    if isinstance(value, list):
        refs: list[str] = []
        for child in value:
            refs.extend(_collect_llm_profile_refs(child))
        return refs
    return []


def test_missing_control_plane_config_defaults_disabled(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "config" / "ai.json").write_text(
        json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        encoding="utf-8",
    )

    config = load_control_plane_config(app_root)

    assert config == ControlPlaneConfig()
