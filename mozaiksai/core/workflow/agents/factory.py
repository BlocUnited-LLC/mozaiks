# ==============================================================================
# FILE: core/workflow/agents/factory.py
# DESCRIPTION: ConversableAgent factory - orchestrates agent creation with tools, context, and hooks
# ==============================================================================
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Callable, Sequence

from autogen import ConversableAgent, UpdateSystemMessage

from ..outputs import get_structured_outputs_for_workflow
from ..workflow_manager import workflow_manager

# Import context utilities (extracted for modularity)
from ..context.context_utils import (
    context_to_dict as _context_to_dict,
    stringify_context_value as _stringify_context_value,
    render_default_context_fragment as _render_default_context_fragment,
    apply_context_exposures as _apply_context_exposures,
    build_exposure_update_hook as _build_exposure_update_hook,
)

# Import message utilities (extracted for modularity)
from ..messages.utils import extract_images_from_conversation

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# PROMPT SECTION COMPOSITION
# ------------------------------------------------------------------

def _compose_prompt_sections(sections: Sequence[Dict[str, Any]] | Dict[str, Any]) -> str:
    """Reconstruct the system message string from structured prompt sections.
    
    Supports multiple formats for maximum adaptability:
    1. Fixed structure (PromptSections): Dict with named fields (role, objective, context, etc.)
    2. Custom array (list[PromptSectionContent]): List of {heading, content} dicts
    
    This allows the runtime to compose ANY section structure while enforcing
    standardization for new agents via schema validation.
    """
    parts: List[str] = []
    
    # Handle fixed structure (PromptSections object from schema)
    if isinstance(sections, dict) and not any(k in sections for k in ("heading", "content")):
        # This is a PromptSections object with named fields (role, objective, etc.)
        # Convert to array format for unified processing
        section_order = [
            "role", "objective", "context", "runtime_integrations",
            "guidelines", "instructions", "examples", "json_output_compliance", "output_format"
        ]
        array_sections = []
        for key in section_order:
            section_data = sections.get(key)
            if section_data and isinstance(section_data, dict):
                array_sections.append(section_data)
        sections = array_sections
    
    # Handle array format (custom sections or converted from fixed structure)
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = section.get("heading")
        content = section.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if heading:
            if content:
                parts.append(f"{heading}\n{content}")
            else:
                parts.append(f"{heading}")
        elif content:
            parts.append(content)
    return "\n\n".join(part.strip() for part in parts if part).strip()


# ==============================================================================
# INTERVIEWAGENT TESTING UTILITIES (TEMPORARY - REMOVE FOR PRODUCTION)
# ==============================================================================
# NOTE: All InterviewAgent-specific code is consolidated in this section.
# To remove InterviewAgent testing hooks, delete this entire section and the
# corresponding registration code (search for "##INTERVIEWAGENT##" markers).
# ==============================================================================

