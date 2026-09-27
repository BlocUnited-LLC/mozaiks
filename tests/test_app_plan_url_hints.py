"""Planner hint cleanup preserves typed bindings and actionable rejection context."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import _validate_page_bindings
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_review import _context, _plan
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


@pytest.mark.parametrize("with_typed_source", [True, False])
def test_review_strips_endpoint_hints_without_inferring_bindings(with_typed_source):
    plan = _plan()
    section = plan["pages"][0]["sections_hint"][0]
    source = deepcopy(section["data_source"]) if with_typed_source else None
    section["data_source"] = source
    config = {
        "api_endpoint": "/api/reports",
        "columns": ["id", "title"],
        "search": True,
        "nested": [{"api_endpoint": "/api/reports/summary", "label": "Summary"}],
    }
    section["config_hint"] = json.dumps(config)
    original = deepcopy(plan)
    context = _context()
    assert isinstance(context, ContextVariablesBridge)

    for _ in range(2):
        result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
        assert result["outcome"] == "ready", result
        cached = detach(context.get("app_build_plan"))["pages"][0]["sections_hint"][0]
        assert cached["data_source"] == source
        assert json.loads(cached["config_hint"]) == {
            "columns": ["id", "title"], "search": True, "nested": [{"label": "Summary"}],
        }
        assert plan == original


@pytest.mark.parametrize("config", [
    {"data_source": {"module_id": "reports"}},
    {"actions": [{"action_type": "submit", "href": "/api/reports", "data_source": {"module_id": "reports"}}]},
    {"actions": [{"action_type": "delete", "href": "/api/reports", "data_source": {"action_id": "delete_report"}}]},
    {"data_source": {"module_id": "reports", "action_id": "list_reports", "api_endpoint": "/api/reports"}},
    {"data_source": {"module_id": "reports", "action_id": "list_reports", "href": "/api/reports"}},
])
def test_review_identifies_page_and_section_for_invalid_config_bindings(config):
    plan = _plan()
    section = plan["pages"][0]["sections_hint"][0]
    section["config_hint"] = json.dumps({"api_endpoint": "/api/reports", **config})
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "Page 'Reports', section 'reports-table'" in result["error"]
    assert "data_source" in result["error"]
    assert context.get("app_plan_feedback") == result["error"]
    assert context.get("app_plan_ready") is False
    assert not context.get("app_task_batch_items")


@pytest.mark.parametrize("action_type", ["submit", "delete"])
@pytest.mark.parametrize("with_typed_source", [True, False])
def test_review_strips_mutation_href_hints_and_preserves_navigation(action_type, with_typed_source):
    plan = _plan()
    source = {"module_id": "reports", "action_id": "update_report"}
    action = {"action_type": action_type, "href": "/api/reports/{report_id}", "label": "Update"}
    if with_typed_source:
        action["data_source"] = source
    navigation = {"action_type": "navigate", "href": "/reports/{report_id}", "label": "Details"}
    section = plan["pages"][0]["sections_hint"][0]
    section["config_hint"] = json.dumps({"children": [{"config": {"actions": [action, navigation]}}]})
    original = deepcopy(plan)
    context = _context()
    assert isinstance(context, ContextVariablesBridge)

    for _ in range(2):
        result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
        assert result["outcome"] == "ready", result
        cached = detach(context.get("app_build_plan"))["pages"][0]["sections_hint"][0]
        actions = json.loads(cached["config_hint"])["children"][0]["config"]["actions"]
        expected = {"action_type": action_type, "label": "Update"}
        if with_typed_source:
            expected["data_source"] = source
        assert actions == [expected, navigation]
        assert plan == original


@pytest.mark.parametrize("binding", [
    {"api_endpoint": "/api/reports"},
    {"action_type": "submit", "href": "/api/reports"},
    {"action_type": "delete", "href": "/api/reports"},
    {"data_source": {"module_id": "managed_reports", "action_id": "list_reports"}},
])
def test_page_binding_validator_keeps_endpoint_and_managed_boundary_checks(binding):
    page = {"name": "Reports", "sections_hint": [{"section_id_hint": "reports-table", **binding}]}

    with pytest.raises(ValueError, match="Page 'Reports', section 'reports-table'"):
        _validate_page_bindings(
            [page], managed_capability_ids=frozenset({"managed_reports"}),
            managed_capability_backing_module_ids=frozenset(),
        )


@pytest.mark.parametrize("section_id", ["reports-table", None])
@pytest.mark.parametrize("source", [
    {"module_id": "reports"},
    {"module_id": "reports", "action_id": 42},
    {"module_id": "reports", "action_id": "list_reports", "endpoint": "/api/reports"},
])
def test_typed_data_source_schema_errors_name_page_and_section(source, section_id):
    plan = _plan()
    section = plan["pages"][0]["sections_hint"][0]
    section.update(section_id_hint=section_id, data_source=source)
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert f"Page 'Reports', section '{section_id or 'sections_hint[0]'}'" in result["error"]
    assert "data_source" in result["error"]
    assert "validation error" in result["error"]
    assert context.get("app_plan_feedback") == result["error"]
    assert context.get("app_plan_ready") is False


def test_designdocs_prompt_examples_do_not_author_endpoint_hints():
    root = Path(__file__).resolve().parents[1]
    text = (root / "factory_app/workflows/DesignDocs/agents.yaml").read_text(encoding="utf-8")
    yaml.safe_load(text)
    assert '\\"api_endpoint\\":' not in text
    assert "Do not emit `api_endpoint` or mutation endpoint URLs in config_hint" in text
