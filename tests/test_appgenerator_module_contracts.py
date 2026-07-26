from __future__ import annotations

from pathlib import Path

import yaml

from mozaiksai.core.workflow.declarative.contracts import parse_structured_outputs_config


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str):
    return yaml.safe_load(_read(relative_path))


def test_appgenerator_structured_outputs_parse_with_runtime_contract_validator() -> None:
    """AppGenerator structured outputs must stay loadable by WorkflowManager."""
    config = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")

    parsed = parse_structured_outputs_config(config)

    assert parsed["models"]["ModuleIdentity"]["fields"]["user_data_scope"]["type"] == "bool"


def test_appgenerator_structured_outputs_include_canonical_module_contract_models() -> None:
    config = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    models = config["models"]
    registry = config["registry"]

    assert registry["ConfigMiddlewareAgent"] == "ConfigMiddlewareOutput"
    assert registry["RefinementHarnessAgent"] == "RefinementHarnessOutput"
    assert registry["DatabaseAgent"] == "DatabaseOutput"
    assert registry["ModelAgent"] == "ModelOutput"
    assert registry["ServiceAgent"] == "ServiceOutput"
    assert registry["ModuleRuntimeQualityAgent"] == "ModuleRuntimeQualityReviewRequest"
    assert registry["FrontendStubAgent"] == "FrontendStubOutput"
    assert registry["ControllerAgent"] == "ControllerOutput"
    assert "module_contract" in models["AppBuildTask"]["fields"]["task_type"]["values"]
    assert "platform_config" not in models["AppBuildTask"]["fields"]["task_type"]["values"]
    assert "provider_adapter" in models["BackendFoundationFile"]["fields"]["kind"]["values"]

    for model_name in [
        "ModuleIdentity",
        "ModuleManifest",
        "ModuleEventsManifest",
        "ModuleReactionsManifest",
        "ModuleNotificationsManifest",
        "ModuleSettingsManifest",
        "ModuleAdminPanel",
        "ModuleAdminManifest",
        "ModuleProfileField",
        "ModuleProfilePanel",
        "ModuleProfileManifest",
        "ModuleRelationshipProvider",
        "ModuleRelationshipsManifest",
        "ModulePolicyHookProvider",
        "ModulePolicyHooksManifest",
        "ModulePythonStub",
        "ModuleJsStub",
        "DatabaseArtifactFile",
        "DatabaseOutput",
        "BackendFoundationFile",
        "BackendFoundationBundle",
        "RefinementHarnessRouteRef",
        "RefinementHarnessArtifactChangeRoutes",
        "RefinementHarnessArtifactRouting",
        "RefinementHarnessRoutingManifest",
        "RefinementHarnessCheckpointManifestOutput",
        "RefinementHarnessManifestOutput",
        "RefinementHarnessToolDefinition",
        "RefinementHarnessToolsManifestOutput",
        "RefinementHarnessPromptFile",
        "RefinementHarnessBundle",
        "RefinementHarnessOutput",
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
        "ModuleRuntimeQualityReviewRequest",
        "ImplementedPythonStub",
        "ServiceOutput",
        "ImplementedJsStub",
        "FrontendStubOutput",
        "ControllerOutput",
        "DeployTargetRuntime",
        "DeployTargetEnvironment",
        "DeployTargetImage",
        "DeployTargetChecks",
        "CiSecretRequirement",
        "CiWorkflowInputRequirement",
        "CiSecretRequirements",
        "SubscriptionConfigBundle",
        "DeployTargetSpec",
        "DeploymentHealthcheck",
        "DeploymentBuildOutput",
        "DeploymentTemplateManifest",
    ]:
        assert model_name in models
    checkpoint_fields = models["RefinementHarnessCheckpointManifestOutput"]["fields"]
    assert checkpoint_fields["event"]["values"] == [
        "request_submitted",
        "scope_requested",
        "contract_surface_requested",
        "coding_requested",
    ]
    assert "id" not in checkpoint_fields
    assert "mode" not in checkpoint_fields
    assert "entrypoint" not in checkpoint_fields
    assert "output_contract" not in checkpoint_fields

    contract_fields = models["ModuleContractBundle"]["fields"]
    assert contract_fields["reactions_yaml"]["type"] == "ModuleReactionsManifest"
    assert contract_fields["admin_yaml"]["type"] == "ModuleAdminManifest"
    assert contract_fields["python_stubs"]["items"] == "ModulePythonStub"
    assert contract_fields["js_stubs"]["items"] == "ModuleJsStub"
    assert "profile_yaml" in contract_fields
    assert contract_fields["profile_yaml"]["variants"] == ["ModuleProfileManifest", "null"]
    assert "relationships_yaml" in contract_fields
    assert contract_fields["relationships_yaml"]["variants"] == ["ModuleRelationshipsManifest", "null"]
    assert "policy_hooks_yaml" in contract_fields
    assert contract_fields["policy_hooks_yaml"]["variants"] == ["ModulePolicyHooksManifest", "null"]

    profile_field_types = models["ModuleProfileField"]["fields"]["type"]["values"]
    assert profile_field_types == ["string", "number", "currency", "date", "boolean", "status"]
    profile_panel_kinds = models["ModuleProfilePanel"]["fields"]["kind"]["values"]
    assert profile_panel_kinds == ["metrics", "list", "component"]
    profile_manifest = models["ModuleProfileManifest"]["fields"]
    assert profile_manifest["schema_version"]["values"] == ["mozaiks.profile.v1"]
    assert profile_manifest["panels"]["items"] == "ModuleProfilePanel"
    assert profile_manifest["tabs"]["items"] == "ModuleProfileTab"
    relationship_provider = models["ModuleRelationshipProvider"]["fields"]
    assert relationship_provider["resource_types"]["items"] == "str"
    assert relationship_provider["relationship_types"]["items"] == "str"
    relationships_manifest = models["ModuleRelationshipsManifest"]["fields"]
    assert relationships_manifest["schema_version"]["values"] == ["mozaiks.relationships.v1"]
    assert relationships_manifest["providers"]["items"] == "ModuleRelationshipProvider"
    policy_hook_provider = models["ModulePolicyHookProvider"]["fields"]
    assert policy_hook_provider["hook_type"]["values"] == ["access", "classification", "decision_input", "custom"]
    assert policy_hook_provider["resource_types"]["items"] == "str"
    policy_hooks_manifest = models["ModulePolicyHooksManifest"]["fields"]
    assert policy_hooks_manifest["schema_version"]["values"] == ["mozaiks.policy_hooks.v1"]
    assert policy_hooks_manifest["hooks"]["items"] == "ModulePolicyHookProvider"
    assert models["ConfigMiddlewareOutput"]["fields"]["mode"]["values"] == [
        "module_contract_bundle",
        "service_foundation",
        "subscription_config",
    ]
    assert models["ConfigMiddlewareOutput"]["fields"]["service_foundation_bundle"]["variants"] == [
        "BackendFoundationBundle",
        "null",
    ]
    assert models["ConfigMiddlewareOutput"]["fields"]["subscription_config_bundle"]["variants"] == [
        "SubscriptionConfigBundle",
        "null",
    ]
    assert models["RefinementHarnessOutput"]["fields"]["refinement_harness"]["type"] == "RefinementHarnessBundle"
    assert models["ModuleJsStub"]["fields"]["surface"]["values"] == ["admin_component", "profile_component"]
    assert models["AppSchemaOutput"]["fields"]["custom_route_bundle"]["variants"] == ["AppCustomRouteBundle", "null"]
    assert models["AppManifest"]["fields"]["custom_routes"]["items"] == "str"
    assert models["AppCustomRouteBundle"]["fields"]["route_manifest"]["items"] == "AppCustomRouteEntry"
    assert models["AppCustomRouteBundle"]["fields"]["page_files"]["items"] == "AppCustomPageFile"
    assert models["AppPageSchema"]["fields"]["navigation"]["variants"] == ["AppPageNavigation", "null"]
    assert models["AppPageSchema"]["fields"]["shell_mode"]["variants"] == ["AppShellMode", "null"]
    assert models["AppBuildPage"]["fields"]["shell_mode_hint"]["variants"] == ["str", "null"]
    assert models["AppBuildPlan"]["fields"]["shell_preset_hint"]["variants"] == ["str", "null"]
    module_action_fields = models["ModuleAction"]["fields"]
    assert "api_surface" in module_action_fields
    assert module_action_fields["api_surface"]["type"] == "optional_str"
    assert "public_readonly" in module_action_fields["api_surface"]["description"]
    assert "AUTH_ENABLED=true" in module_action_fields["api_surface"]["description"]
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
    assert models["AppShellHeaderAction"]["fields"]["intent"]["variants"] == ["AppShellActionIntent", "null"]
    assert models["AppShellHeaderAction"]["fields"]["variants"]["items"] == "AppShellHeaderActionVariant"
    assert models["AppShellActionSurface"]["values"] == [
        "studio",
        "app_studio",
        "workflow_session",
        "transition",
        "public",
        "page",
    ]
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
    assert models["ImplementedJsStub"]["fields"]["surface"]["values"] == ["admin_component", "profile_component"]
    assert models["FrontendStubOutput"]["fields"]["registration_barrel"]["variants"] == ["str", "null"]
    assert models["DatabaseOutput"]["fields"]["database_files"]["items"] == "DatabaseArtifactFile"
    assert models["ModelOutput"]["fields"]["model_files"]["items"] == "ModelFile"
    assert models["AppBuildPlan"]["fields"]["deployment_targets"]["items"] == "DeployTargetSpec"
    assert models["AppBuildPlan"]["fields"]["deployment_template_manifest"]["variants"] == ["DeploymentTemplateManifest", "null"]
    assert "deployment.manifest.json" in models["AppBuildTask"]["fields"]["task_type"]["description"]
    assert "Do not use this model for Dockerfile" in models["BackendFoundationFile"]["fields"]["path"]["description"]
    assert models["DeployTargetSpec"]["fields"]["target_kind"]["values"] == ["container", "compose", "external_adapter"]
    assert models["DeployTargetSpec"]["fields"]["ci_secret_requirements"]["variants"] == ["CiSecretRequirements", "null"]
    assert models["DeploymentTemplateManifest"]["fields"]["ci_secret_requirements"]["variants"] == ["CiSecretRequirements", "null"]
    assert models["CiSecretRequirements"]["fields"]["required"]["items"] == "CiSecretRequirement"
    assert models["CiSecretRequirements"]["fields"]["optional"]["items"] == "CiSecretRequirement"
    assert models["CiSecretRequirements"]["fields"]["workflow_inputs"]["items"] == "CiWorkflowInputRequirement"
    assert models["DeploymentTemplateManifest"]["fields"]["validation_status"]["values"] == ["pending", "valid", "invalid"]
    assert models["AppValidation"]["fields"]["validation_strategy"]["values"] == ["e2b", "local", "skip"]
    assert models["AppValidation"]["fields"]["validation_status"]["values"] == ["passed", "failed", "skipped"]
    admin_panel_fields = models["ModuleAdminPanel"]["fields"]
    assert admin_panel_fields["page"]["type"] == "str"
    assert admin_panel_fields["layout"]["variants"] == ["str", "null"]
    assert admin_panel_fields["sections"]["items"] == "AppPageSection"


