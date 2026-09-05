from __future__ import annotations

import json
import textwrap
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    extract_workflow_integration_metadata_from_bundle_entries,
)
from factory_app.workflows.AgentGenerator.tools.generate_and_download import _write_bundle_to_disk
from factory_app.workflows.AgentGenerator.tools.workflow_converter import promote_generated_workflow
from factory_app.workflows.ExistingAppDiscovery.tools.app_build_plan_handoff import (
    build_app_build_plan_from_discovery,
)
from factory_app.workflows.ExistingAppDiscovery.tools.app_context_mapping import (
    build_existing_app_context_artifacts,
)
from factory_app.workflows.ExistingAppDiscovery.tools.preload_discovery_context import (
    collect_prechat_discovery_context,
)
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
from mozaiksai.core.validation import GeneratedAppValidationRequest, scan_functional_generated_app
from mozaiksai.core.validation.generated_app import validate_generated_app_bundle
from mozaiksai.hosts import platform
from tests.test_generated_app_archetype_matrix import (
    WORKFLOWS_ROOT,
    _ArchetypeSpec,
    _assert_not_missing_or_placeholder,
    _configure_platform,
    _Context,
    _FakeMongoClient,
    _materialize_spec,
    _PersistenceContext,
    _RuntimeAcceptanceCollection,
    _RuntimeAcceptancePersistence,
    _validation_pages,
)


def _write_brownfield_source(root: Path) -> None:
    (root / "backend").mkdir(parents=True)
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "backend" / "main.py").write_text(
        textwrap.dedent(
            """
            from fastapi import FastAPI

            app = FastAPI()

            @app.get("/api/projects")
            async def list_projects():
                return []

            @app.post("/api/projects")
            async def create_project(payload: dict):
                return payload

            @app.patch("/api/projects/{project_id}")
            async def update_project(project_id: str, payload: dict):
                return {"id": project_id, **payload}

            @app.get("/api/work-items")
            async def list_work_items():
                return []
            """
        ).lstrip(),
        encoding="utf-8",
    )
    (root / "frontend" / "src" / "App.jsx").write_text(
        textwrap.dedent(
            """
            export default function App() {
              return <nav><a href="/projects">Projects</a><a href="/work-items">Work Items</a></nav>
            }
            """
        ).lstrip(),
        encoding="utf-8",
    )


def _brownfield_discovery_artifact() -> dict[str, Any]:
    return {
        "request_intent": "Adopt the existing project tracker into Mozaiks-owned canonical app surfaces.",
        "artifact_version": "captured-brownfield-v1",
        "app_type": "brownfield_app",
        "app_summary": "Existing FastAPI/React project tracker with authenticated project and work item surfaces.",
        "existing_product_spec": {
            "app_id": "existing_project_tracker",
            "app_name": "Existing Project Tracker",
            "tech_stack": "FastAPI, React",
            "auth_model": "JWT required for project and work item mutation",
            "routes": ["/projects", "/work-items"],
            "data_entities": ["Project", "WorkItem"],
            "api_endpoints": [
                "GET /api/projects",
                "POST /api/projects",
                "PATCH /api/projects/{project_id}",
                "GET /api/work-items",
                "POST /api/work-items",
            ],
        },
        "capability_specs": [
            {
                "capability_id": "customer_projects",
                "label": "Customer Projects",
                "summary": "CRUD over migrated project records.",
            },
            {
                "capability_id": "work_items",
                "label": "Work Items",
                "summary": "CRUD over migrated work item records.",
            },
        ],
        "agent_augmentation_plan": {
            "adoption_level": "gradual_modernization",
            "adoption_rationale": "Generate Mozaiks-owned canonical modules while preserving the source app as evidence.",
            "ecosystem_bindings": ["customer_projects", "work_items"],
        },
        "discovery_brief": "Projects and work items should survive adoption as canonical pages, modules, and persistence.",
        "unresolved_questions": [],
    }


