"""Authority policy for workflow context variable mutations.

Mozaiks still uses AG2 ``WorkflowState.context_vars`` as the execution state
read by routing conditions. This module owns the deterministic guardrail that
decides whether a proposed context mutation may be handed to AG2.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_CONTEXT_REF_RE = re.compile(r"\$\{([^}]+)\}")


class ContextAuthorityError(ValueError):
    """Raised when a workflow context mutation has no declared authority."""


class ContextAuthorityClass(StrEnum):
    IMMUTABLE_RUNTIME_AUTHORITY = "immutable_runtime_authority"
    CLOSED_WRITER_ROUTING_STATE = "closed_writer_routing_state"
    CLOSED_WRITER_QUALITY_STATE = "closed_writer_quality_state"
    MUTABLE_WORKFLOW_STATE = "mutable_workflow_state"
    MODEL_VISIBLE_INFORMATION = "model_visible_information"
    TOOL_ONLY_INFORMATION = "tool_only_information"
    DERIVED_INFORMATION = "derived_information"


ContextWriterId = Literal[
    "runtime_system",
    "caller_input",
    "transition_router",
    "live_user_context",
    "context_bridge",
    "tool_writeback",
    "agent_text",
    "ui_response_trigger",
    "user_text_trigger",
    "persisted_replay",
    "task_batch",
    "structured_output",
    "lifecycle_tool",
    "deterministic_tool",
]

RUNTIME_SYSTEM_WRITER: ContextWriterId = "runtime_system"
CALLER_INPUT_WRITER: ContextWriterId = "caller_input"
TRANSITION_ROUTER_WRITER: ContextWriterId = "transition_router"
LIVE_USER_CONTEXT_WRITER: ContextWriterId = "live_user_context"
CONTEXT_BRIDGE_WRITER: ContextWriterId = "context_bridge"
TOOL_WRITEBACK_WRITER: ContextWriterId = "tool_writeback"
AGENT_TEXT_WRITER: ContextWriterId = "agent_text"
UI_RESPONSE_TRIGGER_WRITER: ContextWriterId = "ui_response_trigger"
USER_TEXT_TRIGGER_WRITER: ContextWriterId = "user_text_trigger"
PERSISTED_REPLAY_WRITER: ContextWriterId = "persisted_replay"
TASK_BATCH_WRITER: ContextWriterId = "task_batch"
STRUCTURED_OUTPUT_WRITER: ContextWriterId = "structured_output"
LIFECYCLE_TOOL_WRITER: ContextWriterId = "lifecycle_tool"
DETERMINISTIC_TOOL_WRITER: ContextWriterId = "deterministic_tool"
CONTEXT_AUTHORITY_WRITER_DEP = "mozaiks.context_authority.scoped_writer"

_ALL_WRITERS = {
    RUNTIME_SYSTEM_WRITER,
    CALLER_INPUT_WRITER,
    TRANSITION_ROUTER_WRITER,
    LIVE_USER_CONTEXT_WRITER,
    CONTEXT_BRIDGE_WRITER,
    TOOL_WRITEBACK_WRITER,
    AGENT_TEXT_WRITER,
    UI_RESPONSE_TRIGGER_WRITER,
    USER_TEXT_TRIGGER_WRITER,
    PERSISTED_REPLAY_WRITER,
    TASK_BATCH_WRITER,
    STRUCTURED_OUTPUT_WRITER,
    LIFECYCLE_TOOL_WRITER,
    DETERMINISTIC_TOOL_WRITER,
}
_DETERMINISTIC_WRITERS = {
    RUNTIME_SYSTEM_WRITER,
    TRANSITION_ROUTER_WRITER,
    UI_RESPONSE_TRIGGER_WRITER,
    TASK_BATCH_WRITER,
    STRUCTURED_OUTPUT_WRITER,
    LIFECYCLE_TOOL_WRITER,
    DETERMINISTIC_TOOL_WRITER,
}
_ORDINARY_MUTATION_WRITERS = {
    CALLER_INPUT_WRITER,
    TRANSITION_ROUTER_WRITER,
    LIVE_USER_CONTEXT_WRITER,
    CONTEXT_BRIDGE_WRITER,
    TOOL_WRITEBACK_WRITER,
    UI_RESPONSE_TRIGGER_WRITER,
    USER_TEXT_TRIGGER_WRITER,
    PERSISTED_REPLAY_WRITER,
    TASK_BATCH_WRITER,
    STRUCTURED_OUTPUT_WRITER,
    LIFECYCLE_TOOL_WRITER,
    DETERMINISTIC_TOOL_WRITER,
}
_IMMUTABLE_EXACT = {
    "app_id",
    "user_id",
    "chat_id",
    "workflow_name",
    "tenant_id",
    "workspace_id",
    "workspace_path",
    "repo_path",
    "repository_path",
    "permissions",
    "permission_claims",
    "entitlement_state",
    "entitlements",
    "build_registry_id",
}
_IMMUTABLE_SUFFIXES = ("_app_id", "_user_id", "_tenant_id", "_workspace_id")
_IMMUTABLE_PARTS = ("credential", "secret", "token", "password", "api_key")
_ROUTING_EXACT = {
    "build_mode",
    "revision_scope",
    "task_run_mode",
    "sequence_status",
    "routing_mode",
}
_TRANSITION_ROUTER_SEEDED_KEYS = {
    "build_mode",
    "revision_scope",
    "sequence_status",
}
_ROUTING_SUFFIXES = ("_route_ready", "_complete", "_decision", "_selected")
_QUALITY_PARTS = ("quality", "validation_status", "acceptance_status", "tests_passed", "gate")


class ContextAuthorityMetadata(BaseModel):
    """Optional strict metadata on a context variable declaration."""

    model_config = ConfigDict(extra="forbid")

    authority_class: ContextAuthorityClass | None = None
    model_visible: bool | None = None
    tool_visible: bool | None = None
    persisted: bool | None = None
    routing: bool | None = None
    authorization: bool | None = None
    writer_ids: list[ContextWriterId] = Field(default_factory=list)

    @field_validator("writer_ids")
    @classmethod
    def _normalize_writer_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value:
            writer = str(raw or "").strip()
            if not writer or writer in seen:
                continue
            if writer not in _ALL_WRITERS:
                raise ValueError(f"unknown context writer_id {writer!r}")
            seen.add(writer)
            out.append(writer)
        return out

    @model_validator(mode="after")
    def _validate_closed_writers(self) -> ContextAuthorityMetadata:
        if self.authority_class is ContextAuthorityClass.IMMUTABLE_RUNTIME_AUTHORITY and self.writer_ids:
            raise ValueError("immutable_runtime_authority variables must not declare variable writers")
        return self


@dataclass(frozen=True, slots=True)
class ContextVariableAuthority:
    key: str
    authority_class: ContextAuthorityClass
    model_visible: bool
    tool_visible: bool
    persisted: bool
    routing: bool
    authorization: bool
    writer_ids: frozenset[ContextWriterId] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class ContextAuthorityPolicy:
    """Closed mutation policy for one workflow's context variables."""

    workflow_name: str
    variables: Mapping[str, ContextVariableAuthority]

    def require_can_write(self, key: str, *, writer_id: ContextWriterId, operation: str = "set") -> None:
        clean_key = _clean_key(key)
        if not clean_key:
            raise ContextAuthorityError("context_authority.empty_key")
        authority = self.variables.get(clean_key)
        if authority is None:
            raise ContextAuthorityError(
                f"context_authority.unknown_key workflow={self.workflow_name} key={clean_key} writer={writer_id} op={operation}"
            )
        if not self.can_write(clean_key, writer_id=writer_id):
            raise ContextAuthorityError(
                f"context_authority.rejected workflow={self.workflow_name} key={clean_key} writer={writer_id} op={operation}"
            )

    def can_write(self, key: str, *, writer_id: ContextWriterId) -> bool:
        clean_key = _clean_key(key)
        authority = self.variables.get(clean_key)
        if authority is None:
            return False
        if writer_id == RUNTIME_SYSTEM_WRITER:
            return True
        if authority.authority_class is ContextAuthorityClass.IMMUTABLE_RUNTIME_AUTHORITY:
            return writer_id == RUNTIME_SYSTEM_WRITER
        if writer_id == AGENT_TEXT_WRITER:
            return (
                authority.authority_class is ContextAuthorityClass.DERIVED_INFORMATION
                and AGENT_TEXT_WRITER in authority.writer_ids
                and not authority.routing
                and not authority.authorization
            )
        if authority.authority_class in {
            ContextAuthorityClass.CLOSED_WRITER_ROUTING_STATE,
            ContextAuthorityClass.CLOSED_WRITER_QUALITY_STATE,
        }:
            return writer_id in authority.writer_ids and writer_id in _DETERMINISTIC_WRITERS
        if authority.writer_ids:
            return writer_id in authority.writer_ids
        return writer_id in _ORDINARY_MUTATION_WRITERS

    def filter_for_replay(self, values: Mapping[str, Any], *, writer_id: ContextWriterId) -> dict[str, Any]:
        accepted: dict[str, Any] = {}
        for key, value in values.items():
            clean_key = _clean_key(key)
            if not clean_key:
                continue
            self.require_can_write(clean_key, writer_id=writer_id)
            authority = self.variables.get(clean_key)
            if authority is not None and not authority.persisted:
                continue
            accepted[clean_key] = value
        return accepted


