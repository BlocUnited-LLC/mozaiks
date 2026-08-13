from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.validation import GeneratedAppValidationRequest, validate_generated_app_bundle
from tests.import_utils import import_module_directly

WORKSPACE = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = WORKSPACE / "factory_app" / "workflows"
PACK_ROOT = WORKSPACE / "factory_app" / "build_context" / "operator_readiness"
NONDETERMINISTIC_METADATA = frozenset({"provenance.yaml", ".mozaiks/pack_provenance.json"})


class _Context:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


def _load_models() -> dict[str, type]:
    workflow_manager = import_module_directly("mozaiksai.core.workflow.workflow_manager")
    structured = import_module_directly("mozaiksai.core.workflow.outputs.structured")
    workflow_manager.UnifiedWorkflowManager._instance = None
    workflow_manager.initialize_workflows(base_path=str(WORKFLOWS_ROOT))
    structured._workflow_models.clear()
    structured._workflow_registries.clear()
    models, _ = structured.load_workflow_structured_outputs("AppGenerator")
    return models


def _plan_payload() -> dict[str, Any]:
    return {
        "AppBuildPlan": {
            "agent_message": "Materialize the deterministic reports application.",
            "app_kind": "operations_dashboard",
            "pages": [
                {
                    "name": "Reports",
                    "route": "/reports",
                    "purpose": "Review generated operational reports.",
                    "primary_entities": ["Report"],
                    "primary_actions": ["list_reports"],
                    "ui_layout": "full-width",
                    "ui_surface": "declarative_page",
                    "page_type_hint": "record_list",
                    "sections_hint": [
                        {
                            "primitive": "DataTable",
                            "config_hint": json.dumps(
                                {
                                    "columns": ["id", "title", "status"],
                                    "api_endpoint": "/api/modules/reports/list_reports",
                                    "search": True,
                                },
                                sort_keys=True,
                            ),
                            "section_id_hint": "reports-table",
                            "title_hint": "Reports",
                        }
                    ],
                }
            ],
            "entities": [{"name": "Report", "operations": ["read"], "notes": None}],
            "roles": ["operator"],
            "auth_strategy": "public",
            "service_scope": ["reports"],
            "frontend_scope": ["reports"],
            "theme_preferences": "high-contrast operations workspace",
            "brand_intent": None,
            "capability_packs": [
                {
                    "capability_pack_id": "operator_readiness",
                    "surface_id": "operator_readiness",
                    "surface_kind": "app_policy",
                    "pack_type": "custom_domain",
                    "label": "Operator Readiness",
                    "summary": "Deterministic local readiness evidence.",
                    "implementation_mode": "hybrid",
                    "capability_source": "config_file",
                }
            ],
            "readiness_profile": "host_operator_platform",
            "revenue_model": "free",
            "external_integrations": [],
            "agent_backend_required": False,
            "build_tasks": [
                {
                    "task_id": "reports.contract",
                    "task_type": "module_contract",
                    "capability_pack_id": "reports",
                    "surface_id": "reports",
                    "surface_kind": "module",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ConfigMiddlewareAgent",
                    "description": "Materialize the reports module contract.",
                    "initial_message": "Emit the canonical reports module and event contracts.",
                    "owned_paths": [
                        "modules/reports/module.yaml",
                        "modules/reports/contracts/events.yaml",
                        "modules/reports/contracts/reactions.yaml",
                    ],
                    "depends_on": [],
                    "acceptance_criteria": ["Action and event references close."],
                },
                {
                    "task_id": "reports.models",
                    "task_type": "data_models",
                    "capability_pack_id": "reports",
                    "surface_id": "reports",
                    "surface_kind": "module",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ModelAgent",
                    "description": "Materialize typed report schemas.",
                    "initial_message": "Emit the report schema implementation.",
                    "owned_paths": ["modules/reports/backend/schemas.py"],
                    "depends_on": ["reports.contract"],
                    "acceptance_criteria": ["Report schema is typed."],
                },
                {
                    "task_id": "reports.services",
                    "task_type": "business_services",
                    "capability_pack_id": "reports",
                    "surface_id": "reports",
                    "surface_kind": "module",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ServiceAgent",
                    "description": "Materialize report handler and service code.",
                    "initial_message": "Emit contract-bound Python implementations.",
                    "owned_paths": [
                        "modules/reports/backend/handler.py",
                        "modules/reports/backend/service.py",
                    ],
                    "depends_on": ["reports.models"],
                    "acceptance_criteria": ["Handler implements every declared action and reaction."],
                },
                {
                    "task_id": "reports.page",
                    "task_type": "page_bundle",
                    "capability_pack_id": None,
                    "surface_id": "reports_page",
                    "surface_kind": "ui_only",
                    "execution_target": "AppGenerator",
                    "initial_agent": "AppSchemaAgent",
                    "description": "Materialize the schema-native reports route.",
                    "initial_message": "Emit app.json, semantic theme tokens, and the reports page.",
                    "owned_paths": ["app.json", "brand/theme_config.json", "ui/pages/reports.yaml"],
                    "depends_on": ["reports.services"],
                    "acceptance_criteria": ["Page action resolves to reports.list_reports."],
                },
            ],
            "data_contract": None,
            "pending_schema_migration": None,
            "generation_order": [
                "reports.contract",
                "reports.models",
                "reports.services",
                "reports.page",
            ],
            "carry_forward_decisions": [],
        }
    }


