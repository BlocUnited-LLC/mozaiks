"""Factory nullable choices must accept JSON null without admitting string sentinels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.managed_monetization_contract import (
    MANAGED_MONETIZATION_PROVIDER_IDS,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.outputs.structured import build_models_from_config

CHOICES = [
    ("AppBuildPlan", "monetization_provider", {"mozaiks_pay", "entitlement_dispatch"}),
    ("AppMonetizationPlan", "monetization_provider", {"mozaiks_pay", "entitlement_dispatch"}),
    ("AppBuildPage", "ui_layout", {"grid", "sidebar", "full-width", "split"}),
    ("AppBuildPage", "page_type_hint", {
        "record_list", "record_detail", "analytics_dashboard", "workflow_board", "activity_feed",
        "gallery", "wizard", "split_view", "settings", "landing", "checkout_success",
    }),
    ("AppImageConfig", "variant", {"inline", "banner"}),
    ("AppAssetEntry", "aspect", {"square", "landscape", "portrait"}),
]


@pytest.fixture(scope="module")
def models():
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
    definitions = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]
    return build_models_from_config(definitions, exact_model_ids=frozenset(definitions))


@pytest.mark.parametrize(("model_name", "field", "choices"), CHOICES)
def test_nullable_choices_accept_json_null_and_preserve_every_choice(models, model_name, field, choices):
    adapter = TypeAdapter(models[model_name].model_fields[field].annotation)
    assert adapter.validate_json("null") is None
    for choice in choices:
        value = adapter.validate_json(json.dumps(choice))
        assert adapter.dump_python(value, mode="json") == choice
    if field == "monetization_provider":
        assert choices == MANAGED_MONETIZATION_PROVIDER_IDS


@pytest.mark.parametrize(("model_name", "field", "choices"), CHOICES)
@pytest.mark.parametrize("invalid", ["null", "none", "unknown", "", False, 0])
def test_nullable_choices_reject_sentinels_and_unknown_values(models, model_name, field, choices, invalid):
    adapter = TypeAdapter(models[model_name].model_fields[field].annotation)
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(invalid))


@pytest.mark.parametrize(("model_name", "field"), [
    ("AppContextVariable", "value_type"),
    ("JsonSchemaProperty", "type"),
    ("JsonSchemaContract", "type"),
])
def test_json_type_discriminators_still_require_the_literal_type_name(models, model_name, field):
    adapter = TypeAdapter(models[model_name].model_fields[field].annotation)
    value = adapter.validate_json('"null"')
    assert adapter.dump_python(value, mode="json") == "null"
    with pytest.raises(ValidationError):
        adapter.validate_json("null")


def _free_plan():
    return {
        "agent_message": "Plan the approved free community app.",
        "app_kind": "workspace",
        "pages": [{
            "name": "Home", "route": "/", "purpose": "Browse the community catalog.",
            "ui_surface": "declarative_page", "ui_layout": None, "page_type_hint": None,
        }],
        "agent_backend_required": False,
        "build_tasks": [],
        "readiness_profile": "none",
        "monetization_provider": None,
        "monetization_plan": {
            "monetized": False, "revenue_model": "free",
            "subscription_contract_requirement": "not_required", "monetization_provider": None,
            "money_flow_summary": "No payments or paid access.",
            "rationale": "The approved concept is free for all members.",
        },
    }


def test_true_null_plan_passes_exact_output_validation_and_downstream_save(models):
    output = models["AppBuildPlanOutput"].model_validate_json(json.dumps({"AppBuildPlan": _free_plan()}))
    plan = output.model_dump(mode="json")["AppBuildPlan"]
    context = ContextVariablesBridge({"subscription_contract": {"contract_required": False}})
    app_build_plan(AppBuildPlan=plan, context_variables=context)
    cached = detach(context.get("app_build_plan"))
    assert cached["monetization_provider"] is None
    assert cached["monetization_plan"]["monetization_provider"] is None
    assert cached["capability_packs"] == []
    assert context.get("app_plan_ready") is True


def test_null_provider_does_not_satisfy_a_required_subscription_contract(models):
    output = models["AppBuildPlanOutput"].model_validate_json(json.dumps({"AppBuildPlan": _free_plan()}))
    context = ContextVariablesBridge({"subscription_contract": {"contract_required": True}})
    with pytest.raises(ValueError, match="monetization_provider is required"):
        app_build_plan(AppBuildPlan=output.model_dump(mode="json")["AppBuildPlan"], context_variables=context)
    assert context.get("app_build_plan") is None


@pytest.mark.parametrize("invalid", ["null", "none", "free", "", False, 0, "custom"])
@pytest.mark.parametrize("nested", [False, True])
def test_downstream_save_rejects_noncanonical_absence_before_caching(invalid, nested):
    plan = _free_plan()
    target = plan["monetization_plan"] if nested else plan
    target["monetization_provider"] = invalid
    context = ContextVariablesBridge({"subscription_contract": {"contract_required": False}})
    with pytest.raises(ValueError, match="monetization_provider must be one of"):
        app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert context.get("app_build_plan") is None
    assert context.get("app_plan_ready") is False
