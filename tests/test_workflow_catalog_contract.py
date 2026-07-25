from __future__ import annotations

import pytest

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.hosts.routers.workflows import get_workflows


@pytest.mark.asyncio
async def test_workflow_catalog_exposes_structured_output_component_mapping() -> None:
    payload = await get_workflows(
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
    assert value_engine["structured_outputs"]["GapAnalysisAgent"] == "ConceptBlueprint"
    assert value_engine["structured_output_components"] == {
        "GapAnalysisAgent": "ConceptBlueprint"
    }
