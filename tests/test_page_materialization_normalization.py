"""Typed page serialization has one normalization path before runtime validation."""

from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.save_app_schema import _normalize_page_schema
from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.page_plan_utils import normalize_planned_page_content
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests.test_factory_auto_tool_acceptance import factory_manager  # noqa: F401


@pytest.fixture
def page_models(request):
    manager = request.getfixturevalue("factory_manager")
    info = manager.reload_workflow("AppGenerator")
    assert not info.get("error"), info
    models, _ = load_workflow_structured_outputs("AppGenerator")
    return models


def _page(sections):
    return {
        "schema_version": "mozaiks.app_page.v1",
        "name": "dashboard",
        "route": "/dashboard",
        "title": "Dashboard",
        "page_type": "analytics_dashboard",
        "layout": "full-width",
        "sections": sections,
    }


def _materialize(page, *, workflow_names=None):
    before = deepcopy(page)
    files = extract_code_file_map_from_payload({"manifest": None, "pages": [page]})
    assert page == before, "Materialization must not rewrite the validated agent output."
    content = files["ui/pages/dashboard.yaml"]
    # The standalone save tool and task materializer consume the same normalizer.
    assert yaml.safe_load(content) == _normalize_page_schema(page)
    content = normalize_planned_page_content(
        content, path="ui/pages/dashboard.yaml", workflow_names=workflow_names,
    )
    document = yaml.safe_load(content)
    return document, validate_page_schema(document, expected_name="dashboard")


@pytest.mark.parametrize("container", [None, "Grid", "Modal"])
def test_serialized_foreign_null_defaults_do_not_reject_metric(page_models, container):
    metric = {
        "id": "timer",
        "primitive": "Metric",
        "config": {"label": "Focus Timer", "variant": None, "size": None, "action": None},
    }
    section = metric
    if container:
        config = {"children": [metric]}
        if container == "Grid":
            config["columns"] = 1
        section = {"id": "container", "primitive": container, "config": config}
    typed = page_models["AppPageSection"].model_validate(section)
    typed_metric = typed.config.children[0] if container else typed
    assert type(typed_metric.config).__name__ == "AppButtonConfig"
    assert typed_metric.model_dump(mode="json")["config"] == metric["config"]

    document, validated = _materialize(_page([typed.model_dump(mode="json")]))
    emitted = document["sections"][0]
    if container:
        emitted = emitted["config"]["children"][0]
    assert emitted["primitive"] == "Metric"
    assert emitted["config"] == {"label": "Focus Timer"}
    assert validated.sections[0].primitive == (container or "Metric")


@pytest.mark.parametrize("field,value", [
    ("action", {"label": "Open", "action_type": "navigate", "href": "/tasks"}),
    ("variant", "primary"),
    ("size", "large"),
])
def test_nonnull_foreign_config_fields_still_reject(page_models, field, value):
    from mozaiksai.core.runtime.app.page_schema import PageSchemaValidationError

    typed = page_models["AppPageSection"].model_validate({
        "id": "timer", "primitive": "Metric",
        "config": {"label": "Focus Timer", "variant": None, "size": None, "action": None, field: value},
    })
    with pytest.raises(PageSchemaValidationError) as error:
        _materialize(_page([typed.model_dump(mode="json")]))
    assert any(
        item.code == "page_schema.extra_forbidden" and item.location == f"$.sections[0].config.{field}"
        for item in error.value.diagnostics
    )


@pytest.mark.parametrize("value", [None, 0, False, "25:00"])
def test_real_metric_config_preserves_values(page_models, value):
    typed = page_models["AppPageSection"].model_validate({
        "id": "total", "primitive": "Metric",
        "config": {"label": "Total", "value": value, "detail": "Current value"},
    })
    assert type(typed.config).__name__ == "AppMetricConfig"
    document, _ = _materialize(_page([typed.model_dump(mode="json")]))
    config = document["sections"][0]["config"]
    assert config == {
        "label": "Total", "detail": "Current value", **({"value": value} if value is not None else {}),
    }


def test_typed_action_and_route_auth_payloads_keep_runtime_object_shape(page_models):
    event = {"label": "Refresh", "action_type": "event", "event_type": "ui.test.refresh", "payload": [
        {"key": "count", "value": 0}, {"key": "enabled", "value": False},
        {"key": "query", "value": ""},
    ]}
    workflow = {"label": "Review", "action_type": "workflow", "workflow_id": "ReviewFlow", "context_variables": [
        {"key": "record_id", "value": "record-1"},
    ]}
    sections = [
        {"id": "actions", "primitive": "Grid", "config": {"columns": 2, "children": [
            {"id": "refresh", "primitive": "Button", "config": {"label": "Refresh", "action": event}},
            {"id": "review", "primitive": "Button", "config": {"label": "Review", "action": workflow}},
        ]}},
    ]
    page = _page(sections)
    page["meta"] = {"routeAuth": {
        "module": "tasks", "action": "get_tasks", "params": [{"key": "record_id", "value": "record-1"}],
    }}
    typed = page_models["AppPageSchema"].model_validate(page)
    document, _ = _materialize(typed.model_dump(mode="json"), workflow_names={"ReviewFlow"})
    children = document["sections"][0]["config"]["children"]
    assert children[0]["config"]["action"]["payload"] == {"count": 0, "enabled": False, "query": ""}
    assert children[1]["config"]["action"]["context_variables"] == {"record_id": "record-1"}
    assert document["meta"]["routeAuth"]["params"] == {"record_id": "record-1"}
