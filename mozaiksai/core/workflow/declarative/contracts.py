"""Strict declarative contracts for workflow authoring files.

All parser helpers in this module are intended for runtime ingestion of
workflow YAML files. They enforce deterministic, typed authoring contracts and
fail fast when invalid declarations are encountered.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mozaiksai.core.workflow.workflow_ui_catalog import validate_workflow_renderable_primitive_ids


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


class DeclarativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OrchestratorTriggerSpec(DeclarativeModel):
    type: Optional[str] = None
    event: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    description: Optional[str] = None
    capability_id: Optional[str] = None

    @field_validator("type", "event", "endpoint", "method", "description", "capability_id", mode="before")
    @classmethod
    def _normalize_optional_fields(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_trigger(self) -> "OrchestratorTriggerSpec":
        if not (self.type or self.event or self.endpoint):
            raise ValueError("trigger entry must declare at least one of: type, event, endpoint")
        return self


class OrchestratorConfig(DeclarativeModel):
    workflow_name: str
    max_turns: int = 50
    human_in_the_loop: bool = False
    workflow_startup_mode: Literal["AgentDriven", "UserDriven", "BackendOnly"]
    orchestration_pattern: str = "AutoPattern"
    initial_message_to_user: Optional[str] = None
    initial_message: Optional[str] = None
    initial_agent: Optional[str] = None
    triggers: List[OrchestratorTriggerSpec] = Field(default_factory=list)
    runtime_extensions: Optional[List[Dict[str, Any]]] = Field(default=None)

    @field_validator("workflow_name", "orchestration_pattern")
    @classmethod
    def _required_text_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("initial_message_to_user", "initial_message", "initial_agent", mode="before")
    @classmethod
    def _optional_text_fields(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("max_turns")
    @classmethod
    def _validate_max_turns(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_turns must be >= 1")
        if value > 500:
            raise ValueError("max_turns must be <= 500")
        return value


class PromptSectionSpec(DeclarativeModel):
    id: Optional[str] = None
    heading: str
    content: str

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("heading", "content")
    @classmethod
    def _validate_content(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)


class AgentSpec(DeclarativeModel):
    name: str
    prompt_sections: List[PromptSectionSpec] = Field(default_factory=list)
    prompt_sections_custom: List[PromptSectionSpec] = Field(default_factory=list)
    system_message: Optional[str] = None
    description: Optional[str] = None
    human_input_mode: Optional[str] = None
    max_consecutive_auto_reply: int = 2
    structured_outputs_required: bool = False
    image_generation_enabled: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        return _required_text(value, field_name="name")

    @field_validator("system_message", "description", "human_input_mode", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("max_consecutive_auto_reply")
    @classmethod
    def _validate_max_auto_reply(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_consecutive_auto_reply must be >= 0")
        return value

    @model_validator(mode="after")
    def _validate_prompt_shape(self) -> "AgentSpec":
        has_prompt_sections = bool(self.prompt_sections or self.prompt_sections_custom)
        has_system_message = bool(self.system_message)
        if not has_prompt_sections and not has_system_message:
            raise ValueError(
                f"agent '{self.name}' must provide prompt_sections/prompt_sections_custom or system_message"
            )
        return self


class AgentsConfig(DeclarativeModel):
    agents: List[AgentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_names(self) -> "AgentsConfig":
        names = [agent.name for agent in self.agents]
        if len(names) != len(set(names)):
            raise ValueError("agents contains duplicate agent names")
        return self


class HandoffRuleSpec(DeclarativeModel):
    source_agent: str
    target_agent: str
    handoff_type: Literal["after_work", "condition"]
    condition: Optional[str] = None
    condition_type: Optional[Literal["expression", "context_expression", "context", "llm", "string_llm"]] = None
    condition_scope: Optional[str] = None
    transition_target: Optional[str] = None

    @field_validator("source_agent", "target_agent")
    @classmethod
    def _validate_agent_refs(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("condition", "condition_scope", "transition_target", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_condition_shape(self) -> "HandoffRuleSpec":
        if self.handoff_type == "condition" and not self.condition:
            raise ValueError(
                f"handoff rule '{self.source_agent} -> {self.target_agent}' with handoff_type='condition' "
                "requires a non-empty condition"
            )
        return self


class HandoffsConfig(DeclarativeModel):
    handoff_rules: List[HandoffRuleSpec] = Field(default_factory=list)


class ContextTriggerMatchSpec(DeclarativeModel):
    equals: Optional[str] = None
    contains: Optional[str] = None
    regex: Optional[str] = None

    @field_validator("equals", "contains", "regex", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)


class ContextTriggerSpec(DeclarativeModel):
    type: Literal["agent_text", "ui_response"]
    agent: Optional[str] = None
    match: Optional[ContextTriggerMatchSpec] = None
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
        if self.type == "ui_response":
            if not self.tool:
                raise ValueError("ui_response trigger requires 'tool'")
        return self


class ContextVariableSourceSpec(DeclarativeModel):
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


class ContextVariableDefinitionSpec(DeclarativeModel):
    type: Optional[str] = None
    description: Optional[str] = None
    source: ContextVariableSourceSpec

    @field_validator("type", "description", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)


class ContextAgentViewSpec(DeclarativeModel):
    variables: List[str] = Field(default_factory=list)

    @field_validator("variables")
    @classmethod
    def _normalize_variables(cls, value: List[str]) -> List[str]:
        return _normalize_string_list(value)


class ContextVariablesConfig(DeclarativeModel):
    definitions: Dict[str, ContextVariableDefinitionSpec] = Field(default_factory=dict)
    agents: Dict[str, ContextAgentViewSpec] = Field(default_factory=dict)

    @field_validator("definitions")
    @classmethod
    def _validate_definition_keys(
        cls, value: Dict[str, ContextVariableDefinitionSpec]
    ) -> Dict[str, ContextVariableDefinitionSpec]:
        for key in value.keys():
            _required_text(key, field_name="context variable name")
        return value


class ToolUIConfig(DeclarativeModel):
    component: Optional[str] = None
    mode: Optional[str] = None
    workflow_primitive: Optional[str] = None

    @field_validator("component", "mode", "workflow_primitive", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("workflow_primitive")
    @classmethod
    def _validate_workflow_primitive(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_workflow_renderable_primitive_ids(
            [value],
            context="ToolUIConfig.workflow_primitive",
        )[0]


def _default_ui_payload_schema() -> Dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


class UIToolActionSpec(DeclarativeModel):
    id: str
    description: Optional[str] = None
    payload_schema: Dict[str, Any] = Field(default_factory=_default_ui_payload_schema)

    @field_validator("id")
    @classmethod
    def _validate_action_id(cls, value: Any) -> str:
        return _required_text(value, field_name="id")

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("payload_schema", mode="before")
    @classmethod
    def _normalize_payload_schema(cls, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict) or not value:
            return _default_ui_payload_schema()
        return value


class UIToolContractSpec(DeclarativeModel):
    surface_kind: Literal["agent_tool"] = "agent_tool"
    payload_schema: Dict[str, Any] = Field(default_factory=_default_ui_payload_schema)
    actions_schema: List[UIToolActionSpec] = Field(default_factory=list)

    @field_validator("payload_schema", mode="before")
    @classmethod
    def _normalize_payload_schema(cls, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict) or not value:
            return _default_ui_payload_schema()
        return value


class ToolSpec(DeclarativeModel):
    agent: str | List[str]
    file: str
    function: str
    description: Optional[str] = None
    tool_type: Literal["Agent_Tool", "UI_Tool", "UI_Surface"]
    auto_tool_call: bool = False
    ui: Optional[ToolUIConfig] = None
    ui_contract: Optional[UIToolContractSpec] = None

    @field_validator("agent", mode="before")
    @classmethod
    def _normalize_agent(cls, value: Any) -> str | List[str]:
        if isinstance(value, str):
            return _required_text(value, field_name="agent")
        if isinstance(value, list):
            normalized = _normalize_string_list([str(v) for v in value if isinstance(v, str)])
            if not normalized:
                raise ValueError("agent list must include at least one non-empty string")
            return normalized
        raise ValueError("agent must be a string or list of strings")

    @field_validator("file", "function")
    @classmethod
    def _required_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("tool_type", mode="before")
    @classmethod
    def _normalize_tool_type(cls, value: Any) -> str:
        text = _required_text(value, field_name="tool_type")
        normalized = text.lower().replace("-", "_")
        if normalized == "agent_tool":
            return "Agent_Tool"
        if normalized == "ui_tool":
            return "UI_Tool"
        if normalized == "ui_surface":
            return "UI_Surface"
        raise ValueError("tool_type must be 'Agent_Tool', 'UI_Tool', or 'UI_Surface'")

    @model_validator(mode="after")
    def _validate_ui_requirements(self) -> "ToolSpec":
        if self.tool_type in {"UI_Tool", "UI_Surface"}:
            if not self.ui:
                raise ValueError(
                    f"{self.tool_type} '{self.function}' must declare a non-empty ui block with "
                    "component, mode, and workflow_primitive"
                )
            if not self.ui.component or not self.ui.mode or not self.ui.workflow_primitive:
                raise ValueError(
                    f"{self.tool_type} '{self.function}' must declare ui.component, ui.mode, "
                    "and ui.workflow_primitive"
                )
            if self.tool_type == "UI_Tool" and self.ui_contract is None:
                self.ui_contract = UIToolContractSpec()
            if self.tool_type == "UI_Surface" and self.ui_contract is not None:
                raise ValueError(
                    f"UI_Surface '{self.function}' must not declare ui_contract"
                )
        else:
            if self.ui is not None:
                raise ValueError(
                    f"Agent_Tool '{self.function}' must not declare ui"
                )
            if self.ui_contract is not None:
                raise ValueError(
                    f"Agent_Tool '{self.function}' must not declare ui_contract"
                )
        return self


class LifecycleToolSpec(DeclarativeModel):
    trigger: Literal["before_chat", "after_chat", "before_agent", "after_agent"]
    agent: Optional[str] = None
    file: str
    function: str
    description: Optional[str] = None
    tool_type: Literal["Agent_Tool", "UI_Tool", "UI_Surface"] = "Agent_Tool"
    ui: Optional[ToolUIConfig] = None

    @field_validator("agent", "description", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("file", "function")
    @classmethod
    def _required_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("tool_type", mode="before")
    @classmethod
    def _normalize_lifecycle_tool_type(cls, value: Any) -> str:
        if value is None:
            return "Agent_Tool"
        text = _required_text(value, field_name="tool_type")
        normalized = text.lower().replace("-", "_")
        if normalized == "agent_tool":
            return "Agent_Tool"
        if normalized == "ui_tool":
            return "UI_Tool"
        if normalized == "ui_surface":
            return "UI_Surface"
        raise ValueError("tool_type must be 'Agent_Tool', 'UI_Tool', or 'UI_Surface'")

    @model_validator(mode="after")
    def _validate_lifecycle_ui_requirements(self) -> "LifecycleToolSpec":
        if self.tool_type in {"UI_Tool", "UI_Surface"}:
            if not self.ui:
                raise ValueError(
                    f"{self.tool_type} lifecycle tool '{self.function}' must declare a non-empty ui "
                    "block with component, mode, and workflow_primitive"
                )
            if not self.ui.component or not self.ui.mode or not self.ui.workflow_primitive:
                raise ValueError(
                    f"{self.tool_type} lifecycle tool '{self.function}' must declare ui.component, "
                    "ui.mode, and ui.workflow_primitive"
                )
        elif self.ui is not None:
            raise ValueError(
                f"Agent_Tool lifecycle tool '{self.function}' must not declare ui"
            )
        return self


class ToolsConfig(DeclarativeModel):
    tools: List[ToolSpec] = Field(default_factory=list)
    lifecycle_tools: List[LifecycleToolSpec] = Field(default_factory=list)


class HookSpec(DeclarativeModel):
    hook_type: Literal[
        "process_message_before_send",
        "update_agent_state",
        "process_last_received_message",
        "process_all_messages_before_reply",
    ]
    hook_agent: str
    filename: str
    function: str

    @field_validator("hook_agent", "filename", "function")
    @classmethod
    def _required_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)


class HooksConfig(DeclarativeModel):
    hooks: List[HookSpec] = Field(default_factory=list)


class UIConfig(DeclarativeModel):
    # null means "hide all agents"; [] keeps legacy "no filtering" behavior.
    visual_agents: Optional[List[str]] = None

    @field_validator("visual_agents")
    @classmethod
    def _normalize_lists(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        return _normalize_string_list(value)


class A2AClientConfig(DeclarativeModel):
    streaming: bool = True
    polling: bool = False
    use_client_preference: bool = False
    accepted_output_modes: List[str] = Field(default_factory=list)
    extensions: List[str] = Field(default_factory=list)
    supported_transports: List[str] = Field(default_factory=list)
    push_notification_configs: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("accepted_output_modes", "extensions", "supported_transports")
    @classmethod
    def _normalize_string_lists(cls, value: List[str]) -> List[str]:
        return _normalize_string_list(value)


class A2AAgentSpec(DeclarativeModel):
    name: str
    url: str
    enabled: bool = True
    max_reconnects: int = 3
    polling_interval: float = 0.5
    silent: Optional[bool] = None
    client: A2AClientConfig = Field(default_factory=A2AClientConfig)

    @field_validator("name", "url")
    @classmethod
    def _validate_name_url(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("max_reconnects")
    @classmethod
    def _validate_reconnects(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_reconnects must be >= 0")
        return value

    @field_validator("polling_interval")
    @classmethod
    def _validate_polling_interval(cls, value: float) -> float:
        if value < 0:
            raise ValueError("polling_interval must be >= 0")
        return value


class A2AConfig(DeclarativeModel):
    agents: List[A2AAgentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_names(self) -> "A2AConfig":
        names = [agent.name for agent in self.agents]
        if len(names) != len(set(names)):
            raise ValueError("a2a.agents contains duplicate agent names")
        return self


class StructuredOutputFieldSpec(DeclarativeModel):
    type: str
    description: Optional[str] = None
    default: Optional[Any] = None
    items: Optional[str] = None
    values: Optional[List[Any]] = None
    variants: Optional[List[str]] = None

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: Any) -> str:
        return _required_text(value, field_name="type")

    @field_validator("description", "items", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("variants")
    @classmethod
    def _normalize_variants(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return None
        normalized = _normalize_string_list(value)
        return normalized or None

    @model_validator(mode="after")
    def _validate_shape(self) -> "StructuredOutputFieldSpec":
        field_type = self.type
        if field_type in {"list", "optional_list"} and not self.items:
            raise ValueError(f"field type '{field_type}' requires 'items'")
        if field_type == "literal" and not self.values:
            raise ValueError("field type 'literal' requires non-empty 'values'")
        if field_type == "union" and not self.variants:
            raise ValueError("field type 'union' requires non-empty 'variants'")
        return self


class StructuredOutputModelSpec(DeclarativeModel):
    type: Literal["model"]
    description: Optional[str] = None
    fields: Dict[str, StructuredOutputFieldSpec] = Field(default_factory=dict)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, value: Dict[str, StructuredOutputFieldSpec]) -> Dict[str, StructuredOutputFieldSpec]:
        if not value:
            raise ValueError("model.fields must not be empty")
        for field_name in value.keys():
            _required_text(field_name, field_name="field_name")
        return value


class StructuredOutputLiteralSpec(DeclarativeModel):
    type: Literal["literal"]
    description: Optional[str] = None
    values: List[Any] = Field(default_factory=list)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("values")
    @classmethod
    def _validate_values(cls, value: List[Any]) -> List[Any]:
        if not value:
            raise ValueError("literal.values must not be empty")
        return value


class StructuredOutputUnionSpec(DeclarativeModel):
    type: Literal["union"]
    description: Optional[str] = None
    variants: List[str] = Field(default_factory=list)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> Optional[str]:
        return _optional_text(value)

    @field_validator("variants")
    @classmethod
    def _validate_variants(cls, value: List[str]) -> List[str]:
        normalized = _normalize_string_list(value)
        if not normalized:
            raise ValueError("union.variants must not be empty")
        return normalized


class StructuredOutputsConfig(DeclarativeModel):
    registry: Dict[str, Optional[str]] = Field(default_factory=dict)
    models: Dict[str, StructuredOutputModelSpec | StructuredOutputLiteralSpec | StructuredOutputUnionSpec] = (
        Field(default_factory=dict)
    )

    @field_validator("models")
    @classmethod
    def _validate_model_keys(
        cls,
        value: Dict[str, StructuredOutputModelSpec | StructuredOutputLiteralSpec | StructuredOutputUnionSpec],
    ) -> Dict[str, StructuredOutputModelSpec | StructuredOutputLiteralSpec | StructuredOutputUnionSpec]:
        for key in value.keys():
            _required_text(key, field_name="structured output definition name")
        return value

    @model_validator(mode="after")
    def _validate_registry_refs(self) -> "StructuredOutputsConfig":
        for agent_name, model_name in self.registry.items():
            _required_text(agent_name, field_name="registry agent name")
            if model_name is None:
                continue
            resolved_model = _required_text(model_name, field_name=f"registry[{agent_name}]")
            if resolved_model not in self.models:
                raise ValueError(
                    f"registry entry '{agent_name}: {resolved_model}' references unknown model"
                )
            model_def = self.models[resolved_model]
            if getattr(model_def, "type", None) != "model":
                raise ValueError(
                    f"registry entry '{agent_name}: {resolved_model}' must reference a model definition"
                )
        return self


def _validate_config(model_cls: type[BaseModel], raw: Dict[str, Any], file_name: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid {file_name} configuration: root must be an object")
    try:
        validated = model_cls.model_validate(raw)
    except ValidationError as err:
        raise ValueError(f"Invalid {file_name} configuration: {err}") from err
    return validated.model_dump(by_alias=True)


def parse_orchestrator_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(OrchestratorConfig, raw, "orchestrator.yaml")


def parse_agents_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    parsed = _validate_config(AgentsConfig, raw, "agents.yaml")
    agents_list = parsed.get("agents", [])
    agents_map: Dict[str, Any] = {}
    for entry in agents_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            agents_map[name] = entry
    return {"agents": agents_map}


def parse_handoffs_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(HandoffsConfig, raw, "handoffs.yaml")


def parse_tools_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(ToolsConfig, raw, "tools.yaml")


def parse_hooks_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(HooksConfig, raw, "hooks.yaml")


def parse_ui_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(UIConfig, raw, "ui_config.yaml")


def parse_a2a_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(A2AConfig, raw, "a2a.yaml")


def parse_structured_outputs_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(StructuredOutputsConfig, raw, "structured_outputs.yaml")


def parse_context_variables_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    return _validate_config(ContextVariablesConfig, raw, "context_variables.yaml")


__all__ = [
    "parse_orchestrator_config",
    "parse_agents_config",
    "parse_handoffs_config",
    "parse_context_variables_config",
    "parse_structured_outputs_config",
    "parse_tools_config",
    "parse_ui_config",
    "parse_hooks_config",
    "parse_a2a_config",
]
