"""Declared structured outputs are EXACT at runtime.

The generic runtime registry compiles every declared structured-output model
with closed-object acceptance: unknown candidate fields reject — top-level and
nested — before any operation capable of deleting unknown information runs.
The #485 staging decision that left generic runtime loading permissive is
retired; there is no legacy mode, no per-workflow fallback, and no permissive
cached model after reload.

Deliberately-open ``dict``/``optional_dict`` fields keep their semantics: the
FIELD is closed at its containing object level, while arbitrary keys inside
the declared open dict remain valid runtime data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.outputs import structured as _so
from mozaiksai.core.workflow.outputs.runtime_validation import (
    validate_agent_structured_output,
)
from mozaiksai.core.workflow.task_batches import _structured_registry_for_agent
from mozaiksai.core.workflow.workflow_manager import workflow_manager

WORKFLOW = "ExactRuntimeProbe"

_AGENT_TEMPLATE = (
    "  - name: {name}\n"
    "    structured_outputs_required: true\n"
    "    prompt_sections:\n"
    "      - id: role\n"
    "        heading: ROLE\n"
    "        content: probe\n"
)

_STRUCTURED_OUTPUTS = """schema_version: mozaiks.structured_outputs.v1
registry:
  ProbeAgent: Envelope
models:
  Status:
    type: literal
    values: [ready, blocked]
  Leaf:
    type: model
    fields:
      label: { type: str }
      note: { type: optional_str }
  Branch:
    type: model
    fields:
      title: { type: str }
      leaf: { type: Leaf }
  Payload:
    type: union
    variants: [Leaf, Branch]
  Envelope:
    type: model
    fields:
      status: { type: Status }
      child: { type: Leaf }
      payload: { type: Payload }
      attempts: { type: int, default: 1 }
      extras: { type: dict }
      annotations: { type: optional_dict }
      rows: { type: optional_list, items: Leaf }
