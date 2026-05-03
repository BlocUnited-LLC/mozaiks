# ==============================================================================
# FILE: mozaiksai/core/workflow/orchestration_patterns.py
# DESCRIPTION: COMPLETE AG2 execution engine - Single-responsibility pattern for all workflow orchestration
# ==============================================================================

"""
mozaiksai Orchestration Engine (organized)

Purpose
- Single entry point to run a workflow using AG2 patterns with streaming, tools, persistence, and perforamnce.

Sections (skim map)
- Logging setup (chat/workflow/perf)
- run_workflow_orchestration: main orchestration contract and steps
- create_orchestration_pattern: AG2 pattern factory
- logging helpers: agent message details and full conversation logging
"""

from typing import Dict, List, Optional, Any, Callable, Tuple
import os
import uuid
from datetime import datetime, UTC
import logging
from time import perf_counter
import asyncio
import inspect  # used in _build_context_blocking
import os as _os
import json
from collections import Counter

from pydantic import ValidationError

from autogen import ConversableAgent, UserProxyAgent
from autogen.events.agent_events import (
    TextEvent,
    InputRequestEvent,
    SelectSpeakerEvent,
    RunCompletionEvent,
)
from mozaiksai.core.workflow.outputs.structured import get_structured_outputs_for_workflow
from mozaiksai.core.data.persistence import AG2PersistenceManager as _PM
from mozaiksai.core.events.event_serialization import (
    build_ui_event_payload as unified_build_ui_event_payload,
    EventBuildContext as UnifiedEventBuildContext,
    serialize_event_content,
)

from ..data.persistence import AG2PersistenceManager
from .execution import create_termination_handler
from .context import DerivedContextManager
from logs.logging_config import get_workflow_logger
from logs.runtime_artifacts import get_agent_outputs_dir
from mozaiksai.core.observability.ag2_runtime_logger import ag2_logging_session
from mozaiksai.core.observability.performance_manager import get_performance_manager

from .validation import SENTINEL_STATUS

# Extracted modules for separation of concerns
from .messages import (
    normalize_to_strict_ag2 as _normalize_to_strict_ag2,
    normalize_text_content as _normalize_text_content,
    extract_agent_name as _extract_agent_name,
    safe_context_snapshot as _safe_context_snapshot,
)
from .execution import create_ag2_pattern
from .orchestration_utils import (
    _normalize_human_in_the_loop,
    _load_workflow_config,
    _safe_float_value,
    _reconcile_final_usage,
    log_conversation_to_agent_chat_file,
)

logger = logging.getLogger(__name__)

# Consolidated logging with optimized workflow logger
chat_logger = get_workflow_logger("orchestration")
workflow_logger = get_workflow_logger("orchestration")
performance_logger = get_workflow_logger("performance.orchestration")


__all__ = [
    'run_workflow_orchestration',
    'create_ag2_pattern'
]


def _make_static_greeting_reply(greeting: str):
    """One-shot register_reply function for UserDriven workflows.

    Returns the static greeting on first call (no LLM), then falls through
    to normal agent behavior on all subsequent calls.  This makes the
    greeting part of AG2's native transcript so resume/replay works
    automatically.
    """
    fired = {"done": False}

    def _reply_func(recipient, messages, sender, config):
        if fired["done"]:
            return False, None  # fall through to LLM
        fired["done"] = True
        return True, greeting

    return _reply_func

# ===================================================================
# AG2 INTERNAL LOGGING CONFIGURATION
# ===================================================================
# Set AG2 internal logging to INFO level for production
logging.getLogger("autogen.agentchat").setLevel(logging.INFO)
logging.getLogger("autogen.io").setLevel(logging.INFO)
logging.getLogger("autogen.agentchat.group").setLevel(logging.INFO)

# ===================================================================
# NOTE: Helper functions have been extracted to separate modules:
# - orchestration_utils.py: Config loading, usage reconciliation, task management
# - message_utils.py: Message normalization, text extraction, agent name resolution
# - pattern_factory.py: AG2 pattern creation
# ===================================================================

async def _resume_or_initialize_chat(
    persistence_manager: AG2PersistenceManager,
    termination_handler,
    config: Dict[str, Any],
    chat_id: str,
    app_id: str,
    workflow_name: str,
    user_id: Optional[str],
    initial_message: Optional[str],
    initial_agent_name: Optional[str],
    wf_logger,
):
    async def _persist_seed_messages(seed_messages: List[Dict[str, Any]], *, reason: str) -> None:
        if not seed_messages:
            return
        try:
            await persistence_manager.persist_initial_messages(
                chat_id=chat_id,
                app_id=app_id,
                messages=seed_messages,
            )
        except Exception as persist_err:  # pragma: no cover
            wf_logger.debug(f" Failed to persist {reason} for {chat_id}: {persist_err}")

    resumed_messages = await persistence_manager.resume_chat(chat_id, app_id) or []
    resume_raw_count = len(resumed_messages)
    initial_messages: List[Dict[str, Any]] = []

    # Strip general-mode (non-AG2) chatter before handing the transcript back to AG2 orchestration.
    filtered_resumed_messages: List[Dict[str, Any]] = []
    skipped_general = 0
    for msg in resumed_messages:
        metadata = msg.get("metadata") if isinstance(msg, dict) else None
        source = metadata.get("source") if isinstance(metadata, dict) else None
        if source == "general_agent":
            skipped_general += 1
            continue
        filtered_resumed_messages.append(msg)

    if skipped_general:
        wf_logger.info(
            " [RESUME] Ignored %s general-mode messages while preparing workflow resume",
            skipped_general,
        )

    resumed_messages = filtered_resumed_messages
    effective_resume_count = len(resumed_messages)

    # Determine if the resumed messages actually constitute a prior conversation.
    # We ignore purely system/context/metadata scaffolding so brand-new chats created
    # earlier (e.g. by a pre-flight ping) are not misclassified as a resume.
    meaningful_roles = {"user", "assistant", "agent", "tool"}
    meaningful_messages: List[Dict[str, Any]] = []
    for m in resumed_messages:
        role = m.get("role") if isinstance(m, dict) else None
        if role in meaningful_roles:
            meaningful_messages.append(m)

    resume_valid = effective_resume_count > 0 and len(meaningful_messages) > 0

    if resume_valid:
        wf_logger.info(
            f" [RESUME_DETECT] Resuming chat {chat_id}: total_messages={effective_resume_count} meaningful={len(meaningful_messages)}"
        )
        initial_messages = list(resumed_messages)
        if initial_message:
            seed_message = {
                "role": "user",
                "name": "user",
                "content": initial_message,
                "_mozaiks_seed_kind": "initial_message",
            }
            initial_messages.append(seed_message)
            await _persist_seed_messages([seed_message], reason="resume seed message")
    else:
        if resume_raw_count > 0:
            wf_logger.info(
                f" [RESUME_DETECT] Discarding resume for chat {chat_id}: only {resume_raw_count} scaffolding/general messages (meaningful=0). Treating as NEW."
            )
        else:
            wf_logger.info(f" [RESUME_DETECT] No prior messages for chat {chat_id}. Starting NEW chat.")

        resumed_messages = []  # normalize to empty for downstream checks
        if initial_message:
            initial_messages.append(
                {
                    "role": "user",
                    "name": "user",
                    "content": initial_message,
                    "_mozaiks_seed_kind": "initial_message",
                }
            )

        current_user_id = user_id or "system_user"
        if not user_id:
            logger.warning(f"Starting chat {chat_id} without a specific user_id. Defaulting to 'system_user'.")

        try:
            await persistence_manager.create_chat_session(
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                user_id=current_user_id,
            )
        except Exception as cs_err:
            wf_logger.error(f" Failed to create chat session doc for {chat_id}: {cs_err}")

        try:
            await termination_handler.on_conversation_start(user_id=current_user_id)
            logger.info(" Termination handler started for new conversation")
        except Exception as start_err:
            logger.error(f" Termination handler start failed: {start_err}")

        await _persist_seed_messages(initial_messages, reason="initial seed messages")

    if not initial_messages:
        # UserDriven greeting is handled by register_reply on the initial
        # agent (AG2-native). Do NOT seed it here — let AG2 emit it as a
        # normal TextEvent so resume/replay track it natively.

        seed = config.get("initial_message")
        if seed:
            initial_messages = [
                {"role": "user", "name": "user", "content": seed, "_mozaiks_seed_kind": "initial_message"}
            ]
            await _persist_seed_messages(initial_messages, reason="config seed message")
        elif config.get("workflow_startup_mode", "").strip().lower() == "userdriven":
            # UserDriven needs a synthetic trigger so AG2 can start the group
            # chat loop.  The register_reply on the initial agent intercepts
            # before the LLM is ever called and returns the static greeting.
            initial_messages = [
                {"role": "user", "name": "user", "content": ".", "_mozaiks_seed_kind": "userdriven_trigger"}
            ]

    return resumed_messages, initial_messages


