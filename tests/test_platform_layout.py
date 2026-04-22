from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_APP_ROOT = ROOT / "mozaiks-platform" / "app"


def _load_yaml(relative_path: str) -> dict:
    with open(ACTIVE_APP_ROOT / relative_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def test_active_mozaiks_app_runtime_families_exist() -> None:
    assert (ACTIVE_APP_ROOT / "app.json").exists()
    assert (ACTIVE_APP_ROOT.parent / "brand").is_dir()
    assert (ACTIVE_APP_ROOT / "config").is_dir()
    assert (ACTIVE_APP_ROOT / "modules").is_dir()
    assert (ACTIVE_APP_ROOT / "pages").is_dir()
    assert (ACTIVE_APP_ROOT / "workflows").is_dir()


def test_removed_platform_families_stay_removed() -> None:
    assert not (ACTIVE_APP_ROOT / "automations").exists()
    assert not (ACTIVE_APP_ROOT / "components").exists()


def test_mozaiks_builder_workflows_live_in_active_app_root() -> None:
    app_generator = _load_yaml("workflows/AppGenerator/orchestrator.yaml")
    agent_generator = _load_yaml("workflows/AgentGenerator/orchestrator.yaml")
    existing_discovery = _load_yaml("workflows/ExistingAppDiscovery/orchestrator.yaml")

    assert app_generator["workflow_name"] == "AppGenerator"
    assert agent_generator["workflow_name"] == "AgentGenerator"
    assert existing_discovery["workflow_name"] == "ExistingAppDiscovery"
    assert app_generator["workflow_startup_mode"] == "AgentDriven"
    assert agent_generator["workflow_startup_mode"] == "AgentDriven"
