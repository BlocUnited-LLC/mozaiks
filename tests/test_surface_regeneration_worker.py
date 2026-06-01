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

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import (
    ContractSurfacePlan,
    ContractSurfaceUpdate,
    SurfaceExecutionRecord,
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
) -> ContractSurfaceUpdate:
    return ContractSurfaceUpdate(
        kind=kind,  # type: ignore[arg-type]
        target_id=target_id,
        target_kind="module",
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


def _make_worker_with_mock_llm(llm_responses: list[dict[str, Any]]) -> SurfaceRegenerationWorker:
    """Return a worker whose LLM returns the given responses in sequence."""
    call_index = {"n": 0}

    async def _fake_generate_json_completion(**kwargs: Any) -> dict[str, Any]:
        i = call_index["n"]
        call_index["n"] += 1
        resp = llm_responses[i] if i < len(llm_responses) else llm_responses[-1]
        return {"content": "", "parsed": resp, "usage": {}}

    mock_service = MagicMock()
    mock_service.generate_json_completion = _fake_generate_json_completion

    # ControlPlaneConfig() with no llm_profile → resolve_capability_llm_config returns None.
    # That is fine; the service will use the default provider.

    return SurfaceRegenerationWorker(
        capability_service=mock_service,
        config_loader=ControlPlaneConfig,  # callable that returns ControlPlaneConfig()
        pack_loader=_make_mock_pack,
    )


# ---------------------------------------------------------------------------
# _extract_updated_files
# ---------------------------------------------------------------------------


def test_extract_updated_files_returns_valid_paths():
    result = SurfaceRegenerationWorker._extract_updated_files(
        response={
            "updated_files": {
                "modules/projects/module.yaml": "id: projects",
                "modules/projects/backend/handler.py": "# handler",
            }
        },
        allowed_paths={"modules/projects/module.yaml", "modules/projects/backend/handler.py"},
    )
    assert result["modules/projects/module.yaml"] == "id: projects"
    assert result["modules/projects/backend/handler.py"] == "# handler"


def test_extract_updated_files_rejects_out_of_scope_path():
    with pytest.raises(ValueError, match="outside declared surface scope"):
        SurfaceRegenerationWorker._extract_updated_files(
            response={
                "updated_files": {
                    "modules/projects/module.yaml": "id: projects",
                    "modules/other/module.yaml": "sneaky",
                }
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
            response={"updated_files": {"other/path.py": "content"}},
            allowed_paths={"modules/projects/module.yaml"},
        )


def test_extract_updated_files_normalizes_backslashes():
    result = SurfaceRegenerationWorker._extract_updated_files(
        response={"updated_files": {"modules\\projects\\module.yaml": "content"}},
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


def test_build_current_files_empty_string_for_new_file():
    surface = _make_surface("module_action", "projects", ["modules/projects/module.yaml"])
    result = SurfaceRegenerationWorker._build_current_files(
        surface=surface,
        accumulated={},
        workspace_files=None,
    )
    assert result["modules/projects/module.yaml"] == ""


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
            "updated_files": {
                "modules/projects/backend/schemas.py": "class ExportRequest: ...",
            },
            "summary": "added ExportRequest",
            "rationale": "new schema",
        },
        {
            "updated_files": {
                "modules/projects/module.yaml": "id: projects\nactions:\n- export_projects",
                "modules/projects/backend/handler.py": "# handler with export",
            },
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

    received_user_prompts: list[str] = []

    async def _capture_generate(**kwargs: Any) -> dict[str, Any]:
        received_user_prompts.append(kwargs.get("user_prompt", ""))
        call_n = len(received_user_prompts)
        if call_n == 1:
            return {
                "content": "",
                "parsed": {
                    "updated_files": {
                        "modules/tasks/backend/schemas.py": "class CompleteRequest: ...",
                    },
                    "summary": "schema",
                    "rationale": "new schema",
                },
                "usage": {},
            }
        return {
            "content": "",
            "parsed": {
                "updated_files": {
                    "modules/tasks/backend/schemas.py": "class CompleteRequest: ...",
                    "modules/tasks/backend/handler.py": "# handler",
                },
                "summary": "handler",
                "rationale": "new handler",
            },
            "usage": {},
        }

    mock_service = MagicMock()
    mock_service.generate_json_completion = _capture_generate

    mock_pack = _make_mock_pack()

    worker = SurfaceRegenerationWorker(
        capability_service=mock_service,
        config_loader=ControlPlaneConfig,
        pack_loader=lambda: mock_pack,
    )

    request = _make_refinement_request()
    routing = _make_routing_decision()

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=request,
        routing_decision=routing,
        workspace_files={},
    )

    assert result.status == "success"
    # Second surface's prompt should include the updated schemas.py content
    assert "class CompleteRequest" in received_user_prompts[1]


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

    call_n = {"n": 0}

    async def _flaky_generate(**kwargs: Any) -> dict[str, Any]:
        n = call_n["n"]
        call_n["n"] += 1
        if n == 0:
            # First surface fails — no updated_files
            return {"content": "", "parsed": {"summary": "oops"}, "usage": {}}
        return {
            "content": "",
            "parsed": {
                "updated_files": {"modules/projects/module.yaml": "id: projects"},
                "summary": "ok",
                "rationale": "ok",
            },
            "usage": {},
        }

    mock_service = MagicMock()
    mock_service.generate_json_completion = _flaky_generate

    worker = SurfaceRegenerationWorker(
        capability_service=mock_service,
        config_loader=ControlPlaneConfig,
        pack_loader=_make_mock_pack,
    )

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
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

    async def _failing_generate(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("LLM unavailable")

    mock_service = MagicMock()
    mock_service.generate_json_completion = _failing_generate

    worker = SurfaceRegenerationWorker(
        capability_service=mock_service,
        config_loader=ControlPlaneConfig,
        pack_loader=_make_mock_pack,
    )

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
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
            "updated_files": {"modules/orders/backend/schemas.py": "class OrderExport: ..."},
            "summary": "added schema",
            "rationale": "new export schema",
        }
    ])

    result = await worker.execute_plan(
        plan=plan,
        refinement_request=_make_refinement_request(),
        routing_decision=_make_routing_decision(),
    )

    assert result.requires_schema_migration is True


# ---------------------------------------------------------------------------
# OrchestrationControlHarness.execute_surface_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_harness_execute_surface_plan_delegates_to_worker():
    from mozaiksai.control_plane.config import ControlPlaneCapabilityConfig
    from mozaiksai.control_plane.implementations.orchestration_control import OrchestrationControlHarness

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
        contract_surface=ControlPlaneCapabilityConfig(enabled=True),
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
    )

    assert result.status == "success"
    mock_worker.execute_plan.assert_awaited_once()


@pytest.mark.asyncio
async def test_harness_execute_surface_plan_raises_when_disabled():
    from mozaiksai.control_plane.config import ControlPlaneCapabilityConfig
    from mozaiksai.control_plane.implementations.orchestration_control import OrchestrationControlHarness

    disabled_config = ControlPlaneConfig(
        enabled=True,
        contract_surface=ControlPlaneCapabilityConfig(enabled=False),
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
        )