def _brownfield_module_decomposition() -> dict[str, Any]:
    return {
        "proposed_modules": [
            {
                "module_id": "customer_projects",
                "source_capabilities": ["projects"],
                "source_files": ["backend/main.py", "frontend/src/App.jsx"],
                "proposed_actions": ["create_project", "list_projects", "update_project"],
                "persistence_entities": ["Project"],
            },
            {
                "module_id": "work_items",
                "source_capabilities": ["work-items"],
                "source_files": ["backend/main.py", "frontend/src/App.jsx"],
                "proposed_actions": ["create_work_item", "list_work_items"],
                "persistence_entities": ["WorkItem"],
            },
        ],
        "proposed_pages": [
            {
                "page_id": "projects",
                "route": "/projects",
                "title": "Projects",
                "module_id": "customer_projects",
                "page_type_hint": "record_list",
                "primary_entities": ["Project"],
            },
            {
                "page_id": "work_items",
                "route": "/work-items",
                "title": "Work Items",
                "module_id": "work_items",
                "page_type_hint": "record_list",
                "primary_entities": ["WorkItem"],
            },
        ],
        "required_migration_tasks": ["preserve_project_routes", "preserve_project_update_action"],
    }


def _brownfield_checks(client: TestClient, _files: dict[str, str], _loaded: Any) -> None:
    headers = {"Authorization": "Bearer matrix-token"}
    for page_name, route in (("projects", "/projects"), ("work_items", "/work-items")):
        page = client.get(f"/api/pages/{page_name}", headers=headers)
        _assert_not_missing_or_placeholder(page, surface=f"/api/pages/{page_name}")
        assert page.status_code == 200
        assert page.json()["route"] == route

    denied = client.post("/api/modules/customer_projects/update_project", json={"params": {"project_id": "p1"}})
    _assert_not_missing_or_placeholder(denied, surface="/api/modules/customer_projects/update_project")
    assert denied.status_code in {401, 403}

    update = client.post(
        "/api/modules/customer_projects/update_project",
        json={"params": {"project_id": "p1", "name": "Migrated"}},
        headers=headers,
    )
    _assert_not_missing_or_placeholder(update, surface="/api/modules/customer_projects/update_project")
    assert update.status_code == 200

    work_items = client.post("/api/modules/work_items/list_work_items", json={"params": {}}, headers=headers)
    _assert_not_missing_or_placeholder(work_items, surface="/api/modules/work_items/list_work_items")
    assert work_items.status_code == 200


def _captured_agentgenerator_bundle() -> dict[str, Any]:
    workflow_name = "ResearchReviewWorkflow"
    return {
        "workflow_name": workflow_name,
        "pattern_id": "deterministic_research_review",
        "pattern_name": "Deterministic Research Review",
        "agent_message": "Captured AgentGenerator workflow bundle.",
        "files": [
            {
                "filename": "orchestrator.yaml",
                "content": textwrap.dedent(
                    """
                    workflow_name: ResearchReviewWorkflow
                    max_turns: 4
                    human_in_the_loop: false
                    workflow_startup_mode: AgentDriven
                    orchestration_pattern: ag2_network
                    initial_message: Review local source text and summarize it.
                    initial_agent: ResearchPlannerAgent
                    triggers:
                      - type: domain.research.submitted
                        description: Start review when research source is submitted.
                    """
                ).lstrip(),
            },
            {
                "filename": "agents.yaml",
                "content": textwrap.dedent(
                    """
                    agents:
                      - name: ResearchPlannerAgent
                        system_message: Plan deterministic source review.
                        structured_outputs_required: true
                      - name: ResearchSummaryAgent
                        system_message: Produce the final structured summary.
                        structured_outputs_required: true
                    """
                ).lstrip(),
            },
            {
                "filename": "context_variables.yaml",
                "content": textwrap.dedent(
                    """
                    definitions:
                      source_text:
                        type: string
                        source:
                          type: state
                          default: Local source
                      review_depth:
                        type: string
                        source:
                          type: state
                          default: concise
                    agents:
                      ResearchPlannerAgent:
                        variables: [source_text, review_depth]
                      ResearchSummaryAgent:
                        variables: [source_text]
                    """
                ).lstrip(),
            },
            {
                "filename": "structured_outputs.yaml",
                "content": textwrap.dedent(
                    """
                    registry:
                      ResearchPlannerAgent: ResearchPlan
                      ResearchSummaryAgent: ResearchSummary
                    models:
                      ResearchPlan:
                        type: model
                        fields:
                          sections:
                            type: list
                            items: str
                            description: Planned sections.
                      ResearchSummary:
                        type: model
                        fields:
                          summary:
                            type: str
                            description: Source summary.
                    """
                ).lstrip(),
            },
            {
                "filename": "tools.yaml",
                "content": textwrap.dedent(
                    """
                    tools:
                      - agent: ResearchPlannerAgent
                        file: tools/research_tools.py
                        function: summarize_source
                        description: Calls the generated research.summarize_source module action boundary.
                        tool_type: Agent_Tool
                        auto_tool_call: false
                    lifecycle_tools: []
                    """
                ).lstrip(),
            },
            {
                "filename": "tools/__init__.py",
                "content": "",
            },
            {
                "filename": "tools/research_tools.py",
                "content": "async def summarize_source(source_text: str):\n    return {'summary': source_text[:32]}\n",
            },
            {
                "filename": "transition_graph.yaml",
                "content": textwrap.dedent(
                    """
                    transition_rules:
                      - source_agent: ResearchPlannerAgent
                        target_agent: ResearchSummaryAgent
                        transition_type: after_turn
                      - source_agent: ResearchSummaryAgent
                        target_agent: terminate
                        transition_type: after_turn
                    """
                ).lstrip(),
            },
            {
                "filename": "ui_config.yaml",
                "content": "visual_agents:\n  - ResearchPlannerAgent\n  - ResearchSummaryAgent\n",
            },
        ],
    }


