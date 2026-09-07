"""Real OSS Factory auto-tool acceptance through the runtime event path.

ThemeCapture is a checked-in Factory workflow whose auto tool reads
``context_variables.get("structured_output")``. This suite proves the
documented chain against the real workflow assets and the real runtime:

    valid agent output
    -> exact validation (emit_validated_agent_output)
    -> runtime.agent_output_validated (real UnifiedEventDispatcher)
    -> AutoToolEventHandler (real bindings from tools.yaml)
    -> save_captured_theme sees structured_output
    -> tool succeeds (UI card emitted, artifact persisted, context written)

and the permanent extra-field regression: a valid payload plus one
undeclared nested property must REJECT BEFORE NORMALIZATION — no
agent_output_validated event, no auto-tool execution, no persistence, no UI
emission, and no stripped "clean" payload accepted later.
"""

from __future__ import annotations

import types
from enum import Enum
from pathlib import Path
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from mozaiksai.core.events import auto_tool_handler as _auto_tool_mod
from mozaiksai.core.workflow.context.structured_output_overlay import (
    STRUCTURED_OUTPUT_KEY,
)
from mozaiksai.core.workflow.outputs import structured as _so
from mozaiksai.core.workflow.outputs.runtime_events import emit_validated_agent_output
from mozaiksai.core.workflow.workflow_manager import workflow_manager

FACTORY_WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
WORKFLOW = "ThemeCapture"
AGENT = "ThemeConfigAssemblerAgent"

_UNION_ORIGINS = (Union, types.UnionType)


def _value_for(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in _UNION_ORIGINS:
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) < len(get_args(annotation)):
            return None
        return _value_for(args[0])
    if origin is list:
        return []
    if origin is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _payload_for(annotation)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return next(iter(annotation)).value
    if annotation is str:
        return "probe"
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is bool:
        return False
    return "probe"


def _payload_for(model_cls: type[BaseModel]) -> dict[str, Any]:
    return {
        name: _value_for(field.annotation)
        for name, field in model_cls.model_fields.items()
    }


class _PatternContext:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def snapshot(self):
        return dict(self.data)

    def to_dict(self):
        return self.snapshot()