@dataclass(frozen=True, slots=True)
class ScopedContextWriter:
    """Capability object injected through AG2 dependencies for trusted tools."""

    policy: ContextAuthorityPolicy
    writer_id: ContextWriterId

    def set(self, target: Any, key: str, value: Any) -> None:
        self.policy.require_can_write(key, writer_id=self.writer_id, operation="set")
        if hasattr(target, "set"):
            target.set(key, value)
        elif hasattr(target, "__setitem__"):
            target[key] = value
        else:
            raise ContextAuthorityError("context_authority.unsupported_target")


def build_context_authority_policy(
    *,
    workflow_name: str,
    definitions: Mapping[str, Any],
    transition_rules: Any = None,
    task_batch_context_keys: set[str] | None = None,
) -> ContextAuthorityPolicy:
    routing_keys = _transition_context_keys(transition_rules)
    task_keys = set(task_batch_context_keys or set())
    variables: dict[str, ContextVariableAuthority] = {}
    for raw_key, definition in definitions.items():
        key = _clean_key(raw_key)
        if not key:
            continue
        variables[key] = infer_context_authority(
            key,
            definition=definition,
            routing_keys=routing_keys,
            task_batch_context_keys=task_keys,
        )
    return ContextAuthorityPolicy(workflow_name=str(workflow_name or ""), variables=variables)


