# ==============================================================================
# FILE: mozaiksai/core/data/models.py
# DESCRIPTION: Defines persisted chat session and workflow UI-state models.
# ==============================================================================
"""Pydantic schemas for workflow session persistence.

ChatSessions is a durable run index and UI-state projection. AG2 stream history
is persisted through the AG2 stream storage adapter. AG2 1.0 middleware emits
token usage into RuntimeUsageEvents and telemetry spans into OpenTelemetry.

ChatSessions Stored Fields (superset; some optional):
    _id, app_id, workflow_name, user_id, status, created_at, last_updated_at,
    completed_at?, trace_id?, duration_sec (float), messages[], workflow_ui_state
"""

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("chat_workflow_models")

# ===========================================
# EXACT PYDANTIC MODELS (MATCH SPECIFICATION)
# ===========================================

class Role(StrEnum):
    user = "user"
    assistant = "assistant"


class WorkflowStatus(IntEnum):
    """Numeric workflow/chat session status.
    0 = in progress, 1 = completed. Additional states removed per simplification.
    Stored as small integers in MongoDB.
    """
    IN_PROGRESS = 0
    COMPLETED = 1

    def __str__(self) -> str:  # for logging
        return "completed" if self.value == 1 else "in_progress"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role
    content: str
    timestamp: datetime
    event_type: str = Field("message.created")
    event_id: str
    agent_name: str | None = None


class WorkflowUIArtifactState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str | None = None
    tool_call_id: str | None = None
    component_type: str | None = None
    display: str | None = None
    workflow_name: str | None = None
    payload: Any = None
    updated_at: str | None = None


class WorkflowUIPendingInputState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    agent: str
    prompt: str
    component_type: str | None = None
    workflow_name: str | None = None
    tool_name: str | None = None
    display: str = "composer"
    interaction_type: str = "input_request"
    password: bool = False
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class WorkflowUIToolCallState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_call_id: str
    message_index: int = Field(ge=0)
    tool_name: str | None = None
    component_type: str | None = None
    display: str | None = None
    workflow_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    awaiting_response: bool = False
    tool_call_completed: bool = False
    tool_call_status: str | None = None
    timestamp: str | None = None
    updated_at: str | None = None
    responded_at: str | None = None
    completed_at: str | None = None


class WorkflowUIState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    last_artifact: WorkflowUIArtifactState | None = None
    pending_input_request: WorkflowUIPendingInputState | None = None
    tool_calls: dict[str, WorkflowUIToolCallState] = Field(default_factory=dict)


class ChatSessionDoc(BaseModel):
    """Canonical ChatSessions document schema.

    ChatSessions tracks lifecycle and UI state. Token usage is tracked in the
    runtime usage ledger.
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(alias="_id")
    app_id: str
    workflow_name: str
    user_id: str
    status: WorkflowStatus  # stored as int (0/1)
    created_at: datetime
    last_updated_at: datetime
    completed_at: datetime | None = None
    trace_id: str | None = None
    # Pause support: for low balance scenarios where chat stays resumable
    paused: bool = False
    pause_reason: str | None = None
    paused_at: datetime | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    workflow_ui_state: WorkflowUIState = Field(default_factory=WorkflowUIState)

    @model_validator(mode="before")
    @classmethod
    def _coerce_scope(cls, data: Any):  # noqa: ANN001
        if isinstance(data, dict) and not data.get("app_id"):
            for key in ("appId", "AppId", "AppID"):
                if data.get(key):
                    data = dict(data)
                    data["app_id"] = data.get(key)
                    break
        return data

    @property
    def _id(self) -> str:  # noqa: D401
        return self.id

# Validation helpers
def validate_chat_message(msg_dict: dict) -> ChatMessage:
    """Validate and create ChatMessage from dict."""
    return ChatMessage.model_validate(msg_dict)

def validate_chat_session(session_dict: dict) -> ChatSessionDoc:
    """Validate and create ChatSessionDoc from dict."""
    return ChatSessionDoc.model_validate(session_dict)