def _workflow_app_checks(client: TestClient, _files: dict[str, str], _loaded: Any) -> None:
    from mozaiksai.core.workflow.workflow_manager import initialize_workflows, workflow_manager

    workflow_root = Path(__import__("os").environ["MOZAIKS_WORKFLOWS_PATH"])
    initialize_workflows(str(workflow_root))
    assert "ResearchReviewWorkflow" in workflow_manager.get_all_workflow_names(), "WORKFLOW_GAP workflow=ResearchReviewWorkflow"
    config = workflow_manager.get_config("ResearchReviewWorkflow")
    assert "ResearchSummary" in config["structured_outputs"]["models"]
    assert "source_text" in config["context_variables"]["definitions"]
    tool = config["tools"][0]
    assert tool["function"] == "summarize_source"

    headers = {"Authorization": "Bearer matrix-token"}
    page = client.get("/api/pages/research", headers=headers)
    _assert_not_missing_or_placeholder(page, surface="/api/pages/research")
    assert page.status_code == 200
    summarize = client.post(
        "/api/modules/research/summarize_source",
        json={"params": {"source_text": "Deterministic agentgenerator handoff source"}},
        headers=headers,
    )
    _assert_not_missing_or_placeholder(summarize, surface="/api/modules/research/summarize_source")
    assert summarize.status_code == 200
    workflows = client.get("/api/workflows", headers=headers)
    _assert_not_missing_or_placeholder(workflows, surface="/api/workflows")
    assert workflows.status_code in {200, 401, 403}


def _workflow_plan() -> dict[str, Any]:
    from tests.test_generated_app_archetype_matrix import _base_plan

    plan = _base_plan(
        app_kind="agentgenerator_handoff_research",
        pages=[
            {
                "name": "research",
                "route": "/research",
                "title": "Research",
                "page_type_hint": "workflow_board",
                "sections_hint": [
                    {
                        "primitive": "ActionButton",
                        "section_id_hint": "start-research",
                        "title_hint": "Start Research Review",
                        "config_hint": json.dumps({"api_endpoint": "/api/modules/research/start_research"}),
                    }
                ],
            }
        ],
        modules={"research": ["summarize_source", "start_research"]},
        auth_strategy="required",
    )
    plan["agent_backend_required"] = True
    plan["workflow_touchpoints"] = [
        {
            "page_name": "research",
            "workflow_id": "ResearchReviewWorkflow",
            "action_id": "start_research_review",
            "label": "Start research review",
            "placement": "primary_button",
            "purpose": "Launch the research review workflow from the research page.",
            "context_variables": {"source_text": "Local deterministic source"},
        }
    ]
    return plan


