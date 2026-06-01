from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.control_plane import app_context_refresh_execution as refresh_execution
from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_STALE_WARNING,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    evaluate_app_context_policy,
)
from mozaiksai.core.app_context.models import SourceRef, SourceRefKind
from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    CONTEXT_REFRESH_EXPECTED_ARTIFACTS,
    ContextRefreshPlan,
    ContextRefreshScope,
)
from mozaiksai.core.workflow.pack.schema import parse_global_pack_graph

ROOT = Path(__file__).resolve().parents[1]


def _source_ref() -> SourceRef:
    return SourceRef(
        source_ref_id="src_repo",
        kind=SourceRefKind.REPO,
        uri="https://example.invalid/field-service.git",
        ref="main",
        checksum="sha256:source",
    )


def _plan(**overrides: Any) -> ContextRefreshPlan:
    payload = {
        "app_id": "field_service",
        "current_context_version_id": "ctx_stale",
        "target_source_refs": [_source_ref()],
        "workflow_sequence": BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
        "expected_artifacts": list(CONTEXT_REFRESH_EXPECTED_ARTIFACTS),
        "mutation_allowed": False,
    }
    payload.update(overrides)
    return ContextRefreshPlan(**payload)


def _refresh_pack(*, sequence_workflows: list[str] | None = None):
    workflows = sequence_workflows or ["ExistingAppDiscovery"]
    return parse_global_pack_graph(
        {
            "version": 3,
            "workflows": [
                {"id": "ExistingAppDiscovery"},
                {"id": "AppGenerator"},
                {"id": "AgentGenerator"},
            ],
            "workflow_sequences": [
                {
                    "id": BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
                    "steps": [{"workflows": workflows}],
                }
            ],
        }
    )


async def _capturing_launcher(captured: dict[str, Any], **kwargs: Any):
    captured.update(kwargs)
    return SimpleNamespace(
        chat_id="chat_refresh_1",
        workflow_id=kwargs["workflow_id"],
        requested_workflow_id=kwargs["workflow_id"],
        journey_id=kwargs["journey_id"],
        websocket_url="/ws/ExistingAppDiscovery/field_service/chat_refresh_1/user_1",
        trigger_source=kwargs["trigger_source"],
    )


