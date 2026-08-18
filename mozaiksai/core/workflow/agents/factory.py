# ==============================================================================
# FILE: mozaiksai/core/workflow/agents/factory.py
# DESCRIPTION: AG2 Agent factory for workflow orchestration.
# ==============================================================================
from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Mapping, Sequence
from functools import wraps
from typing import Any

from ag2 import Agent
from ag2.middleware.builtin import RetryMiddleware

from mozaiksai.core.adapters.llm_fallback import llm_config_to_ag2_config
from mozaiksai.core.media.ag2 import (
    build_ag2_image_generation_tools,
    image_generation_enabled,
    prepare_llm_config_for_media,
)
from mozaiksai.core.media.middleware import build_ag2_media_harvest_middleware

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
_conv_logger = logging.getLogger("mozaiks.workflow.agent_messages")

_SENSITIVE_CONTEXT_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "key",
    "password",
    "secret",
    "token",
)


def _safe_context_keys(context_dict: dict[str, Any]) -> list[str]:
    """Return context keys that are useful for debugging and safe to list."""

    keys: list[str] = []
    for key in sorted(str(raw_key) for raw_key in context_dict):
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_CONTEXT_KEY_PARTS):
            continue
        keys.append(key)
    return keys


def _supports_ag2_native_web_tools(
    llm_config: Mapping[str, Any] | None,
    model_config: Any | None = None,
) -> bool:
    """Return whether AG2 native hosted web tools fit the active provider.

    AG2's WebSearchTool/WebFetchTool are provider-native tool schemas, not
    normal Python function tools. OpenAI Chat Completions rejects those schemas;
    OpenAI Responses accepts them.
    """

    config = dict(llm_config or {})
    entry = ((config.get("config_list") or [{}])[0] or {})
    api_type = str(entry.get("api_type") or "openai").strip().lower()
    if api_type != "openai":
        return bool(config.get("ag2_native_web_tools"))
    if bool(config.get("use_responses_api") or config.get("responses_api")):
        return True
    model_config_name = type(model_config).__name__ if model_config is not None else ""
    return model_config_name == "OpenAIResponsesConfig"