"""


def _write_probe_workflow(root: Path, *, structured_outputs: str = _STRUCTURED_OUTPUTS) -> None:
    workflow_dir = root / WORKFLOW
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "orchestrator.yaml").write_text(
        f"schema_version: mozaiks.orchestrator.v1\nworkflow_name: {WORKFLOW}\n"
        "workflow_startup_mode: BackendOnly\n"
        "initial_agent: ProbeAgent\n"
        "max_turns: 1\n",
        encoding="utf-8",
    )
    (workflow_dir / "agents.yaml").write_text(
        "agents:\n" + _AGENT_TEMPLATE.format(name="ProbeAgent"),
        encoding="utf-8",
    )
    (workflow_dir / "structured_outputs.yaml").write_text(structured_outputs, encoding="utf-8")


@pytest.fixture
def probe_manager(tmp_path: Path):
    """Point the live manager singleton at a tmp workflow root (state-restoring)."""
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
        _write_probe_workflow(tmp_path)
        info = workflow_manager.reload_workflow(WORKFLOW)
        assert not info.get("error"), info
        yield workflow_manager
    finally:
        workflow_manager.__dict__.clear()
        workflow_manager.__dict__.update(saved_manager_state)
        _so.invalidate_all_workflow_structured_outputs()
        _so._workflow_models.update(saved_structured[0])
        _so._workflow_registries.update(saved_structured[1])
        _so._workflow_structured_agents.update(saved_structured[2])
        _so._provider_response_model_cache.update(saved_structured[3])


def _valid_candidate() -> dict:
    return {
        "status": "ready",
        "child": {"label": "leaf-1", "note": None},
        "payload": {"title": "branch-1", "leaf": {"label": "leaf-2", "note": "n"}},
        "attempts": 2,
        "extras": {"anything": {"nested": True}, "another_key": 3},
        "annotations": None,
        "rows": [{"label": "leaf-3", "note": None}],
    }


def _validate(candidate: dict):
    _, registry = _so.load_workflow_structured_outputs(WORKFLOW)
    return validate_agent_structured_output(
        agent_name="ProbeAgent",
        reply=json.dumps(candidate),
        structured_registry=registry,
    )


def test_exact_valid_output_accepted(probe_manager):
    validation = _validate(_valid_candidate())
    assert validation is not None and validation.validation_passed
    assert validation.structured_data["status"] == "ready"
    assert validation.structured_data["extras"] == {
        "anything": {"nested": True},
        "another_key": 3,
    }


def test_every_declared_runtime_model_is_exact(probe_manager):
    models, registry = _so.load_workflow_structured_outputs(WORKFLOW)
    assert set(models) >= {"Leaf", "Branch", "Envelope"}
    for name, model_cls in models.items():
        assert model_cls.model_config.get("extra") == "forbid", (
            f"runtime model {name!r} compiled permissive"
        )
    assert registry["ProbeAgent"] is models["Envelope"]


def test_top_level_extra_field_rejects_before_normalization(probe_manager):
    candidate = {**_valid_candidate(), "undeclared_top": "x"}
    validation = _validate(candidate)
    assert validation is not None
    assert validation.validation_passed is False
    assert validation.structured_data is None
    # The original candidate is retained verbatim for diagnostics — no
    # stripped "clean" payload exists anywhere in the result.
    assert validation.raw_data["undeclared_top"] == "x"
    assert "undeclared_top" in str(validation.error)


def test_nested_extra_field_rejects_before_normalization(probe_manager):
    candidate = _valid_candidate()
    candidate["child"] = {"label": "leaf-1", "note": None, "undeclared_nested": True}
    validation = _validate(candidate)
    assert validation is not None
    assert validation.validation_passed is False
    assert validation.structured_data is None
    assert validation.raw_data["child"]["undeclared_nested"] is True


def test_declared_open_dict_contents_remain_valid(probe_manager):
    candidate = _valid_candidate()
    candidate["extras"] = {"free_form_key": [1, 2, 3], "deep": {"unknown": "ok"}}
    candidate["annotations"] = {"any": "value"}
    validation = _validate(candidate)
    assert validation is not None and validation.validation_passed
    assert validation.structured_data["extras"]["deep"] == {"unknown": "ok"}
    assert validation.structured_data["annotations"] == {"any": "value"}


@pytest.mark.parametrize(
    "mutation,description",
    [
        (lambda c: c.__setitem__("status", "unknown_literal"), "wrong literal"),
        (lambda c: c.__setitem__("child", {"note": None}), "wrong nested model shape"),
        (
            lambda c: c.__setitem__("payload", {"neither": "variant"}),
            "wrong union variant",
        ),
        (lambda c: c.__setitem__("attempts", "not-an-int"), "wrong scalar type"),
    ],
)
def test_exact_literal_and_shape_attacks_reject(probe_manager, mutation, description):
    candidate = _valid_candidate()
    mutation(candidate)
    validation = _validate(candidate)
    assert validation is not None
    assert validation.validation_passed is False, description
    assert validation.structured_data is None


def test_reload_never_falls_back_to_a_permissive_model(probe_manager, tmp_path):
    models_first, _ = _so.load_workflow_structured_outputs(WORKFLOW)
    assert models_first["Envelope"].model_config.get("extra") == "forbid"

    # Reload the workflow and prove the replacement compile is exact too.
    _write_probe_workflow(tmp_path)
    info = workflow_manager.reload_workflow(WORKFLOW)
    assert not info.get("error"), info
    models_second, _ = _so.load_workflow_structured_outputs(WORKFLOW)
    assert models_second["Envelope"] is not models_first["Envelope"]
    for name, model_cls in models_second.items():
        assert model_cls.model_config.get("extra") == "forbid", name
    with pytest.raises(ValidationError):
        models_second["Envelope"].model_validate(
            {**_valid_candidate(), "undeclared_top": "x"}
        )

    # Same proof across a full cache flush.
    _so.invalidate_all_workflow_structured_outputs()
    models_third, _ = _so.load_workflow_structured_outputs(WORKFLOW)
    for name, model_cls in models_third.items():
        assert model_cls.model_config.get("extra") == "forbid", name


def test_task_batch_registry_serves_the_same_exact_models(probe_manager):
    registry = _structured_registry_for_agent(WORKFLOW, "ProbeAgent")
    model_cls = registry["ProbeAgent"]
    assert model_cls.model_config.get("extra") == "forbid"
    with pytest.raises(ValidationError):
        model_cls.model_validate({**_valid_candidate(), "undeclared_top": "x"})
    assert model_cls is _so.load_workflow_structured_outputs(WORKFLOW)[1]["ProbeAgent"]


def test_provider_wire_projection_parse_matches_its_advertised_schema(probe_manager):
    """The wire model advertises additionalProperties: false and must parse
    the same way — otherwise a permissive provider-side parse becomes the
    first lossy normalization before exact runtime acceptance."""
    _, registry = _so.load_workflow_structured_outputs(WORKFLOW)
    wire = _so.get_provider_response_model(registry["ProbeAgent"])
    schema = wire.model_json_schema()
    assert schema["additionalProperties"] is False
    with pytest.raises(ValidationError):
        wire.model_validate({**_valid_candidate(), "undeclared_top": "x"})
    nested_extra = _valid_candidate()
    nested_extra["child"] = {"label": "leaf-1", "note": None, "undeclared": 1}
    with pytest.raises(ValidationError):
        wire.model_validate(nested_extra)


def test_dynamic_models_share_the_exact_acceptance_contract():
    dynamic = _so.build_dynamic_models(
        [
            {
                "model_name": "DynamicProbe",
                "fields": [{"name": "value", "type": "str"}],
            }
        ],
        existing_models={},
    )
    model_cls = dynamic["DynamicProbe"]
    assert model_cls.model_config.get("extra") == "forbid"
    with pytest.raises(ValidationError):
        model_cls.model_validate({"value": "ok", "undeclared": 1})
