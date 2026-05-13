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
    config = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    models = config["models"]
    registry = config["registry"]

    assert registry["ConfigMiddlewareAgent"] == "ConfigMiddlewareOutput"
    assert registry["DatabaseAgent"] == "DatabaseOutput"
    assert registry["ModelAgent"] == "ModelOutput"
    assert registry["ServiceAgent"] == "ServiceOutput"
    assert registry["FrontendStubAgent"] == "FrontendStubOutput"
    assert registry["ControllerAgent"] == "ControllerOutput"
    assert "module_contract" in models["AppBuildTask"]["fields"]["task_type"]["values"]
    assert "platform_config" not in models["AppBuildTask"]["fields"]["task_type"]["values"]

    for model_name in [
        "ModuleIdentity",
        "ModuleManifest",
        "ModuleEventsManifest",
        "ModuleSubscriptionsManifest",
        "ModuleNotificationsManifest",
        "ModuleSettingsManifest",
        "ModuleAdminPanel",
        "ModuleAdminManifest",
        "ModulePythonStub",
        "ModuleJsStub",
        "DatabaseArtifactFile",
        "DatabaseOutput",
        "BackendFoundationFile",
        "BackendFoundationBundle",
        "ModelFile",
        "ModelOutput",
        "AppBackendAdminPanel",
        "AppBackendAdminConfig",
        "AppCustomRouteEntry",
        "AppCustomPageFile",
        "AppCustomRouteBundle",
        "AppPageNavigation",
        "AppShellMode",
        "AppShellNavigationPatch",
        "AppShellNavigationPolicyPatch",
        "AppShellChromePatch",
        "AppShellChromeModePatch",
        "AppShellChromeViewportPatch",
        "ModuleContractBundle",
        "ConfigMiddlewareOutput",
        "ImplementedPythonStub",
        "ServiceOutput",
        "ImplementedJsStub",
        "FrontendStubOutput",
        "ControllerOutput",
    ]:
        assert model_name in models

    contract_fields = models["ModuleContractBundle"]["fields"]
    assert contract_fields["admin_yaml"]["type"] == "ModuleAdminManifest"
    assert contract_fields["python_stubs"]["items"] == "ModulePythonStub"
    assert contract_fields["js_stubs"]["items"] == "ModuleJsStub"
    assert models["ConfigMiddlewareOutput"]["fields"]["mode"]["values"] == [
        "module_contract_bundle",
        "backend_foundation",
    ]
    assert models["ConfigMiddlewareOutput"]["fields"]["backend_foundation_bundle"]["variants"] == [
        "BackendFoundationBundle",
        "null",
    ]
    assert models["ModuleJsStub"]["fields"]["surface"]["values"] == ["admin_component"]
    assert models["AppSchemaOutput"]["fields"]["custom_route_bundle"]["variants"] == ["AppCustomRouteBundle", "null"]
    assert models["AppManifest"]["fields"]["custom_routes"]["items"] == "str"
    assert models["AppCustomRouteBundle"]["fields"]["route_manifest"]["items"] == "AppCustomRouteEntry"
    assert models["AppCustomRouteBundle"]["fields"]["page_files"]["items"] == "AppCustomPageFile"
    assert models["AppPageSchema"]["fields"]["navigation"]["variants"] == ["AppPageNavigation", "null"]
    assert models["AppPageSchema"]["fields"]["shell_mode"]["variants"] == ["AppShellMode", "null"]
    assert models["AppBuildPage"]["fields"]["shell_mode_hint"]["variants"] == ["str", "null"]
    assert models["AppShellMode"]["values"] == [
        "standard",
        "workspace",
        "conversation",
        "focused",
        "immersive",
        "public",
    ]
    assert models["AppPageNavigation"]["fields"]["scope"]["values"] == ["global", "local", "profile", "footer"]
    assert models["AppShellConfigPatch"]["fields"]["navigation"]["variants"] == ["AppShellNavigationPatch", "null"]
    assert models["AppShellConfigPatch"]["fields"]["chrome"]["variants"] == ["AppShellChromePatch", "null"]
    assert models["AppShellChromePatch"]["fields"]["defaultMode"]["variants"] == ["AppShellMode", "null"]
    assert models["AppShellNavigationItemPatch"]["fields"]["scope"]["values"] == ["global", "local", "profile", "footer"]
    assert models["ModuleManifest"]["fields"]["module"]["type"] == "ModuleIdentity"
    assert models["ModuleAdminManifest"]["fields"]["schema_version"]["description"] == "Must be mozaiks.admin.v2."
    assert models["AppBackendAdminConfig"]["fields"]["schema_version"]["values"] == ["mozaiks.admin.app_backend.v1"]
    assert models["ControllerOutput"]["fields"]["mode"]["values"] == [
        "module_api_adapter",
        "app_backend_admin_surface",
    ]
    assert models["ControllerOutput"]["fields"]["app_backend_admin_config"]["variants"] == [
        "AppBackendAdminConfig",
        "null",
    ]
    assert models["ServiceOutput"]["fields"]["python_files"]["items"] == "ImplementedPythonStub"
    assert models["FrontendStubOutput"]["fields"]["js_files"]["items"] == "ImplementedJsStub"
    assert models["FrontendStubOutput"]["fields"]["registration_barrel"]["variants"] == ["str", "null"]
    assert models["DatabaseOutput"]["fields"]["database_files"]["items"] == "DatabaseArtifactFile"
    assert models["ModelOutput"]["fields"]["model_files"]["items"] == "ModelFile"
    assert models["AppValidation"]["fields"]["validation_strategy"]["values"] == ["e2b", "local", "skip"]
    assert models["AppValidation"]["fields"]["validation_status"]["values"] == ["passed", "failed", "skipped"]
    admin_panel_fields = models["ModuleAdminPanel"]["fields"]
    assert admin_panel_fields["section"]["values"] == [
        "overview",
        "users",
        "billing",
        "usage",
        "operations",
        "settings",
        "integrations",
        "support",
    ]
    assert admin_panel_fields["layout"]["variants"] == ["str", "null"]
    assert admin_panel_fields["sections"]["items"] == "AppPageSection"