class _Logger:
    def warning(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None


@pytest.fixture
def factory_manager():
    saved_manager_state = dict(workflow_manager.__dict__)
    saved_structured = (
        dict(_so._workflow_models),
        dict(_so._workflow_registries),
        dict(_so._workflow_structured_agents),
        dict(_so._provider_response_model_cache),
    )
    try:
        workflow_manager.workflows_base_path = FACTORY_WORKFLOWS
        workflow_manager._workflows = {}
        workflow_manager._workflow_paths = {}
        workflow_manager._config_cache = {}
        _so.invalidate_all_workflow_structured_outputs()
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


@pytest.fixture
def side_effect_probes(monkeypatch):
    """Fake the tool's persistence/UI seams before its module is loaded."""
    probes: dict[str, list] = {"summary_artifacts": [], "ui_surfaces": [], "theme_saves": [], "context_persists": []}

    async def fake_persist_summary_artifact(**kwargs):
        probes["summary_artifacts"].append(kwargs)

    async def fake_emit_ui_surface(component, payload, **kwargs):
        probes["ui_surfaces"].append({"component": component, "payload": payload, **kwargs})

    class _FakeStore:
        async def save_theme_capture(self, **kwargs):
            probes["theme_saves"].append(kwargs)

    class _FakePersistenceManager:
        async def persist_context_variables(self, **kwargs):
            probes["context_persists"].append(dict(kwargs.get("variables") or {}))

    async def _no_transport():
        return None

    monkeypatch.setattr(
        "mozaiksai.core.artifacts.persist_summary_artifact", fake_persist_summary_artifact
    )
    monkeypatch.setattr(
        "mozaiksai.core.workflow.ui_tools.emit_ui_surface", fake_emit_ui_surface
    )
    monkeypatch.setattr(
        "mozaiksai.core.data.persistence.artifact_store.BuilderArtifactStore", _FakeStore
    )
    monkeypatch.setattr(_auto_tool_mod, "AG2PersistenceManager", _FakePersistenceManager)
    monkeypatch.setattr(_auto_tool_mod, "_get_simple_transport", _no_transport)
    return probes


def _valid_theme_payload() -> dict[str, Any]:
    _, registry = _so.load_workflow_structured_outputs(WORKFLOW)
    model_cls = registry[AGENT]
    payload = _payload_for(model_cls)
    validated = model_cls.model_validate(payload)
    assert validated is not None
    return payload


async def _drive_runtime(payload: dict[str, Any], pattern: _PatternContext) -> Any:
    _, registry = _so.load_workflow_structured_outputs(WORKFLOW)
    return await emit_validated_agent_output(
        current_agent_name=AGENT,
        last_reply=payload,
        workflow_name=WORKFLOW,
        chat_id="chat-theme-1",
        app_id="app-theme-1",
        user_id="user-1",
        turn_sequence=1,
        context_vars_dict={"app_id": "app-theme-1"},
        context_bridge=pattern,
        structured_registry=registry,
        auto_tool_agents={AGENT},
        wf_logger=_Logger(),
    )


async def test_theme_capture_auto_tool_succeeds_through_real_runtime_path(
    factory_manager, side_effect_probes
):
    payload = _valid_theme_payload()
    pattern = _PatternContext()

    result = await _drive_runtime(payload, pattern)
    assert result == payload

    # The real save_captured_theme executed through the real dispatcher and
    # handler: it saw structured_output, emitted the preview card, persisted
    # the artifact set, and wrote its declared context keys.
    assert len(side_effect_probes["ui_surfaces"]) == 1
    assert side_effect_probes["ui_surfaces"][0]["component"] == "ThemePreviewCard"
    assert len(side_effect_probes["summary_artifacts"]) == 1
    assert side_effect_probes["summary_artifacts"][0]["artifact_kind"] == "theme_capture"
    assert pattern.data.get("theme_capture_persisted") is True
    expected_theme_config = {k: v for k, v in payload.items() if k != "agent_message"}
    assert pattern.data.get("captured_theme_config") == expected_theme_config

    # The projection never became pattern/workflow state or persisted state.
    assert STRUCTURED_OUTPUT_KEY not in pattern.data
    assert side_effect_probes["context_persists"], "context persistence did not run"
    for snapshot in side_effect_probes["context_persists"]:
        assert STRUCTURED_OUTPUT_KEY not in snapshot


@pytest.mark.parametrize(
    "inject",
    [
        pytest.param(lambda p: p.__setitem__("undeclared_top", "x"), id="top-level-extra"),
        pytest.param(
            lambda p: p["identity"].__setitem__("undeclared_nested", True),
            id="nested-extra",
        ),
    ],
)
async def test_extra_field_attack_rejects_before_normalization(
    factory_manager, side_effect_probes, monkeypatch, inject
):
    emitted: list = []

    class _RecordingDispatcher:
        async def emit(self, event_type, event_payload):
            emitted.append((event_type, event_payload))

    monkeypatch.setattr(
        "mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher",
        lambda: _RecordingDispatcher(),
    )

    payload = _valid_theme_payload()
    inject(payload)
    pattern = _PatternContext()

    result = await _drive_runtime(payload, pattern)

    # Rejected before any normalization: no success event, no auto tool, no
    # persistence, no UI emission, and no stripped "clean" payload accepted.
    assert result is None
    assert emitted == []
    assert side_effect_probes["ui_surfaces"] == []
    assert side_effect_probes["summary_artifacts"] == []
    assert side_effect_probes["theme_saves"] == []
    assert side_effect_probes["context_persists"] == []
    assert pattern.data == {}
