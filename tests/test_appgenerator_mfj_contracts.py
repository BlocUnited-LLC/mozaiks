from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


WORKSPACE = Path(__file__).resolve().parents[1]


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_yaml(relative_path: str) -> dict:
    return yaml.safe_load((WORKSPACE / relative_path).read_text(encoding="utf-8")) or {}


class _Context:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self.data[key] = value


def _build_tasks() -> list[dict]:
    return [
        {
            "task_id": "task_backend_foundation",
            "task_type": "backend_foundation",
            "capability_pack_id": None,
            "surface_id": "app_shell",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "description": "Generate backend glue.",
            "initial_message": "Generate backend glue only.",
            "owned_paths": ["backend/config.py", "backend/middleware.py"],
            "depends_on": [],
            "acceptance_criteria": ["Environment-driven config only"],
        },
        {
            "task_id": "task_notifications_pages",
            "task_type": "page_bundle",
            "capability_pack_id": "notifications_center",
            "surface_id": "notifications_center",
            "surface_kind": "ui_only",
            "execution_target": "AppGenerator",
            "initial_agent": "AppSchemaAgent",
            "description": "Compile persistent notifications pages.",
            "initial_message": "Compile notifications page schemas only.",
            "owned_paths": ["ui/pages/notifications.yaml"],
            "depends_on": ["task_backend_foundation"],
            "acceptance_criteria": ["Notifications route exists"],
        },
    ]


def _base_plan() -> dict:
    return {
        "agent_message": "Planned the app.",
        "app_kind": "operations-console",
        "pages": [
            {
                "name": "Notifications",
                "route": "/notifications",
                "purpose": "Review build and runtime alerts",
            }
        ],
        "entities": [],
        "roles": ["operator"],
        "auth_strategy": "role-based",
        "backend_scope": ["notifications feed"],
        "frontend_scope": ["notifications route"],
        "theme_preferences": "dark restrained",
        "brand_intent": None,
        "capability_packs": [
            {
                "capability_pack_id": "notifications_center",
                "surface_id": "notifications_center",
                "surface_kind": "ui_only",
                "pack_type": "notifications_pack",
                "label": "Notifications",
                "summary": "Review build and runtime notifications.",
                "implementation_mode": "hybrid",
            }
        ],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": _build_tasks(),
        "generation_order": ["backend-foundation", "app-schema-bundle"],
    }


def _valid_workflows() -> list[dict]:
    return [
        {
            "name": "AppGenerator",
            "description": "Backend foundation task",
            "initial_agent": "ConfigMiddlewareAgent",
            "initial_message": "Execute only the backend foundation task.",
            "context_variables": {
                "task_run_mode": True,
                "current_build_task_id": "task_backend_foundation",
                "current_build_task_type": "backend_foundation",
                "current_build_task": {"task_id": "task_backend_foundation"},
            },
        },
        {
            "name": "AppGenerator",
            "description": "Notifications page bundle",
            "initial_agent": "AppSchemaAgent",
            "initial_message": "Execute only the notifications page bundle task.",
            "context_variables": {
                "task_run_mode": True,
                "current_build_task_id": "task_notifications_pages",
                "current_build_task_type": "page_bundle",
                "current_build_task": {"task_id": "task_notifications_pages"},
            },
        },
    ]


def test_app_build_plan_persists_child_workflows_and_hydrates_current_task() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_mfj_contracts",
    )
    context = _Context()

    result = module.app_build_plan(
        AppBuildPlan=_base_plan(),
        workflows=_valid_workflows(),
        context_variables=context,
    )

    child_workflows = context.data["app_child_workflows"]
    assert isinstance(child_workflows, list)
    assert len(child_workflows) == 2
    assert "Child workflows: 2" in result

    page_child = child_workflows[1]
    assert page_child["initial_agent"] == "AppSchemaAgent"
    assert page_child["context_variables"]["current_build_task"]["task_type"] == "page_bundle"
    assert page_child["context_variables"]["current_build_task"]["owned_paths"] == [
        "ui/pages/notifications.yaml"
    ]


def test_app_build_plan_requires_child_workflow_coverage_for_every_task() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_mfj_missing_child",
    )

    with pytest.raises(ValueError, match="must cover every AppGenerator build task"):
        module.app_build_plan(
            AppBuildPlan=_base_plan(),
            workflows=_valid_workflows()[:1],
            context_variables=_Context(),
        )


def test_app_build_plan_rejects_child_workflow_task_type_mismatch() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_mfj_task_type_mismatch",
    )
    workflows = _valid_workflows()
    workflows[1]["context_variables"]["current_build_task_type"] = "backend_foundation"

    with pytest.raises(ValueError, match="current_build_task_type='backend_foundation'"):
        module.app_build_plan(
            AppBuildPlan=_base_plan(),
            workflows=workflows,
            context_variables=_Context(),
        )


def test_app_build_plan_rejects_page_bundle_child_with_wrong_initial_agent() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_mfj_page_bundle_owner",
    )
    workflows = _valid_workflows()
    workflows[1]["initial_agent"] = "ServiceAgent"

    with pytest.raises(ValueError, match="starts at 'AppSchemaAgent'"):
        module.app_build_plan(
            AppBuildPlan=_base_plan(),
            workflows=workflows,
            context_variables=_Context(),
        )


def test_appgenerator_context_exposes_app_child_workflows() -> None:
    context_vars = _read_yaml("factory_app/workflows/AppGenerator/context_variables.yaml")
    definitions = context_vars["definitions"]
    agents = context_vars["agents"]

    assert "app_child_workflows" in definitions
    assert definitions["app_child_workflows"]["source"]["type"] == "computed"
    assert "app_child_workflows" in agents["AssemblyAgent"]["variables"]