async def _load_llm_config(workflow_name: str, wf_logger, workflow_name_upper: str, *, cache_seed: Optional[int] = None):
    from .outputs.structured import get_llm_for_workflow
    try:
        extra = {"cache_seed": cache_seed} if cache_seed is not None else None
        _, llm_config = await get_llm_for_workflow(workflow_name, "base", extra_config=extra)
        wf_logger.info(f" [{workflow_name_upper}] Using workflow-specific LLM config")
    except (ValueError, FileNotFoundError):
        from .llm_config import get_llm_config
        extra = {"cache_seed": cache_seed} if cache_seed is not None else None
        _, llm_config = await get_llm_config(extra_config=extra)
        wf_logger.info(f" [{workflow_name_upper}] Using default LLM config")
    return llm_config


async def _build_context_blocking(
    context_factory: Optional[Callable],
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: Optional[str],
    wf_logger,
    workflow_name_upper: str,
    frontend_context: Optional[Dict[str, Any]] = None,
):
    """Build context and wait for it to be fully populated before first turn.

    - If a context_factory is provided, supports both sync and async factories.
    - If frontend_context is provided, it is merged into the context with 'ui_' prefix.
    """
    try:
        if context_factory:
            result = context_factory()
            if inspect.isawaitable(result):
                ctx = await result
            else:
                ctx = result
        else:
            from .context.variables import _load_context_async
            # Use the internal async loader directly to ensure blocking population
            ctx = await _load_context_async(workflow_name, app_id)
        
        # Merge frontend context with ui_ prefix (avoids collisions with backend context)
        if frontend_context and isinstance(frontend_context, dict) and ctx is not None:
            for key, value in frontend_context.items():
                prefixed_key = f"ui_{key}" if not key.startswith("ui_") else key
                try:
                    # Use set() method if available (RuntimeContextVariables/AG2ContextVariables)
                    if hasattr(ctx, 'set'):
                        ctx.set(prefixed_key, value)
                    elif hasattr(ctx, '__setitem__'):
                        ctx[prefixed_key] = value
                    wf_logger.info(f" [{workflow_name_upper}] Merged frontend context: {prefixed_key}")
                except Exception as fc_err:
                    wf_logger.warning(f" [{workflow_name_upper}] Failed to set frontend context {prefixed_key}: {fc_err}")
        
        return ctx
    except Exception as e:
        wf_logger.error(f" [{workflow_name_upper}] Context load failed: {e}")
        return None


async def _create_agents(agents_factory: Optional[Callable], workflow_name: str, context_variables=None, *, cache_seed: Optional[int] = None):
    """Create agents for the workflow following AG2 patterns.

    Clean API: agents_factory(workflow_name, context_variables, cache_seed)
    """
    if agents_factory:
        return await agents_factory(workflow_name, context_variables, cache_seed)
    from .agents import create_agents
    return await create_agents(workflow_name, context_variables=context_variables, cache_seed=cache_seed)


def _ensure_user_proxy(
    agents: Dict[str, ConversableAgent],
    config: Dict[str, Any],
    workflow_startup_mode: str,
    llm_config: Dict[str, Any],
    human_in_loop: bool,
) -> Tuple[Dict[str, ConversableAgent], Optional[UserProxyAgent], bool]:
    user_proxy_agent: Optional[UserProxyAgent] = None
    user_proxy_exists = any(
        hasattr(a, "name") and a.name.lower() in ("user", "userproxy", "userproxyagent")
        for a in agents.values()
    )
    if not user_proxy_exists:
        human_in_loop_flag = _normalize_human_in_the_loop(config.get("human_in_the_loop", False))
        # ChatUI (and the HTTP transport) provide real user input and should never trigger
        # AG2's terminal/CLI-style feedback prompts ("Please give feedback to chat_manager...").
        # Keep the auto-created user proxy non-interactive.
        if workflow_startup_mode in {"BackendOnly"} or not human_in_loop_flag:
            human_input_mode = "NEVER"
        else:
            # AgentDriven and UserDriven both need InputRequestEvent so the
            # runtime pauses for real user input over WebSocket.
            human_input_mode = "TERMINATE"
        user_proxy_agent = UserProxyAgent(
            name="user",
            human_input_mode=human_input_mode,
            max_consecutive_auto_reply=0,
            code_execution_config={"use_docker": False},
            system_message="You are a helpful user proxy.",
            llm_config=llm_config,
        )
        agents["user"] = user_proxy_agent
        human_in_loop = human_in_loop_flag
    else:
        for a in agents.values():
            if hasattr(a, "name") and a.name.lower() in ("user", "userproxy", "userproxyagent"):
                user_proxy_agent = a  # type: ignore[assignment]
                break
    return agents, user_proxy_agent, human_in_loop


