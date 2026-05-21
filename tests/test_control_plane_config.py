from __future__ import annotations

import json
from pathlib import Path

import yaml

from mozaiksai.control_plane import (
    ALLOWED_CONTROL_PLANE_LLM_PROFILE_IDS,
    ControlPlaneConfig,
    load_control_plane_config,
)


def test_factory_app_ai_config_enables_control_plane() -> None:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    config = load_control_plane_config(app_root)

    assert config.enabled is True
    assert config.profile == "default"
    assert tuple(config.llm_profiles.keys()) == ALLOWED_CONTROL_PLANE_LLM_PROFILE_IDS
    assert config.classifier.enabled is True
    assert config.classifier.llm_profile == "classifier"
    assert config.resolve_capability_llm_config("classifier") == {
        "model": "gpt-4o-mini",
        "temperature": 0.0,
    }
    assert config.coding.enabled is True
    assert config.coding.llm_profile == "codegen"
    assert config.resolve_capability_llm_config("coding") == {
        "model": "gpt-5.2-codex",
        "temperature": 0.1,
    }


def test_control_plane_rejects_unknown_llm_profile_id() -> None:
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
        assert "Unknown control-plane LLM profile id" in str(exc)
    else:
        raise AssertionError("unknown profile id should fail validation")


def test_control_plane_rejects_unknown_capability_profile_reference() -> None:
    config = ControlPlaneConfig.model_validate(
        {
            "enabled": True,
            "classifier": {"enabled": True, "llm_profile": "classifier"},
        }
    )

    try:
        config.resolve_capability_llm_config("classifier")
    except ValueError as exc:
        assert "references unknown LLM profile 'classifier'" in str(exc)
    else:
        raise AssertionError("undeclared profile reference should fail resolution")


def test_control_plane_capability_llm_config_fallback_remains_supported() -> None:
    config = ControlPlaneConfig.model_validate(
        {
            "enabled": True,
            "classifier": {
                "enabled": True,
                "llm_config": {"model": "gpt-4o-mini", "temperature": 0.0},
            },
        }
    )

    assert config.resolve_capability_llm_config("classifier") == {
        "model": "gpt-4o-mini",
        "temperature": 0.0,
    }


def test_factory_workflows_do_not_reference_undeclared_llm_profiles() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    app_root = repo_root / "factory_app" / "app"
    config = load_control_plane_config(app_root)
    declared = set(config.llm_profiles)
    references: list[str] = []

    ai_data = json.loads((app_root / "config" / "ai.json").read_text(encoding="utf-8"))
    control_plane = ai_data["control_plane"]
    for value in control_plane.values():
        if isinstance(value, dict) and isinstance(value.get("llm_profile"), str):
            references.append(value["llm_profile"])

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