def test_appgenerator_prompts_emit_modules_contract_instead_of_removed_operations_contract() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")
    handoffs = _read_yaml("factory_app/workflows/AppGenerator/transition_graph.yaml")
    file_contracts = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
    module_archetypes = _read_yaml("factory_app/build_context/AppGenerator/module_archetypes.yaml")

    assert "task_type: module_contract" in source
    assert "task_type: refinement_harness" in source
    assert "shell_preset_hint" in source
    assert "[SHELL PRESET CONTEXT]" in source
    assert "initial_agent` must be `RefinementHarnessAgent`" in source
    assert "app/config/refinement_policy.yaml" in source
    assert "Output MUST be a valid JSON object matching `RefinementHarnessOutput`" in source
    assert "`current_build_task_type` must equal `refinement_harness`" in source
    assert "RefinementHarnessBundle" in source
    assert "Routes use workflow_sequence only" in source
    assert "service_foundation_bundle" in source
    assert "Do NOT include an `admin_config` build task." in source
    assert "Fail the task" in source
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
    assert "friends, direct messages, user posts, or activity feed" in source
    assert "surface `profile_component`" in source
    assert "actions that render another user's public profile data must declare `input_schema.properties.user_id`" in source
    assert "`ui/route_manifest.json` + `ui/pages/custom/*.jsx`" in source
    assert "custom_route_bundle" in source
    assert "contracts/reactions.yaml" in source
    assert "schema_version: mozaiks.admin.app_backend.v1" in source
    assert "ControllerOutput.app_backend_admin_config" in source
    assert "\"mode\": \"app_backend_admin_surface\"" in source
    assert "\"app_backend_admin_config\"" in source
    assert "backend/admin_config.py" in source
    assert "services/routes/admin.py" in source
    assert "APIRouter" in source
    assert "self-contained FastAPI" in source
    assert "`app/app.json` `admins`" in source
    assert "`platform/config/admin.json`" not in source
    assert "panels: []" in source  # emitted when module has no list actions
    assert "Derive admin panels from the module" in source
    assert "structured-output-first contract" in source
    assert "app_validation_strategy" in source
    assert "validation_status" in source
    assert "validate_app_build" in source
    assert "passed` or explicit `skipped`" in source
    assert "provider-neutral outputs from the deployment contract" in source
    assert "Deployment, DNS/domain, billing, wallet, and platform operations may be supplied by managed platform capabilities" in source
    assert "Do not generate provider adapters for those operations into a customer app bundle" in source
    assert "Generated app deployment packaging is not a `service_foundation` or `api_surface` task" in source
    assert "Do not declare `Dockerfile`, `docker-compose.yml`, `.env.example`, `.env.staging.example`, `.env.production.example`, `deployment.manifest.json`, or `.github/workflows/*.yml`" in source
    assert "Do not include this page merely because DownloadAgent will emit provider-neutral deployment artifacts" in source
    assert "Generated artifacts must never commit secrets" in source
    assert "ci_secret_requirements" in source
    assert "pre-deploy validation/preview only" in source
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
    assert "Mode B (service_foundation) example" not in source
    assert "Split app-backend admin surface example" not in source
    assert "states.yaml" not in source
    assert "transitions.yaml" not in source
    assert any(
        rule["source_agent"] == "ServiceAgent" and rule["target_agent"] == "ModuleRuntimeQualityAgent"
        for rule in handoffs["transition_rules"]
    )
    assert any(
        rule["source_agent"] == "ModuleRuntimeQualityAgent" and rule["target_agent"] == "FrontendStubAgent"
        for rule in handoffs["transition_rules"]
    )
    assert any(
        rule["source_agent"] == "FrontendStubAgent" and rule["target_agent"] == "ControllerAgent"
        for rule in handoffs["transition_rules"]
    )

    assert "task_type: platform_config" not in source
    assert "operations/{pack_name}" not in source
    assert "subscription.yaml" not in source
    assert "operation.yaml" not in source
    assert "admin_surfaces" not in source
    assert "removed standalone" not in source
    assert "backend/main.py" not in source
    assert "services/routes/api_router.py" not in source
    assert "RouteAgent" not in source
    assert "EntryPointAgent" not in source
    assert "page_component" not in _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    assert "shell_extension" not in _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")

    module_contract = file_contracts["task_contracts"]["module_contract"]
    refinement_harness = file_contracts["task_contracts"]["refinement_harness"]
    assert module_contract["required_outputs"] == ["modules/{pack_name}/module.yaml"]
    assert refinement_harness["required_outputs"] == [
        "app/config/refinement_policy.yaml",
        "refinement_harness/config/harness.yaml",
        "refinement_harness/config/tools.yaml",
    ]
    assert "modules/{pack_name}/contracts/reactions.yaml" in module_contract["optional_outputs"]
    assert "modules/{pack_name}/contracts/subscriptions.yaml" not in module_contract["optional_outputs"]
    assert "modules/{pack_name}/contracts/admin.yaml" in module_contract["optional_outputs"]
    assert "modules/{pack_name}/contracts/profile.yaml" in module_contract["optional_outputs"]
    assert "modules/{pack_name}/contracts/relationships.yaml" in module_contract["optional_outputs"]
    assert "modules/{pack_name}/contracts/policy_hooks.yaml" in module_contract["optional_outputs"]
    assert "backend/handler.py" in module_contract["downstream_backend_defaults"]
    assert "backend/notifications.py" in module_contract["optional_backend_hooks"]
    assert "backend/subscriptions.py" not in module_contract["optional_backend_hooks"]
    assert "ui/index.js" in module_contract["optional_frontend_stubs"]

    workflow_archetype = module_archetypes["archetypes"]["workflow"]
    messaging_archetype = module_archetypes["archetypes"]["messaging"]
    assert "The policy layer exposes a validate_transition helper." in workflow_archetype["hard_constraints"]
    assert "Event payloads include delivery metadata needed for real-time routing." in messaging_archetype["hard_constraints"]

    for archetype_name in ["standard", "messaging", "workflow", "transactional"]:
        archetype = module_archetypes["archetypes"][archetype_name]
        optional_family = archetype["canonical_yaml_family"]["optional"]
        assert "profile.yaml" in optional_family, f"{archetype_name} archetype missing profile.yaml in optional family"
        assert "relationships.yaml" in optional_family, f"{archetype_name} archetype missing relationships.yaml in optional family"
        assert "policy_hooks.yaml" in optional_family, f"{archetype_name} archetype missing policy_hooks.yaml in optional family"
    assert "Use one of these exact top-level shapes:" in source
    assert "Use one of these exact bounded shapes:" in source
    assert "Exact nested field shapes come from `ConfigMiddlewareOutput`, `ModuleContractBundle`, `BackendFoundationBundle`, and `SubscriptionConfigBundle` in `structured_outputs.yaml`." in source
    assert "Exact nested field shapes come from `RefinementHarnessOutput` and `RefinementHarnessBundle` in `structured_outputs.yaml`." in source
    assert "Exact nested field shapes come from `ControllerOutput` and `AppBackendAdminConfig` in `structured_outputs.yaml`." in source