def _resolve_initiating_agent(agents: Dict[str, ConversableAgent], initial_agent_name: Optional[str], workflow_name: str):
    initiating_agent = None
    if initial_agent_name:
        initiating_agent = agents.get(initial_agent_name)
        if not initiating_agent:
            for a in agents.values():
                if getattr(a, "name", None) == initial_agent_name:
                    initiating_agent = a
                    break
    if not initiating_agent:
        initiating_agent = next(iter(agents.values())) if agents else None
        if not initiating_agent:
            raise ValueError(f"No agents available for workflow {workflow_name}")
    return initiating_agent


def _filter_agents_for_pattern(
    agents: Dict[str, ConversableAgent],
    human_in_loop: bool,
    user_proxy_agent: Optional[UserProxyAgent]
) -> List[ConversableAgent]:
    """Filter agents list for AG2 pattern, excluding user proxy if handled separately."""
    agents_list = []
    for name, agent in agents.items():
        # Skip user proxy if it's handled separately in human-in-the-loop mode
        if name == "user" and human_in_loop and user_proxy_agent is not None:
            continue
        agents_list.append(agent)
    return agents_list


def _convert_to_ag2_context(context_variables: Any, wf_logger) -> Any:
    """Convert context variables to AG2 ContextVariables instance."""
    from autogen.agentchat.group import ContextVariables as AG2ContextVariables

    if context_variables is None:
        return AG2ContextVariables()
    elif isinstance(context_variables, AG2ContextVariables):
        return context_variables
    else:
        # Convert from our context system to AG2 ContextVariables
        try:
            if hasattr(context_variables, 'to_dict'):
                return AG2ContextVariables(data=context_variables.to_dict())
            elif isinstance(context_variables, dict):
                return AG2ContextVariables(data=context_variables)
            else:
                return AG2ContextVariables(data={"value": context_variables})
        except Exception as _cv_err:
            wf_logger.warning(f" [CONTEXT] Context conversion failed: {_cv_err}")
            return AG2ContextVariables()


async def _create_ag2_pattern(
    orchestration_pattern: str,
    workflow_name: str,
    agents: Dict[str, ConversableAgent],
    initiating_agent: ConversableAgent,
    user_proxy_agent: Optional[UserProxyAgent],
    human_in_loop: bool,
    context_variables: Any,
    llm_config: Dict[str, Any],
    handoffs_factory: Optional[Callable],
    wf_logger,
    chat_id: str,
    app_id: str,
    user_id: Optional[str],
):
    """Create AG2 Pattern with proper context variables integration."""
    # Convert agents dict to list for AG2 pattern (exclude user proxy if handled separately)
    agents_list = _filter_agents_for_pattern(agents, human_in_loop, user_proxy_agent)
    
    # Ensure we have proper AG2 ContextVariables instance
    ag2_context = _convert_to_ag2_context(context_variables, wf_logger)
    
    # Ensure core WebSocket path parameters are always available
    # These may already be set by _build_context_blocking, but we ensure they're present
    if not ag2_context.get("workflow_name"):
        ag2_context.set("workflow_name", workflow_name)
    if not ag2_context.get("app_id"):
        ag2_context.set("app_id", app_id)
    if not ag2_context.get("chat_id"):
        ag2_context.set("chat_id", chat_id)
    # Optionally attach user_id if provided
    if user_id and not ag2_context.get("user_id"):
        ag2_context.set("user_id", user_id)

    # Log final context state with emphasis on routing keys
    context_keys = list(ag2_context.data.keys())
    wf_logger.info(
        f"[CONTEXT] AG2 ContextVariables ready | total_keys={len(context_keys)} | "
        f"workflow_name={ag2_context.get('workflow_name')} | "
        f"app_id={ag2_context.get('app_id')} | "
        f"chat_id={ag2_context.get('chat_id')} | "
        f"user_id={ag2_context.get('user_id')}"
    )

    # Create AG2 Pattern following proper constructor signature
    pattern = create_ag2_pattern(
        pattern_name=orchestration_pattern,
        initial_agent=initiating_agent,
        agents=agents_list,
        user_agent=user_proxy_agent,
        context_variables=ag2_context,
        group_manager_args={"llm_config": llm_config},
    )
    try:
        snapshot = _safe_context_snapshot(ag2_context)
        wf_logger.info(
            f" [CONTEXT_INIT] AG2 context constructed | keys={list(snapshot.keys())}"
        )
        wf_logger.debug(
            f" [CONTEXT_INIT_DEBUG] snapshot={snapshot}"
        )
    except Exception as _snap_log_err:  # pragma: no cover
        wf_logger.debug(f" [CONTEXT_INIT] snapshot logging failed: {_snap_log_err}")
    try:
        # Light sanity: if pattern exposes group_manager/context_variables, log keys
        gm = getattr(pattern, "group_manager", None)
        if gm and hasattr(gm, "context_variables"):
            cv = getattr(gm, "context_variables")
            keys = list(getattr(cv, "data", {}).keys()) if hasattr(cv, "data") else []
            wf_logger.info(f" [PATTERN] ContextVariables attached to group manager | keys={keys}")
            try:
                wf_logger.debug(f" [PATTERN_DEBUG] group_manager.context snapshot={_safe_context_snapshot(cv)}")
            except Exception:
                pass
        else:
            wf_logger.debug("[PATTERN] Group manager or context_variables attribute not available for logging")
    except Exception as _pat_log_err:
        wf_logger.debug(f"[PATTERN] Context logging skipped: {_pat_log_err}")
    if orchestration_pattern == "DefaultPattern":
        try:
            if handoffs_factory:
                await handoffs_factory(agents)
            else:
                from .agents.handoffs import wire_handoffs_with_debugging
                wire_handoffs_with_debugging(workflow_name, agents)
        except Exception as he:
            wf_logger.warning(f"Handoffs wiring failed: {he}")
    return pattern, ag2_context