def infer_context_authority(
    key: str,
    *,
    definition: Any | None,
    routing_keys: frozenset[str],
    task_batch_context_keys: set[str] | None = None,
) -> ContextVariableAuthority:
    clean_key = _clean_key(key)
    metadata = _metadata_from_definition(definition)
    source = _definition_source(definition)
    source_type = str(_value(source, "type", "") or "").strip()
    triggers = list(_value(source, "triggers", []) or [])
    trigger_types = {str(_value(trigger, "type", "") or "").strip() for trigger in triggers}
    task_keys = task_batch_context_keys or set()

    authority_class = metadata.authority_class
    if authority_class is None:
        authority_class = _infer_authority_class(clean_key, source_type, trigger_types, routing_keys, task_keys)

    routing = bool(metadata.routing) if metadata.routing is not None else clean_key in routing_keys or authority_class is ContextAuthorityClass.CLOSED_WRITER_ROUTING_STATE
    authorization = bool(metadata.authorization) if metadata.authorization is not None else _is_immutable_key(clean_key)
    model_visible = bool(metadata.model_visible) if metadata.model_visible is not None else authority_class not in {
        ContextAuthorityClass.IMMUTABLE_RUNTIME_AUTHORITY,
        ContextAuthorityClass.TOOL_ONLY_INFORMATION,
    }
    tool_visible = bool(metadata.tool_visible) if metadata.tool_visible is not None else True
    persisted = bool(metadata.persisted) if metadata.persisted is not None else authority_class not in {
        ContextAuthorityClass.IMMUTABLE_RUNTIME_AUTHORITY,
        ContextAuthorityClass.TOOL_ONLY_INFORMATION,
    }
    writer_ids = set(metadata.writer_ids)
    if not writer_ids:
        writer_ids = _infer_writer_ids(authority_class, source_type, trigger_types, clean_key, task_keys)

    if authority_class is ContextAuthorityClass.IMMUTABLE_RUNTIME_AUTHORITY:
        writer_ids = set()
    if authority_class in {
        ContextAuthorityClass.CLOSED_WRITER_ROUTING_STATE,
        ContextAuthorityClass.CLOSED_WRITER_QUALITY_STATE,
    }:
        writer_ids = {writer for writer in writer_ids if writer in _DETERMINISTIC_WRITERS}

    return ContextVariableAuthority(
        key=clean_key,
        authority_class=authority_class,
        model_visible=model_visible,
        tool_visible=tool_visible,
        persisted=persisted,
        routing=routing,
        authorization=authorization,
        writer_ids=frozenset(writer_ids),
    )