def _build_interview_message_hook(
    exposures: List[Dict[str, Any]],
    fallback_variables: List[str],
) -> Callable[..., Any]:
    """Build a process_message_before_send hook for InterviewAgent.
    
    TESTING MODE ONLY - REMOVE FOR PRODUCTION
    
    First message: Shows context variables and asks the automation question.
    Second+ messages: Auto-responds with "NEXT" for testing purposes.
    
    To disable auto-NEXT behavior, comment out the reply_count check below.
    """
    exposures_copy = [exp.copy() for exp in exposures if isinstance(exp, dict)] or []
    
    # Track number of times InterviewAgent has sent a message (conversation counter)
    reply_count = {"count": 0}

    def _hook(sender=None, message=None, recipient=None, silent=False):
        try:
            # Increment reply count for this agent
            reply_count["count"] += 1
            current_count = reply_count["count"]
            
            logger.debug(f"[InterviewAgent][HOOK] Processing message #{current_count} before send")
            
            # TESTING MODE: Auto-respond with "NEXT" on second+ replies
            # Comment out this block for production to allow natural conversation
            if current_count > 1:
                logger.info(f"[InterviewAgent][HOOK] Auto-responding with NEXT (reply #{current_count})")
                if isinstance(message, dict):
                    updated = dict(message)
                    updated["content"] = "NEXT"
                    return updated
                return "NEXT"
            
            # First message: Build context-aware greeting
            raw_message = message.get("content") if isinstance(message, dict) else message
            if not isinstance(raw_message, str):
                logger.debug(f"[InterviewAgent][HOOK] Non-string message, returning as-is")
                return message
                
            container = getattr(sender, "context_variables", None)
            context_dict = _context_to_dict(container) if container is not None else {}

            if fallback_variables:
                visible_snapshot: Dict[str, str] = {}
                for var in fallback_variables:
                    if not isinstance(var, str) or not var.strip():
                        continue
                    value = _stringify_context_value(context_dict.get(var), "null")
                    if len(value) > 500:
                        value = f"{value[:497]}..."
                    visible_snapshot[var] = value
                if visible_snapshot:
                    logger.info(
                        "[InterviewAgent][HOOK] Context variables snapshot",
                        extra={"variables": visible_snapshot},
                    )

            if exposures_copy:
                fragment = _apply_context_exposures("", exposures_copy, context_dict, fallback_variables).strip()
            else:
                fragment = _render_default_context_fragment(fallback_variables, context_dict).strip()
                
            if fragment:
                header, _, body = fragment.partition("\n")
                header = header.strip() or "Context Variables"
                body = body.strip()
                if not body:
                    body = "null"
                context_block = f"{header}:\n{body}"
            else:
                context_block = "Context Variables:\nnull"
                
            question_line = "What would you like to automate?"
            final = f"{question_line}\n\n{context_block}".strip()
            
            logger.debug(f"[InterviewAgent][HOOK] First message prepared: {final!r}")
            
            if isinstance(message, dict):
                updated = dict(message)
                updated["content"] = final
                return updated
            return final
            
        except Exception as hook_err:  # pragma: no cover
            logger.error(f"[InterviewAgent][HOOK] Error in message hook: {hook_err}", exc_info=True)
            return message

    return _hook

# ==============================================================================
# END INTERVIEWAGENT TESTING UTILITIES
# ==============================================================================


