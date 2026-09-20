"""``structured_output`` is reserved runtime vocabulary — never application state.

One shared authority (``mozaiksai.core.workflow.reserved_context_keys``)
rejects the reserved identifier at every canonical context declaration
surface: declarative contract validation, typed context schema validation,
and therefore normal workflow declaration loading. No declaration metadata
(authority_class, writer_ids, persisted, source, trigger metadata) can
legalize it, and agent views cannot reference it. The runtime auto-tool
projection needs no declaration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.context.structured_output_overlay import (
    STRUCTURED_OUTPUT_KEY,
)
from mozaiksai.core.workflow.declarative import parse_context_variables_config
from mozaiksai.core.workflow.outputs import structured as _so
from mozaiksai.core.workflow.reserved_context_keys import (
    RUNTIME_RESERVED_CONTEXT_KEYS,
    STRUCTURED_OUTPUT_CONTEXT_KEY,
    require_application_context_name_allowed,
)
from mozaiksai.core.workflow.workflow_manager import workflow_manager

WORKFLOWS_ROOT = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


def _reserved_definition(**extra: object) -> dict:
    return {
        "definitions": {
            "structured_output": {
                "type": "object",
                "description": "illegal application claim of the runtime key",
                "source": {"type": "state", "default": None},
                **extra,
            }
        }
    }


def test_shared_vocabulary_is_the_single_authority():
    assert STRUCTURED_OUTPUT_CONTEXT_KEY == "structured_output"
    assert STRUCTURED_OUTPUT_KEY is STRUCTURED_OUTPUT_CONTEXT_KEY
    assert RUNTIME_RESERVED_CONTEXT_KEYS == frozenset({"structured_output"})
    with pytest.raises(ValueError, match="reserved runtime"):
        require_application_context_name_allowed("structured_output", where="probe")
    assert require_application_context_name_allowed("ordinary_key", where="probe") == "ordinary_key"


def test_declarative_definition_of_reserved_key_rejects():
    with pytest.raises(ValueError, match="reserved runtime"):
        parse_context_variables_config(_reserved_definition())


@pytest.mark.parametrize(
    "metadata",
    [
        {"authority_class": "mutable_workflow_state"},
        {"writer_ids": ["deterministic_tool"]},
        {"writer_ids": ["structured_output"]},
        {"persisted": False},
        {"authority_class": "tool_only_information", "persisted": False, "model_visible": False},
        {
            "source": {
                "type": "state",
                "default": None,
                "triggers": [
                    {"type": "agent_text", "agent": "ProbeAgent", "match": {"equals": "DONE"}}
                ],
            }
        },
    ],
)
def test_no_declaration_metadata_legalizes_the_reserved_key(metadata: dict):
    document = _reserved_definition()
    document["definitions"]["structured_output"].update(metadata)
    with pytest.raises(ValueError, match="reserved runtime"):
        parse_context_variables_config(document)
    with pytest.raises(ValueError, match="reserved runtime"):
        load_context_variables_config(document)


def test_agent_view_reference_to_reserved_key_rejects():
    document = {
        "definitions": {
            "ordinary_key": {"type": "string", "source": {"type": "state", "default": None}}
        },
        "agents": {"ProbeAgent": {"variables": ["ordinary_key", "structured_output"]}},
    }
    with pytest.raises(ValueError, match="reserved runtime"):
        parse_context_variables_config(document)
    with pytest.raises(ValueError, match="reserved runtime"):
        load_context_variables_config(document)


def test_typed_context_schema_rejects_reserved_definition():
    with pytest.raises(ValueError, match="reserved runtime"):
        load_context_variables_config(_reserved_definition())


def test_workflow_declaration_validation_rejects_reserved_key(tmp_path: Path):
    """The failure occurs during normal workflow declaration loading."""
    saved_manager_state = dict(workflow_manager.__dict__)
    saved_structured = (
        dict(_so._workflow_models),
        dict(_so._workflow_registries),
        dict(_so._workflow_structured_agents),
        dict(_so._provider_response_model_cache),
    )
    try:
        workflow_manager.workflows_base_path = tmp_path
        workflow_manager._workflows = {}
        workflow_manager._workflow_paths = {}
        workflow_manager._config_cache = {}
        _so.invalidate_all_workflow_structured_outputs()
        workflow_dir = tmp_path / "ReservedProbe"
        workflow_dir.mkdir(parents=True)
        (workflow_dir / "orchestrator.yaml").write_text(
            "schema_version: mozaiks.orchestrator.v1\nworkflow_name: ReservedProbe\n"
            "workflow_startup_mode: BackendOnly\nmax_turns: 1\n",
            encoding="utf-8",
        )
        (workflow_dir / "context_variables.yaml").write_text(
            "definitions:\n"
            "  structured_output:\n"
            "    type: object\n"
            "    description: illegal\n"
            "    source:\n"
            "      type: state\n"
            "      default: null\n",
            encoding="utf-8",
        )
        info = workflow_manager.reload_workflow("ReservedProbe")
        assert info.get("error"), "reserved declaration must fail workflow loading"
        assert "reserved runtime" in str(info)
    finally:
        workflow_manager.__dict__.clear()
        workflow_manager.__dict__.update(saved_manager_state)
        _so.invalidate_all_workflow_structured_outputs()
        _so._workflow_models.update(saved_structured[0])
        _so._workflow_registries.update(saved_structured[1])
        _so._workflow_structured_agents.update(saved_structured[2])
        _so._provider_response_model_cache.update(saved_structured[3])


def test_first_party_workflow_corpus_declares_no_reserved_keys():
    """Permanent census: no shipped OSS workflow claims runtime vocabulary."""
    import yaml

    offenders: list[str] = []
    for path in sorted(WORKFLOWS_ROOT.glob("*/context_variables.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        declared = set((document.get("definitions") or {}).keys())
        for view in (document.get("agents") or {}).values():
            declared.update((view or {}).get("variables") or [])
        reserved = declared & RUNTIME_RESERVED_CONTEXT_KEYS
        if reserved:
            offenders.append(f"{path.parent.name}: {sorted(reserved)}")
    assert offenders == []