def test_appgenerator_prompts_emit_modules_contract_instead_of_legacy_operations_contract() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")
    handoffs = _read_yaml("factory_app/workflows/AppGenerator/handoffs.yaml")
    file_contracts = _read_yaml("factory_app/workflows/AppGenerator/tools/file_contracts.yaml")
    module_archetypes = _read_yaml("factory_app/workflows/AppGenerator/tools/module_archetypes.yaml")

    assert "task_type: module_contract" in source
    assert "backend_foundation_bundle" in source
    assert "Do NOT include an `admin_config` build task." in source
    assert "Fail the task rather than guessing a fallback mode." in source
    assert "backend/handler.py" in source
    assert "Frontend Stub Agent" in source
    assert "module_contract.js_stubs" in source
    assert "ServiceOutput" in source
    assert "FrontendStubOutput" in source
    assert "\"python_files\"" in source
    assert "\"js_files\"" in source
    assert "\"registration_barrel\"" in source
    assert "`ui/index.js` registration barrel" in source
    assert "Persistent app pages still belong in `app.json` + `ui/pages/*.yaml`." in source
    assert "`ui/route_manifest.json` + `ui/pages/custom/*.jsx`" in source
    assert "custom_route_bundle" in source
    assert "schema_version: mozaiks.admin.app_backend.v1" in source
    assert "ControllerOutput.app_backend_admin_config" in source
    assert "\"mode\": \"app_backend_admin_surface\"" in source
    assert "\"app_backend_admin_config\"" in source
    assert "backend/admin_config.py" in source
    assert "backend/routes/admin.py" in source
    assert "APIRouter" in source
    assert "self-contained FastAPI" in source
    assert "`app/app.json` `admins`" in source
    assert "`platform/config/admin.json`" not in source
    assert "panels: []" in source  # admin.yaml default is empty panels
    assert "do not invent admin panels" in source
    assert "structured-output-first contract" in source
    assert "app_validation_strategy" in source
    assert "validation_status" in source
    assert "validate_app_build" in source
    assert "passed` or explicit `skipped`" in source
    assert "[FILE CONTRACTS CONTEXT]" in source
    assert "[MODULE ARCHETYPES CONTEXT]" in source
    assert "python_stubs" in source
    assert "js_stubs" in source
    assert "\"database_files\"" in source
    assert "\"model_files\"" in source
    assert "contract_refs" in source
    assert "`page_component` and `shell_extension`" not in source
    assert "task_type: admin_config" not in source
    assert "mode\": \"admin_config_bundle\"" not in source
    assert "host_admin_config" not in source
    assert "`app/config/admin.json`" not in source
    assert "Module contract file set (Mode A)" not in source
    assert "Mode A (module_contract) example" not in source
    assert "Mode B (backend_foundation) example" not in source
    assert "Split app-backend admin surface example" not in source
    assert "states.yaml" not in source
    assert "transitions.yaml" not in source
    assert any(
        rule["source_agent"] == "ServiceAgent" and rule["target_agent"] == "FrontendStubAgent"
        for rule in handoffs["handoff_rules"]
    )
    assert any(
        rule["source_agent"] == "FrontendStubAgent" and rule["target_agent"] == "ControllerAgent"
        for rule in handoffs["handoff_rules"]
    )

    assert "task_type: platform_config" not in source
    assert "operations/{pack_name}" not in source
    assert "subscription.yaml" not in source
    assert "operation.yaml" not in source
    assert "admin_surfaces" not in source
    assert "legacy standalone" not in source
    assert "backend/main.py" not in source
    assert "backend/routes/api_router.py" not in source
    assert "RouteAgent" not in source
    assert "EntryPointAgent" not in source
    assert "page_component" not in _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    assert "shell_extension" not in _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")

    module_contract = file_contracts["task_contracts"]["module_contract"]
    assert module_contract["required_outputs"] == ["modules/{pack_name}/module.yaml"]
    assert "modules/{pack_name}/subscriptions.yaml" in module_contract["optional_outputs"]
    assert "modules/{pack_name}/admin.yaml" in module_contract["optional_outputs"]
    assert "backend/handler.py" in module_contract["downstream_python_defaults"]
    assert "backend/notifications.py" in module_contract["optional_python_hooks"]
    assert "ui/index.js" in module_contract["optional_js_stubs"]

    workflow_archetype = module_archetypes["archetypes"]["workflow"]
    messaging_archetype = module_archetypes["archetypes"]["messaging"]
    assert "policy.py exposes a validate_transition helper." in workflow_archetype["hard_constraints"]
    assert "Event payloads include delivery metadata needed for real-time routing." in messaging_archetype["hard_constraints"]
    assert "Use one of these exact top-level shapes:" in source
    assert "Use one of these exact bounded shapes:" in source
    assert "Exact nested field shapes come from `ConfigMiddlewareOutput`, `ModuleContractBundle`, and `BackendFoundationBundle` in `structured_outputs.yaml`." in source
    assert "Exact nested field shapes come from `ControllerOutput` and `AppBackendAdminConfig` in `structured_outputs.yaml`." in source