def _typed_task_outputs(models: dict[str, type]) -> dict[str, dict[str, Any]]:
    config_output = models["ConfigMiddlewareOutput"].model_validate(
        {
            "mode": "module_contract_bundle",
            "module_contract": None,
            "service_foundation_bundle": None,
            "subscription_config_bundle": None,
            "code_files": [
                {
                    "filename": "modules/reports/module.yaml",
                    "content": (
                        "schema_version: mozaiks.module.v1\n"
                        "module:\n"
                        "  id: reports\n"
                        "  display_name: Reports\n"
                        "  version: 1.0.0\n"
                        "  handler: backend.handler:ReportsModule\n"
                        "actions:\n"
                        "  - id: list_reports\n"
                        "    description: List operational reports.\n"
                        "    handler_method: list_reports\n"
                        "    emits: [domain.reports.report_viewed]\n"
                        "    input_schema: {type: object, properties: {}}\n"
                        "    output_schema: {type: object}\n"
                        "capabilities:\n"
                        "  - capability_id: reports.view\n"
                        "    kind: action\n"
                        "    target: list_reports\n"
                        "    title: View reports\n"
                    ),
                },
                {
                    "filename": "modules/reports/contracts/events.yaml",
                    "content": (
                        "schema_version: mozaiks.events.v1\n"
                        "events:\n"
                        "  - type: domain.reports.report_viewed\n"
                        "    version: 1\n"
                        "    description: A report list was viewed.\n"
                        "    producer: reports\n"
                        "    payload_schema: {type: object, properties: {}}\n"
                    ),
                },
                {
                    "filename": "modules/reports/contracts/reactions.yaml",
                    "content": (
                        "schema_version: mozaiks.reactions.v1\n"
                        "reactions:\n"
                        "  - id: record_report_view\n"
                        "    event_type: domain.reports.report_viewed\n"
                        "    target:\n"
                        "      kind: handler\n"
                        "      handler_method: record_report_view\n"
                    ),
                },
            ],
            "deleted_files": None,
            "agent_message": "Materialized reports contracts.",
        }
    )
    model_output = models["ModelOutput"].model_validate(
        {
            "model_files": [
                {
                    "path": "modules/reports/backend/schemas.py",
                    "entity_name": "Report",
                    "purpose": "Typed report response.",
                    "content": "from typing import TypedDict\n\nclass Report(TypedDict):\n    id: str\n    title: str\n    status: str\n",
                }
            ],
            "code_files": [],
            "agent_message": "Materialized report schemas.",
        }
    )
    service_output = models["ServiceOutput"].model_validate(
        {
            "python_files": [
                {
                    "path": "modules/reports/backend/handler.py",
                    "kind": "handler",
                    "purpose": "Contract-bound reports dispatch.",
                    "contract_refs": ["module_yaml.actions[list_reports]"],
                    "content": (
                        "from .service import ReportsService\n\n"
                        "class ReportsModule:\n"
                        "    async def list_reports(self, ctx, **params):\n"
                        "        return await ReportsService().list_reports(ctx, **params)\n\n"
                        "    async def record_report_view(self, ctx, **params):\n"
                        "        return {\"recorded\": True}\n"
                    ),
                },
                {
                    "path": "modules/reports/backend/service.py",
                    "kind": "service",
                    "purpose": "Bounded report behavior behind the module contract.",
                    "contract_refs": ["module_yaml.actions[list_reports]"],
                    "content": (
                        "class ReportsService:\n"
                        "    async def list_reports(self, ctx, **params):\n"
                        "        return {\"reports\": [{\"id\": \"report-1\", \"title\": \"Readiness\", \"status\": \"ready\"}]}\n"
                    ),
                },
            ],
            "code_files": [],
            "deleted_files": None,
            "agent_message": "Materialized report runtime code.",
        }
    )
    schema_output = models["AppSchemaOutput"].model_validate(
        {
            "agent_message": "Materialized the reports application shell.",
            "manifest": {
                "app_name": "Deterministic Reports",
                "description": "Offline deterministic materialization proof.",
                "tagline": None,
                "value_proposition": None,
                "version": "1.0.0",
                "auth_strategy": "public",
                "roles": ["operator"],
                "default_route": "/reports",
                "pages": ["reports"],
                "custom_routes": [],
            },
            "pages": [
                {
                    "name": "Reports",
                    "route": "/reports",
                    "title": "Reports",
                    "page_type": "record_list",
                    "layout": "grid",
                    "shell_mode": "standard",
                    "roles": None,
                    "navigation": None,
                    "meta": None,
                    "sections": [],
                    "extensions": None,
                }
            ],
            "custom_route_bundle": None,
            "theme_config_patch": {
                "theme": {
                    "primary": "#0f766e",
                    "variant": "professional",
                    "radius": "6px",
                    "font": "IBM Plex Sans",
                    "font_heading": "IBM Plex Serif",
                    "appearance": "light",
                    "density": "compact",
                    "branding": None,
                },
                "identity": {
                    "name": "Deterministic Reports",
                    "tagline": "Operational clarity",
                    "app_name": "Deterministic Reports",
                },
                "fonts": None,
            },
            "shell_config": None,
            "asset_manifest": None,
            "data_contract": None,
        }
    )
    return {
        "reports.contract": config_output.model_dump(mode="json"),
        "reports.models": model_output.model_dump(mode="json"),
        "reports.services": service_output.model_dump(mode="json"),
        "reports.page": schema_output.model_dump(mode="json"),
    }


