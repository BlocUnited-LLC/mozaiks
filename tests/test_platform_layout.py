from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.import_utils import active_app_root

ROOT = Path(__file__).resolve().parents[1]
FACTORY_APP_ROOT = ROOT / "factory_app" / "app"


def _load_yaml(app_root: Path, relative_path: str) -> dict:
    with open(app_root / relative_path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def test_factory_app_bundle_satisfies_app_runtime_contract() -> None:
    assert (FACTORY_APP_ROOT / "app.json").exists()
    assert (FACTORY_APP_ROOT / "brand").is_dir()
    assert (FACTORY_APP_ROOT / "config").is_dir()
    assert (FACTORY_APP_ROOT / "modules").is_dir()
    assert (FACTORY_APP_ROOT / "ui" / "pages").is_dir()


def test_factory_app_runtime_families_exist() -> None:
    assert (FACTORY_APP_ROOT / "app.json").exists()
    assert (FACTORY_APP_ROOT / "brand").is_dir()
    assert (FACTORY_APP_ROOT / "brand" / "theme_config.json").exists()
    assert (FACTORY_APP_ROOT / "config").is_dir()
    assert (FACTORY_APP_ROOT / "config" / "ai.json").exists()
    assert (FACTORY_APP_ROOT / "config" / "shell.json").exists()
    assert not (FACTORY_APP_ROOT / "config" / "admin.json").exists()
    assert (FACTORY_APP_ROOT / "modules").is_dir()
    assert (FACTORY_APP_ROOT / "ui" / "index.js").exists()
    assert (FACTORY_APP_ROOT / "ui" / "route_manifest.json").exists()
    assert (FACTORY_APP_ROOT / "ui" / "pages").is_dir()
    assert (ROOT / "factory_app" / "workflows").is_dir()


def test_factory_app_bundle_has_no_retired_families() -> None:
    assert not (FACTORY_APP_ROOT / "automations").exists()
    assert not (FACTORY_APP_ROOT / "components").exists()
    assert not (FACTORY_APP_ROOT / "app.yaml").exists()


def test_shared_builder_workflows_exist() -> None:
    """Core shared builder workflows must always be present under factory_app/workflows."""
    shared_root = ROOT / "factory_app" / "workflows"
    for workflow_name in ("AppGenerator", "AgentGenerator", "ExistingAppDiscovery"):
        orchestrator = shared_root / workflow_name / "orchestrator.yaml"
        assert orchestrator.exists(), f"Expected shared workflow: {workflow_name}"


def test_product_workspace_workflows_are_local_and_builder_workflows_are_shared() -> None:
    """Product workspace app-local workflows must live beside app/, not in factory_app/workflows."""
    app_root = active_app_root()
    workflows_root = app_root.parent / "workflows" if app_root.name == "app" else app_root / "workflows"
    if not (workflows_root / "AppMarketing").is_dir():
        pytest.skip("Product workspace not configured — AppMarketing workflow missing")
    app_marketing = yaml.safe_load((workflows_root / "AppMarketing" / "orchestrator.yaml").read_text(encoding="utf-8"))
    investor_marketplace = yaml.safe_load((workflows_root / "InvestorMarketplace" / "orchestrator.yaml").read_text(encoding="utf-8"))

    assert app_marketing["workflow_name"] == "AppMarketing"
    assert investor_marketplace["workflow_name"] == "InvestorMarketplace"
    assert app_marketing["workflow_startup_mode"] == "AgentDriven"
    assert investor_marketplace["workflow_startup_mode"] == "AgentDriven"