def validate_transition_context_authority(
    *,
    workflow_name: str,
    policy: ContextAuthorityPolicy,
    transition_rules: Any,
) -> None:
    failures: list[str] = []
    for key in sorted(_transition_context_keys(transition_rules)):
        authority = policy.variables.get(key)
        if authority is None:
            failures.append(f"unknown routing context variable {key!r}")
            continue
        if not authority.routing:
            failures.append(f"non-routing context variable {key!r} used as routing condition")
            continue
        if not (authority.writer_ids & _DETERMINISTIC_WRITERS):
            failures.append(f"routing context variable {key!r} has no authorized deterministic writer")
    if failures:
        raise ContextAuthorityError(
            f"{workflow_name} context authority contract mismatch: {'; '.join(failures)}"
        )


def _metadata_from_definition(definition: Any | None) -> ContextAuthorityMetadata:
    if definition is None:
        return ContextAuthorityMetadata()
    raw = {
        "authority_class": _value(definition, "authority_class", None),
        "model_visible": _value(definition, "model_visible", None),
        "tool_visible": _value(definition, "tool_visible", None),
        "persisted": _value(definition, "persisted", None),
        "routing": _value(definition, "routing", None),
        "authorization": _value(definition, "authorization", None),
        "writer_ids": _value(definition, "writer_ids", []) or [],
    }
    return ContextAuthorityMetadata.model_validate(raw)


def _definition_source(definition: Any | None) -> Any:
    if isinstance(definition, Mapping):
        return definition.get("source") or {}
    return getattr(definition, "source", None)


def _value(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, Mapping):
        return container.get(key, default)
    return getattr(container, key, default)


def _infer_authority_class(
    key: str,
    source_type: str,
    trigger_types: set[str],
    routing_keys: frozenset[str],
    task_batch_context_keys: set[str],
) -> ContextAuthorityClass:
    if _is_immutable_key(key):
        return ContextAuthorityClass.IMMUTABLE_RUNTIME_AUTHORITY
    if _is_quality_key(key):
        return ContextAuthorityClass.CLOSED_WRITER_QUALITY_STATE
    if key in routing_keys or key in _ROUTING_EXACT or key.endswith(_ROUTING_SUFFIXES):
        return ContextAuthorityClass.CLOSED_WRITER_ROUTING_STATE
    if key in task_batch_context_keys:
        return ContextAuthorityClass.CLOSED_WRITER_ROUTING_STATE
    if "agent_text" in trigger_types:
        return ContextAuthorityClass.DERIVED_INFORMATION
    if source_type in {"config", "data_reference", "data_entity", "file", "build_context", "external"}:
        return ContextAuthorityClass.TOOL_ONLY_INFORMATION
    return ContextAuthorityClass.MUTABLE_WORKFLOW_STATE


