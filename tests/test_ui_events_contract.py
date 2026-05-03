"""
Tests for the L2 typed ui.* WebSocket event contract.

Covers:
  - UIDisplayMode enum values match the frontend constants
  - UIRenderData validation and serialisation
  - UIUpdateData validation and serialisation
  - UIDismissData validation and serialisation
  - model_dump() output shapes match the WebSocket envelope the runtime emits
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.core.transport.ui_events import (
    UIDismissData,
    UIDisplayMode,
    UIRenderData,
    UIUpdateData,
)


# ---------------------------------------------------------------------------
# UIDisplayMode
# ---------------------------------------------------------------------------

def test_display_mode_values() -> None:
    assert UIDisplayMode.INLINE == "inline"
    assert UIDisplayMode.ARTIFACT == "artifact"


def test_display_mode_is_str_enum() -> None:
    assert isinstance(UIDisplayMode.INLINE, str)
    assert isinstance(UIDisplayMode.ARTIFACT, str)


# ---------------------------------------------------------------------------
# UIRenderData
# ---------------------------------------------------------------------------

def test_render_data_minimal() -> None:
    data = UIRenderData(
        tool_call_id="tc-1",
        component="UserInputRequest",
        display_mode=UIDisplayMode.INLINE,
    )
    assert data.tool_call_id == "tc-1"
    assert data.component == "UserInputRequest"
    assert data.display_mode == UIDisplayMode.INLINE
    assert data.awaiting_response is True
    assert data.workflow is None
    assert data.agent is None
    assert data.payload == {}
    assert data.interaction_type == "ui_tool"


def test_render_data_full() -> None:
    data = UIRenderData(
        tool_call_id="tc-abc",
        component="ApprovalCard",
        display_mode=UIDisplayMode.ARTIFACT,
        awaiting_response=True,
        workflow="AppGenerator",
        agent="ReviewAgent",
        payload={"title": "Approve", "checkpoints": ["a", "b"]},
        interaction_type="ui_tool",
    )
    dumped = data.model_dump()
    assert dumped["tool_call_id"] == "tc-abc"
    assert dumped["component"] == "ApprovalCard"
    assert dumped["display_mode"] == "artifact"
    assert dumped["workflow"] == "AppGenerator"
    assert dumped["payload"] == {"title": "Approve", "checkpoints": ["a", "b"]}


def test_render_data_string_display_mode() -> None:
    """The display_mode field must coerce the string enum value."""
    data = UIRenderData(
        tool_call_id="tc-2",
        component="DataTable",
        display_mode="inline",  # type: ignore[arg-type]
    )
    assert data.display_mode == UIDisplayMode.INLINE


def test_render_data_invalid_display_mode() -> None:
    with pytest.raises(ValidationError):
        UIRenderData(
            tool_call_id="tc-3",
            component="Card",
            display_mode="popup",  # type: ignore[arg-type]
        )


def test_render_data_model_dump_serialises_enum_as_string() -> None:
    data = UIRenderData(
        tool_call_id="tc-4",
        component="Stat",
        display_mode=UIDisplayMode.ARTIFACT,
    )
    dumped = data.model_dump()
    # The WebSocket envelope must carry the string value, not the enum object
    assert isinstance(dumped["display_mode"], str)
    assert dumped["display_mode"] == "artifact"


def test_render_data_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        UIRenderData(component="Card", display_mode=UIDisplayMode.INLINE)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# UIUpdateData
# ---------------------------------------------------------------------------

def test_update_data_minimal() -> None:
    data = UIUpdateData(tool_call_id="tc-5")
    assert data.tool_call_id == "tc-5"
    assert data.patch == {}


def test_update_data_with_patch() -> None:
    data = UIUpdateData(tool_call_id="tc-6", patch={"status": "done", "value": 42})
    dumped = data.model_dump()
    assert dumped["tool_call_id"] == "tc-6"
    assert dumped["patch"] == {"status": "done", "value": 42}


def test_update_data_missing_tool_call_id() -> None:
    with pytest.raises(ValidationError):
        UIUpdateData(patch={"key": "val"})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# UIDismissData
# ---------------------------------------------------------------------------

def test_dismiss_data() -> None:
    data = UIDismissData(tool_call_id="tc-7")
    assert data.tool_call_id == "tc-7"
    dumped = data.model_dump()
    assert dumped == {"tool_call_id": "tc-7"}


def test_dismiss_data_missing_tool_call_id() -> None:
    with pytest.raises(ValidationError):
        UIDismissData()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Cross-model: tool_call_id correlation
# ---------------------------------------------------------------------------

def test_render_update_dismiss_share_tool_call_id() -> None:
    """The same tool_call_id can flow through render → update → dismiss."""
    tid = "corr-xyz"
    render  = UIRenderData(tool_call_id=tid, component="ProgressTracker", display_mode=UIDisplayMode.INLINE)
    update  = UIUpdateData(tool_call_id=tid, patch={"stages": [{"label": "Done", "status": "done"}]})
    dismiss = UIDismissData(tool_call_id=tid)

    assert render.tool_call_id == update.tool_call_id == dismiss.tool_call_id == tid
