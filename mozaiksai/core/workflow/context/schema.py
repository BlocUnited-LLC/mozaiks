"""Typed schema for agent-centric context variable specifications."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator


class ContextTriggerMatch(BaseModel):
    """Declarative trigger match conditions."""

    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None


class ContextTriggerSpec(BaseModel):
    """Declarative trigger definition for state variables.
    
    Trigger Types:
    - agent_text: Passive agent text detection (state transitions via messages)
    - ui_response: Active UI interaction (tool code updates value)
    """

    type: Literal["agent_text", "ui_response"]
    agent: Optional[str] = None  # Required for agent_text, optional for ui_response
    match: Optional[ContextTriggerMatch] = None  # Required for agent_text, N/A for ui_response
    
    # UI response trigger fields (type="ui_response")
    tool: Optional[str] = None  # Tool name that handles UI interaction
    response_key: Optional[str] = None  # Key in response dict to extract

    # Optional display control (primarily for agent_text triggers)
    ui_hidden: Optional[bool] = None

    @model_validator(mode="before")
    def _normalize_match(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        match_cfg = data.get("match")
        if not isinstance(match_cfg, dict):
            data = data.copy()
            data["match"] = {
                "equals": data.get("equals"),
                "contains": data.get("contains"),
                "regex": data.get("regex"),
            }
        return data


class ContextVariableSource(BaseModel):
    """Source metadata for resolving a context variable.
    
    Source Types (Production-Ready):
    - config: Deployment configuration from environment variables
    - data_reference: Read from existing MongoDB collections
    - data_entity: Create and persist new MongoDB data
    - computed: Business logic outputs (optionally persisted)
    - state: Workflow orchestration state with transitions
    - external: Third-party API data with caching/retry
    """

    type: Literal["config", "data_reference", "data_entity", "computed", "state", "external", "file"]
    
    # Config source fields (type="config")
    env_var: Optional[str] = None
    default: Optional[Any] = None
    required: Optional[bool] = None
    
    # Data reference source fields (type="data_reference")
    database_name: Optional[str] = None
    collection: Optional[str] = None
    query_template: Optional[Dict[str, Any]] = None
    fields: Optional[List[str]] = None
    refresh_strategy: Optional[Literal["once", "per_phase", "on_demand"]] = None
    
    # Data entity source fields (type="data_entity")
    # Reuses database_name
    entity_schema: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    indexes: Optional[List[Dict[str, Any]]] = None
    write_strategy: Optional[Literal["immediate", "on_phase_transition", "on_workflow_end"]] = None
    search_by: Optional[str] = None
    
    # Computed source fields (type="computed")
    computation: Optional[str] = None
    inputs: Optional[List[str]] = None
    output_type: Optional[str] = None
    persist_to: Optional[Dict[str, Any]] = None
    
    # State source fields (type="state")
    triggers: List[ContextTriggerSpec] = Field(default_factory=list)
    persist: Optional[bool] = None
    
    # External source fields (type="external")
    service: Optional[str] = None
    operation: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    auth: Optional[Dict[str, Any]] = None
    cache: Optional[Dict[str, Any]] = None
    retry: Optional[Dict[str, Any]] = None

    # File source fields (type="file")
    path: Optional[str] = None
    format: Optional[Literal["json", "yaml", "text"]] = None
    encoding: Optional[str] = None


class ContextVariableDefinition(BaseModel):
    """Full definition for a context variable."""

    type: Optional[str] = None
    description: Optional[str] = None
    source: ContextVariableSource


class ContextAgentView(BaseModel):
    """Per-agent context requirements (variables list only - exposures handled by AG2's UpdateSystemMessage)."""

    variables: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    def _default_lists(cls, data: Any) -> Any:
        if isinstance(data, dict):
            updated = data.copy()
            if updated.get("variables") is None:
                updated["variables"] = []
            return updated
        return data


class ContextVariablesPlan(BaseModel):
    """Canonical context plan consumed by the runtime."""

    definitions: Dict[str, ContextVariableDefinition] = Field(default_factory=dict)
    agents: Dict[str, ContextAgentView] = Field(default_factory=dict)

    @model_validator(mode="before")
    def _coerce_collections(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        updated = dict(data)

        raw_definitions = updated.get("definitions")
        if isinstance(raw_definitions, list):
            remapped: Dict[str, Any] = {}
            for entry in raw_definitions:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name") or entry.get("key")
                if not isinstance(name, str) or not name.strip():
                    continue
                payload = dict(entry)
                payload.pop("name", None)
                payload.pop("key", None)
                remapped[name] = payload
            updated["definitions"] = remapped

        raw_agents = updated.get("agents")
        if isinstance(raw_agents, list):
            remapped_agents: Dict[str, Any] = {}
            for entry in raw_agents:
                if not isinstance(entry, dict):
                    continue
                agent_name = (
                    entry.get("agent")
                    or entry.get("agent_name")
                    or entry.get("name")
                )
                if not isinstance(agent_name, str) or not agent_name.strip():
                    continue
                variables = entry.get("variables")
                if isinstance(variables, list):
                    filtered = [str(var) for var in variables if isinstance(var, str)]
                elif variables is None:
                    filtered = []
                else:
                    filtered = [str(variables)]
                remapped_agents[agent_name] = {"variables": filtered}
            updated["agents"] = remapped_agents

        return updated


def load_context_variables_config(raw: Dict[str, Any]) -> ContextVariablesPlan:
    """Parse raw workflow JSON into a ContextVariablesPlan model."""

    candidate: Dict[str, Any] = {}
    if isinstance(raw, dict):
        candidate = raw.get("context_variables") if "context_variables" in raw else raw
        if not isinstance(candidate, dict):
            candidate = {}
    try:
        return ContextVariablesPlan.model_validate(candidate)
    except ValidationError as err:
        raise ValueError(f"Invalid context variables configuration: {err}") from err


__all__ = [
    "ContextVariablesPlan",
    "ContextVariableDefinition",
    "ContextVariableSource",
    "ContextAgentView",
    "ContextTriggerSpec",
    "StateTransitionSpec",
    "ContextTriggerMatch",
    "load_context_variables_config",
]

