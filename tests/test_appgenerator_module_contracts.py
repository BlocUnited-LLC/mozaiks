from __future__ import annotations

from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str):
    return yaml.safe_load(_read(relative_path))


def test_appgenerator_structured_outputs_include_canonical_module_contract_models() -> None:
    config = _read_yaml("mozaiks-platform/app/workflows/AppGenerator/structured_outputs.yaml")
    models = config["models"]
    registry = config["registry"]

    assert registry["ConfigMiddlewareAgent"] == "ConfigMiddlewareOutput"
    assert "module_contract" in models["AppBuildTask"]["fields"]["task_type"]["values"]
    assert "platform_config" not in models["AppBuildTask"]["fields"]["task_type"]["values"]

    for model_name in [
        "ModuleManifest",
        "ModuleEventsManifest",
        "ModuleSubscriptionsManifest",
        "ModuleNotificationsManifest",
        "ModuleSettingsManifest",
        "ModuleAdminPanel",
        "ModuleAdminManifest",
        "ModuleContractBundle",
        "ConfigMiddlewareOutput",
    ]:
        assert model_name in models

    contract_fields = models["ModuleContractBundle"]["fields"]
    assert contract_fields["admin_yaml"]["type"] == "ModuleAdminManifest"


def test_appgenerator_prompts_emit_modules_contract_instead_of_legacy_operations_contract() -> None:
    source = _read("mozaiks-platform/app/workflows/AppGenerator/agents.yaml")

    assert "task_type: module_contract" in source
    assert "modules/{pack_name}/module.yaml" in source
    assert "modules/{pack_name}/subscriptions.yaml" in source
    assert "modules/{pack_name}/admin.yaml" in source
    assert "backend/handler.py" in source
    assert "domain.task_manager.task_created" in source
    assert "schema_version: mozaiks.admin.v1" in source

    assert "task_type: platform_config" not in source
    assert "operations/{pack_name}" not in source
    assert "subscription.yaml" not in source
    assert "operation.yaml" not in source
    assert "mozaiks-core-public" not in source
    assert "admin_surfaces" not in source


def test_appgenerator_download_tool_does_not_inject_legacy_admin_surfaces() -> None:
    source = _read("mozaiks-platform/app/workflows/AppGenerator/tools/generate_and_download.py")

    assert "admin_surfaces" not in source
    assert "_inject_admin_surfaces(files_map)" not in source
