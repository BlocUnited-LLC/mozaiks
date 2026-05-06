"""Typed schema for strict context variable contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..declarative import parse_context_variables_config


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_string_list(values: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
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

    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None

    @field_validator("equals", "contains", "regex", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @model_validator(mode="after")
    def _require_at_least_one_match(self) -> "ContextTriggerMatch":
        if not (self.equals or self.contains or self.regex):
            raise ValueError("trigger.match must include at least one of: equals, contains, regex")
        return self


class ContextTriggerSpec(ContextModel):
    """Declarative trigger definition for state variables."""

    type: Literal["agent_text", "ui_response", "user_text"]
    agent: Optional[str] = None
    match: Optional[ContextTriggerMatch] = None
    tool: Optional[str] = None
    response_key: Optional[str] = None
    ui_hidden: Optional[bool] = None

    @field_validator("agent", "tool", "response_key", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_trigger(self) -> "ContextTriggerSpec":
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

    type: Literal["config", "data_reference", "data_entity", "computed", "state", "external", "file"]

    env_var: Optional[str] = None
    default: Optional[Any] = None
    required: Optional[bool] = None

    database_name: Optional[str] = None
    collection: Optional[str] = None
    query_template: Optional[Dict[str, Any]] = None
    fields: Optional[List[str]] = None
    refresh_strategy: Optional[Literal["once", "per_phase", "on_demand"]] = None

    entity_schema: Optional[Dict[str, Any]] = Field(default=None, alias="schema", serialization_alias="schema")
    indexes: Optional[List[Dict[str, Any]]] = None
    write_strategy: Optional[Literal["immediate", "on_phase_transition", "on_workflow_end"]] = None
    search_by: Optional[str] = None

    computation: Optional[str] = None
    inputs: Optional[List[str]] = None
    output_type: Optional[str] = None
    persist_to: Optional[Dict[str, Any]] = None

    triggers: List[ContextTriggerSpec] = Field(default_factory=list)
    persist: Optional[bool] = None

    service: Optional[str] = None
    operation: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    auth: Optional[Dict[str, Any]] = None
    cache: Optional[Dict[str, Any]] = None
    retry: Optional[Dict[str, Any]] = None

    path: Optional[str] = None
    format: Optional[Literal["json", "yaml", "text"]] = None
    encoding: Optional[str] = None

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
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("fields", "inputs")
    @classmethod
    def _normalize_string_lists(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        normalized = _normalize_string_list(value)
        return normalized or None


class ContextVariableDefinition(ContextModel):
    """Full definition for a context variable."""

    type: Optional[str] = None
    description: Optional[str] = None
    source: ContextVariableSource

    @field_validator("type", "description", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)


class ContextAgentView(ContextModel):
    """Per-agent context requirements."""

    variables: List[str] = Field(default_factory=list)

    @field_validator("variables")
    @classmethod
    def _normalize_variables(cls, value: List[str]) -> List[str]:
        return _normalize_string_list(value)


class ContextVariablesPlan(ContextModel):
    """Canonical context plan consumed by the runtime."""

    definitions: Dict[str, ContextVariableDefinition] = Field(default_factory=dict)
    agents: Dict[str, ContextAgentView] = Field(default_factory=dict)

    @field_validator("definitions")
    @classmethod
    def _validate_definition_keys(
        cls, value: Dict[str, ContextVariableDefinition]
    ) -> Dict[str, ContextVariableDefinition]:
        for key in value.keys():
            _required_text(key, field_name="context variable name")
        return value


def load_context_variables_config(raw: Dict[str, Any]) -> ContextVariablesPlan:
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