# ===================================================================
# AG2 EVENT STREAM PROCESSING
# ===================================================================
async def _stream_events(
    pattern,
    resumed_messages,
    initial_messages,
    max_turns: int,
    agents: Dict[str, ConversableAgent],
    chat_id: str,
    app_id: str,
    workflow_name: str,
    wf_logger,
    workflow_name_upper: str,
    transport,
    user_id: Optional[str],
    persistence_manager: AG2PersistenceManager,
    perf_mgr,
    derived_context_manager: Optional[DerivedContextManager] = None,
    lifecycle_manager = None,
):
    """
    Stream AG2 events using handler dispatch architecture.

    Uses the stream/ module for clean event handler separation.
    See stream/handlers/ for individual event type implementations.
    """
    from .stream import EventStreamProcessor, StreamContext, StreamState
    from .outputs.structured import get_structured_outputs_for_workflow
    from .workflow_manager import workflow_manager
    from collections import Counter
    from autogen.agentchat import a_run_group_chat_iter
    import inspect as _inspect

    async def _resolve_run_iter_response(value: Any) -> Any:
        """
        Normalize AG2 run/resume return types across versions.

        Some versions return an awaitable, others return AsyncRunIterResponse
        directly. This helper supports both.
        """
        if _inspect.isawaitable(value):
            return await value
        return value

    # Get structured outputs registry
    try:
        structured_registry = get_structured_outputs_for_workflow(workflow_name)
    except Exception as so_err:
        structured_registry = {}
        wf_logger.debug(f"[{workflow_name_upper}] Structured outputs unavailable: {so_err}")

    # Pydantic-backed validated outputs are a separate concern from the
    # downstream consumers that react to them. MFJ and auto-tools both
    # subscribe to the validated-output event, but neither defines what a
    # validated-output agent is.
    validated_output_agents = set(structured_registry.keys())

    # Derive auto-tool agents from tools.yaml (agents with auto_tool_call: true tools)
    auto_tool_agents = workflow_manager.get_auto_tool_agents(workflow_name)

    # Get event dispatcher
    from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

    dispatcher = get_event_dispatcher()

    # Determine resume mode
    resumed_mode = bool(resumed_messages)

    wf_logger.info(
        f" [{'AG2_RESUME' if resumed_mode else 'AG2_RUN'}] "
        f"{'Resuming' if resumed_mode else 'Starting NEW'} chat {chat_id}"
    )
    response = await _resolve_run_iter_response(
        a_run_group_chat_iter(
            pattern=pattern,
            messages=initial_messages,
            max_rounds=max_turns,
        )
    )

    # Build StreamContext
    ctx = StreamContext(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_name,
        user_id=user_id,
        pattern=pattern,
        transport=transport,
        persistence_manager=persistence_manager,
        lifecycle_manager=lifecycle_manager,
        derived_context_manager=derived_context_manager,
        perf_mgr=perf_mgr,
        dispatcher=dispatcher,
        agents=agents,
        structured_registry=structured_registry,
        validated_output_agents=validated_output_agents,
        auto_tool_agents=auto_tool_agents,
        max_turns=max_turns,
        wf_logger=wf_logger,
        workflow_name_upper=workflow_name_upper,
        resumed_mode=resumed_mode,
        initial_messages=initial_messages,
    )

    # Build initial StreamState with seed message tracking
    seed_user_messages = Counter()
    try:
        for seed in initial_messages or []:
            if (
                isinstance(seed, dict)
                and seed.get('role') == 'user'
                and seed.get('_mozaiks_seed_kind') in ('initial_message', 'userdriven_trigger')
            ):
                content = seed.get('content')
                if isinstance(content, str) and content.strip():
                    seed_user_messages[content.strip()] += 1
    except Exception:
        seed_user_messages = Counter()

    state = StreamState(
        seed_user_messages=seed_user_messages,
    )

    # Process event stream using new processor
    processor = EventStreamProcessor()
    result = await processor.process_stream(response, ctx, state)

    return result