def test_appgenerator_guides_module_auth_surface_and_scope_contract() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")
    file_contracts = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
    module_archetypes = _read_yaml("factory_app/build_context/AppGenerator/module_archetypes.yaml")
    structured_outputs = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")

    module_action = structured_outputs["models"]["ModuleAction"]["fields"]
    assert module_action["api_surface"]["type"] == "optional_str"
    assert "public_readonly" in module_action["api_surface"]["description"]
    assert "entitlement_gate" in module_action["api_surface"]["description"]

    module_constraints = "\n".join(file_contracts["task_contracts"]["module_contract"]["hard_constraints"])
    api_constraints = "\n".join(file_contracts["task_contracts"]["api_surface"]["hard_constraints"])
    standard_constraints = "\n".join(module_archetypes["archetypes"]["standard"]["hard_constraints"])

    assert "actions[].api_surface is part of the module contract" in module_constraints
    assert "AUTH_ENABLED=true" in module_constraints
    assert "External HTTP dispatch and internal runtime dispatch are separate paths" in module_constraints
    assert "ctx.workspace_id" in module_constraints
    assert "Custom API adapters must not bypass module action auth" in api_constraints
    assert "Tenant, workspace, user, mutation, and admin actions require authenticated HTTP dispatch" in standard_constraints

    assert "`actions`: list of `{id, description, handler_method, api_surface" in source
    assert "`actions[].api_surface` controls anonymous HTTP eligibility" in source
    assert "Do not mark tenant, workspace, user, mutation, or admin actions public" in source
    assert "`context.workspace_id`" in source
    assert "never trust `tenant_id`, `workspace_id`, or `user_id` values from params as authorization" in source


