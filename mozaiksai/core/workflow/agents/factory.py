# ==============================================================================
# FILE: mozaiksai/core/workflow/agents/factory.py
# DESCRIPTION: autogen.beta.Agent factory for workflow orchestration.
# ==============================================================================
from __future__ import annotations

import asyncio
import logging
import inspect
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Sequence

from autogen.beta import Agent
from autogen.beta.assembly import AssemblyPolicy
from autogen.beta.config import OpenAIConfig
from autogen.beta.context import ConversationContext
from autogen.beta.events import BaseEvent

from ..outputs.structured import (
    get_provider_response_model,
    get_structured_outputs_for_workflow,
    supports_provider_response_format,
)
from ..workflow_manager import workflow_manager
from .a2a import create_a2a_remote_agent, load_a2a_agent_specs

from ..context.context_utils import (
    context_to_dict as _context_to_dict,
    apply_context_exposures as _apply_context_exposures,
)
from ..messages.utils import extract_images_from_conversation

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# CONTEXT BRIDGE
# ------------------------------------------------------------------

class ContextVariablesBridge:
    """Bridges a plain dict to the AG2 ContextVariables-compatible interface.

    Tools written for the old system call .get() / .set() / .data — this
    bridge satisfies those calls against a shared mutable dict so writes
    from tools propagate through the orchestration loop.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    # AG2-compatible read/write API
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    @property
    def data(self) -> Dict[str, Any]:
        return self._data


# ------------------------------------------------------------------
# LLM CONFIG BRIDGE
# ------------------------------------------------------------------

def llm_config_to_openai_config(llm_config: Dict[str, Any]) -> OpenAIConfig:
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
        seed=llm_config.get("cache_seed") or None,
        streaming=True,
    )


# ------------------------------------------------------------------
# PROMPT SECTION COMPOSITION
# ------------------------------------------------------------------

def _compose_prompt_sections(sections: Sequence[Dict[str, Any]] | Dict[str, Any]) -> str:
    """Reconstruct the system message string from structured prompt sections."""
    parts: List[str] = []

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
# ASSEMBLY POLICY — hook-driven prompt injection via AG2 AssemblyPolicy
# ------------------------------------------------------------------

class MozaiksHookPolicy:
    """Wraps ``update_agent_state`` hooks as an AG2 ``AssemblyPolicy``.

    Runs before each LLM call.  If a hook calls ``agent.update_system_message()``
    the returned string replaces the current prompt list; otherwise prompts are
    unchanged.  This replaces the old ``_SystemMessageCapture`` + per-turn
    ``_compute_hook_prompt`` pattern.
    """

    name: str = "mozaiks_update_agent_state"

    def __init__(
        self,
        hooks: List[Callable],
        agent_name: str,
        base_system_message: str,
        context_bridge: Any,
    ) -> None:
        self._hooks = hooks
        self._agent_name = agent_name
        self._base = base_system_message
        self._context_bridge = context_bridge

    async def apply(
        self,
        prompts: List[str],
        events: List[BaseEvent],
        context: ConversationContext,
    ) -> tuple[List[str], List[BaseEvent]]:
        if not self._hooks:
            return prompts, events

        class _Capture:
            def __init__(self, name: str, ctx: Any, base_message: str) -> None:
                self.name = name
                self.context_variables = ctx
                self.system_message = base_message
                self._system_message = base_message
                self._captured: Optional[str] = None

            def update_system_message(self, msg: str) -> None:
                self.system_message = msg
                self._system_message = msg
                self._captured = msg

        history = context.variables.get("_mozaiks_history", [])
        capture = _Capture(self._agent_name, self._context_bridge, self._base)

        for hook in self._hooks:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(capture, history)
                else:
                    hook(capture, history)
            except Exception as exc:
                logger.debug("[HOOKS] update_agent_state hook failed: %s", exc)

        if capture._captured and capture._captured != self._base:
            return [capture._captured], events

        return prompts, events


# ------------------------------------------------------------------
# AGENT CREATION
# ------------------------------------------------------------------

async def create_agents(
    workflow_name: str,
    context_variables: Optional[Any] = None,
    cache_seed: Optional[int] = None,
) -> Dict[str, Agent]:
    """Create autogen.beta.Agent instances for a workflow."""

    logger.info("[AGENTS] Creating beta agents for workflow: %s", workflow_name)
    from time import perf_counter

    start_time = perf_counter()
    workflow_config = workflow_manager.get_config(workflow_name) or {}
    agent_configs = workflow_config.get("agents", {})
    if "agents" in agent_configs:
        agent_configs = agent_configs["agents"]

    if isinstance(agent_configs, list):
        normalized: Dict[str, Any] = {}
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
        ctx_dict: Dict[str, Any] = {}
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

    base_llm_config: Dict[str, Any] = {}
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

    context_dict: Dict[str, Any] = {}
    if context_variables is not None:
        try:
            context_dict = _context_to_dict(context_variables)
        except Exception:
            pass

    exposures_map = getattr(context_variables, "_mozaiks_context_exposures", {}) or {}
    agent_plan_map = getattr(context_variables, "_mozaiks_context_agents", {}) or {}

    agents: Dict[str, Agent] = {}

    for agent_name, agent_config in agent_configs.items():
        # A2A remote agents
        a2a_spec = a2a_specs.get(agent_name)
        if a2a_spec is not None:
            try:
                remote = create_a2a_remote_agent(a2a_spec, context_variables=context_variables)
                setattr(remote, "_mozaiks_agent_kind", "a2a_remote")
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

        raw_tool_fns: List[Callable] = [] if auto_tool_call_enabled else agent_tool_functions.get(agent_name, [])

        # Wrap tools to inject context_variables
        wrapped_tools: List[Callable] = [
            _wrap_tool_with_context(fn, context_bridge) for fn in raw_tool_fns
        ]

        # Load update_agent_state hooks for pre-turn prompt injection
        update_hooks: List[Callable] = []
        try:
            from ..execution.hooks import _resolve_import, load_hook_entries
            workflow_path = workflow_manager.resolve_workflow_path(workflow_name)
            if workflow_path is not None:
                for entry in load_hook_entries(workflow_name, base_path=str(workflow_path.parent)):
                    if (
                        isinstance(entry, dict)
                        and entry.get("hook_type") == "update_agent_state"
                        and entry.get("hook_agent") in (agent_name, "all")
                    ):
                        fn, qual = _resolve_import(workflow_name, entry["filename"], entry["function"], workflow_path)
                        if fn:
                            update_hooks.append(fn)
                            logger.debug("[AGENTS] Loaded update_agent_state hook %s for %s", qual, agent_name)
        except Exception as hook_err:
            logger.debug("[AGENTS] Hook pre-load failed for '%s': %s", agent_name, hook_err)

        # Collect any "all" scoped update_agent_state hooks that we might have missed
        # above if per-agent hooks were already added (dedup by function identity)
        # The orchestrator is the canonical caller of these hooks — we only store them.

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

        # Build AssemblyPolicy from hooks (replaces _SystemMessageCapture + _compute_hook_prompt)
        assembly = []
        if update_hooks:
            assembly.append(
                MozaiksHookPolicy(update_hooks, agent_name, system_message, context_bridge)
            )

        # Create beta Agent
        agent = Agent(
            agent_name,
            prompt=system_message,
            config=model_config,
            tools=tuple(wrapped_tools),
            response_schema=beta_response_schema,
            assembly=assembly,
        )

        # Store Mozaiks metadata
        if prompt_sections and isinstance(prompt_sections, Sequence):
            setattr(agent, "_mozaiks_prompt_sections", prompt_sections)
        setattr(agent, "_mozaiks_base_system_message", system_message)
        setattr(agent, "_mozaiks_update_hooks", update_hooks)  # kept for introspection only
        setattr(agent, "_mozaiks_agent_kind", "local")
        setattr(agent, "_mozaiks_context_bridge", context_bridge)

        if structured_model_cls is not None:
            model_name = getattr(structured_model_cls, "__name__", None)
            if model_name:
                setattr(agent, "_mozaiks_structured_model_name", model_name)
            setattr(agent, "_mozaiks_structured_model_cls", structured_model_cls)

        agents[agent_name] = agent

    duration = perf_counter() - start_time
    logger.info("[AGENTS] Created %d beta agents for '%s' in %.2fs", len(agents), workflow_name, duration)

    return agents


# ------------------------------------------------------------------
# RUNTIME INSPECTION UTILITIES
# ------------------------------------------------------------------

def list_agent_hooks(agent: Any) -> Dict[str, List[str]]:
    """Return hook names stored on a beta Agent."""
    out: Dict[str, List[str]] = {}
    hooks = getattr(agent, "_mozaiks_update_hooks", [])
    if hooks:
        out["update_agent_state"] = [getattr(fn, "__name__", repr(fn)) for fn in hooks]
    return out


def list_hooks_for_workflow(agents: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    return {name: list_agent_hooks(agent) for name, agent in agents.items()}


__all__ = [
    "create_agents",
    "ContextVariablesBridge",
    "MozaiksHookPolicy",
    "llm_config_to_openai_config",
    "extract_images_from_conversation",
    "list_agent_hooks",
    "list_hooks_for_workflow",
]
