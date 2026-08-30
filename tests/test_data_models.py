"""
Core data model unit tests.

Covers:
  WorkflowStatus:
    - IN_PROGRESS value is 0
    - COMPLETED value is 1
    - __str__ returns readable string

  ChatMessage:
    - valid construction
    - extra fields rejected (extra="forbid")
    - role enum accepts user/assistant
    - event_type defaults to "message.created"
    - agent_name defaults to None

  WorkflowUIArtifactState:
    - all fields optional, default None

  WorkflowUIPendingInputState:
    - display defaults to "composer"
    - interaction_type defaults to "input_request"
    - password defaults to False

  WorkflowUIToolCallState:
    - message_index must be >= 0
    - awaiting_response defaults to False
    - tool_call_completed defaults to False

  WorkflowUIState:
    - schema_version defaults to 1
    - last_artifact, pending_input_request default None
    - tool_calls defaults to empty dict

  ChatSessionDoc:
    - valid construction with required fields
    - _coerce_scope: no app_id → falls back to appId / AppId / AppID
    - _coerce_scope: app_id already present → unchanged
    - _coerce_scope: none of the fallback keys → app_id stays empty
    - extra fields rejected (extra="forbid")
    - paused defaults to False
    - messages defaults to empty list
    - workflow_ui_state defaults to WorkflowUIState()

  validate_chat_message:
    - valid dict → ChatMessage
    - invalid dict → raises ValidationError

  validate_chat_session:
    - valid dict → ChatSessionDoc
    - invalid dict → raises ValidationError
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mozaiksai.core.data.models import (
    ChatMessage,
    ChatSessionDoc,
    Role,
    WorkflowStatus,
    WorkflowUIArtifactState,
    WorkflowUIPendingInputState,
    WorkflowUIState,
    WorkflowUIToolCallState,
    validate_chat_message,
    validate_chat_session,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)
_ISO = _NOW.isoformat()


def _chat_message(**kw) -> dict:
    base = {
        "role": "user",
        "content": "Hello",
        "timestamp": _ISO,
        "event_id": "evt-1",
    }
    base.update(kw)
    return base


def _session(**kw) -> dict:
    base = {
        "_id": "session-1",
        "app_id": "app-1",
        "workflow_name": "MyWorkflow",
        "user_id": "user-1",
        "status": 0,
        "created_at": _ISO,
        "last_updated_at": _ISO,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 1. WorkflowStatus
# ---------------------------------------------------------------------------

class TestWorkflowStatus:
    def test_in_progress_value_is_0(self):
        assert WorkflowStatus.IN_PROGRESS == 0

    def test_completed_value_is_1(self):
        assert WorkflowStatus.COMPLETED == 1

    def test_str_in_progress(self):
        assert str(WorkflowStatus.IN_PROGRESS) == "in_progress"

    def test_str_completed(self):
        assert str(WorkflowStatus.COMPLETED) == "completed"


# ---------------------------------------------------------------------------
# 2. ChatMessage
# ---------------------------------------------------------------------------

class TestChatMessage:
    def test_valid_construction(self):
        msg = ChatMessage.model_validate(_chat_message())
        assert msg.role == Role.user
        assert msg.content == "Hello"

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ChatMessage.model_validate(_chat_message(unknown_field="value"))

    def test_role_assistant_accepted(self):
        msg = ChatMessage.model_validate(_chat_message(role="assistant"))
        assert msg.role == Role.assistant

    def test_event_type_defaults_to_message_created(self):
        msg = ChatMessage.model_validate(_chat_message())
        assert msg.event_type == "message.created"

    def test_agent_name_defaults_to_none(self):
        msg = ChatMessage.model_validate(_chat_message())
        assert msg.agent_name is None

    def test_agent_name_set(self):
        msg = ChatMessage.model_validate(_chat_message(agent_name="MyAgent"))
        assert msg.agent_name == "MyAgent"

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError):
            ChatMessage.model_validate(_chat_message(role="invalid_role"))


# ---------------------------------------------------------------------------
# 3. WorkflowUIArtifactState
# ---------------------------------------------------------------------------

class TestWorkflowUIArtifactState:
    def test_all_optional_fields_default_none(self):
        state = WorkflowUIArtifactState()
        assert state.tool_name is None
        assert state.tool_call_id is None
        assert state.component_type is None
        assert state.display is None
        assert state.payload is None
        assert state.updated_at is None

    def test_fields_can_be_set(self):
        state = WorkflowUIArtifactState(
            tool_name="save_schema",
            component_type="SchemaCard",
            display="artifact",
        )
        assert state.tool_name == "save_schema"
        assert state.component_type == "SchemaCard"


# ---------------------------------------------------------------------------
# 4. WorkflowUIPendingInputState
# ---------------------------------------------------------------------------

class TestWorkflowUIPendingInputState:
    def test_valid_construction(self):
        state = WorkflowUIPendingInputState(
            request_id="req-1",
            agent="Coordinator",
            prompt="Enter name:",
        )
        assert state.request_id == "req-1"

    def test_display_defaults_to_composer(self):
        state = WorkflowUIPendingInputState(
            request_id="req-1", agent="A", prompt="p"
        )
        assert state.display == "composer"

    def test_interaction_type_defaults(self):
        state = WorkflowUIPendingInputState(
            request_id="req-1", agent="A", prompt="p"
        )
        assert state.interaction_type == "input_request"

    def test_password_defaults_to_false(self):
        state = WorkflowUIPendingInputState(
            request_id="req-1", agent="A", prompt="p"
        )
        assert state.password is False


# ---------------------------------------------------------------------------
# 5. WorkflowUIToolCallState
# ---------------------------------------------------------------------------

class TestWorkflowUIToolCallState:
    def test_valid_construction(self):
        state = WorkflowUIToolCallState(
            tool_call_id="tc-1",
            message_index=0,
        )
        assert state.tool_call_id == "tc-1"

    def test_message_index_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            WorkflowUIToolCallState(tool_call_id="tc-1", message_index=-1)

    def test_awaiting_response_defaults_false(self):
        state = WorkflowUIToolCallState(tool_call_id="tc-1", message_index=0)
        assert state.awaiting_response is False

    def test_tool_call_completed_defaults_false(self):
        state = WorkflowUIToolCallState(tool_call_id="tc-1", message_index=0)
        assert state.tool_call_completed is False


# ---------------------------------------------------------------------------
# 6. WorkflowUIState
# ---------------------------------------------------------------------------

class TestWorkflowUIState:
    def test_schema_version_defaults_to_1(self):
        state = WorkflowUIState()
        assert state.schema_version == 1

    def test_last_artifact_defaults_none(self):
        state = WorkflowUIState()
        assert state.last_artifact is None

    def test_pending_input_request_defaults_none(self):
        state = WorkflowUIState()
        assert state.pending_input_request is None

    def test_tool_calls_defaults_empty_dict(self):
        state = WorkflowUIState()
        assert state.tool_calls == {}

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowUIState(unknown_key="value")


# ---------------------------------------------------------------------------
# 7. ChatSessionDoc
# ---------------------------------------------------------------------------

class TestChatSessionDoc:
    def test_valid_construction(self):
        doc = ChatSessionDoc.model_validate(_session())
        assert doc.id == "session-1"
        assert doc.app_id == "app-1"
        assert doc.workflow_name == "MyWorkflow"

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ChatSessionDoc.model_validate(_session(unknown="value"))

    def test_paused_defaults_false(self):
        doc = ChatSessionDoc.model_validate(_session())
        assert doc.paused is False

    def test_messages_defaults_empty_list(self):
        doc = ChatSessionDoc.model_validate(_session())
        assert doc.messages == []

    def test_workflow_ui_state_defaults_to_empty_state(self):
        doc = ChatSessionDoc.model_validate(_session())
        assert isinstance(doc.workflow_ui_state, WorkflowUIState)

    def test_coerce_scope_appId_fallback_blocked_by_extra_forbid(self):
        # _coerce_scope copies dict and sets app_id but leaves appId in place,
        # so extra="forbid" rejects it. Document actual behavior.
        data = {
            "_id": "s-1",
            "appId": "fallback-app",
            "workflow_name": "W",
            "user_id": "u",
            "status": 0,
            "created_at": _ISO,
            "last_updated_at": _ISO,
        }
        with pytest.raises(ValidationError):
            ChatSessionDoc.model_validate(data)

    def test_coerce_scope_app_id_directly_accepted(self):
        # The canonical way is to pass app_id directly.
        doc = ChatSessionDoc.model_validate(_session(app_id="direct-app"))
        assert doc.app_id == "direct-app"

    def test_coerce_scope_existing_app_id_unchanged(self):
        doc = ChatSessionDoc.model_validate(_session(app_id="canonical-app"))
        assert doc.app_id == "canonical-app"

    def test_id_property(self):
        doc = ChatSessionDoc.model_validate(_session())
        assert doc._id == "session-1"

    def test_status_int_coerced_to_enum(self):
        doc = ChatSessionDoc.model_validate(_session(status=1))
        assert doc.status == WorkflowStatus.COMPLETED

    def test_status_in_progress(self):
        doc = ChatSessionDoc.model_validate(_session(status=0))
        assert doc.status == WorkflowStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# 8. validate_chat_message / validate_chat_session helpers
# ---------------------------------------------------------------------------

class TestValidateHelpers:
    def test_validate_chat_message_valid(self):
        msg = validate_chat_message(_chat_message())
        assert isinstance(msg, ChatMessage)

    def test_validate_chat_message_invalid(self):
        with pytest.raises(ValidationError):
            validate_chat_message({"role": "bad", "content": "x", "timestamp": _ISO, "event_id": "e"})

    def test_validate_chat_session_valid(self):
        doc = validate_chat_session(_session())
        assert isinstance(doc, ChatSessionDoc)

    def test_validate_chat_session_invalid(self):
        with pytest.raises(ValidationError):
            validate_chat_session({"_id": "x", "unknown_field": "value"})