async def _materialize(models: dict[str, type]) -> tuple[dict[str, str], _Context]:
    typed_plan = models["AppBuildPlanOutput"].model_validate(_plan_payload())
    context = _Context(
        {
            "app_id": "deterministic-reports",
            "app_name": "Deterministic Reports",
            "app_slug": "deterministic-reports",
            "readiness_profile": "host_operator_platform",
            "evidence_mode": "local_no_spend",
            "capability_packs": [
                {
                    "id": "operator_readiness",
                    "capability_source": "config_file",
                    "pack_source_path": str(PACK_ROOT),
                }
            ],
        }
    )
    plan = typed_plan.AppBuildPlan.model_dump(mode="json")
    app_build_plan(AppBuildPlan=plan, context_variables=context)
    context.set("app_task_batch_results", _typed_task_outputs(models))
    assembled = await assemble_app_tasks(context_variables=context)
    files = {str(item["filename"]): str(item["content"]) for item in assembled["code_files"]}
    return files, context


def _deterministic_files(files: dict[str, str]) -> dict[str, bytes]:
    return {
        path: content.encode("utf-8")
        for path, content in files.items()
        if path not in NONDETERMINISTIC_METADATA
    }


def test_typed_plan_rejects_unknown_taxonomy() -> None:
    models = _load_models()
    invalid = deepcopy(_plan_payload())
    invalid["AppBuildPlan"]["pages"][0]["page_type_hint"] = "invented_dashboard"

    with pytest.raises(ValidationError, match="page_type_hint"):
        models["AppBuildPlanOutput"].model_validate(invalid)


def test_plan_rejects_unresolved_task_reference() -> None:
    models = _load_models()
    typed_plan = models["AppBuildPlanOutput"].model_validate(_plan_payload())
    plan = typed_plan.AppBuildPlan.model_dump(mode="json")
    plan["build_tasks"][1]["depends_on"] = ["missing.contract"]

    with pytest.raises(ValueError, match="unknown task ids"):
        app_build_plan(AppBuildPlan=plan, context_variables=_Context())


@pytest.mark.asyncio
async def test_materializer_rejects_path_traversal() -> None:
    models = _load_models()
    files, context = await _materialize(models)
    context.set(
        "code_files",
        [
            {"filename": "../../outside.txt", "content": "escape"},
            {"filename": "config/safe.txt", "content": "inside"},
        ],
    )
    assembled = await assemble_app_tasks(context_variables=context)
    paths = [str(item["filename"]) for item in assembled["code_files"]]

    assert "../../outside.txt" not in paths
    assert "config/safe.txt" in paths
    assert all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in paths)
    assert files


@pytest.mark.asyncio
async def test_typed_plan_materializes_through_jinja_and_passes_production_validation() -> None:
    models = _load_models()
    files_one, context_one = await _materialize(models)
    files_two, _ = await _materialize(models)

    required = {
        "app.json",
        "brand/theme_config.json",
        "ui/pages/reports.yaml",
        "modules/reports/module.yaml",
        "modules/reports/contracts/events.yaml",
        "modules/reports/contracts/reactions.yaml",
        "modules/reports/backend/handler.py",
        "modules/reports/backend/service.py",
        "modules/reports/backend/schemas.py",
        "config/operator_readiness.yaml",
        "scripts/check_operator_readiness_local.ps1",
        "docs/operations/operator-readiness.md",
    }
    assert required <= files_one.keys()
    assert list(files_one) == sorted(files_one)
    assert "readiness_profile: host_operator_platform" in files_one["config/operator_readiness.yaml"]
    assert "{{ readiness_profile }}" not in files_one["config/operator_readiness.yaml"]
    assert _deterministic_files(files_one) == _deterministic_files(files_two)
    assert set(files_one) - set(_deterministic_files(files_one)) == NONDETERMINISTIC_METADATA

    plan = context_one.get("app_build_plan")
    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=files_one,
            build_tasks=plan["build_tasks"],
            capability_packs=plan["capability_packs"],
        )
    )
    assert scan_generated_bundle(files_one) == []
    assert validation.passed is True, validation.diagnostics

    acceptance = await run_app_bundle_acceptance_gate(
        files=files_one,
        context_variables=context_one,
        capability_packs=plan["capability_packs"],
    )
    assert acceptance["passed"] is True, acceptance
    assert context_one.get("generated_files") == files_one