from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.workflow.pack import config as pack_config
from mozaiksai.hosts import runtime as runtime_host
from mozaiksai.hosts.routers.workflows import get_workflows as get_router_workflows


@pytest.mark.parametrize("catalog", [get_router_workflows, runtime_host.get_workflows])
async def test_each_catalog_request_reads_one_current_registry(monkeypatch, catalog) -> None:
    custom = SimpleNamespace(id="ValueEngine", launch_behavior="wait_for_user")
    load = Mock(side_effect=[SimpleNamespace(workflows=[custom]), None, ValueError("Missing registry")])
    monkeypatch.setattr(pack_config, "load_global_pack_graph", load)
    principal = UserPrincipal(user_id="tester", email=None, name=None, roles=[], scopes=[], raw_claims={})
    for call_count, expected in enumerate(["wait_for_user", "auto_start", "auto_start"], start=1):
        result = await catalog(principal=principal)
        rows = {row["name"]: row for row in result["workflows"]}
        assert len(rows) > 1
        assert rows["ValueEngine"]["launch_behavior"] == expected
        assert load.call_count == call_count


@pytest.mark.asyncio
async def test_workflow_catalog_exposes_structured_output_component_mapping() -> None:
    payload = await get_router_workflows(
        principal=UserPrincipal(
            user_id="test-user",
            email=None,
            name=None,
            roles=[],
            scopes=[],
            raw_claims={},
            provider="test",
        )
    )

    workflows = {
        workflow["name"]: workflow
        for workflow in payload["workflows"]
    }

    value_engine = workflows["ValueEngine"]
    assert value_engine["interaction_mode"] == "user_guided"
    assert value_engine["launch_behavior"] == "auto_start"
    assert value_engine["handoff_style"] == "continuous_chat"
    assert value_engine["structured_outputs"]["GapAnalysisAgent"] == "ConceptBlueprint"
    assert value_engine["structured_output_components"] == {
        "GapAnalysisAgent": "ConceptBlueprint",
        "ValueInterviewAgent": "ValueInterviewResult",
    }


@pytest.mark.asyncio
async def test_runtime_workflow_catalog_exposes_launch_taxonomy() -> None:
    payload = await runtime_host.get_workflows(
        principal=UserPrincipal(
            user_id="test-user",
            email=None,
            name=None,
            roles=[],
            scopes=[],
            raw_claims={},
            provider="test",
        )
    )

    workflows = {workflow["name"]: workflow for workflow in payload["workflows"]}
    value_engine = workflows["ValueEngine"]
    assert value_engine["startup_mode"] == "UserDriven"
    assert value_engine["interaction_mode"] == "user_guided"
    assert value_engine["launch_behavior"] == "auto_start"
    assert value_engine["handoff_style"] == "continuous_chat"
