"""Concept API hints must not teach a second CRUD routing contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.workflow.agents.factory import _compose_prompt_sections
from mozaiksai.core.workflow.outputs.structured import build_models_from_config

WORKFLOW = Path(__file__).resolve().parents[1] / "factory_app/workflows/ValueEngine"


def _prompt_and_example():
    roster = yaml.safe_load((WORKFLOW / "agents.yaml").read_text(encoding="utf-8"))["agents"]
    author = next(agent for agent in roster if agent["name"] == "GapAnalysisAgent")
    prompt = _compose_prompt_sections(author["prompt_sections"])
    examples = [json.loads(block) for block in re.findall(r"```json\s*(\{.*?\})\s*```", prompt, re.S)]
    return prompt, next(example for example in examples if "concept_overview" in example)


def _models_config():
    return yaml.safe_load((WORKFLOW / "structured_outputs.yaml").read_text(encoding="utf-8"))["models"]


def test_blueprint_producer_defers_module_urls_instead_of_inventing_rest_routes():
    prompt, example = _prompt_and_example()
    prompt = " ".join(prompt.split())
    assert example["api_endpoints"] == []
    assert "ordinary module-backed capabilities" in prompt
    assert "Do not invent REST paths" in prompt
    assert "explicit existing API or required custom integration boundary" in prompt
    assert "downstream declared module/action contracts" in prompt
    assert not any(path in prompt for path in ("/api/tasks", "/api/prioritize", "/api/users"))


def test_endpoint_descriptions_keep_the_same_advisory_shape():
    models = _models_config()
    field = models["ConceptBlueprint"]["fields"]["api_endpoints"]
    assert {key: value for key, value in field.items() if key != "description"} == {
        "type": "optional_list", "items": "ApiEndpointSpec",
    }
    assert "ordinary module-backed capabilities" in field["description"]
    endpoint = models["ApiEndpointSpec"]["fields"]
    assert set(endpoint) == {"method", "path", "description"}
    assert endpoint["method"]["values"] == ["GET", "POST", "PUT", "PATCH", "DELETE"]
    assert endpoint["path"]["type"] == "str"
    assert "supplied contract" in endpoint["path"]["description"]


@pytest.mark.parametrize("endpoints", [[], [{
    "method": "POST", "path": "/callbacks/order-events",
    "description": "Receive events at the callback path supplied by the integration contract.",
}]])
def test_typed_blueprint_still_carries_empty_or_explicit_integration_hints(endpoints):
    _, example = _prompt_and_example()
    example["api_endpoints"] = endpoints
    model = build_models_from_config(_models_config())["ConceptBlueprint"]
    actual = model.model_validate(example).model_dump(mode="json")
    assert actual["api_endpoints"] == endpoints