def test_refinement_harness_codegen_seeds_refinement_policy_yaml() -> None:
    from factory_app.workflows.AppGenerator.tools.refinement_harness_codegen import (
        build_refinement_harness_code_files,
    )

    files = build_refinement_harness_code_files(
        {
            "harness_yaml": {
                "schema_version": "mozaiks.refinement_harness.v1",
                "routing": {
                    "default_artifact_kind": "app_bundle",
                    "artifacts": [],
                },
                "checkpoints": [],
            },
            "tools_yaml": {
                "schema_version": "mozaiks.refinement_harness.tools.v1",
                "tools": [],
            },
        }
    )

    file_map = {item["filename"]: item["content"] for item in files}

    assert "app/config/refinement_policy.yaml" in file_map
    runtime_yaml = yaml.safe_load(file_map["app/config/refinement_policy.yaml"])
    assert runtime_yaml["schema_version"] == "mozaiks.refinement.policy.v1"
    assert runtime_yaml["classifier"]["llm_profile"] == "classifier"


def test_appgenerator_download_tool_does_not_inject_removed_admin_surfaces() -> None:
    source = _read("factory_app/workflows/AppGenerator/tools/generate_and_download.py")
    assembly = _read("factory_app/workflows/AppGenerator/tools/assembly_phase.py")

    assert "admin_surfaces" not in source
    assert "_inject_admin_surfaces(files_map)" not in source
    assert "extract_code_file_map_from_payload" in source
    assert "extract_code_file_entries_from_payload" in assembly