def _log_existing_app_discovery_projection(
    *,
    agent_name: str,
    agent_variables: list[str],
    context_dict: dict[str, Any],
    system_message: str,
) -> None:
    """Log evidence coverage without writing repository content or credentials."""
    if agent_name != "DiscoveryHostAgent":
        return

    evidence_keys = (
        "app_intelligence_summary",
        "app_intelligence_catalog",
        "app_intelligence_progress",
        "app_intelligence_health",
        "source_context_catalog",
        "context_graph_pack",
        "preload_summary",
        "repo_summary",
        "frontend_repo_summary",
        "backend_repo_summary",
        "api_inventory",
        "runtime_observations",
        "auth_hypothesis",
        "app_name",
        "tech_stack",
        "existing_experience_summary",
    )
    present = [key for key in evidence_keys if context_dict.get(key)]
    missing = [key for key in evidence_keys if key not in present]
    prompt_markers = [key for key in evidence_keys if f"{key.upper()}:" in system_message]
    repo_summary = context_dict.get("repo_summary")
    app_intelligence_catalog = context_dict.get("app_intelligence_catalog")
    logger.info(
        "[EXISTING_APP_DISCOVERY_CONTEXT_PROJECTION] agent=%s preload_status=%s "
        "intake_ready=%s app_intelligence_status=%s app_intelligence_ready=%s "
        "app_intelligence_present=%s declared=%s evidence_present=%s evidence_missing=%s "
        "prompt_markers=%s repo_metadata_present=%s prompt_chars=%s",
        agent_name,
        context_dict.get("preload_status"),
        bool(context_dict.get("preloaded_context_ready")),
        context_dict.get("app_intelligence_status"),
        bool(context_dict.get("app_intelligence_ready")),
        bool(isinstance(app_intelligence_catalog, dict) and app_intelligence_catalog.get("present")),
        len(agent_variables),
        present,
        missing,
        prompt_markers,
        bool(isinstance(repo_summary, dict) and repo_summary.get("success")),
        len(system_message),
    )


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
    """Create AG2 Agent instances for a workflow."""

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

        # Per-agent LLM config → AG2 ModelConfig
        try:
            from ..outputs.structured import get_llm_for_workflow as _get_structured_llm

            extra = {"cache_seed": cache_seed} if cache_seed is not None else None
            _, llm_config_dict = await _get_structured_llm(
                workflow_name, "base", agent_name=agent_name, extra_config=extra,
            )
        except Exception:
            llm_config_dict = base_llm_config

        try:
            if agent_name not in auto_tool_agent_names:
                llm_config_dict = prepare_llm_config_for_media(agent_config, llm_config_dict)
            model_config = llm_config_to_ag2_config(llm_config_dict)
        except Exception as cfg_err:
            logger.error("[AGENTS] Cannot build AG2 ModelConfig for '%s': %s", agent_name, cfg_err)
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

        _log_existing_app_discovery_projection(
            agent_name=agent_name,
            agent_variables=agent_variables,
            context_dict=context_dict,
            system_message=system_message,
        )
        visible_context_keys = _safe_context_keys(context_dict)
        _conv_logger.info(
            "[%s] AGENT_CONTEXT_READY agent=%s context_keys=%s exposed=%s declared=%s "
            "prompt_chars=%s",
            workflow_name,
            agent_name,
            visible_context_keys,
            agent_exposures,
            agent_variables,
            len(system_message),
            extra={
                "agent_transcript_scope": "summary",
                "workflow_name": workflow_name,
                "agent_name": agent_name,
                "context_key_count": len(visible_context_keys),
            },
        )

        # Full prompt transcripts are only written when their dedicated logger
        # has been explicitly enabled by the local operator.
        _conv_logger.info(
            "[%s] AGENT_SYSTEM_PROMPT agent=%s\n%s",
            workflow_name,
            agent_name,
            system_message,
            extra={
                "agent_transcript_scope": "full",
                "workflow_name": workflow_name,
                "agent_name": agent_name,
            },
        )

        # Tool binding (skip for auto_tool_call agents — they don't call tools directly)
        auto_tool_call_enabled = agent_name in auto_tool_agent_names
        structured_model_cls = structured_registry.get(agent_name) if structured_registry else None

        if auto_tool_call_enabled and structured_model_cls is None:
            raise ValueError(
                f"[AGENTS] Agent '{agent_name}' has auto_tool_call but no structured output model"
            )

        raw_tool_fns: list[Callable] = [] if auto_tool_call_enabled else agent_tool_functions.get(agent_name, [])

        # Inject SandboxShellTool when agent declares sandbox_shell: true.
        # SandboxShellTool is an AG2 Tool subclass and does not need context wrapping.
        shell_tools: list[Any] = []
        if not auto_tool_call_enabled and agent_config.get("sandbox_shell"):
            try:
                from ag2.tools.shell import SandboxShellTool
                shell_tools = [SandboxShellTool()]
                logger.debug("[AGENTS] SandboxShellTool attached to '%s'", agent_name)
            except Exception as shell_err:
                logger.warning(
                    "[AGENTS] sandbox_shell requested for '%s' but SandboxShellTool unavailable: %s",
                    agent_name,
                    shell_err,
                )

        native_web_tools_supported = _supports_ag2_native_web_tools(
            llm_config_dict,
            model_config,
        )

        # Inject AG2 built-in WebSearchTool when agent declares web_search: true.
        # These are provider-native hosted tools; skip them for providers such as
        # OpenAI Chat Completions that only accept function tools.
        web_tools: list[Any] = []
        if not auto_tool_call_enabled and agent_config.get("web_search") and native_web_tools_supported:
            try:
                from ag2.tools import WebSearchTool
                web_tools.append(WebSearchTool())
                logger.debug("[AGENTS] WebSearchTool attached to '%s'", agent_name)
            except Exception as ws_err:
                logger.warning(
                    "[AGENTS] web_search requested for '%s' but WebSearchTool unavailable: %s",
                    agent_name,
                    ws_err,
                )
        elif not auto_tool_call_enabled and agent_config.get("web_search"):
            logger.warning(
                "[AGENTS] web_search requested for '%s' but skipped: provider-native "
                "AG2 web tools require a supported Responses-style provider",
                agent_name,
            )

        # Inject AG2 built-in WebFetchTool when agent declares web_fetch: true.
        # Allows agents to retrieve full page content from URLs discovered via search.
        if not auto_tool_call_enabled and agent_config.get("web_fetch") and native_web_tools_supported:
            try:
                from ag2.tools import WebFetchTool
                web_tools.append(WebFetchTool())
                logger.debug("[AGENTS] WebFetchTool attached to '%s'", agent_name)
            except Exception as wf_err:
                logger.warning(
                    "[AGENTS] web_fetch requested for '%s' but WebFetchTool unavailable: %s",
                    agent_name,
                    wf_err,
                )
        elif not auto_tool_call_enabled and agent_config.get("web_fetch"):
            logger.warning(
                "[AGENTS] web_fetch requested for '%s' but skipped: provider-native "
                "AG2 web tools require a supported Responses-style provider",
                agent_name,
            )

        # Inject DuckDuckSearchTool when agent declares duck_search: true.
        # No API key required — works with any model. Good fit for interview and
        # design agents that need quick market lookups, not deep research crawls.
        if not auto_tool_call_enabled and agent_config.get("duck_search"):
            try:
                from ag2.tools import DuckDuckSearchTool
                web_tools.append(DuckDuckSearchTool())
                logger.debug("[AGENTS] DuckDuckSearchTool attached to '%s'", agent_name)
            except Exception as dd_err:
                logger.warning(
                    "[AGENTS] duck_search requested for '%s' but DuckDuckSearchTool unavailable: %s",
                    agent_name,
                    dd_err,
                )

        image_generation_tools: list[Any] = []
        if not auto_tool_call_enabled:
            image_generation_tools = build_ag2_image_generation_tools(
                agent_config,
                llm_config_dict,
                logger=logger,
                agent_name=agent_name,
            )
            if image_generation_tools:
                logger.debug("[AGENTS] ImageGenerationTool attached to '%s'", agent_name)
        elif image_generation_enabled(agent_config):
            logger.warning(
                "[AGENTS] image_generation_enabled ignored for auto_tool_call agent '%s'",
                agent_name,
            )

        # Wrap tools to inject context_variables
        wrapped_tools: list[Any] = [
            _wrap_tool_with_context(fn, context_bridge) for fn in raw_tool_fns
        ] + shell_tools + web_tools + image_generation_tools

        # Load workflow-local AG2 1.0 beta prompt middleware declarations.
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
            # OpenAI strict structured outputs reject Dict[str, Any] fields.
            # Anthropic (tool_use), Gemini, and Ollama have no such restriction —
            # AG2 converts the Pydantic schema to a tool input schema internally,
            # which handles freeform object fields. Only apply the strict-field
            # gate for OpenAI providers; pass response_schema unconditionally for
            # all other providers.
            try:
                _api_type = (
                    ((llm_config_dict or {}).get("config_list") or [{}])[0].get("api_type") or "openai"
                ).lower()
                _openai_strict = _api_type in ("openai", "azure")
                if _openai_strict:
                    supports_strict, _ = supports_provider_response_format(structured_model_cls)
                    if supports_strict:
                        beta_response_schema = get_provider_response_model(structured_model_cls)
                else:
                    # Anthropic / Gemini / Ollama — pass the schema directly;
                    # AG2 routes it through the provider's native mechanism.
                    beta_response_schema = structured_model_cls
            except Exception as so_err:
                logger.warning(
                    "[AGENTS] Structured output schema resolution failed for '%s' — response_schema disabled: %s",
                    agent_name,
                    so_err,
                )
                beta_response_schema = None

        middleware = []
        observers = []
        telemetry_enabled = False
        try:
            from mozaiksai.core.observability import build_ag2_telemetry_middleware

            _cfg_entry = ((llm_config_dict or {}).get("config_list") or [{}])[0]
            telemetry_middleware = build_ag2_telemetry_middleware(
                agent_name=agent_name,
                workflow_name=workflow_name,
                context_variables=context_bridge,
                provider_name=(_cfg_entry.get("api_type") or "openai"),
                model_name=str(_cfg_entry.get("model") or "").strip() or None,
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
                    model_name=str(_cfg_entry.get("model") or "").strip() or None,
                )
            )
        except Exception as usage_err:
            logger.debug("[AGENTS] AG2 usage middleware skipped for '%s': %s", agent_name, usage_err)

        if not auto_tool_call_enabled and image_generation_enabled(agent_config):
            try:
                raw_media_config = agent_config.get("image_generation") if isinstance(agent_config, Mapping) else None
                media_generation_params = dict(raw_media_config) if isinstance(raw_media_config, Mapping) else {}
                media_promotion_targets = media_generation_params.get("promotion_targets")
                middleware.append(
                    build_ag2_media_harvest_middleware(
                        agent_name=agent_name,
                        workflow_name=workflow_name,
                        context_variables=context_bridge,
                        generation_params=media_generation_params,
                        promotion_targets=media_promotion_targets if isinstance(media_promotion_targets, list) else None,
                    )
                )
            except Exception as media_err:
                logger.debug("[AGENTS] AG2 media harvest middleware skipped for '%s': %s", agent_name, media_err)

        try:
            from mozaiksai.core.observability import build_ag2_metrics_middleware

            metrics_middleware = build_ag2_metrics_middleware()
            if metrics_middleware is not None:
                middleware.append(metrics_middleware)
        except Exception as metrics_err:
            logger.debug("[AGENTS] AG2 metrics middleware skipped for '%s': %s", agent_name, metrics_err)

        try:
            from mozaiksai.core.observability import build_ag2_token_watchdog_observers

            observers.extend(
                build_ag2_token_watchdog_observers(
                    agent_name=agent_name,
                    workflow_name=workflow_name,
                    context_variables=context_bridge,
                )
            )
        except Exception as watchdog_err:
            logger.debug("[AGENTS] AG2 token watchdog observers skipped for '%s': %s", agent_name, watchdog_err)

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

        # Structured-output agents get RetryMiddleware so provider/network failures
        # retry before the caller sees an exception (mirrors AG2StructuredAgentRunner).
        if beta_response_schema is not None:
            middleware.append(RetryMiddleware(max_retries=2))

        # Create beta Agent
        agent = Agent(
            agent_name,
            prompt=system_message,
            config=model_config,
            tools=tuple(wrapped_tools),
            response_schema=beta_response_schema,
            middleware=middleware,
            observers=observers,
        )

        # Store Mozaiks metadata
        if prompt_sections and isinstance(prompt_sections, Sequence):
            agent._mozaiks_prompt_sections = prompt_sections
        agent._mozaiks_base_system_message = system_message
        agent._mozaiks_prompt_middleware = prompt_middleware_functions
        agent._mozaiks_ag2_telemetry_enabled = telemetry_enabled
        agent._mozaiks_ag2_token_watchdog_enabled = bool(observers)
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
    """Return Mozaiks prompt middleware registered as AG2 1.0 beta middleware."""
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
    "llm_config_to_ag2_config",
    "list_agent_middleware",
    "list_middleware_for_workflow",
]
