"""Typed schema for strict context variable contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..declarative import parse_context_variables_config
from .authority import ContextAuthorityClass, ContextWriterId


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


class ContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ContextTriggerMatch(ContextModel):
    """Declarative trigger match conditions."""

    equals: str | None = None
    contains: str | None = None
    regex: str | None = None

    @field_validator("equals", "contains", "regex", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _require_at_least_one_match(self) -> ContextTriggerMatch:
        if not (self.equals or self.contains or self.regex):
            raise ValueError("trigger.match must include at least one of: equals, contains, regex")
        return self


class ContextTriggerSpec(ContextModel):
    """Declarative trigger definition for state variables."""

    type: Literal["agent_text", "ui_response", "user_text"]
    agent: str | None = None
    match: ContextTriggerMatch | None = None
    tool: str | None = None
    response_key: str | None = None
    ui_hidden: bool | None = None

    @field_validator("agent", "tool", "response_key", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_trigger(self) -> ContextTriggerSpec:
        if self.type == "agent_text":
            if not self.agent:
                raise ValueError("agent_text trigger requires 'agent'")
            if not self.match:
                raise ValueError("agent_text trigger requires 'match'")
        if self.type == "user_text":
            if not self.match:
                raise ValueError("user_text trigger requires 'match'")
        if self.type == "ui_response":
            if not self.tool:
                raise ValueError("ui_response trigger requires 'tool'")
        return self


class ContextVariableSource(ContextModel):
    """Source metadata for resolving a context variable."""

    type: Literal["config", "data_reference", "data_entity", "computed", "state", "external", "file", "build_context"]

    env_var: str | None = None
    default: Any | None = None
    required: bool | None = None

    database_name: str | None = None
    collection: str | None = None
    query_template: dict[str, Any] | None = None
    fields: list[str] | None = None
    refresh_strategy: Literal["once", "per_phase", "on_demand"] | None = None

    entity_schema: dict[str, Any] | None = Field(default=None, alias="schema", serialization_alias="schema")
    indexes: list[dict[str, Any]] | None = None
    write_strategy: Literal["immediate", "on_phase_transition", "on_workflow_end"] | None = None
    search_by: str | None = None

    computation: str | None = None
    inputs: list[str] | None = None
    output_type: str | None = None
    persist_to: dict[str, Any] | None = None

    triggers: list[ContextTriggerSpec] = Field(default_factory=list)
    persist: bool | None = None

    service: str | None = None
    operation: str | None = None
    params: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
    cache: dict[str, Any] | None = None
    retry: dict[str, Any] | None = None

    path: str | None = None
    format: Literal["json", "yaml", "text"] | None = None
    encoding: str | None = None

    @field_validator(
        "env_var",
        "database_name",
        "collection",
        "search_by",
        "computation",
        "output_type",
        "service",
        "operation",
        "path",
        "encoding",
        mode="before",
    )
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("fields", "inputs")
    @classmethod
    def _normalize_string_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = _normalize_string_list(value)
        return normalized or None


class ContextVariableDefinition(ContextModel):
    """Full definition for a context variable."""

    type: str | None = None
    description: str | None = None
    authority_class: ContextAuthorityClass | None = None
    model_visible: bool | None = None
    tool_visible: bool | None = None
    persisted: bool | None = None
    routing: bool | None = None
    authorization: bool | None = None
    writer_ids: list[ContextWriterId] = Field(default_factory=list)
    source: ContextVariableSource

    @field_validator("type", "description", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("writer_ids")
    @classmethod
    def _normalize_writer_ids(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class ContextAgentView(ContextModel):
    """Per-agent context requirements."""

    variables: list[str] = Field(default_factory=list)

    @field_validator("variables")
    @classmethod
    def _normalize_variables(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class ContextVariablesPlan(ContextModel):
    """Canonical context plan consumed by the runtime."""

    definitions: dict[str, ContextVariableDefinition] = Field(default_factory=dict)
    agents: dict[str, ContextAgentView] = Field(default_factory=dict)

    @field_validator("definitions")
    @classmethod
    def _validate_definition_keys(
        cls, value: dict[str, ContextVariableDefinition]
    ) -> dict[str, ContextVariableDefinition]:
        for key in value.keys():
            _required_text(key, field_name="context variable name")
        return value

    @model_validator(mode="after")
    def _validate_agent_variable_refs(self) -> ContextVariablesPlan:
        declared = set(self.definitions)
        for agent_name, view in self.agents.items():
            _required_text(agent_name, field_name="context agent name")
            missing = sorted(set(view.variables).difference(declared))
            if missing:
                raise ValueError(
                    f"agents.{agent_name}.variables references undeclared context variables: {missing}"
                )
        return self


def load_context_variables_config(raw: dict[str, Any]) -> ContextVariablesPlan:
    """Parse strict context variable configuration."""

    if not isinstance(raw, dict):
        raise ValueError("Invalid context variables configuration: root must be an object")
    try:
        parsed = parse_context_variables_config(raw)
        return ContextVariablesPlan.model_validate(parsed)
    except (ValidationError, ValueError) as err:
        raise ValueError(f"Invalid context variables configuration: {err}") from err


__all__ = [
    "ContextVariablesPlan",
    "ContextVariableDefinition",
    "ContextVariableSource",
    "ContextAgentView",
    "ContextTriggerSpec",
    "ContextTriggerMatch",
    "load_context_variables_config",
]