def test_appgenerator_prompt_time_contract_artifacts_align_with_structured_outputs() -> None:
    structured_outputs = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    file_contracts = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
    module_archetypes = _read_yaml("factory_app/build_context/AppGenerator/module_archetypes.yaml")

    task_type_values = structured_outputs["models"]["AppBuildTask"]["fields"]["task_type"]["values"]
    module_type_values = structured_outputs["models"]["ModuleIdentity"]["fields"]["type"]["values"]

    assert set(file_contracts["task_contracts"].keys()).issubset(set(task_type_values))
    assert file_contracts["task_contracts"]["page_bundle"]["owner_agent"] == "AppSchemaAgent"
    assert file_contracts["task_contracts"]["module_contract"]["owner_agent"] == "ConfigMiddlewareAgent"
    assert file_contracts["task_contracts"]["service_foundation"]["owner_agent"] == "ConfigMiddlewareAgent"
    assert file_contracts["task_contracts"]["refinement_harness"]["owner_agent"] == "RefinementHarnessAgent"
    assert file_contracts["task_contracts"]["api_surface"]["owner_agent"] == "ControllerAgent"

    assert set(module_archetypes["archetypes"].keys()) == set(module_type_values)


def test_module_identity_declares_user_data_scope_field() -> None:
    """ModuleIdentity must expose user_data_scope so generators can flag GDPR-relevant modules."""
    config = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    fields = config["models"]["ModuleIdentity"]["fields"]
    assert "user_data_scope" in fields, "ModuleIdentity is missing user_data_scope field"
    uds = fields["user_data_scope"]
    assert uds["type"] == "bool"
    assert "optional" not in uds
    assert "account_data" in uds["description"].lower() or "user_data_scope" in uds["description"].lower()


def test_module_python_stub_includes_account_data_handler_kind() -> None:
    """ModulePythonStub must declare account_data_handler kind so GDPR handlers can be scaffolded."""
    config = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    kind_values = config["models"]["ModulePythonStub"]["fields"]["kind"]["values"]
    assert "account_data_handler" in kind_values, (
        "ModulePythonStub.kind is missing account_data_handler — "
        "generators cannot scaffold GDPR delete/export handlers"
    )
    description = config["models"]["ModulePythonStub"]["fields"]["kind"]["description"]
    assert "user_data_scope" in description