async def _run_platform_acceptance(
    *,
    spec: _ArchetypeSpec,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, str], Path, Any]:
    previous_state = {
        "executor_registry": getattr(platform.app.state, "executor_registry", None),
        "subscriptions_config": getattr(platform.app.state, "subscriptions_config", None),
        "failed_module_names": list(getattr(platform.app.state, "failed_module_names", [])),
        "startup_degraded": getattr(platform.app.state, "startup_degraded", False),
        "startup_degraded_reason": getattr(platform.app.state, "startup_degraded_reason", None),
        "module_action_surfaces": deepcopy(getattr(platform.app.state, "module_action_surfaces", {})),
    }
    _PersistenceContext.reset()
    try:
        files_one, app_root, loaded = await _materialize_spec(spec, tmp_path / "run1")
        files_two, _, _ = await _materialize_spec(spec, tmp_path / "run2")
        assert files_one == files_two, f"MATERIALIZATION_GAP nondeterministic file map for {spec.archetype_id}"

        _configure_platform(app_root=app_root, loaded=loaded, spec=spec, monkeypatch=monkeypatch)
        fake_persistence = _RuntimeAcceptancePersistence()
        fake_chat_collection = _RuntimeAcceptanceCollection()

        async def _fake_chat_coll() -> _RuntimeAcceptanceCollection:
            return fake_chat_collection

        monkeypatch.setattr(platform, "persistence_manager", fake_persistence)
        monkeypatch.setattr(platform.runtime_app, "persistence_manager", fake_persistence)
        monkeypatch.setattr(platform.runtime_app, "_chat_coll", _fake_chat_coll)
        monkeypatch.setattr(platform.runtime_app, "simple_transport", None)
        monkeypatch.setattr(platform.runtime_app, "mongo_client", _FakeMongoClient())

        with TestClient(platform.app, raise_server_exceptions=False) as client:
            health = client.get("/health")
            assert health.status_code == 200, health.text
            shell = client.get("/api/shell-config?surface=platform")
            _assert_not_missing_or_placeholder(shell, surface="/api/shell-config")
            assert shell.status_code == 200, shell.text
            spec.runtime_checks(client, files_one, loaded)
        return files_one, app_root, loaded
    finally:
        from mozaiksai.core.workflow.workflow_manager import initialize_workflows

        initialize_workflows(str(WORKFLOWS_ROOT))
        platform.app.state.executor_registry = previous_state["executor_registry"]
        platform.app.state.subscriptions_config = previous_state["subscriptions_config"]
        platform.app.state.failed_module_names = previous_state["failed_module_names"]
        platform.app.state.startup_degraded = previous_state["startup_degraded"]
        platform.app.state.startup_degraded_reason = previous_state["startup_degraded_reason"]
        platform.app.state.module_action_surfaces = previous_state["module_action_surfaces"]
        auth_registry._adapter_registry.clear()
        reset_auth_adapter()


