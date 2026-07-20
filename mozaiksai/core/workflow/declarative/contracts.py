"""Strict declarative contracts for workflow authoring files.

All parser helpers in this module are intended for runtime ingestion of
workflow YAML files. They enforce deterministic, typed authoring contracts and
fail fast when invalid declarations are encountered.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from mozaiksai.core.workflow.workflow_ui_catalog import (
    infer_workflow_ui_realization,
    validate_workflow_renderable_primitive_ids,
    validate_workflow_ui_realization_ids,
)


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


class DeclarativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OrchestratorTriggerSpec(DeclarativeModel):
    type: str | None = None
    event: str | None = None
    endpoint: str | None = None
    method: str | None = None
    description: str | None = None
    capability_id: str | None = None

    @field_validator("type", "event", "endpoint", "method", "description", "capability_id", mode="before")
    @classmethod
    def _normalize_optional_fields(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_trigger(self) -> OrchestratorTriggerSpec:
        if not (self.type or self.event or self.endpoint):
            raise ValueError("trigger entry must declare at least one of: type, event, endpoint")
        return self


class OrchestratorConfig(DeclarativeModel):
    workflow_name: str
    max_turns: int = 50
    human_in_the_loop: bool = False
    workflow_startup_mode: Literal["AgentDriven", "UserDriven", "BackendOnly"]
    orchestration_pattern: str = "ag2_network"
    initial_message: str | None = None
    initial_agent: str | None = None
    triggers: list[OrchestratorTriggerSpec] = Field(default_factory=list)
    @field_validator("workflow_name", "orchestration_pattern")
    @classmethod
    def _required_text_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("initial_message", "initial_agent", mode="before")
    @classmethod
    def _optional_text_fields(cls, value: Any) -> str | None:
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
    id: str | None = None
    heading: str
    content: str

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("heading", "content")
    @classmethod
    def _validate_content(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)


class AgentSpec(DeclarativeModel):
    name: str
    prompt_sections: list[PromptSectionSpec] = Field(default_factory=list)
    prompt_sections_custom: list[PromptSectionSpec] = Field(default_factory=list)
    system_message: str | None = None
    description: str | None = None
    human_input_mode: str | None = None
    max_consecutive_auto_reply: int = 2
    structured_outputs_required: bool = False
    image_generation_enabled: bool = False
    sandbox_shell: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        return _required_text(value, field_name="name")

    @field_validator("system_message", "description", "human_input_mode", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("max_consecutive_auto_reply")
    @classmethod
    def _validate_max_auto_reply(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_consecutive_auto_reply must be >= 0")
        return value

    @model_validator(mode="after")
    def _validate_prompt_shape(self) -> AgentSpec:
        has_prompt_sections = bool(self.prompt_sections or self.prompt_sections_custom)
        has_system_message = bool(self.system_message)
        if not has_prompt_sections and not has_system_message:
            raise ValueError(
                f"agent '{self.name}' must provide prompt_sections/prompt_sections_custom or system_message"
            )
        return self


class AgentsConfig(DeclarativeModel):
    agents: list[AgentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_names(self) -> AgentsConfig:
        names = [agent.name for agent in self.agents]
        if len(names) != len(set(names)):
            raise ValueError("agents contains duplicate agent names")
        return self


class TransitionRuleSpec(DeclarativeModel):
    source_agent: str
    target_agent: str
    transition_type: Literal["after_turn", "condition"]
    condition_type: Literal["context_equals", "context_expression", "tool_called"] | None = None
    condition_key: str | None = None
    condition_value: Any | None = None
    context_expression: str | None = None
    tool_name: str | None = None
    transition_target: str | None = None

    @field_validator("source_agent", "target_agent")
    @classmethod
    def _validate_agent_refs(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("condition_type", mode="before")
    @classmethod
    def _normalize_condition_type(cls, value: Any) -> str | None:
        text = _optional_text(value)
        return text.lower() if text else None

    @field_validator("condition_key", "context_expression", "tool_name", "transition_target", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def _validate_condition_shape(self) -> TransitionRuleSpec:
        if self.transition_type == "after_turn":
            if (
                self.condition_type
                or self.condition_key
                or self.condition_value is not None
                or self.context_expression
                or self.tool_name
            ):
                raise ValueError(
                    f"transition rule '{self.source_agent} -> {self.target_agent}' with "
                    "transition_type='after_turn' must not declare condition fields"
                )
            return self

        if not self.condition_type:
            raise ValueError(
                f"transition rule '{self.source_agent} -> {self.target_agent}' with transition_type='condition' "
                "requires condition_type"
            )
        if self.condition_type == "context_equals":
            if not self.condition_key:
                raise ValueError(
                    f"transition rule '{self.source_agent} -> {self.target_agent}' with "
                    "condition_type='context_equals' requires condition_key"
                )
            if self.tool_name:
                raise ValueError("condition_type='context_equals' must not declare tool_name")
            if self.context_expression:
                raise ValueError("condition_type='context_equals' must not declare context_expression")
        if self.condition_type == "context_expression":
            if not self.context_expression:
                raise ValueError(
                    f"transition rule '{self.source_agent} -> {self.target_agent}' with "
                    "condition_type='context_expression' requires context_expression"
                )
            if self.condition_key or self.condition_value is not None or self.tool_name:
                raise ValueError(
                    "condition_type='context_expression' must not declare condition_key, "
                    "condition_value, or tool_name"
                )
        if self.condition_type == "tool_called":
            if not self.tool_name:
                raise ValueError(
                    f"transition rule '{self.source_agent} -> {self.target_agent}' with "
                    "condition_type='tool_called' requires tool_name"
                )
            if self.condition_key or self.condition_value is not None or self.context_expression:
                raise ValueError("condition_type='tool_called' must not declare context condition fields")
        return self


class TransitionGraphConfig(DeclarativeModel):
    transition_rules: list[TransitionRuleSpec] = Field(default_factory=list)


class ContextTriggerMatchSpec(DeclarativeModel):
    equals: str | None = None
    contains: str | None = None
    regex: str | None = None

    @field_validator("equals", "contains", "regex", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)


class ContextTriggerSpec(DeclarativeModel):
    type: Literal["agent_text", "ui_response", "user_text"]
    agent: str | None = None
    match: ContextTriggerMatchSpec | None = None
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


class ContextVariableSourceSpec(DeclarativeModel):
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


class ContextVariableDefinitionSpec(DeclarativeModel):
    type: str | None = None
    description: str | None = None
    source: ContextVariableSourceSpec

    @field_validator("type", "description", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)


class ContextAgentViewSpec(DeclarativeModel):
    variables: list[str] = Field(default_factory=list)

    @field_validator("variables")
    @classmethod
    def _normalize_variables(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class ContextVariablesConfig(DeclarativeModel):
    definitions: dict[str, ContextVariableDefinitionSpec] = Field(default_factory=dict)
    agents: dict[str, ContextAgentViewSpec] = Field(default_factory=dict)

    @field_validator("definitions")
    @classmethod
    def _validate_definition_keys(
        cls, value: dict[str, ContextVariableDefinitionSpec]
    ) -> dict[str, ContextVariableDefinitionSpec]:
        for key in value.keys():
            _required_text(key, field_name="context variable name")
        return value


class ToolUIConfig(DeclarativeModel):
    component: str | None = None
    mode: str | None = None
    workflow_primitive: str | None = None
    realization: str | None = None

    @field_validator("component", "mode", "workflow_primitive", "realization", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("workflow_primitive")
    @classmethod
    def _validate_workflow_primitive(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_workflow_renderable_primitive_ids(
            [value],
            context="ToolUIConfig.workflow_primitive",
        )[0]

    @field_validator("realization")
    @classmethod
    def _validate_realization(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_workflow_ui_realization_ids(
            [value],
            context="ToolUIConfig.realization",
            include_shell_builtin=True,
        )[0]

    @model_validator(mode="after")
    def _normalize_realization(self) -> ToolUIConfig:
        if self.workflow_primitive is None:
            if self.realization is not None:
                raise ValueError("ui.realization requires ui.workflow_primitive")
            return self

        inferred_realization = infer_workflow_ui_realization(self.workflow_primitive, self.component)
        if inferred_realization is None:
            return self
        if inferred_realization == "shell_builtin":
            raise ValueError("ui.workflow_primitive for declarative UI tools must not resolve to shell_builtin")
        if self.realization is None:
            raise ValueError("ui.realization is required for declarative UI tools")
        if self.realization != inferred_realization:
            raise ValueError(
                "ui.realization does not match the declared workflow primitive/component relationship "
                f"(expected {inferred_realization}, got {self.realization})"
            )
        return self


def _default_ui_payload_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


class UIToolActionSpec(DeclarativeModel):
    id: str
    label: str | None = None
    description: str | None = None
    variant: str | None = None
    approved: bool | None = None
    payload_schema: dict[str, Any] = Field(default_factory=_default_ui_payload_schema)

    @field_validator("id")
    @classmethod
    def _validate_action_id(cls, value: Any) -> str:
        return _required_text(value, field_name="id")

    @field_validator("label", "description", "variant", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("payload_schema", mode="before")
    @classmethod
    def _normalize_payload_schema(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            return _default_ui_payload_schema()
        return value


class UIToolContractSpec(DeclarativeModel):
    surface_kind: Literal["agent_tool"] = "agent_tool"
    payload_schema: dict[str, Any] = Field(default_factory=_default_ui_payload_schema)
    actions_schema: list[UIToolActionSpec] = Field(default_factory=list)

    @field_validator("payload_schema", mode="before")
    @classmethod
    def _normalize_payload_schema(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            return _default_ui_payload_schema()
        return value


class ToolSpec(DeclarativeModel):
    agent: str | list[str]
    file: str
    function: str
    description: str | None = None
    tool_type: Literal["Agent_Tool", "UI_Tool", "UI_Surface"]
    auto_tool_call: bool = False
    bind_to_agent: bool = True
    ui: ToolUIConfig | None = None
    ui_contract: UIToolContractSpec | None = None

    @field_validator("agent", mode="before")
    @classmethod
    def _normalize_agent(cls, value: Any) -> str | list[str]:
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
    def _normalize_description(cls, value: Any) -> str | None:
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
    def _validate_ui_requirements(self) -> ToolSpec:
        if self.tool_type in {"UI_Tool", "UI_Surface"}:
            if not self.ui:
                raise ValueError(
                    f"{self.tool_type} '{self.function}' must declare a non-empty ui block with "
                    "component, mode, workflow_primitive, and realization"
                )
            if not self.ui.component or not self.ui.mode or not self.ui.workflow_primitive or not self.ui.realization:
                raise ValueError(
                    f"{self.tool_type} '{self.function}' must declare ui.component, ui.mode, "
                    "ui.workflow_primitive, and ui.realization"
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
    trigger: Literal[
        "before_chat", "after_chat", "before_agent", "after_agent",
        "on_start", "on_complete", "on_fail",
    ]
    agent: str | None = None
    file: str
    function: str
    description: str | None = None
    tool_type: Literal["Agent_Tool", "UI_Tool", "UI_Surface"] = "Agent_Tool"
    ui: ToolUIConfig | None = None
    ui_contract: UIToolContractSpec | None = None

    @field_validator("agent", "description", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
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
    def _validate_run_level_triggers(self) -> LifecycleToolSpec:
        if self.trigger in {"on_start", "on_complete", "on_fail"} and self.agent is not None:
            raise ValueError(
                f"Run-level lifecycle trigger '{self.trigger}' must have agent=null "
                "(it fires once per run, not per agent turn)"
            )
        return self

    @model_validator(mode="after")
    def _validate_lifecycle_ui_requirements(self) -> LifecycleToolSpec:
        if self.tool_type in {"UI_Tool", "UI_Surface"}:
            if not self.ui:
                raise ValueError(
                    f"{self.tool_type} lifecycle tool '{self.function}' must declare a non-empty ui "
                    "block with component, mode, workflow_primitive, and realization"
                )
            if not self.ui.component or not self.ui.mode or not self.ui.workflow_primitive or not self.ui.realization:
                raise ValueError(
                    f"{self.tool_type} lifecycle tool '{self.function}' must declare ui.component, "
                    "ui.mode, ui.workflow_primitive, and ui.realization"
                )
            if self.tool_type == "UI_Tool" and self.ui_contract is None:
                self.ui_contract = UIToolContractSpec()
            if self.tool_type == "UI_Surface" and self.ui_contract is not None:
                raise ValueError(
                    f"UI_Surface lifecycle tool '{self.function}' must not declare ui_contract"
                )
        elif self.ui is not None:
            raise ValueError(
                f"Agent_Tool lifecycle tool '{self.function}' must not declare ui"
            )
        elif self.ui_contract is not None:
            raise ValueError(
                f"Agent_Tool lifecycle tool '{self.function}' must not declare ui_contract"
            )
        return self


class ToolsConfig(DeclarativeModel):
    tools: list[ToolSpec] = Field(default_factory=list)
    lifecycle_tools: list[LifecycleToolSpec] = Field(default_factory=list)


class PromptMiddlewareSpec(DeclarativeModel):
    agent: str
    filename: str | None = None
    function: str

    @field_validator("agent", "function")
    @classmethod
    def _required_fields(cls, value: Any, info):  # type: ignore[no-untyped-def]
        return _required_text(value, field_name=info.field_name)

    @field_validator("filename")
    @classmethod
    def _optional_filename(cls, value: Any):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        return _required_text(value, field_name="filename")


class MiddlewareConfig(DeclarativeModel):
    prompt_middleware: list[PromptMiddlewareSpec] = Field(default_factory=list)


class UIConfig(DeclarativeModel):
    # null means "hide all agents"; [] means "no filtering".
    visual_agents: list[str] | None = None

    @field_validator("visual_agents")
    @classmethod
    def _normalize_lists(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_string_list(value)


class A2AClientConfig(DeclarativeModel):
    streaming: bool = True
    polling: bool = False
    use_client_preference: bool = False
    accepted_output_modes: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    supported_transports: list[str] = Field(default_factory=list)
    push_notification_configs: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("accepted_output_modes", "extensions", "supported_transports")
    @classmethod
    def _normalize_string_lists(cls, value: list[str]) -> list[str]:
        return _normalize_string_list(value)


class A2AAgentSpec(DeclarativeModel):
    name: str
    url: str
    enabled: bool = True
    max_reconnects: int = 3
    polling_interval: float = 0.5
    silent: bool | None = None
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
    agents: list[A2AAgentSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_names(self) -> A2AConfig:
        names = [agent.name for agent in self.agents]
        if len(names) != len(set(names)):
            raise ValueError("a2a.agents contains duplicate agent names")
        return self


class StructuredOutputFieldSpec(DeclarativeModel):
    type: str
    description: str | None = None
    default: Any | None = None
    items: str | None = None
    values: list[Any] | None = None
    variants: list[str] | None = None

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: Any) -> str:
        return _required_text(value, field_name="type")

    @field_validator("description", "items", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("variants")
    @classmethod
    def _normalize_variants(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = _normalize_string_list(value)
        return normalized or None

    @model_validator(mode="after")
    def _validate_shape(self) -> StructuredOutputFieldSpec:
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
    description: str | None = None
    fields: dict[str, StructuredOutputFieldSpec] = Field(default_factory=dict)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, value: dict[str, StructuredOutputFieldSpec]) -> dict[str, StructuredOutputFieldSpec]:
        if not value:
            raise ValueError("model.fields must not be empty")
        for field_name in value.keys():
            _required_text(field_name, field_name="field_name")
        return value


class StructuredOutputLiteralSpec(DeclarativeModel):
    type: Literal["literal"]
    description: str | None = None
    values: list[Any] = Field(default_factory=list)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("values")
    @classmethod
    def _validate_values(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("literal.values must not be empty")
        return value


class StructuredOutputUnionSpec(DeclarativeModel):
    type: Literal["union"]
    description: str | None = None
    variants: list[str] = Field(default_factory=list)

    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, value: Any) -> str | None:
        return _optional_text(value)

    @field_validator("variants")
    @classmethod
    def _validate_variants(cls, value: list[str]) -> list[str]:
        normalized = _normalize_string_list(value)
        if not normalized:
            raise ValueError("union.variants must not be empty")
        return normalized


class StructuredOutputsConfig(DeclarativeModel):
    registry: dict[str, str | None] = Field(default_factory=dict)
    models: dict[str, StructuredOutputModelSpec | StructuredOutputLiteralSpec | StructuredOutputUnionSpec] = (
        Field(default_factory=dict)
    )

    @field_validator("models")
    @classmethod
    def _validate_model_keys(
        cls,
        value: dict[str, StructuredOutputModelSpec | StructuredOutputLiteralSpec | StructuredOutputUnionSpec],
    ) -> dict[str, StructuredOutputModelSpec | StructuredOutputLiteralSpec | StructuredOutputUnionSpec]:
        for key in value.keys():
            _required_text(key, field_name="structured output definition name")
        return value

    @model_validator(mode="after")
    def _validate_registry_refs(self) -> StructuredOutputsConfig:
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


def _validate_config(model_cls: type[BaseModel], raw: dict[str, Any], file_name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid {file_name} configuration: root must be an object")
    try:
        validated = model_cls.model_validate(raw)
    except ValidationError as err:
        raise ValueError(f"Invalid {file_name} configuration: {err}") from err
    return validated.model_dump(by_alias=True)


def parse_orchestrator_config(raw: dict[str, Any]) -> dict[str, Any]:
    return _validate_config(OrchestratorConfig, raw, "orchestrator.yaml")


def parse_agents_config(raw: dict[str, Any]) -> dict[str, Any]:
    parsed = _validate_config(AgentsConfig, raw, "agents.yaml")
    agents_list = parsed.get("agents", [])
    agents_map: dict[str, Any] = {}
    for entry in agents_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            agents_map[name] = entry
    return {"agents": agents_map}


def parse_transition_graph_config(raw: dict[str, Any]) -> dict[str, Any]:
    return _validate_config(TransitionGraphConfig, raw, "transition_graph.yaml")


def parse_tools_config(raw: dict[str, Any]) -> dict[str, Any]:
    return _validate_config(ToolsConfig, raw, "tools.yaml")


def parse_middleware_config(raw: dict[str, Any]) -> dict[str, Any]:
    return _validate_config(MiddlewareConfig, raw, "middleware.yaml")


def parse_ui_config(raw: dict[str, Any]) -> dict[str, Any]:
    return _validate_config(UIConfig, raw, "ui_config.yaml")


def parse_a2a_config(raw: dict[str, Any]) -> dict[str, Any]:
    return _validate_config(A2AConfig, raw, "a2a.yaml")


def parse_structured_outputs_config(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Invalid structured_outputs.yaml configuration: root must be an object")
    try:
        validated = StructuredOutputsConfig.model_validate(raw)
    except ValidationError as err:
        raise ValueError(f"Invalid structured_outputs.yaml configuration: {err}") from err
    # Use exclude_unset=True so StructuredOutputFieldSpec.default is absent (not null) when
    # the YAML does not declare a default. This lets resolve_field_type distinguish
    # "field has no default (required)" from "field has explicit default: null (optional)".
    return validated.model_dump(by_alias=True, exclude_unset=True)


def parse_context_variables_config(raw: dict[str, Any]) -> dict[str, Any]:
    return _validate_config(ContextVariablesConfig, raw, "context_variables.yaml")


__all__ = [
    "parse_orchestrator_config",
    "parse_agents_config",
    "parse_transition_graph_config",
    "parse_context_variables_config",
    "parse_structured_outputs_config",
    "parse_tools_config",
    "parse_ui_config",
    "parse_middleware_config",
    "parse_a2a_config",
]
