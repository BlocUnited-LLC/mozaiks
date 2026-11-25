"""Platform authority for one private-record result on a module-event workflow."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.runtime.app.module_loader import ModuleReactionResultWrite
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.persistence.workflow_result import (
    ResultRecordSubject,
    capture_result_subject,
)


class WorkflowResultGrant(BaseModel):
    """Stored only at session creation. No grant fields are agent inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module_id: str = Field(min_length=1)
    reaction_id: str = Field(min_length=1)
    workflow: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    declaration: ModuleReactionResultWrite
    subject: ResultRecordSubject


@dataclass
class _EmittingEvent:
    identity: tuple[str, str, str, str, str]
    active: bool = True


_emitting: ContextVar[_EmittingEvent | None] = ContextVar("workflow_result_emission", default=None)


@contextmanager
def bind_result_event(app_id: str, module_id: str, action_id: str, event_type: str, event_id: str) -> Iterator[None]:
    """Admit only a validated ctx.emit while its original dispatch is active."""
    event = _EmittingEvent((app_id, module_id, action_id, event_type, event_id))
    token = _emitting.set(event)
    try:
        yield
    finally:
        event.active = False
        _emitting.reset(token)


def issue_workflow_result_grant(
    *, app_id: str, user_id: str, workflow: str, chat_id: str, capability_id: str, invocation_id: str,
    source_event: dict[str, Any], subscription: dict[str, Any],
) -> WorkflowResultGrant:
    target = subscription["target"]
    declaration = ModuleReactionResultWrite.model_validate(target["result_write"])
    module_id = subscription["module_id"]
    event_type, event_id = source_event["type"], source_event["id"]
    emitting = _emitting.get()
    if (emitting is None or not emitting.active
            or emitting.identity != (app_id, module_id, declaration.source_action, event_type, event_id)
            or subscription["event_type"] != event_type
            or target["kind"] != "capability" or target["capability_id"] != capability_id):
        raise PermissionError("Workflow result requires its originating module event")
    subject = capture_result_subject(
        app_id=app_id, module_id=module_id, action_id=declaration.source_action, collection=declaration.collection,
        record_key=declaration.record_key, record_id=source_event["payload"].get(declaration.record_key),
    )
    tenant = source_event["tenant"]
    if (subject.user_id != user_id or subject.tenant_id != tenant.get("tenant_id")
            or subject.workspace_id != tenant.get("workspace_id")):
        raise PermissionError("Workflow result event ownership does not match the stored record")
    return WorkflowResultGrant(
        module_id=module_id, reaction_id=subscription["id"], workflow=workflow, chat_id=chat_id, capability_id=capability_id,
        event_type=event_type, event_id=event_id, invocation_id=invocation_id, declaration=declaration, subject=subject,
    )


class WorkflowResultActionContext(ModuleContext):
    """The internal handler can approve its input exactly once. The host commits it after validation."""

    def __init__(self, grant: WorkflowResultGrant, values: dict[str, Any]) -> None:
        super().__init__(
            app_id=grant.subject.app_id, user_id=grant.subject.user_id, tenant_id=grant.subject.tenant_id,
            workspace_id=grant.subject.workspace_id, module_id=grant.module_id, action_id=grant.declaration.action,
            permissions=[],
        )
        self._expected = json.dumps(values, sort_keys=True, allow_nan=False)
        self.requested = False
        self._invalid = False

    async def commit_workflow_result(self, values: dict[str, Any]) -> dict[str, bool]:
        if self.requested or self._expected != json.dumps(values, sort_keys=True, allow_nan=False):
            self._invalid = True
            raise PermissionError("Result action must commit its validated input exactly once")
        self.requested = True
        return {"accepted": True}

    @property
    def metrics(self) -> Any:
        raise PermissionError("Workflow result actions may only approve their structured input")

    @property
    def approved(self) -> bool:
        return self.requested and not self._invalid
