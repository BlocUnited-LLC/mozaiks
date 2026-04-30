from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import active_app_root


ROOT = Path(__file__).resolve().parents[1]
FACTORY_APP_ROOT = ROOT / "factory_app" / "app"


def _load_yaml(app_root: Path, relative_path: str) -> dict:
    with open(app_root / relative_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def test_active_mozaiks_app_runtime_families_exist() -> None:
    app_root = active_app_root()
    assert (app_root / "app.json").exists()
    assert (app_root / "brand").is_dir()
    assert (app_root / "config").is_dir()
    assert (app_root / "modules").is_dir()
    assert (app_root / "ui" / "pages").is_dir()
    assert (app_root / "workflows").is_dir()


def test_factory_app_runtime_families_exist() -> None:
    assert (FACTORY_APP_ROOT / "app.json").exists()
    assert (FACTORY_APP_ROOT / "brand").is_dir()
    assert (FACTORY_APP_ROOT / "brand" / "theme_config.json").exists()
    assert (FACTORY_APP_ROOT / "config").is_dir()
    assert (FACTORY_APP_ROOT / "config" / "ai.json").exists()
    assert (FACTORY_APP_ROOT / "config" / "shell.json").exists()
    assert (FACTORY_APP_ROOT / "config" / "admin.json").exists()
    assert (FACTORY_APP_ROOT / "modules").is_dir()
    assert (FACTORY_APP_ROOT / "ui" / "index.js").exists()
    assert (FACTORY_APP_ROOT / "ui" / "route_manifest.json").exists()
    assert (FACTORY_APP_ROOT / "ui" / "pages").is_dir()
    assert (FACTORY_APP_ROOT / "workflows").is_dir()


def test_removed_platform_families_stay_removed() -> None:
    app_root = active_app_root()
    assert not (app_root / "automations").exists()
    assert not (app_root / "components").exists()


def test_mozaiks_app_workflows_are_local_and_builder_workflows_are_shared() -> None:
    app_root = active_app_root()
    app_marketing = _load_yaml(app_root, "workflows/AppMarketing/orchestrator.yaml")
    investor_marketplace = _load_yaml(app_root, "workflows/InvestorMarketplace/orchestrator.yaml")

    assert app_marketing["workflow_name"] == "AppMarketing"
    assert investor_marketplace["workflow_name"] == "InvestorMarketplace"
    assert app_marketing["workflow_startup_mode"] == "AgentDriven"
    assert investor_marketplace["workflow_startup_mode"] == "AgentDriven"

    shared_root = ROOT / "factory_app" / "app" / "workflows"
    for workflow_name in ("AppGenerator", "AgentGenerator", "ExistingAppDiscovery"):
        orchestrator = shared_root / workflow_name / "orchestrator.yaml"
        assert orchestrator.exists(), f"Expected shared workflow: {workflow_name}"