def test_appgenerator_download_tool_does_not_inject_legacy_admin_surfaces() -> None:
    source = _read("factory_app/workflows/AppGenerator/tools/generate_and_download.py")
    assembly = _read("factory_app/workflows/AppGenerator/tools/assembly_phase.py")

    assert "admin_surfaces" not in source
    assert "_inject_admin_surfaces(files_map)" not in source
    assert "extract_code_file_map_from_payload" in source
    assert "extract_code_file_entries_from_payload" in assembly


def test_appgenerator_prompt_time_contract_artifacts_align_with_structured_outputs() -> None:
    structured_outputs = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    file_contracts = _read_yaml("factory_app/workflows/AppGenerator/tools/file_contracts.yaml")
    module_archetypes = _read_yaml("factory_app/workflows/AppGenerator/tools/module_archetypes.yaml")

    task_type_values = structured_outputs["models"]["AppBuildTask"]["fields"]["task_type"]["values"]
    module_type_values = structured_outputs["models"]["ModuleIdentity"]["fields"]["type"]["values"]

    assert set(file_contracts["task_contracts"].keys()).issubset(set(task_type_values))
    assert file_contracts["task_contracts"]["page_bundle"]["owner_agent"] == "AppSchemaAgent"
    assert file_contracts["task_contracts"]["module_contract"]["owner_agent"] == "ConfigMiddlewareAgent"
    assert file_contracts["task_contracts"]["backend_foundation"]["owner_agent"] == "ConfigMiddlewareAgent"
    assert file_contracts["task_contracts"]["api_surface"]["owner_agent"] == "ControllerAgent"

    assert set(module_archetypes["archetypes"].keys()) == set(module_type_values)

