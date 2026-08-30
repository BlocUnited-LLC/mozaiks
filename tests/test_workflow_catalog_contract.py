from __future__ import annotations

import pytest

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.hosts import runtime as runtime_host
from mozaiksai.hosts.routers.workflows import get_workflows as get_router_workflows


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
        "GapAnalysisAgent": "ConceptBlueprint"
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
