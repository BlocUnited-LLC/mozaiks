"""The design and page workers must be able to author what the page renderer accepts."""

from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app.page_schema import (
    _CHILD_CONFIG_MODELS,
    _TOP_LEVEL_CONFIG_MODELS,
    validate_page_schema,
)
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.page_plan_utils import compile_authored_page_files
from mozaiksai.core.workflow.outputs.structured import (
    get_provider_response_model,
    load_workflow_structured_outputs,
)
from tests.factory_context import factory_context


def test_design_and_page_workers_cover_the_runtime_primitive_inventory():
    design, _ = load_workflow_structured_outputs("DesignDocs")
    pages, _ = load_workflow_structured_outputs("AppGenerator")
    for model in (design["UIPageSection"], pages["PrimitiveSectionHint"]):
        assert {item.value for item in model.model_fields["primitive"].annotation} == set(_TOP_LEVEL_CONFIG_MODELS)

    for name, inventory in (("AppPageSection", _TOP_LEVEL_CONFIG_MODELS), ("AppPageChildSection", _CHILD_CONFIG_MODELS)):
        fields = pages[name].model_fields
        assert {item.value for item in fields["primitive"].annotation} == set(inventory)
        assert {model.__name__ for model in get_args(fields["config"].annotation)} == {
            model.__name__ for model in inventory.values()
        }


def _page_output(sections):
    _, registry = load_workflow_structured_outputs("AppGenerator")
    model = registry["AppSchemaAgent"]
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "membership", "route": "/membership",
        "title": "Membership", "page_type": "landing", "layout": "full-width", "sections": sections,
    }
    response = model.model_validate({"agent_message": "Membership page", "manifest": None, "pages": [page]})
    return get_provider_response_model(model).model_validate(response.model_dump(mode="json")).model_dump(mode="json")


def test_pricing_sections_round_trip_through_the_worker_and_page_loader():
    plan = {"plan_id": "studio", "label": "Studio", "pricing": {"display": "$12", "interval": "month"}}
    add_on = {"add_on_id": "archive", "label": "Archive", "price_display": "$5"}
    sections = [
        {"id": "available", "primitive": "PricingCatalog", "config": {
            "data_source": {"module_id": "membership", "action_id": "list_plans"}, "plans_key": "plans",
        }},
        {"id": "preview", "primitive": "Grid", "config": {"columns": 1, "children": [
            {"id": "example", "primitive": "PricingCatalog", "config": {"plans": [plan], "add_ons": [add_on]}},
        ]}},
    ]
    output = _page_output(sections)
    files = extract_code_file_map_from_payload(output)
    with pytest.raises(ValueError, match="list_plans"):
        compile_authored_page_files(files, payload=output, context=factory_context())

    module = {"module": {"id": "membership"}, "actions": [{"id": "list_plans"}]}
    context = factory_context({"generated_files": {"modules/membership/module.yaml": yaml.safe_dump(module)}})
    compiled = compile_authored_page_files(files, payload=output, context=context)
    page = validate_page_schema(yaml.safe_load(compiled["ui/pages/membership.yaml"]))
    assert page.sections[0].config == {"api_endpoint": "/api/modules/membership/list_plans", "plans_key": "plans"}
    assert page.sections[1].config["children"][0]["config"] == {"plans": [plan], "add_ons": [add_on]}


@pytest.mark.parametrize("config", [
    {"plans": [{"plan_id": "studio", "label": "Studio", "unknown_field": True}]},
    {"groups": [{"group_id": "studio", "label": "Studio", "kind": "unknown"}]},
    {"add_ons": [{"id": "extra-storage", "add_on_id": "archive", "label": "Archive"}]},
    {"api_endpoint": "/api/modules/membership/list_plans"},
])
def test_worker_rejects_undeclared_pricing_fields_and_raw_endpoint(config):
    with pytest.raises(ValidationError):
        _page_output([{"id": "rates", "primitive": "PricingCatalog", "config": config}])
