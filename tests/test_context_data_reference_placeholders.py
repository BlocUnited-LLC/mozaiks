from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.import_utils import import_module_directly

_schema_mod = import_module_directly("mozaiksai.core.workflow.context.schema")
_vars_mod = import_module_directly("mozaiksai.core.workflow.context.variables")


def _build_plan_for_var(variable_name: str):
    return _schema_mod.load_context_variables_config(
        {
            "definitions": {
                variable_name: {
                    "type": "string",
                    "source": {
                        "type": "data_reference",
                        "database_name": "test_db",
                        "collection": "test_collection",
                        "fields": ["value"],
                    },
                }
            },
            "agents": {},
        }
    )


def test_lookup_data_reference_placeholder_prefers_workflow_scope() -> None:
    placeholders = {
        "global": {"concept_overview": "global overview"},
        "workflows": {
            "AgentGenerator": {"concept_overview": "workflow overview"},
        },
    }

    value = _vars_mod._lookup_data_reference_placeholder(placeholders, "AgentGenerator", "concept_overview")
    assert value == "workflow overview"


def test_lookup_data_reference_placeholder_supports_flat_root() -> None:
    placeholders = {
        "concept_overview": "flat overview",
    }

    value = _vars_mod._lookup_data_reference_placeholder(placeholders, "AnyWorkflow", "concept_overview")
    assert value == "flat overview"


@pytest.mark.asyncio
async def test_load_context_uses_placeholder_when_data_reference_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_name = "AgentGenerator"
    variable_name = "concept_overview"
    placeholder_value = "Placeholder overview for tests"

    placeholder_file = tmp_path / "placeholders.json"
    placeholder_file.write_text(
        json.dumps(
            {
                "workflows": {
                    workflow_name: {
                        variable_name: placeholder_value,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    plan = _build_plan_for_var(variable_name)
    monkeypatch.setattr(_vars_mod, "_load_workflow_plan", lambda _wf: (plan, {}))

    async def _missing_data_reference(*args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(_vars_mod, "_load_data_reference_value", _missing_data_reference)
    monkeypatch.setenv("CONTEXT_INCLUDE_SCHEMA", "false")
    monkeypatch.setattr(_vars_mod, "_FILE_CONTEXT_ALLOW_OUTSIDE_ROOT", True)
    monkeypatch.setenv("MOZAIKS_CONTEXT_PLACEHOLDERS_FILE", str(placeholder_file))

    context = await _vars_mod._load_context_async(workflow_name, "app-test")
    assert context.get(variable_name) == placeholder_value


@pytest.mark.asyncio
async def test_load_context_prefers_db_value_over_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_name = "AgentGenerator"
    variable_name = "concept_overview"
    db_value = "DB overview"

    placeholder_file = tmp_path / "placeholders.json"
    placeholder_file.write_text(
        json.dumps(
            {
                "workflows": {
                    workflow_name: {
                        variable_name: "placeholder overview",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    plan = _build_plan_for_var(variable_name)
    monkeypatch.setattr(_vars_mod, "_load_workflow_plan", lambda _wf: (plan, {}))

    async def _present_data_reference(*args, **kwargs):  # type: ignore[no-untyped-def]
        return db_value

    monkeypatch.setattr(_vars_mod, "_load_data_reference_value", _present_data_reference)
    monkeypatch.setenv("CONTEXT_INCLUDE_SCHEMA", "false")
    monkeypatch.setattr(_vars_mod, "_FILE_CONTEXT_ALLOW_OUTSIDE_ROOT", True)
    monkeypatch.setenv("MOZAIKS_CONTEXT_PLACEHOLDERS_FILE", str(placeholder_file))

    context = await _vars_mod._load_context_async(workflow_name, "app-test")
    assert context.get(variable_name) == db_value