async def create_agents(
    workflow_name: str,
    context_variables=None,
    cache_seed: Optional[int] = None,
) -> Dict[str, ConversableAgent]:
    """Create ConversableAgent instances for a workflow."""

    logger.info(f"[AGENTS] Creating agents for workflow: {workflow_name}")
    from time import perf_counter

    start_time = perf_counter()
    workflow_config = workflow_manager.get_config(workflow_name) or {}
    agent_configs = workflow_config.get("agents", {})
    if "agents" in agent_configs:
        agent_configs = agent_configs["agents"]

    # Support the canonical JSON form used by most workflows:
    #   {"agents": [{"name": "AgentA", ...}, {"name": "AgentB", ...}]}
    # Internally we normalize to a mapping of agent_name -> agent_config.
    if isinstance(agent_configs, list):
        normalized: Dict[str, Any] = {}
        for item in agent_configs:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            normalized[name.strip()] = item
        agent_configs = normalized

    if not isinstance(agent_configs, dict):
        logger.warning(f"[AGENTS] Invalid agents config shape for '{workflow_name}': {type(agent_configs)}")
        agent_configs = {}

    try:
        from ..validation.llm_config import get_llm_config as _get_base_llm_config

        extra = {"cache_seed": cache_seed} if cache_seed is not None else None
        _, base_llm_config = await _get_base_llm_config(stream=True, extra_config=extra)
    except Exception as err:
        logger.error(f"[AGENTS] Failed to load base LLM config: {err}")
        return {}

    try:
        from .tools import load_agent_tool_functions

        agent_tool_functions = load_agent_tool_functions(workflow_name)
    except Exception as tool_err:
        logger.warning(f"[AGENTS] Failed loading agent tool functions: {tool_err}")
        agent_tool_functions = {}

    try:
        structured_registry = get_structured_outputs_for_workflow(workflow_name)
    except Exception as so_err:
        structured_registry = {}
        logger.debug(f"[AGENTS] Structured outputs unavailable for '{workflow_name}': {so_err}")

    if context_variables is not None:
        try:
            context_dict: Dict[str, Any] = _context_to_dict(context_variables)
            logger.debug(f"[AGENTS] context_variables snapshot: {context_dict}")
        except Exception as ctx_err:
            logger.debug(f"[AGENTS] context_variables snapshot unavailable: {ctx_err}")
            context_dict = {}
    else:
        context_dict = {}
    exposures_map = (
        getattr(context_variables, "_mozaiks_context_exposures", {}) if context_variables is not None else {}
    )
    agent_plan_map = (
        getattr(context_variables, "_mozaiks_context_agents", {}) if context_variables is not None else {}
    )

    agents: Dict[str, ConversableAgent] = {}

    for agent_name, agent_config in agent_configs.items():
        try:
            from ..outputs.structured import get_llm_for_workflow as _get_structured_llm

            extra = {"cache_seed": cache_seed} if cache_seed is not None else None
            _, llm_config = await _get_structured_llm(
                workflow_name,
                "base",
                agent_name=agent_name,
                extra_config=extra,
            )
        except Exception:
            llm_config = base_llm_config

        auto_tool_mode = bool(agent_config.get("auto_tool_mode"))
        structured_model_cls = structured_registry.get(agent_name) if structured_registry else None
        if auto_tool_mode and structured_model_cls is None:
            raise ValueError(
                f"[AGENTS] auto_tool_mode enabled for '{agent_name}' but no structured output model is registered"
            )

        agent_functions = [] if auto_tool_mode else agent_tool_functions.get(agent_name, [])
        for idx, fn in enumerate(agent_functions):
            if not callable(fn):
                logger.error(
                    f"[AGENTS] Tool function at index {idx} for agent '{agent_name}' is not callable: {fn}"
                )

        if isinstance(llm_config, dict):
            if "tools" not in llm_config:
                llm_config["tools"] = []
            elif auto_tool_mode:
                llm_config["tools"] = []

        # Try prompt_sections first (fixed structure - enforces standardization)
        prompt_sections = agent_config.get("prompt_sections")
        # Fallback to prompt_sections_custom (flexible array - adapts to any structure)
        if not prompt_sections:
            prompt_sections = agent_config.get("prompt_sections_custom")
        
        system_message: str
        if prompt_sections:
            # Handles both dict (PromptSections) and list (PromptSectionContent[])
            # Runtime adapts to whatever structure is provided
            system_message = _compose_prompt_sections(prompt_sections)
        else:
            # Final fallback for agents still using system_message string directly
            system_message = agent_config.get("system_message", "You are a helpful AI assistant.")
        agent_exposures = []
        if isinstance(exposures_map, dict):
            agent_exposures = exposures_map.get(agent_name, []) or []

        agent_plan = None
        if isinstance(agent_plan_map, dict):
            agent_plan = agent_plan_map.get(agent_name)
        agent_variables = list(getattr(agent_plan, "variables", []) or [])

        base_system_message = system_message
        update_hooks: List[Callable[..., Any] | UpdateSystemMessage] = []
        if agent_exposures:
            system_message = _apply_context_exposures(
                base_system_message,
                agent_exposures,
                context_dict,
                agent_variables,
            )
            exposure_hook = _build_exposure_update_hook(
                agent_name,
                base_system_message,
                agent_exposures,
                agent_variables,
            )
            if exposure_hook:
                update_hooks.append(exposure_hook)
        else:
            system_message = base_system_message

        # Load update_agent_state hooks from hooks.json for this agent
        # CRITICAL: These must be added BEFORE agent construction to work with AG2's update_agent_state_before_reply
        try:
            from ..execution.hooks import _resolve_import
            from pathlib import Path
            
            hooks_json_path = Path("workflows") / workflow_name / "hooks.json"
            if hooks_json_path.exists():
                import json
                with open(hooks_json_path, 'r', encoding='utf-8') as f:
                    hooks_data = json.load(f) or {}
                hooks_entries = hooks_data.get("hooks") or []
                
                for entry in hooks_entries:
                    if (isinstance(entry, dict) and 
                        entry.get("hook_type") == "update_agent_state" and 
                        entry.get("hook_agent") == agent_name):
                        
                        file_value = entry.get("filename")
                        fn_value = entry.get("function")
                        
                        if file_value and fn_value:
                            workflow_path = Path("workflows") / workflow_name
                            fn, qual = _resolve_import(workflow_name, file_value, fn_value, workflow_path)
                            if fn:
                                update_hooks.append(fn)
                                logger.debug(f"[AGENTS] Pre-loaded update_agent_state hook {qual} for {agent_name}")
        except Exception as hook_load_err:
            logger.debug(f"[AGENTS] Failed to pre-load update_agent_state hooks for {agent_name}: {hook_load_err}")

        # ##INTERVIEWAGENT## TESTING MODE - Build auto-NEXT hook (REMOVE FOR PRODUCTION)
        interview_message_hook = None
        if agent_name == "InterviewAgent":
            interview_message_hook = _build_interview_message_hook(agent_exposures, agent_variables)
            logger.debug(f"[AGENTS] Built interview message hook for InterviewAgent (auto-NEXT enabled for testing)")
        # ##INTERVIEWAGENT## END
        
        try:
            raw_human_mode = agent_config.get("human_input_mode")
            if raw_human_mode and str(raw_human_mode).upper() not in ("", "NEVER", "NONE"):
                logger.debug(
                    f"[AGENTS] Ignoring configured human_input_mode {raw_human_mode} for {agent_name}; enforcing NEVER"
                )
            human_input_mode = "NEVER"

            agent = ConversableAgent(
                name=agent_name,
                system_message=system_message,
                llm_config=llm_config,
                human_input_mode=human_input_mode,
                max_consecutive_auto_reply=agent_config.get("max_consecutive_auto_reply", 2),
                functions=agent_functions,
                context_variables=context_variables,
                update_agent_state_before_reply=update_hooks or None,
            )
            if isinstance(prompt_sections, Sequence) and prompt_sections:
                setattr(agent, "_mozaiks_prompt_sections", prompt_sections)
            
            # ##INTERVIEWAGENT## TESTING MODE - Register auto-NEXT hook (REMOVE FOR PRODUCTION)
            if agent_name == "InterviewAgent" and interview_message_hook:
                try:
                    agent.register_hook("process_message_before_send", interview_message_hook)
                    logger.info(f"[AGENTS][HOOK] ✓ Registered process_message_before_send hook for InterviewAgent (auto-NEXT enabled)")
                except Exception as hook_reg_err:
                    logger.error(f"[AGENTS][HOOK] Failed to register InterviewAgent message hook: {hook_reg_err}", exc_info=True)
            # ##INTERVIEWAGENT## END
            
        except Exception as err:
            logger.error(f"[AGENTS] CRITICAL ERROR creating ConversableAgent {agent_name}: {err}")
            raise

        # ==============================================================================
        # IMAGE GENERATION CAPABILITY (AG2 addon)
        # ==============================================================================
        if agent_config.get("image_generation_enabled", False):
            logger.info(f"[AGENTS][CAPABILITY] Image generation enabled for {agent_name} - attaching AG2 capability")
            
            try:
                # Import AG2 image generation components
                from autogen.agentchat.contrib.capabilities import generate_images
                
                logger.debug(f"[AGENTS][CAPABILITY] Imported AG2 generate_images module for {agent_name}")
                
                # Load DALL-E specific config
                from ..validation.llm_config import get_dalle_llm_config
                dalle_config = await get_dalle_llm_config(cache_seed=cache_seed)
                
                logger.info(
                    f"[AGENTS][CAPABILITY] Built DALL-E config for {agent_name}: "
                    f"model={dalle_config['config_list'][0].get('model')}"
                )
                
                # Create DALL-E image generator
                dalle_gen = generate_images.DalleImageGenerator(
                    llm_config=dalle_config,
                    resolution="1024x1024",  # Default, can be made configurable
                    quality="standard",       # Default, can be made configurable
                    num_images=1
                )
                
                logger.debug(f"[AGENTS][CAPABILITY] Created DalleImageGenerator for {agent_name}")
                
                # Create image generation capability
                image_capability = generate_images.ImageGeneration(
                    image_generator=dalle_gen,
                    text_analyzer_llm_config=llm_config,  # Use main config for text analysis
                    verbosity=1  # Set to 2 for full debug logs
                )
                
                logger.debug(f"[AGENTS][CAPABILITY] Created ImageGeneration capability for {agent_name}")
                
                # Attach capability to agent
                image_capability.add_to_agent(agent)
                
                logger.info(
                    f"[AGENTS][CAPABILITY] ✓ Successfully attached image generation capability to {agent_name} "
                    f"(DALL-E model={dalle_config['config_list'][0].get('model')}, resolution=1024x1024)"
                )
                
                # Mark agent with capability flag for runtime introspection
                setattr(agent, "_mozaiks_has_image_generation", True)
                
            except ImportError as imp_err:
                logger.error(
                    f"[AGENTS][CAPABILITY] Failed to import AG2 image generation for {agent_name}: {imp_err}. "
                    f"Install with: pip install ag2[lmm,openai]"
                )
                raise
            except Exception as cap_err:
                logger.error(
                    f"[AGENTS][CAPABILITY] Failed to attach image generation capability to {agent_name}: {cap_err}",
                    exc_info=True
                )
                raise

        setattr(agent, "_mozaiks_auto_tool_mode", auto_tool_mode)
        if structured_model_cls is not None:
            try:
                model_name = getattr(structured_model_cls, "__name__", None)
            except Exception:
                model_name = None
            if model_name:
                setattr(agent, "_mozaiks_structured_model_name", model_name)
            setattr(agent, "_mozaiks_structured_model_cls", structured_model_cls)
        setattr(agent, "_mozaiks_base_system_message", base_system_message)
        agents[agent_name] = agent

    duration = perf_counter() - start_time
    logger.info(f"[AGENTS] Created {len(agents)} agents for '{workflow_name}' in {duration:.2f}s")

    try:
        from logs.logging_config import get_workflow_session_logger

        workflow_logger = get_workflow_session_logger(workflow_name)
        total_tools = sum(len(tools) for tools in agent_tool_functions.values())
        workflow_logger.log_tool_binding_summary("ALL_AGENTS", total_tools, list(agent_tool_functions.keys()))
    except Exception:
        logger.debug("[AGENTS] Tool binding summary skipped")

    try:
        from ..workflow_manager import get_workflow_manager

        wm = get_workflow_manager()
        already_loaded = workflow_name in getattr(wm, "_hooks_loaded_workflows", set())
        registered = wm.register_hooks(workflow_name, agents, force=False)
        if registered:
            logger.info(
                f"[HOOKS] Registered {len(registered)} hooks for '{workflow_name}' (already_loaded={already_loaded})"
            )
    except Exception as hook_err:  # pragma: no cover
        logger.warning(f"[HOOKS] Failed to register hooks for '{workflow_name}': {hook_err}")

    return agents


# ------------------------------------------------------------------
# RUNTIME INSPECTION UTILITIES
# ------------------------------------------------------------------

def list_agent_hooks(agent: Any) -> Dict[str, List[str]]:
    """Return a mapping of hook_type -> list of function names for a given agent."""

    out: Dict[str, List[str]] = {}
    try:
        for attr in ("_hooks", "hooks"):
            if hasattr(agent, attr):
                raw = getattr(agent, attr)
                if isinstance(raw, dict):
                    for htype, fns in raw.items():
                        names: List[str] = []
                        try:
                            for fn in fns or []:  # type: ignore
                                names.append(getattr(fn, "__name__", repr(fn)))
                        except Exception:
                            names.append("<error>")
                        out[htype] = names
                break
    except Exception:
        pass
    return out


def list_hooks_for_workflow(agents: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
    """Return hooks per agent for an agents dict."""

    return {name: list_agent_hooks(agent) for name, agent in agents.items()}


__all__ = [
    "create_agents",
    "extract_images_from_conversation",
    "list_agent_hooks",
    "list_hooks_for_workflow",
]