@pytest.mark.asyncio
async def test_brownfield_discovery_handoff_materializes_deterministically_and_boots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    _write_brownfield_source(source_root)
    preload_context = _Context({"repo_path": str(source_root), "chat_id": "brownfield-chat", "app_id": "existing_project_tracker"})
    preload = await collect_prechat_discovery_context(preload_context)
    assert preload["preload_status"] in {"ready", "partial"}
    assert preload_context.get("repo_path") == str(source_root)

    discovery = _brownfield_discovery_artifact()
    decomposition = _brownfield_module_decomposition()
    context = _Context(
        {
            "app_id": "existing_project_tracker",
            "chat_id": "brownfield-chat",
            "module_decomposition_plan": decomposition,
            "source_refs": [{"source_ref_id": "src_existing_project_tracker", "kind": "local_directory", "uri": str(source_root)}],
        }
    )
    drafts = build_existing_app_context_artifacts(discovery, context)
    payloads = drafts.as_artifact_payloads()
    assert payloads["adoption_plan"]["recommended_path"] == "gradual_modernization"
    assert "customer_projects" in json.dumps(payloads, sort_keys=True)

    plan = build_app_build_plan_from_discovery(discovery, module_decomposition_plan=decomposition)
    spec = _ArchetypeSpec(
        archetype_id="brownfield_project_tracker",
        app_id="existing_project_tracker",
        app_name="Existing Project Tracker",
        plan=plan,
        modules=("customer_projects", "work_items"),
        pages=("projects", "work_items"),
        runtime_checks=_brownfield_checks,
        auth_enabled=True,
    )
    files, _, _ = await _run_platform_acceptance(spec=spec, tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert "route: /projects" in files["ui/pages/projects.yaml"], "PLAN_LOSS source route /projects was dropped"
    assert "modules/customer_projects/module.yaml" in files, "MODULE_GAP source Project entity module was dropped"
    assert "update_project" in files["modules/customer_projects/module.yaml"], "MODULE_GAP source update action was dropped"
    assert "Project" in json.loads(files["data/contract.json"])["surfaces"][0]["collections"][0]["entity_name"] or "customer_projects" in files["data/contract.json"]


@pytest.mark.asyncio
async def test_brownfield_acceptance_fails_when_required_action_is_dropped(tmp_path: Path) -> None:
    plan = build_app_build_plan_from_discovery(
        _brownfield_discovery_artifact(),
        module_decomposition_plan=_brownfield_module_decomposition(),
    )
    spec = _ArchetypeSpec(
        archetype_id="brownfield_project_tracker_negative",
        app_id="existing_project_tracker",
        app_name="Existing Project Tracker",
        plan=plan,
        modules=("customer_projects", "work_items"),
        pages=("projects", "work_items"),
        runtime_checks=_brownfield_checks,
        auth_enabled=True,
    )
    files, _, _ = await _materialize_spec(spec, tmp_path / "run")
    mutated = dict(files)
    mutated["modules/customer_projects/backend/handler.py"] = mutated["modules/customer_projects/backend/handler.py"].replace(
        "\n    async def update_project(self, ctx, **params):\n        return {\"ok\": True, \"action\": \"update_project\"}\n",
        "\n",
    )

    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=mutated,
            pages=_validation_pages(spec.plan, mutated),
            build_tasks=spec.plan.get("build_tasks", []),
            capability_packs=spec.plan.get("capability_packs", []),
        )
    )
    codes = {item.code for item in scan_functional_generated_app(mutated)}

    assert validation.passed is False
    assert "MISSING_MODULE_HANDLER" in codes or "MISSING_MODULE_ACTION" in codes


@pytest.mark.asyncio
async def test_agentgenerator_bundle_handoff_materializes_app_and_workflow_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _captured_agentgenerator_bundle()
    generated_root = tmp_path / "generated_workflows"
    workflow_dir, created = _write_bundle_to_disk(bundle["workflow_name"], bundle["files"], generated_root)
    assert "orchestrator.yaml" in created

    active_workflows_root = tmp_path / "workspace" / "workflows"
    promotion = promote_generated_workflow(workflow_dir, active_workflows_root)
    assert Path(promotion["target_dir"]).exists()

    metadata = extract_workflow_integration_metadata_from_bundle_entries([bundle], bundle_name="ResearchReviewPack")
    assert metadata is not None
    context = _Context({})
    apply_workflow_integration_context(context, metadata)
    assert context.get("generated_workflow_name") == "ResearchReviewWorkflow"

    workspace_files = {
        str(path.relative_to(active_workflows_root.parent).as_posix()): path.read_text(encoding="utf-8")
        for path in sorted(active_workflows_root.rglob("*"))
        if path.is_file()
    }
    plan = _workflow_plan()
    spec = _ArchetypeSpec(
        archetype_id="agentgenerator_appgenerator_handoff",
        app_id="agentgenerator-handoff",
        app_name="AgentGenerator Handoff",
        plan=plan,
        modules=("research",),
        pages=("research",),
        runtime_checks=_workflow_app_checks,
        workspace_files=workspace_files,
        auth_enabled=True,
    )
    files, _, _ = await _run_platform_acceptance(spec=spec, tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert "ResearchReviewWorkflow" in workspace_files["workflows/ResearchReviewWorkflow/orchestrator.yaml"]
    assert "summarize_source" in workspace_files["workflows/ResearchReviewWorkflow/tools.yaml"]
    assert "summarize_source" in files["modules/research/module.yaml"], "MODULE_GAP AgentGenerator tool target action was dropped"
    assert any(
        touchpoint.get("workflow_id") == context.get("generated_workflow_name")
        for touchpoint in spec.plan.get("workflow_touchpoints") or []
    ), "PLAN_LOSS AgentGenerator workflow was not referenced by AppBuildPlan"
