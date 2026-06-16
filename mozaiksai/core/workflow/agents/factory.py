# ==============================================================================
# FILE: mozaiksai/core/workflow/agents/factory.py
# DESCRIPTION: autogen.beta.Agent factory for workflow orchestration.
# ==============================================================================
from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any

from autogen.beta import Agent
from autogen.beta.config import OpenAIConfig

from ..context.context_utils import (
    apply_context_exposures as _apply_context_exposures,
)
from ..context.context_utils import (
    context_to_dict as _context_to_dict,
)
from ..outputs.structured import (
    get_provider_response_model,
    get_structured_outputs_for_workflow,
    supports_provider_response_format,
)
from ..workflow_manager import workflow_manager
from .a2a import create_a2a_remote_agent, load_a2a_agent_specs

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# CONTEXT BRIDGE
# ------------------------------------------------------------------

class ContextVariablesBridge:
    """Shared workflow context exposed to tools and committed through AG2.

    Tool code mutates this bridge with a small dict-like API. During an AG2
    Network turn, the runner consumes the recorded mutations and injects them
    into the AG2 workflow packet's ``context_updates`` payload so AG2 routing
    conditions see the same state that tools just wrote.
    """

    __slots__ = ("_data", "_pending_set", "_pending_delete")

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._pending_set: dict[str, Any] = {}
        self._pending_delete: set[str] = set()

    # AG2-compatible read/write API
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise KeyError("context variable key must be non-empty")
        self._data[clean_key] = value
        self._pending_set[clean_key] = value
        self._pending_delete.discard(clean_key)

    def pop(self, key: str, default: Any = None) -> Any:
        clean_key = str(key or "").strip()
        if not clean_key:
            raise KeyError("context variable key must be non-empty")
        existed = clean_key in self._data
        value = self._data.pop(clean_key, default)
        if existed:
            self._pending_set.pop(clean_key, None)
            self._pending_delete.add(clean_key)
        return value

    def delete(self, key: str) -> None:
        self.pop(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def clear_context_updates(self) -> None:
        self._pending_set.clear()
        self._pending_delete.clear()

    def consume_context_updates(self) -> dict[str, Any]:
        updates = {
            "set": dict(self._pending_set),
            "delete": sorted(self._pending_delete),
        }
        self.clear_context_updates()
        return updates

    @property
    def data(self) -> dict[str, Any]:
        return self._data


# ------------------------------------------------------------------
# LLM CONFIG BRIDGE
# ------------------------------------------------------------------

def llm_config_to_openai_config(llm_config: dict[str, Any]) -> OpenAIConfig:
    """Convert an AG2 llm_config dict to an autogen.beta OpenAIConfig."""
    config_list = llm_config.get("config_list") or []
    if not config_list:
        raise ValueError("llm_config has no config_list entries")
    entry = config_list[0]
    return OpenAIConfig(
        model=entry.get("model", "gpt-4o-mini"),
        api_key=entry.get("api_key") or None,
        base_url=entry.get("base_url") or None,
        temperature=llm_config.get("temperature"),
        streaming=True,
    )


# ------------------------------------------------------------------
# PROMPT SECTION COMPOSITION
# ------------------------------------------------------------------

def _compose_prompt_sections(sections: Sequence[dict[str, Any]] | dict[str, Any]) -> str:
    """Reconstruct the system message string from structured prompt sections."""
    parts: list[str] = []

    if isinstance(sections, dict) and not any(k in sections for k in ("heading", "content")):
        section_order = [
            "role", "objective", "context", "runtime_integrations",
            "guidelines", "instructions", "examples", "json_output_compliance", "output_format",
        ]
        array_sections = []
        for key in section_order:
            section_data = sections.get(key)
            if section_data and isinstance(section_data, dict):
                array_sections.append(section_data)
        sections = array_sections

    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = section.get("heading")
        content = section.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if heading:
            parts.append(f"{heading}\n{content}" if content else heading)
        elif content:
            parts.append(content)

    return "\n\n".join(part.strip() for part in parts if part).strip()


# ------------------------------------------------------------------
# TOOL CONTEXT INJECTION
# ------------------------------------------------------------------

def _wrap_tool_with_context(fn: Callable, context_bridge: ContextVariablesBridge) -> Callable:
    """Wrap a tool function so it receives a ContextVariablesBridge for its
    ``context_variables`` parameter.  Functions that don't accept that
    parameter are returned unchanged.
    """
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        return fn

    if "context_variables" not in sig.parameters:
        return fn

    # Hide context_variables from the schema exposed to the LLM.
    new_params = [p for name, p in sig.parameters.items() if name != "context_variables"]
    new_sig = sig.replace(parameters=new_params)

    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            kwargs.setdefault("context_variables", context_bridge)
            return await fn(*args, **kwargs)

        async_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
        return async_wrapper
    else:
        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            kwargs.setdefault("context_variables", context_bridge)
            return fn(*args, **kwargs)

        sync_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
        return sync_wrapper


# ------------------------------------------------------------------
# AGENT CREATION
# ------------------------------------------------------------------

async def create_agents(
    workflow_name: str,
    context_variables: Any | None = None,
    cache_seed: int | None = None,
) -> dict[str, Agent]:
    """Create autogen.beta.Agent instances for a workflow."""

    logger.debug("[AGENTS] Creating beta agents for workflow: %s", workflow_name)
    from time import perf_counter

    start_time = perf_counter()
    workflow_config = workflow_manager.get_config(workflow_name) or {}
    agent_configs = workflow_config.get("agents", {})
    if "agents" in agent_configs:
        agent_configs = agent_configs["agents"]

    if isinstance(agent_configs, list):
        normalized: dict[str, Any] = {}
        for item in agent_configs:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                normalized[name.strip()] = item
        agent_configs = normalized

    if not isinstance(agent_configs, dict):
        logger.warning("[AGENTS] Invalid agents config for '%s'", workflow_name)
        agent_configs = {}

    # Normalise context to a mutable dict for the bridge
    if context_variables is None:
        ctx_dict: dict[str, Any] = {}
    elif isinstance(context_variables, dict):
        ctx_dict = context_variables
    elif hasattr(context_variables, "to_dict"):
        ctx_dict = context_variables.to_dict()
    elif hasattr(context_variables, "data") and isinstance(getattr(context_variables, "data", None), dict):
        ctx_dict = context_variables.data
    else:
        ctx_dict = {}

    context_bridge = ContextVariablesBridge(ctx_dict)

    a2a_specs = load_a2a_agent_specs(workflow_config)
    local_agent_names = [n for n in agent_configs if n not in a2a_specs]

    base_llm_config: dict[str, Any] = {}
    if local_agent_names:
        try:
            from ..llm_config import get_llm_config as _get_base_llm_config

            extra = {"cache_seed": cache_seed} if cache_seed is not None else None
            _, base_llm_config = await _get_base_llm_config(extra_config=extra)
        except Exception as err:
            logger.error("[AGENTS] Failed to load base LLM config: %s", err)
            return {}

    try:
        from .tools import load_agent_tool_functions

        agent_tool_functions = load_agent_tool_functions(workflow_name)
    except Exception as tool_err:
        logger.warning("[AGENTS] Failed loading tool functions: %s", tool_err)
        agent_tool_functions = {}

    auto_tool_agent_names = workflow_manager.get_auto_tool_agents(workflow_name)

    try:
        structured_registry = get_structured_outputs_for_workflow(workflow_name)
    except Exception:
        structured_registry = {}

    context_dict: dict[str, Any] = {}
    if context_variables is not None:
        try:
            context_dict = _context_to_dict(context_variables)
        except Exception:
            pass

    exposures_map = getattr(context_variables, "_mozaiks_context_exposures", {}) or {}
    agent_plan_map = getattr(context_variables, "_mozaiks_context_agents", {}) or {}

    agents: dict[str, Agent] = {}

    for agent_name, agent_config in agent_configs.items():
        # A2A remote agents
        a2a_spec = a2a_specs.get(agent_name)
        if a2a_spec is not None:
            try:
                remote = create_a2a_remote_agent(a2a_spec, context_variables=context_variables)
                remote._mozaiks_agent_kind = "a2a_remote"
                agents[agent_name] = remote
                continue
            except Exception as a2a_err:
                logger.error("[AGENTS] A2A agent '%s' failed: %s", agent_name, a2a_err)
                raise

        # Per-agent LLM config → OpenAIConfig
        try:
            from ..outputs.structured import get_llm_for_workflow as _get_structured_llm

            extra = {"cache_seed": cache_seed} if cache_seed is not None else None
            _, llm_config_dict = await _get_structured_llm(
                workflow_name, "base", agent_name=agent_name, extra_config=extra,
            )
        except Exception:
            llm_config_dict = base_llm_config

        try:
            model_config = llm_config_to_openai_config(llm_config_dict)
        except Exception as cfg_err:
            logger.error("[AGENTS] Cannot build OpenAIConfig for '%s': %s", agent_name, cfg_err)
            raise

        # System prompt
        prompt_sections = agent_config.get("prompt_sections") or agent_config.get("prompt_sections_custom")
        if prompt_sections:
            system_message = _compose_prompt_sections(prompt_sections)
        else:
            system_message = agent_config.get("system_message", "You are a helpful AI assistant.")

        # Apply context exposures to the base prompt
        agent_exposures = (exposures_map or {}).get(agent_name, []) or []
        agent_plan = (agent_plan_map or {}).get(agent_name)
        agent_variables = list(getattr(agent_plan, "variables", []) or [])

        if agent_exposures or agent_variables:
            system_message = _apply_context_exposures(
                system_message, agent_exposures, context_dict, agent_variables,
            )

        # Tool binding (skip for auto_tool_call agents — they don't call tools directly)
        auto_tool_call_enabled = agent_name in auto_tool_agent_names
        structured_model_cls = structured_registry.get(agent_name) if structured_registry else None

        if auto_tool_call_enabled and structured_model_cls is None:
            raise ValueError(
                f"[AGENTS] Agent '{agent_name}' has auto_tool_call but no structured output model"
            )

        raw_tool_fns: list[Callable] = [] if auto_tool_call_enabled else agent_tool_functions.get(agent_name, [])

        # Inject LocalShellTool when agent declares sandbox_shell: true.
        # LocalShellTool is an AG2 Tool subclass and does not need context wrapping.
        shell_tools: list[Any] = []
        if not auto_tool_call_enabled and agent_config.get("sandbox_shell"):
            try:
                from autogen.beta.tools import LocalShellTool
                shell_tools = [LocalShellTool()]
                logger.debug("[AGENTS] LocalShellTool attached to '%s'", agent_name)
            except Exception as shell_err:
                logger.warning(
                    "[AGENTS] sandbox_shell requested for '%s' but LocalShellTool unavailable: %s",
                    agent_name,
                    shell_err,
                )

        # Wrap tools to inject context_variables
        wrapped_tools: list[Any] = [
            _wrap_tool_with_context(fn, context_bridge) for fn in raw_tool_fns
        ] + shell_tools

        # Load workflow-local AG2 beta prompt middleware declarations.
        prompt_middleware_functions: list[Callable] = []
        try:
            from ..execution.middleware import _resolve_import, load_prompt_middleware_entries
            workflow_path = workflow_manager.resolve_workflow_path(workflow_name)
            if workflow_path is not None:
                for entry in load_prompt_middleware_entries(workflow_name, base_path=str(workflow_path.parent)):
                    if (
                        isinstance(entry, dict)
                        and entry.get("agent") in (agent_name, "all")
                    ):
                        fn, qual = _resolve_import(workflow_name, entry.get("filename"), entry["function"], workflow_path)
                        if fn:
                            prompt_middleware_functions.append(fn)
                            logger.debug(
                                "[AGENTS] Loaded prompt middleware %s for %s",
                                qual,
                                agent_name,
                            )
        except Exception as middleware_err:
            logger.debug("[AGENTS] Prompt middleware pre-load failed for '%s': %s", agent_name, middleware_err)

        # Determine response schema for structured outputs
        beta_response_schema = None
        if structured_model_cls is not None:
            # Beta response_schema is provider-enforced. Only pass models that
            # are compatible with OpenAI strict structured outputs; models with
            # open-ended dict fields are parsed and validated after the response.
            try:
                supports_strict, _ = supports_provider_response_format(structured_model_cls)
                if supports_strict:
                    beta_response_schema = get_provider_response_model(structured_model_cls)
            except Exception:
                beta_response_schema = None

        middleware = []
        telemetry_enabled = False
        try:
            from mozaiksai.core.observability import build_ag2_telemetry_middleware

            telemetry_middleware = build_ag2_telemetry_middleware(
                agent_name=agent_name,
                workflow_name=workflow_name,
                context_variables=context_bridge,
                provider_name="openai",
                model_name=str((llm_config_dict or {}).get("model") or "").strip() or None,
            )
            if telemetry_middleware is not None:
                middleware.append(telemetry_middleware)
                telemetry_enabled = True
        except Exception as telemetry_err:
            logger.debug("[AGENTS] AG2 telemetry middleware skipped for '%s': %s", agent_name, telemetry_err)

        try:
            from mozaiksai.core.observability import build_ag2_usage_middleware

            middleware.append(
                build_ag2_usage_middleware(
                    agent_name=agent_name,
                    workflow_name=workflow_name,
                    context_variables=context_bridge,
                    model_name=str((llm_config_dict or {}).get("model") or "").strip() or None,
                )
            )
        except Exception as usage_err:
            logger.debug("[AGENTS] AG2 usage middleware skipped for '%s': %s", agent_name, usage_err)

        if prompt_middleware_functions:
            from ..execution.middleware import build_prompt_middleware

            middleware.append(
                build_prompt_middleware(
                    middleware_functions=prompt_middleware_functions,
                    agent_name=agent_name,
                    base_system_message=system_message,
                    context_bridge=context_bridge,
                )
            )

        # Create beta Agent
        agent = Agent(
            agent_name,
            prompt=system_message,
            config=model_config,
            tools=tuple(wrapped_tools),
            response_schema=beta_response_schema,
            middleware=middleware,
        )

        # Store Mozaiks metadata
        if prompt_sections and isinstance(prompt_sections, Sequence):
            agent._mozaiks_prompt_sections = prompt_sections
        agent._mozaiks_base_system_message = system_message
        agent._mozaiks_prompt_middleware = prompt_middleware_functions
        agent._mozaiks_ag2_telemetry_enabled = telemetry_enabled
        agent._mozaiks_agent_kind = "local"
        agent._mozaiks_context_bridge = context_bridge

        if structured_model_cls is not None:
            model_name = getattr(structured_model_cls, "__name__", None)
            if model_name:
                agent._mozaiks_structured_model_name = model_name
            agent._mozaiks_structured_model_cls = structured_model_cls

        agents[agent_name] = agent

    duration = perf_counter() - start_time
    logger.debug("[AGENTS] Created %d beta agents for '%s' in %.2fs", len(agents), workflow_name, duration)

    return agents


# ------------------------------------------------------------------
# RUNTIME INSPECTION UTILITIES
# ------------------------------------------------------------------

def list_agent_middleware(agent: Any) -> dict[str, list[str]]:
    """Return Mozaiks prompt middleware registered as AG2 beta middleware."""
    out: dict[str, list[str]] = {}
    middleware_functions = getattr(agent, "_mozaiks_prompt_middleware", [])
    if middleware_functions:
        out["prompt_middleware"] = [
            getattr(fn, "__name__", repr(fn)) for fn in middleware_functions
        ]
    return out


def list_middleware_for_workflow(agents: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    return {name: list_agent_middleware(agent) for name, agent in agents.items()}


__all__ = [
    "create_agents",
    "ContextVariablesBridge",
    "llm_config_to_openai_config",
    "list_agent_middleware",
    "list_middleware_for_workflow",
]
