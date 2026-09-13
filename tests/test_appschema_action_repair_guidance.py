from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.hook_primitive_catalog import inject_primitive_catalog
from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.workflow.generator_support.page_plan_utils import validate_planned_page

ROOT = Path(__file__).resolve().parents[1]


def _page() -> dict:
    return {
        "schema_version": "mozaiks.app_page.v1", "name": "records", "route": "/records",
        "title": "Records", "page_type": "record_list", "layout": "full-width",
        "sections": [
            {"id": "list", "primitive": "DataTable", "config": {
                "columns": ["title"], "api_endpoint": "/api/modules/records/list",
            }},
            {"id": "edit-record", "primitive": "Modal", "config": {
                "title": "Edit Record", "children": [{"id": "edit-form", "primitive": "Form", "config": {
                    "initial_values_key": "selected_row",
                    "fields": [{"name": "title", "label": "Title", "type": "text"}],
                    "submit_action": {
                        "id": "save-record", "label": "Save", "action_type": "submit", "href": None,
                        "payload": {"record_id": "{selected_row.record_id}", "title": "{form.title}"},
                    },
                }}],
            }},
        ],
    }


@pytest.mark.parametrize("href", [None, ""])
def test_nested_submit_feedback_preserves_the_runtime_reason(href):
    page = _page()
    page["sections"][1]["config"]["children"][0]["config"]["submit_action"]["href"] = href
    with pytest.raises(ValueError) as caught:
        validate_planned_page(yaml.safe_dump(page), {"route": "/records"}, "ui/pages/records.yaml")
    message = str(caught.value)
    assert "$.sections[1].children[0].submit_action" in message
    assert "submit actions require href" in message
    assert "input_value" not in message
    assert "selected_row.record_id" not in message


@pytest.mark.parametrize("action_type,required_field", [
    ("navigate", "href"), ("delete", "href"), ("event", "event_type"), ("workflow", "workflow_id"),
])
def test_builder_action_feedback_uses_only_known_runtime_messages(action_type, required_field):
    page = _page()
    page["sections"][1]["config"]["children"][0]["config"]["submit_action"] = {
        "label": "Synthetic private text", "action_type": action_type,
    }
    with pytest.raises(ValueError) as caught:
        validate_planned_page(yaml.safe_dump(page), {"route": "/records"}, "ui/pages/records.yaml")
    assert f"{action_type} actions require {required_field}" in str(caught.value)
    assert "Synthetic private text" not in str(caught.value)


def test_unrecognized_runtime_value_errors_stay_sanitized():
    page = _page()
    page["sections"] = [
        {"id": "synthetic-private-value", "primitive": "PageHeader", "config": {"title": "One"}},
        {"id": "synthetic-private-value", "primitive": "PageHeader", "config": {"title": "Two"}},
    ]
    with pytest.raises(ValueError) as caught:
        validate_planned_page(yaml.safe_dump(page), {"route": "/records"}, "ui/pages/records.yaml")
    assert "page_schema.value_error" in str(caught.value)
    assert "synthetic-private-value" not in str(caught.value)
    assert "duplicate section" not in str(caught.value)


def test_separate_create_edit_forms_satisfy_the_unchanged_runtime_contract():
    page = _page()
    edit = page["sections"][1]["config"]["children"][0]["config"]
    edit["submit_action"]["href"] = "/api/modules/records/update"
    create_modal = deepcopy(page["sections"][1])
    create_modal["id"] = "create-record"
    create_modal["config"]["title"] = "Create Record"
    create_child = create_modal["config"]["children"][0]
    create_child["id"] = "create-form"
    create = create_child["config"]
    del create["initial_values_key"]
    create["submit_action"] = {
        "label": "Create", "action_type": "submit", "href": "/api/modules/records/create", "payload": None,
    }
    page["sections"].append(create_modal)
    validate_planned_page(yaml.safe_dump(page), {"route": "/records"}, "ui/pages/records.yaml")
    validate_page_schema(page, action_index={"records": frozenset({"list", "create", "update"})})


class _Agent:
    name = "AppSchemaAgent"
    system_message = "App schema guidance."

    def update_system_message(self, message):
        self.system_message = message


def test_live_primitive_hook_explains_fixed_endpoint_form_binding():
    agent = _Agent()
    inject_primitive_catalog(agent, [])
    inject_primitive_catalog(agent, [])
    assert agent.system_message.count("[SHIPPED PAGE PRIMITIVES]") == 1
    assert "submit actions require href" in agent.system_message
    assert "separate create/edit" in agent.system_message
    assert "one fixed" in agent.system_message
    assert "initial_values_key: selected_row" in agent.system_message


def test_structured_form_guidance_explains_fixed_href_and_split_forms():
    config = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    models = config["models"]
    href = models["AppPageAction"]["fields"]["href"]["description"]
    submit = models["AppFormConfig"]["fields"]["submit_action"]["description"]
    assert "fixed" in href
    assert "separate create/edit" in submit
    assert "href" in submit


def test_appschema_prompt_does_not_imply_one_form_can_switch_endpoints():
    config = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8"))
    agents = config["agents"]
    agent = next(agent for agent in agents if agent["name"] == "AppSchemaAgent")
    prompt = "\n".join(section["content"] for section in agent["prompt_sections"])
    assert "separate create/edit" in prompt
    assert "one fixed" in prompt
