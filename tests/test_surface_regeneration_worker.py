"""Tests for SurfaceRegenerationWorker and execute_surface_plan.

Covers:
- _extract_updated_files path validation
- execute_plan success path with file accumulation across surfaces
- execute_plan partial failure (first surface fails, second succeeds)
- execute_plan total failure
- execute_plan empty plan
- SurfacePlanExecutionResult status values
- OrchestrationControlHarness.execute_surface_plan delegation
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import (
    ContractSurfacePlan,
    ContractSurfaceUpdate,
    SurfacePlanExecutionResult,
)
from mozaiksai.control_plane.implementations.surface_regeneration_worker import (
    SurfaceRegenerationWorker,
)
from mozaiksai.control_plane.schema import LoadedControlPlanePack

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_surface(
    kind: str,
    target_id: str,
    affected_paths: list[str],
    dependency_order: int = 0,
    generation_hint: str = "add feature",
    target_kind: str = "module",
) -> ContractSurfaceUpdate:
    return ContractSurfaceUpdate(
        kind=kind,  # type: ignore[arg-type]
        target_id=target_id,
        target_kind=target_kind,
        affected_paths=affected_paths,
        dependency_order=dependency_order,
        rationale=f"update {kind} for {target_id}",
        confidence=0.9,
        generation_hint=generation_hint,
    )


def _make_routing_decision(change_class: str = "feature") -> Any:
    from unittest.mock import MagicMock

    from mozaiksai.control_plane.implementations.refinement_router import ChangeClass

    intent = MagicMock()
    intent.change_class = ChangeClass(change_class)
    intent.rationale = "add export"
    intent.confidence = 0.9

    rd = MagicMock()
    rd.change_intent = intent
    rd.workflow_id = "AppGenerator"
    rd.workflow_sequence = "app_revision"
    return rd


def _make_refinement_request(
    app_id: str = "app-1",
    user_id: str = "user-1",
    raw_user_request: str = "add export controls to projects",
    artifact_kind: str = "app_bundle",
) -> Any:
    from mozaiksai.control_plane.implementations.refinement_router import RefinementRequest

    return RefinementRequest(
        app_id=app_id,
        user_id=user_id,
        raw_user_request=raw_user_request,
        artifact_kind=artifact_kind,
    )


def _make_mock_pack() -> MagicMock:
    """Return a MagicMock that passes isinstance(mock, LoadedControlPlanePack)."""
    mock_prompt = MagicMock()
    mock_prompt.content = "You are a Mozaiks coding agent."

    mock_checkpoint = MagicMock()
    mock_checkpoint.prompt_id = "coding_refinement_system"

    # spec= makes isinstance(mock, LoadedControlPlanePack) return True,
    # so _load_pack() takes the identity branch instead of calling model_validate.
    mock_pack = MagicMock(spec=LoadedControlPlanePack)
    mock_pack.checkpoint_by_event.return_value = mock_checkpoint
    mock_pack.prompt_by_id.return_value = mock_prompt
    return mock_pack


class _FakeAgentRunner:
    def __init__(self, responses: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self._responses = responses or []
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> Any:
        if self._error is not None:
            raise self._error
        index = len(self.calls)
        self.calls.append(kwargs)
        if not self._responses:
            payload = {}
        elif index < len(self._responses):
            payload = self._responses[index]
        else:
            payload = self._responses[-1]
        return kwargs["response_schema"].model_validate(payload)


def _surface_config() -> ControlPlaneConfig:
    return ControlPlaneConfig.model_validate({
        "llm_profiles": {"codegen": {"llm_config": {"model": "test-generation-model"}}},
        "contract_surface": {"regeneration_llm_profile": "codegen"},
    })


def _make_worker_with_mock_llm(llm_responses: list[dict[str, Any]]) -> SurfaceRegenerationWorker:
    """Return a worker whose LLM returns the given responses in sequence."""
    return SurfaceRegenerationWorker(
        agent_runner=_FakeAgentRunner(llm_responses),
        config_loader=_surface_config,
        pack_loader=_make_mock_pack,
    )


def _module_workspace(module_id: str) -> dict[str, str]:
    return {
        f"modules/{module_id}/module.yaml": (
            "schema_version: mozaiks.module.v1\n"
            f"module:\n  id: {module_id}\n  handler: backend.handler:Handler\n"
            "actions: []\n"
        ),
        f"modules/{module_id}/backend/schemas.py": "class Request: pass\n",
        f"modules/{module_id}/backend/handler.py": "class Handler: pass\n",
    }


def _custom_page_workspace() -> dict[str, str]:
    return {
        "ui/route_manifest.json": json.dumps({
            "pages": [{"id": "Focus", "path": "/", "component": "Focus"}],
        }),
        "ui/index.js": (
            "import FocusView from './pages/custom/focus.jsx';\n"
            "registerComponent('Focus', FocusView);\n"
        ),
        "ui/pages/custom/focus.jsx": "export default function FocusView() { return <p>Focus</p>; }\n",
    }


def _plan_for(surface: ContractSurfaceUpdate, *, build_family: str = "app_bundle") -> ContractSurfacePlan:
    return ContractSurfacePlan(
        surfaces=[surface], summary="Update the saved surface", change_class="feature",
        build_family=build_family, confidence=0.9,
    )


def _prompt_payload(call: dict[str, Any]) -> dict[str, Any]:
    payload, _ = json.JSONDecoder().raw_decode(call["user_prompt"].split("payload_json:\n", 1)[1])
    return payload


# ---------------------------------------------------------------------------
# _extract_updated_files
# ---------------------------------------------------------------------------


def test_extract_updated_files_returns_valid_paths():
    result = SurfaceRegenerationWorker._extract_updated_files(
        response={
            "updated_files": [
                {"path": "modules/projects/module.yaml", "content": "id: projects"},
                {"path": "modules/projects/backend/handler.py", "content": "# handler"},
            ]
        },
        allowed_paths={"modules/projects/module.yaml", "modules/projects/backend/handler.py"},
    )
    assert result["modules/projects/module.yaml"] == "id: projects"
    assert result["modules/projects/backend/handler.py"] == "# handler"


def test_extract_updated_files_rejects_out_of_scope_path():
    with pytest.raises(ValueError, match="outside declared surface scope"):
        SurfaceRegenerationWorker._extract_updated_files(
            response={
                "updated_files": [
                    {"path": "modules/projects/module.yaml", "content": "id: projects"},
                    {"path": "modules/other/module.yaml", "content": "sneaky"},
                ]
            },
            allowed_paths={"modules/projects/module.yaml"},
        )


def test_extract_updated_files_raises_on_empty_response():
    with pytest.raises(ValueError, match="no updated_files"):
        SurfaceRegenerationWorker._extract_updated_files(
            response={},
            allowed_paths={"modules/projects/module.yaml"},
        )


def test_extract_updated_files_raises_when_all_paths_out_of_scope():
    with pytest.raises(ValueError):
        SurfaceRegenerationWorker._extract_updated_files(
            response={"updated_files": [{"path": "other/path.py", "content": "content"}]},
            allowed_paths={"modules/projects/module.yaml"},
        )


def test_extract_updated_files_normalizes_backslashes():
    result = SurfaceRegenerationWorker._extract_updated_files(
        response={"updated_files": [{"path": "modules\\projects\\module.yaml", "content": "content"}]},
        allowed_paths={"modules/projects/module.yaml"},
    )
    assert "modules/projects/module.yaml" in result


# ---------------------------------------------------------------------------
# _build_current_files
# ---------------------------------------------------------------------------


def test_build_current_files_prefers_accumulated_over_workspace():
    surface = _make_surface("module_action", "projects", ["modules/projects/module.yaml"])
    result = SurfaceRegenerationWorker._build_current_files(
        surface=surface,
        accumulated={"modules/projects/module.yaml": "accumulated version"},
        workspace_files={"modules/projects/module.yaml": "workspace version"},
    )
    assert result["modules/projects/module.yaml"] == "accumulated version"


def test_build_current_files_falls_back_to_workspace():
    surface = _make_surface("module_action", "projects", ["modules/projects/module.yaml"])
    result = SurfaceRegenerationWorker._build_current_files(
        surface=surface,
        accumulated={},
        workspace_files={"modules/projects/module.yaml": "workspace version"},
    )
    assert result["modules/projects/module.yaml"] == "workspace version"


def test_build_current_files_requires_saved_source():
    surface = _make_surface("module_action", "projects", ["modules/projects/module.yaml"])
    with pytest.raises(KeyError, match="modules/projects/module.yaml"):
        SurfaceRegenerationWorker._build_current_files(
            surface=surface,
            accumulated={},
            workspace_files={},
        )


# ---------------------------------------------------------------------------
# execute_plan: success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_plan_success_two_surfaces():
    schema_surface = _make_surface(
        "data_schema", "projects",
        ["modules/projects/backend/schemas.py"],
        dependency_order=0,
    )
    action_surface = _make_surface(
        "module_action", "projects",
        ["modules/projects/module.yaml", "modules/projects/backend/handler.py"],
        dependency_order=2,
    )
    plan = ContractSurfacePlan(
        surfaces=[schema_surface, action_surface],
        summary="Add export_projects feature",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )

    worker = _make_worker_with_mock_llm([
        {
            "updated_files": [
                {"path": "modules/projects/backend/schemas.py", "content": "class ExportRequest: ..."},
            ],
            "summary": "added ExportRequest",
            "rationale": "new schema",
        },
        {
            "updated_files": [
                {"path": "modules/projects/module.yaml", "content": "id: projects\nactions:\n- export_projects"},
                {"path": "modules/projects/backend/handler.py", "content": "# handler with export"},
            ],
            "summary": "added export action",
            "rationale": "new action",
        },
    ])

    request = _make_refinement_request()
    routing = _make_routing_decision()

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=request,
        routing_decision=routing,
        workspace_files=_module_workspace("projects"),
    )

    assert result.status == "success"
    assert len(result.surfaces_executed) == 2
    assert all(r.status == "success" for r in result.surfaces_executed)
    assert "modules/projects/backend/schemas.py" in result.all_files
    assert "modules/projects/module.yaml" in result.all_files
    assert "modules/projects/backend/handler.py" in result.all_files
    assert result.metadata["surfaces_succeeded"] == 2
    assert result.metadata["surfaces_failed"] == 0


@pytest.mark.asyncio
async def test_surface_regeneration_uses_explicit_generation_model_after_surface_selection():
    plan = ContractSurfacePlan(
        surfaces=[_make_surface("module_action", "projects", ["modules/projects/module.yaml"])],
        summary="Update projects",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )
    config = ControlPlaneConfig.model_validate({
        "llm_profiles": {
            "impact_analyzer": {"llm_config": {"model": "planning-model"}},
            "codegen": {"llm_config": {"model": "generation-model"}},
        },
        "contract_surface": {
            "enabled": True,
            "llm_profile": "impact_analyzer",
            "regeneration_llm_profile": "codegen",
        },
        "coding": {"enabled": False, "llm_profile": "impact_analyzer"},
    })
    agent_runner = _FakeAgentRunner([{
        "updated_files": [{"path": "modules/projects/module.yaml", "content": "id: projects"}],
        "summary": "updated projects",
        "rationale": "requested change",
    }])
    worker = SurfaceRegenerationWorker(
        agent_runner=agent_runner,
        config_loader=lambda: config,
        pack_loader=_make_mock_pack,
    )

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
        workspace_files=_module_workspace("projects"),
    )

    assert result.status == "success"
    assert agent_runner.calls[0]["llm_config"] == {"model": "generation-model"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_data", "error"),
    [
        ({}, "regeneration_llm_profile is required"),
        ({"contract_surface": {"regeneration_llm_profile": "codegen"}}, "unknown LLM profile"),
        ({
            "contract_surface": {"regeneration_llm_profile": "codegen"},
            "llm_profiles": {"codegen": {"llm_config": {}}},
        }, "requires a non-empty model"),
    ],
)
async def test_surface_regeneration_rejects_missing_generation_model_before_agent_call(
    config_data: dict[str, Any], error: str,
) -> None:
    runner = _FakeAgentRunner()
    config = ControlPlaneConfig.model_validate(config_data)
    worker = SurfaceRegenerationWorker(
        agent_runner=runner, config_loader=lambda: config, pack_loader=_make_mock_pack,
    )

    with pytest.raises(ValueError, match=error):
        await worker.execute_plan(
            plan=_plan_for(_make_surface("module_action", "projects", ["modules/projects/module.yaml"])),
            refinement_request=_make_refinement_request(),
            routing_decision=_make_routing_decision(),
            workspace_files=_module_workspace("projects"),
        )

    assert runner.calls == []


@pytest.mark.asyncio
async def test_execute_plan_later_surface_sees_earlier_file():
    """Accumulated files from surface 1 are visible to surface 2."""
    schema_surface = _make_surface(
        "data_schema", "tasks",
        ["modules/tasks/backend/schemas.py"],
        dependency_order=0,
    )
    action_surface = _make_surface(
        "module_action", "tasks",
        ["modules/tasks/backend/schemas.py", "modules/tasks/backend/handler.py"],
        dependency_order=2,
    )
    plan = ContractSurfacePlan(
        surfaces=[schema_surface, action_surface],
        summary="Add complete_task action",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )

    mock_pack = _make_mock_pack()
    agent_runner = _FakeAgentRunner(
        [
            {
                "updated_files": [
                    {"path": "modules/tasks/backend/schemas.py", "content": "class CompleteRequest: ..."},
                ],
                "summary": "schema",
                "rationale": "new schema",
            },
            {
                "updated_files": [
                    {"path": "modules/tasks/backend/schemas.py", "content": "class CompleteRequest: ..."},
                    {"path": "modules/tasks/backend/handler.py", "content": "# handler"},
                ],
                "summary": "handler",
                "rationale": "new handler",
            },
        ]
    )

    worker = SurfaceRegenerationWorker(
        agent_runner=agent_runner,
        config_loader=_surface_config,
        pack_loader=lambda: mock_pack,
    )

    request = _make_refinement_request()
    routing = _make_routing_decision()

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=request,
        routing_decision=routing,
        workspace_files=_module_workspace("tasks"),
    )

    assert result.status == "success"
    # Second surface's prompt should include the updated schemas.py content
    assert "class CompleteRequest" in agent_runner.calls[1]["user_prompt"]


# ---------------------------------------------------------------------------
# execute_plan: partial failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_plan_partial_failure():
    schema_surface = _make_surface(
        "data_schema", "projects",
        ["modules/projects/backend/schemas.py"],
        dependency_order=0,
    )
    action_surface = _make_surface(
        "module_action", "projects",
        ["modules/projects/module.yaml"],
        dependency_order=2,
    )
    plan = ContractSurfacePlan(
        surfaces=[schema_surface, action_surface],
        summary="Add export",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )

    worker = SurfaceRegenerationWorker(
        agent_runner=_FakeAgentRunner(
            [
                {
                    "updated_files": [],
                    "summary": "oops",
                    "rationale": "missing files",
                },
                {
                    "updated_files": [{"path": "modules/projects/module.yaml", "content": "id: projects"}],
                    "summary": "ok",
                    "rationale": "ok",
                },
            ]
        ),
        config_loader=_surface_config,
        pack_loader=_make_mock_pack,
    )

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
        workspace_files=_module_workspace("projects"),
    )

    assert result.status == "partial"
    assert result.surfaces_executed[0].status == "failed"
    assert result.surfaces_executed[1].status == "success"
    assert result.metadata["surfaces_succeeded"] == 1
    assert result.metadata["surfaces_failed"] == 1


@pytest.mark.asyncio
async def test_execute_plan_all_failed():
    surface = _make_surface("module_action", "x", ["modules/x/module.yaml"])
    plan = ContractSurfacePlan(
        surfaces=[surface],
        summary="test",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )

    worker = SurfaceRegenerationWorker(
        agent_runner=_FakeAgentRunner(error=RuntimeError("LLM unavailable")),
        config_loader=_surface_config,
        pack_loader=_make_mock_pack,
    )

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
        workspace_files=_module_workspace("x"),
    )

    assert result.status == "failed"
    assert result.surfaces_executed[0].status == "failed"
    assert "LLM unavailable" in (result.surfaces_executed[0].error or "")


@pytest.mark.asyncio
async def test_execute_plan_empty_surfaces():
    plan = ContractSurfacePlan(
        surfaces=[],
        summary="nothing",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )
    worker = _make_worker_with_mock_llm([])
    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
        workspace_files={},
    )
    assert result.status == "failed"
    assert result.all_files == {}
    assert result.surfaces_executed == []


# ---------------------------------------------------------------------------
# execute_plan: schema_migration flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_plan_propagates_requires_schema_migration():
    surface = _make_surface("data_schema", "orders", ["modules/orders/backend/schemas.py"])
    plan = ContractSurfacePlan(
        surfaces=[surface],
        summary="Add OrderExport schema",
        change_class="feature",
        artifact_kind="app_bundle",
        requires_schema_migration=True,
        confidence=0.9,
        fallback_to_workflow=False,
    )

    worker = _make_worker_with_mock_llm([
        {
            "updated_files": [{"path": "modules/orders/backend/schemas.py", "content": "class OrderExport: ..."}],
            "summary": "added schema",
            "rationale": "new export schema",
        }
    ])

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
        workspace_files=_module_workspace("orders"),
    )

    assert result.requires_schema_migration is True


# ---------------------------------------------------------------------------
# execute_plan: saved contract admission and read-only binding context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("surface_type", ["schema_page", "custom_page", "workflow_agent"])
async def test_execute_plan_admits_saved_schema_custom_and_workflow_sources(surface_type):
    build_family = "app_bundle"
    if surface_type == "schema_page":
        path = "ui/pages/projects.yaml"
        workspace = {path: (
            "schema_version: mozaiks.app_page.v1\nname: projects\nroute: /projects\n"
            "title: Projects\npage_type: landing\nlayout: full-width\n"
            "sections:\n  - id: heading\n    primitive: PageHeader\n    config:\n      title: Projects\n"
        )}
        surface = _make_surface("page_binding", "projects", [path], target_kind="page")
        updated = workspace[path].replace("title: Projects", "title: Your projects")
    elif surface_type == "custom_page":
        path = "ui/pages/custom/focus.jsx"
        workspace = _custom_page_workspace()
        surface = _make_surface("page_binding", "Focus", [path], target_kind="page")
        updated = "export default function FocusView() { return <p>Ready to focus</p>; }\n"
    else:
        build_family = "workflow_bundle"
        path = "workflows/Support/agents.yaml"
        workspace = {
            "workflows/Support/orchestrator.yaml": (
                "schema_version: mozaiks.orchestrator.v1\nworkflow_name: Support\n"
            ),
            path: "agents: []\n",
        }
        surface = _make_surface("workflow_agent", "Support", [path], target_kind="workflow")
        updated = "agents:\n  - name: Helper\n    system_message: Help the user.\n"
    baseline = dict(workspace)
    runner = _FakeAgentRunner([{
        "summary": "Updated saved source", "rationale": "Requested change",
        "updated_files": [{"path": path, "content": updated}],
    }])
    worker = SurfaceRegenerationWorker(
        agent_runner=runner, config_loader=_surface_config, pack_loader=_make_mock_pack,
    )

    result = await worker.execute_plan(
        plan=_plan_for(surface, build_family=build_family),
        refinement_request=_make_refinement_request(artifact_kind=build_family),
        routing_decision=_make_routing_decision(), workspace_files=workspace,
        allowed_paths=[path],
    )

    assert result.status == "success"
    assert result.all_files == {path: updated}
    assert len(runner.calls) == 1
    payload = _prompt_payload(runner.calls[0])
    assert payload["current_files"] == {path: baseline[path]}
    assert payload["affected_paths"] == [path]
    expected_context = {
        key: baseline[key] for key in ("ui/route_manifest.json", "ui/index.js") if key in baseline
    }
    assert payload["read_only_files"] == expected_context
    assert not set(payload["read_only_files"]) & set(payload["affected_paths"])
    assert workspace == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "excluded_scope", "empty_scope", "wrong_artifact", "wrong_plane", "wrong_target_kind",
    "missing_source", "empty_source", "missing_registry", "ambiguous_registration",
    "later_invalid_surface", "write_registry", "patch_class", "workflow_fallback",
])
async def test_execute_plan_rejects_invalid_whole_plan_before_config_or_model(case):
    path = "ui/pages/custom/focus.jsx"
    workspace = _custom_page_workspace()
    surface = _make_surface("page_binding", "Focus", [path], target_kind="page")
    plan = _plan_for(surface)
    request = _make_refinement_request()
    routing = _make_routing_decision()
    allowed_paths = None
    if case == "excluded_scope":
        allowed_paths = ["ui/index.js"]
    elif case == "empty_scope":
        allowed_paths = []
    elif case == "wrong_artifact":
        plan.build_family = "workflow_bundle"
    elif case == "wrong_plane":
        workspace.update({
            "workflows/Support/orchestrator.yaml": "workflow_name: Support\n",
            "workflows/Support/tools.yaml": "tools: []\n",
        })
        plan.surfaces = [_make_surface(
            "workflow_tool", "Support", ["workflows/Support/tools.yaml"], target_kind="workflow",
        )]
    elif case == "wrong_target_kind":
        surface.target_kind = "module"
    elif case == "missing_source":
        del workspace[path]
    elif case == "empty_source":
        workspace[path] = "  \n"
    elif case == "missing_registry":
        del workspace["ui/index.js"]
    elif case == "ambiguous_registration":
        workspace["ui/index.js"] += (
            "import OtherView from './pages/custom/other.jsx';\n"
            "registerComponent('Focus', OtherView);\n"
        )
        workspace["ui/pages/custom/other.jsx"] = "export default function OtherView() { return null; }\n"
    elif case == "later_invalid_surface":
        # A valid first surface must not consume a model call before the second is rejected.
        plan.surfaces.append(_make_surface(
            "module_action", "missing", ["modules/missing/backend/service.py"], dependency_order=4,
        ))
    elif case == "write_registry":
        surface.affected_paths.append("ui/index.js")
    elif case == "patch_class":
        routing = _make_routing_decision("patch")
    elif case == "workflow_fallback":
        plan.fallback_to_workflow = True
    baseline = dict(workspace)
    runner = _FakeAgentRunner()
    config_loader = MagicMock(side_effect=AssertionError("Invalid plan loaded model configuration"))
    pack_loader = MagicMock(side_effect=AssertionError("Invalid plan loaded generation prompt"))
    worker = SurfaceRegenerationWorker(
        agent_runner=runner, config_loader=config_loader, pack_loader=pack_loader,
    )

    result = await worker.execute_plan(
        plan=plan, refinement_request=request, routing_decision=routing,
        workspace_files=workspace, allowed_paths=allowed_paths,
    )

    assert result.status == "failed"
    assert result.all_files == {}
    assert len(result.surfaces_executed) == len(plan.surfaces)
    assert all(record.status == "failed" and record.error for record in result.surfaces_executed)
    assert result.metadata["surfaces_succeeded"] == 0
    assert result.metadata["surfaces_failed"] == len(plan.surfaces)
    assert runner.calls == []
    config_loader.assert_not_called()
    pack_loader.assert_not_called()
    assert workspace == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only_path", ["ui/route_manifest.json", "ui/index.js"])
async def test_execute_plan_rejects_model_write_to_read_only_page_binding(read_only_path):
    path = "ui/pages/custom/focus.jsx"
    workspace = _custom_page_workspace()
    baseline = dict(workspace)
    runner = _FakeAgentRunner([{
        "summary": "Attempted binding edit", "rationale": "Outside the saved source scope",
        "updated_files": [
            {"path": path, "content": "export default function FocusView() { return null; }\n"},
            {"path": read_only_path, "content": "unauthorized binding rewrite"},
        ],
    }])
    worker = SurfaceRegenerationWorker(
        agent_runner=runner, config_loader=_surface_config, pack_loader=_make_mock_pack,
    )
    result = await worker.execute_plan(
        plan=_plan_for(_make_surface("page_binding", "Focus", [path], target_kind="page")),
        refinement_request=_make_refinement_request(), routing_decision=_make_routing_decision(),
        workspace_files=workspace, allowed_paths=[path],
    )

    assert len(runner.calls) == 1
    assert _prompt_payload(runner.calls[0])["read_only_files"][read_only_path] == baseline[read_only_path]
    assert result.status == "failed"
    assert result.all_files == {}
    assert "outside declared surface scope" in result.surfaces_executed[0].error
    assert read_only_path in result.surfaces_executed[0].error
    assert workspace == baseline


# ---------------------------------------------------------------------------
# OrchestrationControlHarness.execute_surface_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("operator,docker_available,local_available,expected", [
    (None, True, True, "docker"),
    (None, False, True, "local"),
    (None, False, False, "skip"),
    ("local", True, True, "local"),
    ("docker", False, True, "docker"),
    ("skip", True, True, "skip"),
    ("e2b", True, True, "e2b"),
    ("invalid", True, True, None),
])
async def test_surface_finalization_reuses_validation_and_saves_complete_target_bundle(
    monkeypatch, tmp_path, operator, docker_available, local_available, expected,
):
    import zipfile

    from mozaiksai.control_plane.implementations.orchestration_control import (
        OrchestrationControlHarness,
    )
    from mozaiksai.core.session.build_binding import RunBuildBinding
    from mozaiksai.core.workflow.generator_support import app_validation_strategy
    from tests.test_coding_worker import ScopedRefinementCodingWorker, _FakeArtifactStore

    if operator is None:
        monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    else:
        monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", operator)
    monkeypatch.setattr(app_validation_strategy, "docker_app_validation_available", lambda: docker_available)
    monkeypatch.setattr(app_validation_strategy, "local_app_validation_available", lambda: local_available)
    validation_status = "skipped" if expected == "skip" else "passed"

    async def validate(**kwargs):
        assert kwargs["validation_strategy"] == expected
        assert kwargs["app_id"] == "tracker"
        assert kwargs["files"] == {
            "app.json": '{"appId":"tracker"}',
            "modules/x/backend/service.py": "VALUE = 2\n",
            "brand/theme_config.json": '{"accent":"coral"}',
        }
        return {
            "validation_status": validation_status,
            "app_bundle_acceptance_result": {
                "status": "pending" if expected == "skip" else "passed", "passed": expected != "skip",
            },
            "app_validation_result": {"validation_status": validation_status, "validation_strategy": expected},
        }

    validator = AsyncMock(side_effect=validate)
    store = _FakeArtifactStore()
    worker = ScopedRefinementCodingWorker(candidate_validation_runner=validator, artifact_store=store, output_root=tmp_path)
    harness = OrchestrationControlHarness(coding_worker=worker)
    binding = RunBuildBinding(target_app_id="tracker", build_registry_id="registry", build_id="revision", phase="refinement")
    request = _make_refinement_request(app_id="factory").model_copy(update={"target_app_id": "tracker", "build_record_id": "parent"})
    plan = ContractSurfacePlan(
        summary="Update service", build_family="app_bundle", change_class="feature",
        surfaces=[_make_surface("module_action", "x", ["modules/x/backend/service.py"])],
    )
    result = await harness.finalize_surface_output(
        plan=plan, result=SurfacePlanExecutionResult(status="success", all_files={"modules/x/backend/service.py": "VALUE = 2\n"}),
        refinement_request=request, routing_decision=_make_routing_decision(), run_build_binding=binding,
        workspace_files={
            "app.json": '{"appId":"tracker"}',
            "modules/x/backend/service.py": "VALUE = 1\n",
            "brand/theme_config.json": '{"accent":"coral"}',
        },
    )
    if expected is None:
        assert result.status == "failed"
        assert "Unsupported app validation strategy" in result.error
        validator.assert_not_awaited()
        assert store.calls == []
        return
    validator.assert_awaited_once()
    assert result.status == ("planned" if expected == "skip" else "validated"), result.error
    assert result.plan.validation_strategy == expected
    assert len(store.calls) == 1
    assert store.calls[0]["app_id"] == "tracker"
    assert store.calls[0]["parent_build_record_id"] == "parent"
    assert store.calls[0]["app_validation_strategy"] == expected
    assert store.calls[0]["app_validation_status"] == validation_status
    assert store.calls[0]["validation_status"].value == validation_status
    assert store.calls[0]["lifecycle_status"].value == "draft"
    metadata = store.calls[0]["commit_metadata"]["metadata"]
    assert all(metadata[key] == value for key, value in binding.model_dump().items())
    with zipfile.ZipFile(metadata["artifact_path"]) as archive:
        assert {name: archive.read(name).decode() for name in archive.namelist()} == {
            "app.json": '{"appId":"tracker"}',
            "modules/x/backend/service.py": "VALUE = 2\n",
            "brand/theme_config.json": '{"accent":"coral"}',
        }


@pytest.mark.asyncio
async def test_harness_execute_surface_plan_delegates_to_worker():
    from mozaiksai.control_plane.config import ControlPlaneContractSurfaceCapabilityConfig
    from mozaiksai.control_plane.implementations.orchestration_control import (
        OrchestrationControlHarness,
    )

    mock_result = SurfacePlanExecutionResult(
        status="success",
        surfaces_executed=[],
        all_files={"modules/x/module.yaml": "id: x"},
        metadata={},
    )

    mock_worker = MagicMock()
    mock_worker.execute_plan = AsyncMock(return_value=mock_result)

    enabled_config = ControlPlaneConfig(
        enabled=True,
        contract_surface=ControlPlaneContractSurfaceCapabilityConfig(enabled=True),
    )

    harness = OrchestrationControlHarness(
        surface_regeneration_worker=mock_worker,
        config_loader=lambda: enabled_config,
    )

    plan = ContractSurfacePlan(
        surfaces=[],
        summary="test plan",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )
    request = _make_refinement_request()
    routing = _make_routing_decision()

    result = await harness.execute_surface_plan(
        plan=plan,
        refinement_request=request,
        routing_decision=routing,
        workspace_files={"modules/x/module.yaml": "id: x"},
        allowed_paths=["modules/x/module.yaml"],
    )

    assert result.status == "success"
    mock_worker.execute_plan.assert_awaited_once_with(
        plan=plan,
        refinement_request=request,
        routing_decision=routing,
        workspace_files={"modules/x/module.yaml": "id: x"},
        allowed_paths=["modules/x/module.yaml"],
    )


@pytest.mark.asyncio
async def test_harness_execute_surface_plan_raises_when_disabled():
    from mozaiksai.control_plane.config import ControlPlaneContractSurfaceCapabilityConfig
    from mozaiksai.control_plane.implementations.orchestration_control import (
        OrchestrationControlHarness,
    )

    disabled_config = ControlPlaneConfig(
        enabled=True,
        contract_surface=ControlPlaneContractSurfaceCapabilityConfig(enabled=False),
    )

    harness = OrchestrationControlHarness(config_loader=lambda: disabled_config)

    plan = ContractSurfacePlan(
        surfaces=[],
        summary="test",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.9,
        fallback_to_workflow=False,
    )
    with pytest.raises(RuntimeError, match="disabled"):
        await harness.execute_surface_plan(
            plan=plan,
            refinement_request=_make_refinement_request(),
            routing_decision=_make_routing_decision(),
            workspace_files={},
        )