def _infer_writer_ids(
    authority_class: ContextAuthorityClass,
    source_type: str,
    trigger_types: set[str],
    key: str,
    task_batch_context_keys: set[str],
) -> set[ContextWriterId]:
    if authority_class is ContextAuthorityClass.DERIVED_INFORMATION and "agent_text" in trigger_types:
        return {AGENT_TEXT_WRITER}
    writers: set[ContextWriterId] = set()
    if "ui_response" in trigger_types:
        writers.add(UI_RESPONSE_TRIGGER_WRITER)
    if "user_text" in trigger_types:
        writers.add(USER_TEXT_TRIGGER_WRITER)
    if key in task_batch_context_keys:
        writers.add(TASK_BATCH_WRITER)
    if key in _TRANSITION_ROUTER_SEEDED_KEYS:
        writers.add(TRANSITION_ROUTER_WRITER)
    if source_type == "state" and authority_class not in {
        ContextAuthorityClass.CLOSED_WRITER_ROUTING_STATE,
        ContextAuthorityClass.CLOSED_WRITER_QUALITY_STATE,
    }:
        writers.update({
            CALLER_INPUT_WRITER,
            TRANSITION_ROUTER_WRITER,
            CONTEXT_BRIDGE_WRITER,
            TOOL_WRITEBACK_WRITER,
            PERSISTED_REPLAY_WRITER,
            LIVE_USER_CONTEXT_WRITER,
        })
    return writers


def _transition_context_keys(transition_rules: Any) -> frozenset[str]:
    keys: set[str] = set()
    if not isinstance(transition_rules, list):
        return frozenset()
    for rule in transition_rules:
        if not isinstance(rule, Mapping):
            continue
        condition_type = str(rule.get("condition_type") or "").strip()
        if condition_type == "context_equals":
            key = _clean_key(rule.get("condition_key"))
            if key:
                keys.add(key)
        elif condition_type == "context_expression":
            expression = str(rule.get("context_expression") or "")
            keys.update(_clean_key(match.group(1)) for match in _CONTEXT_REF_RE.finditer(expression))
    return frozenset(key for key in keys if key)


def _is_immutable_key(key: str) -> bool:
    lower = key.lower()
    return (
        lower in _IMMUTABLE_EXACT
        or lower.endswith(_IMMUTABLE_SUFFIXES)
        or any(part in lower for part in _IMMUTABLE_PARTS)
    )


def _is_quality_key(key: str) -> bool:
    lower = key.lower()
    return any(part in lower for part in _QUALITY_PARTS)


def _clean_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key or not _IDENTIFIER_RE.match(key):
        return ""
    return key


__all__ = [
    "AGENT_TEXT_WRITER",
    "CALLER_INPUT_WRITER",
    "CONTEXT_BRIDGE_WRITER",
    "DETERMINISTIC_TOOL_WRITER",
    "LIFECYCLE_TOOL_WRITER",
    "LIVE_USER_CONTEXT_WRITER",
    "PERSISTED_REPLAY_WRITER",
    "RUNTIME_SYSTEM_WRITER",
    "STRUCTURED_OUTPUT_WRITER",
    "TASK_BATCH_WRITER",
    "TOOL_WRITEBACK_WRITER",
    "TRANSITION_ROUTER_WRITER",
    "UI_RESPONSE_TRIGGER_WRITER",
    "USER_TEXT_TRIGGER_WRITER",
    "ContextAuthorityClass",
    "ContextAuthorityError",
    "ContextAuthorityMetadata",
    "ContextAuthorityPolicy",
    "ContextVariableAuthority",
    "ContextWriterId",
    "ScopedContextWriter",
    "build_context_authority_policy",
    "validate_transition_context_authority",
]