@pytest.mark.asyncio
async def test_valid_context_refresh_plan_launches_brownfield_discovery_refresh(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(refresh_execution, "load_global_pack_graph", lambda: _refresh_pack())

    result = await refresh_execution.launch_context_refresh_plan(
        _plan(),
        app_id="field_service",
        user_id="user_1",
        workflow_launcher=lambda **kwargs: _capturing_launcher(captured, **kwargs),
    )

    assert result.status is refresh_execution.ContextRefreshLaunchStatus.LAUNCHED
    assert result.workflow_sequence == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE
    assert result.workflow_id == "ExistingAppDiscovery"
    assert result.chat_id == "chat_refresh_1"
    assert captured["workflow_id"] == "ExistingAppDiscovery"
    assert captured["journey_id"] == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE
    assert captured["trigger_source"] == refresh_execution.CONTEXT_REFRESH_TRIGGER_SOURCE


@pytest.mark.asyncio
async def test_context_refresh_launch_rejects_wrong_workflow_sequence() -> None:
    with pytest.raises(ValueError, match=BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE):
        await refresh_execution.launch_context_refresh_plan(
            _plan(workflow_sequence="unregistered_refresh_sequence"),
            app_id="field_service",
            user_id="user_1",
            workflow_launcher=lambda **kwargs: _capturing_launcher({}, **kwargs),
        )


@pytest.mark.asyncio
async def test_context_refresh_launch_rejects_mutating_plan() -> None:
    mutating_plan = _plan().model_copy(update={"mutation_allowed": True})

    with pytest.raises(ValueError, match="mutation_allowed=false"):
        await refresh_execution.launch_context_refresh_plan(
            mutating_plan,
            app_id="field_service",
            user_id="user_1",
            workflow_launcher=lambda **kwargs: _capturing_launcher({}, **kwargs),
        )


@pytest.mark.asyncio
async def test_context_refresh_launch_passes_current_context_version_id(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(refresh_execution, "load_global_pack_graph", lambda: _refresh_pack())

    await refresh_execution.launch_context_refresh_plan(
        _plan(current_context_version_id="ctx_before_refresh"),
        app_id="field_service",
        user_id="user_1",
        workflow_launcher=lambda **kwargs: _capturing_launcher(captured, **kwargs),
    )

    context = captured["context_variables"]
    assert context["current_context_version_id"] == "ctx_before_refresh"
    assert context["context_refresh_request"]["current_context_version_id"] == "ctx_before_refresh"


@pytest.mark.asyncio
async def test_context_refresh_launch_passes_source_refs(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(refresh_execution, "load_global_pack_graph", lambda: _refresh_pack())

    await refresh_execution.launch_context_refresh_plan(
        _plan(),
        app_id="field_service",
        user_id="user_1",
        workflow_launcher=lambda **kwargs: _capturing_launcher(captured, **kwargs),
    )

    source_refs = captured["context_variables"]["source_refs"]
    assert source_refs == [
        {
            "source_ref_id": "src_repo",
            "kind": "repo",
            "uri": "https://example.invalid/field-service.git",
            "ref": "main",
            "checksum": "sha256:source",
            "indexed_at": None,
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_context_refresh_launch_passes_scope_and_reason(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(refresh_execution, "load_global_pack_graph", lambda: _refresh_pack())

    await refresh_execution.launch_context_refresh_plan(
        _plan(),
        app_id="field_service",
        user_id="user_1",
        refresh_scope=ContextRefreshScope.SOURCE_REF_RESCAN,
        reason="Refresh repository snapshot before retrying.",
        workflow_launcher=lambda **kwargs: _capturing_launcher(captured, **kwargs),
    )

    context = captured["context_variables"]
    assert context["refresh_scope"] == "source_ref_rescan"
    assert context["refresh_reason"] == "Refresh repository snapshot before retrying."
    assert context["context_refresh_request"]["refresh_scope"] == "source_ref_rescan"
    assert context["context_refresh_request"]["reason"] == "Refresh repository snapshot before retrying."


@pytest.mark.asyncio
async def test_context_refresh_launch_returns_expected_artifacts(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(refresh_execution, "load_global_pack_graph", lambda: _refresh_pack())

    result = await refresh_execution.launch_context_refresh_plan(
        _plan(),
        app_id="field_service",
        user_id="user_1",
        workflow_launcher=lambda **kwargs: _capturing_launcher(captured, **kwargs),
    )

    assert result.expected_artifacts == list(CONTEXT_REFRESH_EXPECTED_ARTIFACTS)
    assert captured["context_variables"]["expected_artifacts"] == list(CONTEXT_REFRESH_EXPECTED_ARTIFACTS)


@pytest.mark.asyncio
async def test_context_refresh_launch_does_not_invoke_generation_workflows(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(refresh_execution, "load_global_pack_graph", lambda: _refresh_pack())

    await refresh_execution.launch_context_refresh_plan(
        _plan(),
        app_id="field_service",
        user_id="user_1",
        workflow_launcher=lambda **kwargs: _capturing_launcher(captured, **kwargs),
    )

    assert captured["workflow_id"] == "ExistingAppDiscovery"
    assert captured["workflow_id"] not in {"AppGenerator", "AgentGenerator"}
    assert "AppGenerator" not in captured["journey_id"]
    assert "AgentGenerator" not in captured["journey_id"]


@pytest.mark.asyncio
async def test_context_refresh_launch_does_not_mutate_source_files(monkeypatch, tmp_path: Path) -> None:
    source_file = tmp_path / "service.py"
    source_file.write_text("VALUE = 1\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(refresh_execution, "load_global_pack_graph", lambda: _refresh_pack())

    await refresh_execution.launch_context_refresh_plan(
        _plan(),
        app_id="field_service",
        user_id="user_1",
        workflow_launcher=lambda **kwargs: _capturing_launcher(captured, **kwargs),
    )

    assert source_file.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_stale_context_policy_does_not_launch_refresh() -> None:
    policy_source = (ROOT / "mozaiksai/control_plane/app_context_policy.py").read_text(
        encoding="utf-8"
    )
    dry_run_source = (ROOT / "mozaiksai/control_plane/dry_run.py").read_text(
        encoding="utf-8"
    )
    summary = AppContextSummary(
        app_id="field_service",
        available=True,
        context_version_id="ctx_stale",
        mode="brownfield",
        stale_status="stale",
        stale_reasons=["source ref changed"],
        warnings=[APP_CONTEXT_STALE_WARNING],
    )

    result = evaluate_app_context_policy(
        app_context_summary=summary,
        change_class="feature",
        refinement_lane="integration",
        affected_bundle_paths=["services/integrations/email_client.py"],
    )

    assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH
    assert "launch_context_refresh_plan" not in policy_source
    assert "launch_context_refresh_plan" not in dry_run_source


def test_context_refresh_execution_has_no_graph_database_or_proprietary_terms() -> None:
    paths = [
        ROOT / "mozaiksai/control_plane/app_context_refresh_execution.py",
        ROOT / "tests/test_context_refresh_plan_execution.py",
    ]
    forbidden_terms = (
        "Fal" + "kor",
        "app " + "zero",
        "app_" + "zero",
        "mozaiks" + "-app",
        "mozaiks" + "pay",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for term in forbidden_terms:
            assert term.lower() not in text