async def run_workflow_orchestration(
    workflow_name: str,
    app_id: str,
    chat_id: str,
    user_id: Optional[str] = None,
    initial_message: Optional[str] = None,
    initial_agent_name_override: Optional[str] = None,
    agents_factory: Optional[Callable] = None,
    context_factory: Optional[Callable] = None,
    handoffs_factory: Optional[Callable] = None,
    **kwargs
) -> Any:
    start_time = perf_counter()
    workflow_name_upper = workflow_name.upper()
    orchestration_pattern = "unknown"
    agents: Dict[str, Any] = {}
    stream_state: Dict[str, Any] = {}
    result_payload: Optional[Dict[str, Any]] = None
    workflow_status_value = 0

    # Create workflow logger for this session  
    wf_lifecycle_logger = get_workflow_logger(workflow_name, chat_id=chat_id)
    
    wf_logger = get_workflow_logger(workflow_name, chat_id=chat_id, app_id=app_id)
    
    # Log orchestration start with session summary instead of verbose details
    logger.info(f" [ORCHESTRATION] Starting {workflow_name} workflow")
    


    # Persistence / transport / termination handler 
    persistence_manager = AG2PersistenceManager()

    from mozaiksai.core.transport.simple_transport import SimpleTransport
    transport = await SimpleTransport.get_instance()
    if not transport:
        raise RuntimeError(f"SimpleTransport instance not available for {workflow_name} workflow")

    termination_handler = create_termination_handler(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_name,
        transport=transport
    )

    result_payload: Optional[Dict[str, Any]] = None
    # Pre-initialize to ensure safe access in final logs even if an early exception occurs
    stream_state: Dict[str, Any] = {}

    # -----------------------------------------------------------------
    # Reconnect handshake (optional) - if client supplies last_seen_sequence
    # kwargs key: last_seen_sequence (int). If provided we replay diff of
    # normalized events (sequence > last_seen_sequence) to the UI transport
    # BEFORE starting the AG2 pattern run. This is a best-effort replay; any
    # failures are logged and ignored (live stream then proceeds).
    # -----------------------------------------------------------------

    # Generate trace_id for this workflow session
    import uuid
    trace_id_hex = uuid.uuid4().hex
    logger.debug(f"Generated trace_id for workflow {workflow_name}: {trace_id_hex}")

    perf_mgr = await get_performance_manager()
    await perf_mgr.initialize()
    await perf_mgr.record_workflow_start(chat_id, app_id, workflow_name, user_id or "unknown")
    await perf_mgr.attach_trace_id(chat_id, trace_id_hex)

    # Start AG2 runtime logging for this workflow session and keep it active
    # across the orchestration run so AG2 events (like LLM/tool calls) are captured.
    with ag2_logging_session(chat_id, workflow_name, app_id):
        # Set up realtime token logger for immediate token tracking
        try:
            from mozaiksai.core.observability.realtime_token_logger import get_realtime_token_logger
            realtime_logger = get_realtime_token_logger()
            realtime_logger.set_user(user_id or "unknown")
            realtime_logger.set_active_agent(workflow_name)
            wf_logger.info(f" [REALTIME_TOKENS] Realtime token logging prepared for chat {chat_id}")
        except Exception as rt_err:
            wf_logger.warning(f" [REALTIME_TOKENS] Failed to prepare realtime token logging: {rt_err}")

        try:
            # -----------------------------------------------------------------
            # 1) Load configuration
            # -----------------------------------------------------------------
            cfg = _load_workflow_config(workflow_name)
            config = cfg["config"]
            max_turns = cfg["max_turns"]
            orchestration_pattern = cfg["orchestration_pattern"]
            workflow_startup_mode = cfg["workflow_startup_mode"]
            human_in_loop = cfg["human_in_loop"]
            initial_agent_name = cfg["initial_agent_name"]

            # Adapter-level override: allow a caller to force where AG2 starts/resumes.
            # This is intentionally generic (no workflow-specific knowledge) and is
            # validated later by _resolve_initiating_agent.
            if initial_agent_name_override:
                initial_agent_name = str(initial_agent_name_override)

            # Brief, structured visibility into effective normalized config
            try:
                wf_logger.info(
                    f" [{workflow_name_upper}] CONFIG: workflow_startup_mode={workflow_startup_mode} human_in_loop={human_in_loop} pattern={orchestration_pattern} initial_agent={initial_agent_name}"
                )
            except Exception as _cfg_log_err:  # pragma: no cover
                logger.debug(f"config log failed: {_cfg_log_err}")

            # -----------------------------------------------------------------
            # 2) Resume or start chat
            # -----------------------------------------------------------------
            resumed_messages, initial_messages = await _resume_or_initialize_chat(
                persistence_manager=persistence_manager,
                termination_handler=termination_handler,
                config=config,
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                user_id=user_id,
                initial_message=initial_message,
                initial_agent_name=initial_agent_name,
                wf_logger=wf_logger,
            )

            # Track resume mode early so downstream logging can reference it safely
            resumed_mode = bool(resumed_messages)

            # -----------------------------------------------------------------
            # 3) LLM config (per-chat cache seed)
            # -----------------------------------------------------------------
            try:
                cache_seed = await persistence_manager.get_or_assign_cache_seed(chat_id, app_id)
            except Exception as seed_err:
                cache_seed = None
                wf_logger.debug(f" [{workflow_name_upper}] cache_seed assignment failed for chat {chat_id}: {seed_err}")
            llm_config = await _load_llm_config(workflow_name, wf_logger, workflow_name_upper, cache_seed=cache_seed)

            # -----------------------------------------------------------------
            # 3.5) Structured outputs preload (blocking)
            # -----------------------------------------------------------------
            try:
                from .outputs.structured import load_workflow_structured_outputs as _preload_so
                _preload_so(workflow_name)
                wf_logger.info(f" [{workflow_name_upper}] Structured outputs preloaded")
            except Exception as so_err:
                # Do not fail the run, but surface misconfiguration early
                wf_logger.warning(f" [{workflow_name_upper}] Structured outputs preload failed: {so_err}")

            # Log start
            chat_logger.info(f"[{workflow_name_upper}] WORKFLOW_STARTED chat_id={chat_id} pattern={orchestration_pattern}")
            wf_logger.info(
                "WORKFLOW_STARTED",
                event_type=f"{workflow_name_upper}_WORKFLOW_STARTED",
                description=f"{workflow_name} workflow orchestration initialized",
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                pattern=orchestration_pattern,
                workflow_startup_mode=workflow_startup_mode,
                initial_message_count=len(initial_messages),
                trace_id=trace_id_hex,
            )

            # -----------------------------------------------------------------
            # 4) Context build
            # -----------------------------------------------------------------
            context = None
            context_start = perf_counter()
            
            # Retrieve frontend context from transport connection metadata (set by host app)
            frontend_context = None
            try:
                if transport and hasattr(transport, 'connections') and chat_id in transport.connections:
                    frontend_context = transport.connections[chat_id].get("frontend_context")
                    if frontend_context:
                        wf_logger.info(f" [{workflow_name_upper}] Found frontend context: {list(frontend_context.keys())}")
            except Exception as fc_lookup_err:
                wf_logger.debug(f" [{workflow_name_upper}] Frontend context lookup failed: {fc_lookup_err}")
            
            context = await _build_context_blocking(
                context_factory=context_factory,
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                wf_logger=wf_logger,
                workflow_name_upper=workflow_name_upper,
                frontend_context=frontend_context,
            )

            # Merge persisted session metadata (extra_fields) into context.
            # This enables parent/child correlation and generator-subrun seeding.
            try:
                if context is not None:
                    extra_ctx = await persistence_manager.fetch_chat_session_extra_context(chat_id=chat_id, app_id=app_id)
                    if isinstance(extra_ctx, dict) and extra_ctx:
                        for k, v in extra_ctx.items():
                            try:
                                # Runtime MFJ keys must always override defaults so
                                # resume-router handoffs receive fresh fan-in payload.
                                force_runtime_override = (
                                    isinstance(k, str) and (k.startswith("_mfj_") or k.startswith("mfj_"))
                                )
                                existing = None
                                if hasattr(context, "get"):
                                    existing = context.get(k)  # type: ignore[call-arg]
                                elif hasattr(context, "data") and isinstance(getattr(context, "data"), dict):
                                    existing = getattr(context, "data").get(k)
                                if force_runtime_override or existing is None:
                                    if hasattr(context, "set"):
                                        context.set(k, v)
                                    elif hasattr(context, "__setitem__"):
                                        context[k] = v
                            except Exception:
                                continue

                        # Derive child marker when parent_chat_id exists.
                        try:
                            parent_chat_id = extra_ctx.get("parent_chat_id")
                            if parent_chat_id and hasattr(context, "get") and not context.get("is_child_workflow"):
                                context.set("is_child_workflow", True)
                        except Exception:
                            pass
            except Exception as _seed_err:
                wf_logger.debug(f" [{workflow_name_upper}] Failed merging persisted extra context: {_seed_err}")

            # Permanent runtime variable: does this workflow declare MFJ child runs?
            try:
                if context is not None:
                    from mozaiksai.core.workflow.pack.graph import workflow_has_mid_flight_journeys

                    context.set("has_children", bool(workflow_has_mid_flight_journeys(workflow_name)))
            except Exception:
                pass
            context_time = (perf_counter() - context_start) * 1000
            performance_logger.info(
                "context_load_duration_ms",
                extra={
                    "metric_name": "context_load_duration_ms",
                    "value": float(context_time),
                    "unit": "ms",
                    "workflow_name": workflow_name,
                    "app_id": app_id,
                },
            )

            # -----------------------------------------------------------------
            # 6) Agents creation following AG2 patterns
            # -----------------------------------------------------------------
            agents = await _create_agents(agents_factory, workflow_name, context_variables=context, cache_seed=cache_seed)
            agents = agents or {}
            if not agents:
                raise RuntimeError(f"No agents defined for workflow '{workflow_name}'")

            derived_context_manager = DerivedContextManager(workflow_name, agents, context)
            if derived_context_manager.has_variables():
                derived_context_manager.seed_defaults()

                def _derived_listener(payload: Dict[str, Any]):  # type: ignore
                    try:
                        var_name = payload.get('variable')
                        value = payload.get('value')
                        if not var_name:
                            return
                        if transport:
                            evt = {
                                'kind': 'context_update',
                                'variable': var_name,
                                'value': value,
                            }
                            asyncio.create_task(transport.send_event_to_ui(evt, chat_id))
                    except Exception as _dl_err:  # pragma: no cover
                        wf_logger.debug(f"Derived listener emit failed: {_dl_err}")

                try:
                    derived_context_manager.add_listener(_derived_listener)
                except Exception as _lerr:  # pragma: no cover
                    wf_logger.debug(f"Failed registering derived listener: {_lerr}")
            else:
                derived_context_manager = None

            # Expose derived context manager to transport so UI tool responses can
            # apply declarative ui_response triggers into AG2 ContextVariables.
            if transport and derived_context_manager and hasattr(transport, "register_derived_context_manager"):
                try:
                    transport.register_derived_context_manager(chat_id, derived_context_manager)
                except Exception as _reg_err:  # pragma: no cover
                    wf_logger.debug(f"Failed registering derived context manager with transport: {_reg_err}")

            # Get tool binding data for summary
            from .agents.tools import load_agent_tool_functions
            agent_tools = load_agent_tool_functions(workflow_name)

            try:
                # Produce a concise debug summary of loaded tools per agent
                _tool_summary = {a: [getattr(f, '__name__', '<noname>') for f in funcs] for a, funcs in agent_tools.items()}
                workflow_logger.debug(f"[ORCH][TRACE] Loaded agent tool mapping for {workflow_name}: {_tool_summary}")
            except Exception as _e:  # pragma: no cover
                workflow_logger.debug(f"[ORCH][TRACE] Failed building tool summary: {_e}")
            # Basic sanity: at least one tool across all agents if workflow expects tools
            total_tool_count = sum(len(funcs) for funcs in agent_tools.values())
            wf_logger.info(f" [{workflow_name_upper}] Tools bound across agents: {total_tool_count}")

            # Log consolidated agent setup summary using existing logger
            try:
                wf_logger.info(
                    f" [WORKFLOW_SETUP] {workflow_name}: agents={list(agents.keys())} tools={len(agent_tools)}"
                )
            except Exception as log_err:
                logger.debug(f"Agent setup summary logging failed: {log_err}")

            # -----------------------------------------------------------------
            # 6.5) Hooks readiness snapshot (blocking check via current agents)
            # -----------------------------------------------------------------
            try:
                from .agents import list_hooks_for_workflow as _list_hooks
                hooks_snapshot = _list_hooks(agents)
                total_hooks = sum(len(funcs) for agent_hooks in hooks_snapshot.values() for funcs in agent_hooks.values())
                wf_logger.info(f" [{workflow_name_upper}] Hooks registered across agents: {total_hooks}")
                workflow_logger.debug(f"[ORCH][TRACE] Hooks snapshot: {hooks_snapshot}")
            except Exception as hook_snap_err:  # pragma: no cover
                wf_logger.debug(f"Hooks snapshot failed: {hook_snap_err}")

            # Defer start log until after agents + initiating agent known
            try:
                context_var_count = (len(context) if context is not None and hasattr(context, '__len__') else 0)
            except Exception:
                context_var_count = 0

            wf_logger.debug(
                f" [{workflow_name_upper}] Chat START chat_id={chat_id} agents={len(agents)} max_turns={max_turns} "
                f"workflow_startup_mode={workflow_startup_mode} human_in_loop={human_in_loop} context_vars={context_var_count} resumed={resumed_mode}"
            )

            # -----------------------------------------------------------------
            # Store agents on transport
            try:
                if transport and hasattr(transport, 'connections') and chat_id in transport.connections:
                    transport.connections[chat_id]['agents'] = agents
                    # Expose context for component actions & UI updates
                    if context is not None:
                        transport.connections[chat_id]['context'] = context
            except Exception as _agents_store_err:
                wf_logger.debug(f"agent store failed: {_agents_store_err}")

            # Ensure user proxy presence (always named "user")
            agents, user_proxy_agent, human_in_loop = _ensure_user_proxy(
                agents=agents,
                config=config,
                workflow_startup_mode=workflow_startup_mode,
                llm_config=llm_config,
                human_in_loop=human_in_loop,
            )

            # -----------------------------------------------------------------
            # 7) Initiating agent (explicit or first available)
            # -----------------------------------------------------------------
            initiating_agent = _resolve_initiating_agent(
                agents=agents,
                initial_agent_name=initial_agent_name,
                workflow_name=workflow_name,
            )

            wf_logger.info(
                f" [{workflow_name_upper}] Initial agent resolved: {getattr(initiating_agent,'name',None)}"
            )

            # -----------------------------------------------------------------
            # 7.5) UserDriven static greeting via register_reply (AG2-native)
            # -----------------------------------------------------------------
            # For NEW UserDriven chats, register a one-shot reply on the
            # initial agent that returns the greeting without an LLM call.
            # This prevents AG2 from making an LLM call for the first response.
            # NOTE: ws_protocol.py MAY send the greeting on initial connection,
            # so we check the connection flag to decide whether to suppress.
            if (
                not resumed_mode
                and workflow_startup_mode == "UserDriven"
                and config.get("initial_message_to_user")
            ):
                _greeting = str(config["initial_message_to_user"]).strip()
                if _greeting:
                    # Check if ws_protocol.py already sent the greeting
                    _bootstrap_already_visible = False
                    if transport and hasattr(transport, 'connections') and chat_id:
                        _conn = transport.connections.get(chat_id, {})
                        _bootstrap_already_visible = _conn.get("userdriven_bootstrap_visible", False)

                    if _bootstrap_already_visible:
                        # ws_protocol already sent greeting — flag the dispatcher so
                        # the AG2 transcript echo is emitted as chat.greeting_echo
                        # instead of a duplicate chat.text.
                        _final_greeting = _greeting
                        try:
                            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
                            get_event_dispatcher().mark_greeting_echo(
                                chat_id, getattr(initiating_agent, 'name', None)
                            )
                        except Exception:
                            pass
                        wf_logger.info(
                            f" [{workflow_name_upper}] Greeting already sent by ws_protocol - flagged as echo"
                        )
                    else:
                        # No greeting sent yet (mode switch case) - send it normally
                        _final_greeting = _greeting
                        wf_logger.info(
                            f" [{workflow_name_upper}] No prior greeting - will send via register_reply"
                        )

                    _reply_fn = _make_static_greeting_reply(_final_greeting)
                    initiating_agent.register_reply(
                        [ConversableAgent, None], _reply_fn, position=0,
                    )
                    wf_logger.info(
                        f" [{workflow_name_upper}] Registered static greeting reply on "
                        f"{getattr(initiating_agent, 'name', '?')} (UserDriven, no LLM)"
                    )

            # -----------------------------------------------------------------
            # 7.6) Early select_speaker event for UI thinking indicator
            # -----------------------------------------------------------------
            # Emit a synthetic select_speaker event so the frontend can show
            # a thinking bubble immediately, before AG2 emits its own events.
            if transport and not resumed_mode:
                _init_agent_name = getattr(initiating_agent, 'name', None)
                if _init_agent_name:
                    try:
                        _select_speaker_evt = {
                            "kind": "select_speaker",
                            "agent": _init_agent_name,
                            "source": "workflow_init",
                            "_synthetic": True,
                        }
                        asyncio.create_task(transport.send_event_to_ui(_select_speaker_evt, chat_id))
                        wf_logger.info(
                            f" [{workflow_name_upper}] Emitted early select_speaker for {_init_agent_name}"
                        )
                    except Exception as _ss_err:
                        wf_logger.debug(f" [{workflow_name_upper}] Early select_speaker failed: {_ss_err}")

            # -----------------------------------------------------------------
            # 8) STRICT resume prep: normalize + enforce HIL (no tail stripping)
            # -----------------------------------------------------------------
            initial_messages = _normalize_to_strict_ag2(initial_messages, default_user_name="user")

            # Enforce human-in-the-loop if any user turns are present in history
            if any(m.get("role") == "user" for m in initial_messages):
                human_in_loop = True

            # -----------------------------------------------------------------
            # 9) Pattern creation (AG2 native)
            # -----------------------------------------------------------------
            pattern, ag2_context = await _create_ag2_pattern(
                orchestration_pattern=orchestration_pattern,
                workflow_name=workflow_name,
                agents=agents,
                initiating_agent=initiating_agent,
                user_proxy_agent=user_proxy_agent,
                human_in_loop=human_in_loop,
                context_variables=context,
                llm_config=llm_config,
                handoffs_factory=handoffs_factory,
                wf_logger=wf_logger,
                chat_id=chat_id,
                app_id=app_id,
                user_id=user_id,
            )

            try:
                wf_logger.info(" [CONTEXT_BRIDGE] Pattern created; preparing to register providers")
                gm = getattr(pattern, 'group_manager', None)
                if gm and hasattr(gm, 'context_variables'):
                    wf_logger.debug(
                        f" [CONTEXT_BRIDGE_DEBUG] group_manager.context id={id(gm.context_variables)} keys={list(getattr(gm.context_variables,'data',{}).keys())}"
                    )
            except Exception as _bridge_err:
                wf_logger.debug(f" [CONTEXT_BRIDGE] logging failed: {_bridge_err}")

            if derived_context_manager:
                # Register the AG2 pattern's context variables as the primary provider
                # This ensures derived variables update the actual context used by AG2
                if hasattr(pattern, "group_manager"):
                    group_manager = getattr(pattern, "group_manager", None)
                    if group_manager and hasattr(group_manager, "context_variables"):
                        pattern_context_vars = getattr(group_manager, "context_variables")
                        derived_context_manager.register_additional_provider(pattern_context_vars)
                        try:
                            wf_logger.info(
                                f" [DERIVED_CONTEXT] Registered group_manager context_variables provider | id={id(pattern_context_vars)} keys={list(getattr(pattern_context_vars,'data',{}).keys())}"
                            )
                        except Exception:
                            wf_logger.info(" [DERIVED_CONTEXT] Registered group_manager context_variables as provider (keys unavailable)")

                # Also register pattern-level context variables if available
                pattern_context = getattr(pattern, "context_variables", None)
                if pattern_context:
                    derived_context_manager.register_additional_provider(pattern_context)
                    try:
                        wf_logger.info(
                            f" [DERIVED_CONTEXT] Registered pattern.context_variables provider | id={id(pattern_context)} keys={list(getattr(pattern_context,'data',{}).keys())}"
                        )
                    except Exception:
                        wf_logger.info(" [DERIVED_CONTEXT] Registered pattern context_variables as provider")

                # Register the ag2_context we created as the primary provider
                # This ensures derived variables can update the same context AG2 uses
                if ag2_context:
                    derived_context_manager.register_additional_provider(ag2_context)
                    try:
                        wf_logger.info(
                            f" [DERIVED_CONTEXT] Registered ag2_context provider | id={id(ag2_context)} keys={list(getattr(ag2_context,'data',{}).keys())}"
                        )
                    except Exception:
                        wf_logger.info(" [DERIVED_CONTEXT] Registered ag2_context as primary provider")

                # Seed defaults into all newly registered providers
                derived_context_manager.seed_defaults()

                # Log final provider count for debugging
                provider_count = len(derived_context_manager.providers) if hasattr(derived_context_manager, 'providers') else 0
                try:
                    # Enumerate providers briefly
                    details = []
                    for idx, prov in enumerate(getattr(derived_context_manager, 'providers', [])):
                        keys = []
                        if hasattr(prov, 'data') and isinstance(getattr(prov,'data'), dict):
                            keys = list(getattr(prov,'data').keys())
                        elif hasattr(prov, 'to_dict'):
                            try:
                                keys = list(prov.to_dict().keys())  # type: ignore
                            except Exception:
                                keys = []
                        details.append({"idx": idx, "id": id(prov), "key_count": len(keys)})
                    wf_logger.info(f" [DERIVED_CONTEXT] Final provider count: {provider_count} | providers={details}")
                except Exception:
                    wf_logger.info(f" [DERIVED_CONTEXT] Final provider count: {provider_count}")
            # Hooks are  registered once inside define_agents() via workflow_manager.register_hooks.
            # This avoids duplicate log noise and ensures _hooks_loaded_workflows gating is respected.
            
            # -----------------------------------------------------------------
            # 10.5) Lifecycle Tools: before_chat trigger
            # -----------------------------------------------------------------
            try:
                from mozaiksai.core.workflow.execution.lifecycle import get_lifecycle_manager
                lifecycle_manager = get_lifecycle_manager(workflow_name)
                await lifecycle_manager.trigger_before_chat(context_variables=ag2_context)
                wf_logger.info(f" [{workflow_name_upper}] Lifecycle before_chat triggers completed")
            except Exception as lc_err:
                wf_logger.debug(f" [{workflow_name_upper}] Lifecycle before_chat failed: {lc_err}")
            
            # -----------------------------------------------------------------
            # 10.6) Token streaming handled by transport layer
            # -----------------------------------------------------------------
            # The SimpleTransport automatically chunks chat.text events into
            # stream_chunk + stream_end for typewriter effect (see lines 660-714
            # in simple_transport.py). No additional setup needed here.

            # -----------------------------------------------------------------
            # 11) Execute AG2 group chat with proper event streaming
            # -----------------------------------------------------------------
            wf_lifecycle_logger.info(
                f" [{workflow_name_upper}] Starting AG2 workflow execution",
                agent_count=len(agents),
                tool_count=sum(len(getattr(agent, 'tool_names', [])) for agent in agents.values()),
                pattern_name=orchestration_pattern,
                message_count=len(initial_messages),
                max_turns=max_turns,
                is_resume=bool(resumed_messages)
            )
                
            stream_state = await _stream_events(
                pattern=pattern,
                resumed_messages=resumed_messages,
                initial_messages=initial_messages,
                max_turns=max_turns,
                agents=agents,
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name,
                wf_logger=wf_logger,
                workflow_name_upper=workflow_name_upper,
                transport=transport,
                user_id=user_id,
                persistence_manager=persistence_manager,
                perf_mgr=perf_mgr,
                derived_context_manager=derived_context_manager,
                lifecycle_manager=lifecycle_manager,
            )
            response = stream_state["response"]

            # Final usage reconciliation
            await _reconcile_final_usage(
                agents=agents,
                persistence_manager=persistence_manager,
                chat_id=chat_id,
                app_id=app_id,
                user_id=user_id,
                workflow_name=workflow_name,
                wf_logger=wf_logger,
            )

            max_turns_reached = getattr(response, 'max_turns_reached', False)

            awaiting_user_input = bool(stream_state.get("awaiting_user_input", False))
            workflow_complete = bool(stream_state.get("run_completed", False)) or not awaiting_user_input
            workflow_status_value = 1 if workflow_complete else 0

            if workflow_complete:
                try:
                    termination_result = await termination_handler.on_conversation_end(
                        max_turns_reached=max_turns_reached
                    )
                    try:
                        status_val = getattr(termination_result, 'status', 'completed')
                        logger.info(f" Termination completed: {status_val}")
                    except Exception:
                        logger.info(" Termination completed (offline mode)")
                except Exception as term_err:
                    logger.error(f" Termination handler failed: {term_err}")
            else:
                wf_logger.info(
                    " [%s] Run paused awaiting user input; chat remains resumable",
                    workflow_name_upper,
                )

            # Safely extract messages for logging and returning.
            # Some AG2 responses expose `messages` as an awaitable; never leak the coroutine.
            messages_obj = None
            try:
                messages_obj = getattr(response, 'messages', None)
                if asyncio.iscoroutine(messages_obj):
                    messages_obj = await messages_obj
                if messages_obj is not None:
                    await log_conversation_to_agent_chat_file(messages_obj, chat_id, app_id, workflow_name)
            except Exception as log_err:
                logger.error(f" Failed to log conversation to agent chat file for {chat_id}: {log_err}")

            # Log execution completion
            duration_sec = perf_counter() - start_time
            wf_logger.info(f" [EXECUTION_COMPLETE] Duration: {duration_sec:.2f}s")

            result_payload = {
                "workflow_name": workflow_name,
                "chat_id": chat_id,
                "app_id": app_id,
                "user_id": user_id,
                "messages": messages_obj,
                "max_turns_reached": max_turns_reached,
                "response": response,
                "run_completed": workflow_complete,
                "awaiting_user_input": awaiting_user_input,
                "run_status": workflow_status_value,
            }
                
        except Exception as e:
            logger.error(f" [{workflow_name_upper}] Orchestration failed: {e}", exc_info=True)
            try:
                await termination_handler.on_conversation_end()
                logger.info(" Termination handler called for error case")
            except Exception as term_err:
                logger.error(f" Termination handler error cleanup failed: {term_err}")
            raise
        finally:
            try:
                await perf_mgr.record_workflow_end(chat_id, workflow_status_value)
                await perf_mgr.flush(chat_id)
            except Exception as e:
                logger.debug(f"perf finalize failed: {e}")
            duration_sec = perf_counter() - start_time
        # AG2 runtime logging cleanup is now handled automatically by the context manager

    # Final logging & cleanup
    try:
        duration = perf_counter() - start_time
        
        # Log workflow completion with summary
        final_status_label = "completed" if workflow_status_value == 1 else "awaiting_user_input"
        wf_lifecycle_logger.info(
            f" [{workflow_name_upper}] Workflow execution settled",
            duration_sec=duration,
            event_count=(stream_state.get('sequence_counter', 0) if isinstance(stream_state, dict) else 0),
            agent_count=len(agents),
            pattern_used=orchestration_pattern,
            chat_id=chat_id,
            app_id=app_id,
            result_status=final_status_label if result_payload else "empty",
        )
        
        # Single consolidated completion log instead of multiple lines
        chat_logger.info(
            f"[{workflow_name_upper}] WORKFLOW_{'COMPLETED' if workflow_status_value == 1 else 'AWAITING_INPUT'} "
            f"chat_id={chat_id} duration={duration:.2f}s agents={len(agents)}"
        )
        
        # Log agent outputs file location
        try:
            agent_outputs_file = get_agent_outputs_dir() / f"agent_outputs_{chat_id}.jsonl"
            if agent_outputs_file.exists():
                file_size = agent_outputs_file.stat().st_size
                with open(agent_outputs_file, 'r', encoding='utf-8') as f:
                    line_count = sum(1 for _ in f)
                
                abs_path = agent_outputs_file.resolve()
                print("\n" + "=" * 80)
                print(f"📋 AGENT OUTPUTS LOG:")
                print(f"   File: {abs_path}")
                print(f"   Agent outputs captured: {line_count}")
                print(f"   Size: {file_size:,} bytes")
                print("=" * 80 + "\n")
                chat_logger.info(f"[{workflow_name_upper}] Agent outputs saved: {abs_path} ({line_count} outputs, {file_size:,} bytes)")
        except Exception:
            pass
        
    finally:
        # Transport cleanup: ensure per-chat trigger managers are released.
        try:
            if transport and hasattr(transport, "unregister_derived_context_manager"):
                transport.unregister_derived_context_manager(chat_id)
        except Exception:  # pragma: no cover
            pass

    return result_payload
