"""GroupChatExecutor — owns the full AG2 prepare-and-launch lifecycle.

Responsibilities
----------------
* Load workflow configuration
* Resume or initialize a chat session
* Load LLM config (with per-chat cache seed)
* Build context variables (sync/async factory, frontend context merge)
* Create agents and bind tools
* Create AG2 pattern with handoffs and context wiring
* Launch AG2 group-chat (resume path or new-run path)
* Hand back a :class:`PreparedRun` so the event pipeline can iterate events

This module deliberately keeps **all** AG2 imports (except streaming/adapter)
in one place so that orchestration.py is free of direct AG2 coupling.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import uuid
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import ValidationError

# ---------------------------------------------------------------------------
# AG2 imports — contained in this module
# ---------------------------------------------------------------------------
from autogen import ConversableAgent, UserProxyAgent

# ---------------------------------------------------------------------------
# Mozaiks imports
# ---------------------------------------------------------------------------
from mozaiksai.runtime.data.persistence import AG2PersistenceManager
from mozaiksai.engine.execution import create_termination_handler, LifecycleTrigger
from mozaiksai.engine.context import DerivedContextManager
from mozaiksai.engine.outputs import get_structured_outputs_for_workflow
from mozaiksai.engine.messages import (
    normalize_to_strict_ag2 as _normalize_to_strict_ag2,
    safe_context_snapshot as _safe_context_snapshot,
)
from mozaiksai.engine.execution import create_ag2_pattern
from logs.logging_config import get_workflow_logger
from mozaiksai.runtime.observability.performance_manager import get_performance_manager
from mozaiksai.engine.observability.runtime_logger import ag2_logging_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PreparedRun:
    """Everything the event pipeline needs after the executor finishes setup."""

    response: Any
    """AG2 response object — exposes ``.events`` async iterator."""

    pattern: Any
    """AG2 Pattern (access ``pattern.group_manager.context_variables``)."""

    agents: Dict[str, ConversableAgent]
    """Name → agent mapping for the workflow run."""

    iostream_bridge: Any
    """IOStream bridge — must stay alive during event iteration."""

    resumed_messages: List[Dict[str, Any]]
    """Messages from a prior session (empty list for new chats)."""

    initial_messages: List[Dict[str, Any]]
    """Seed messages fed to AG2 (includes user initial + config seeds)."""

    max_turns: int = 50

    # Orchestration metadata
    workflow_name: str = ""
    workflow_name_upper: str = ""
    app_id: str = ""
    chat_id: str = ""
    user_id: Optional[str] = None

    # Infrastructure handles
    transport: Any = None
    persistence_manager: Any = None
    perf_mgr: Any = None
    termination_handler: Any = None
    derived_context_manager: Optional[DerivedContextManager] = None
    lifecycle_manager: Any = None
    ag2_context: Any = None

    # Logging
    wf_logger: Any = None

    # Config fields
    config: Dict[str, Any] = field(default_factory=dict)
    orchestration_pattern: str = "AutoPattern"
    startup_mode: str = "AgentDriven"
    human_in_loop: bool = False
    cache_seed: Optional[int] = None
    trace_id: str = ""


# ===================================================================
# ORCHESTRATION HELPERS (moved from orchestration.py)
# ===================================================================

def _normalize_human_in_the_loop(value) -> bool:
    """Normalize human_in_the_loop config values to a strict boolean."""
    if isinstance(value, bool):
        return value
    try:
        if isinstance(value, (int, float)):
            return bool(int(value))
    except Exception:
        pass
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "1", "on", "always"}:
            return True
        if v in {"false", "no", "0", "of", "never"}:
            return False
    return False


def _load_workflow_config(workflow_name: str) -> Dict[str, Any]:
    """Load and normalize workflow config block."""
    from mozaiksai.kernel.workflow_manager import workflow_manager
    config = workflow_manager.get_config(workflow_name)
    return {
        "config": config,
        "max_turns": config.get("max_turns", 50),
        "orchestration_pattern": config.get("orchestration_pattern", "AutoPattern"),
        "startup_mode": config.get("startup_mode", "AgentDriven"),
        "human_in_loop": _normalize_human_in_the_loop(config.get("human_in_the_loop", False)),
        "initial_agent_name": config.get("initial_agent", None),
    }


async def _resume_or_initialize_chat(
    persistence_manager: AG2PersistenceManager,
    termination_handler,
    config: Dict[str, Any],
    chat_id: str,
    app_id: str,
    workflow_name: str,
    user_id: Optional[str],
    initial_message: Optional[str],
    wf_logger,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resume from Mongo or create a new session; returns (resumed_messages, initial_messages)."""
    resumed_messages = await persistence_manager.resume_chat(chat_id, app_id) or []
    resume_raw_count = len(resumed_messages)
    initial_messages: List[Dict[str, Any]] = []

    # Strip general-mode (non-AG2) chatter
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
            initial_messages.append(
                {
                    "role": "user",
                    "name": "user",
                    "content": initial_message,
                    "_mozaiks_seed_kind": "initial_message",
                }
            )
    else:
        if resume_raw_count > 0:
            wf_logger.info(
                f" [RESUME_DETECT] Discarding resume for chat {chat_id}: only {resume_raw_count} scaffolding/general messages (meaningful=0). Treating as NEW."
            )
        else:
            wf_logger.info(f" [RESUME_DETECT] No prior messages for chat {chat_id}. Starting NEW chat.")

        resumed_messages = []
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

        if os.getenv("ENABLE_MANUAL_INITIAL_PERSIST") == "1":
            try:
                if initial_messages:
                    await persistence_manager.persist_initial_messages(
                        chat_id=chat_id,
                        app_id=app_id,
                        messages=initial_messages,
                    )
            except Exception as init_persist_err:
                wf_logger.debug(f" Failed to persist initial messages for {chat_id}: {init_persist_err}")

    if not initial_messages:
        seed = config.get("initial_message") or config.get("initial_message_to_user")
        if seed:
            seed_kind = "initial_message" if config.get("initial_message") else "initial_message_to_user"
            initial_messages = [
                {"role": "user", "name": "user", "content": seed, "_mozaiks_seed_kind": seed_kind}
            ]
            if os.getenv("ENABLE_MANUAL_INITIAL_PERSIST") == "1":
                try:
                    await persistence_manager.persist_initial_messages(
                        chat_id=chat_id,
                        app_id=app_id,
                        messages=initial_messages,
                    )
                except Exception as seed_persist_err:
                    wf_logger.debug(f" Failed to persist config seed message for {chat_id}: {seed_persist_err}")

    return resumed_messages, initial_messages


async def _load_llm_config(
    workflow_name: str, wf_logger, workflow_name_upper: str, *, cache_seed: Optional[int] = None
) -> Dict[str, Any]:
    from mozaiksai.engine.outputs.structured import get_llm_for_workflow
    try:
        extra = {"cache_seed": cache_seed} if cache_seed is not None else None
        _, llm_config = await get_llm_for_workflow(workflow_name, "base", extra_config=extra)
        wf_logger.info(f" [{workflow_name_upper}] Using workflow-specific LLM config")
    except (ValueError, FileNotFoundError):
        from mozaiksai.engine.validation.llm_config import get_llm_config
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
    """Build context and wait for it to be fully populated before first turn."""
    try:
        if context_factory:
            result = context_factory()
            if inspect.isawaitable(result):
                ctx = await result
            else:
                ctx = result
        else:
            from mozaiksai.engine.context.variables import _load_context_async
            ctx = await _load_context_async(workflow_name, app_id)

        if frontend_context and isinstance(frontend_context, dict) and ctx is not None:
            for key, value in frontend_context.items():
                prefixed_key = f"ui_{key}" if not key.startswith("ui_") else key
                try:
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


async def _create_agents(
    agents_factory: Optional[Callable],
    workflow_name: str,
    context_variables=None,
    *,
    cache_seed: Optional[int] = None,
):
    """Create agents for the workflow following AG2 patterns."""
    if agents_factory:
        return await agents_factory(workflow_name, context_variables, cache_seed)
    from mozaiksai.engine.agents import create_agents
    return await create_agents(workflow_name, context_variables=context_variables, cache_seed=cache_seed)


def _ensure_user_proxy(
    agents: Dict[str, ConversableAgent],
    config: Dict[str, Any],
    startup_mode: str,
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
        if startup_mode in {"BackendOnly", "UserDriven"}:
            human_input_mode = "NEVER"
        else:
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


def _resolve_initiating_agent(
    agents: Dict[str, ConversableAgent],
    initial_agent_name: Optional[str],
    workflow_name: str,
):
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
    user_proxy_agent: Optional[UserProxyAgent],
) -> List[ConversableAgent]:
    """Filter agents list for AG2 pattern, excluding user proxy if handled separately."""
    agents_list = []
    for name, agent in agents.items():
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
    agents_list = _filter_agents_for_pattern(agents, human_in_loop, user_proxy_agent)
    ag2_context = _convert_to_ag2_context(context_variables, wf_logger)

    # Ensure core WebSocket path parameters are always available
    if not ag2_context.get("workflow_name"):
        ag2_context.set("workflow_name", workflow_name)
    if not ag2_context.get("app_id"):
        ag2_context.set("app_id", app_id)
    if not ag2_context.get("chat_id"):
        ag2_context.set("chat_id", chat_id)
    if user_id and not ag2_context.get("user_id"):
        ag2_context.set("user_id", user_id)

    context_keys = list(ag2_context.data.keys())
    wf_logger.info(
        f"[CONTEXT] AG2 ContextVariables ready | total_keys={len(context_keys)} | "
        f"workflow_name={ag2_context.get('workflow_name')} | "
        f"app_id={ag2_context.get('app_id')} | "
        f"chat_id={ag2_context.get('chat_id')} | "
        f"user_id={ag2_context.get('user_id')}"
    )

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
        wf_logger.info(f" [CONTEXT_INIT] AG2 context constructed | keys={list(snapshot.keys())}")
        wf_logger.debug(f" [CONTEXT_INIT_DEBUG] snapshot={snapshot}")
    except Exception as _snap_log_err:
        wf_logger.debug(f" [CONTEXT_INIT] snapshot logging failed: {_snap_log_err}")
    try:
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
                from mozaiksai.engine.handoffs import wire_handoffs_with_debugging
                wire_handoffs_with_debugging(workflow_name, agents)
        except Exception as he:
            wf_logger.warning(f"Handoffs wiring failed: {he}")
    return pattern, ag2_context


# ---------------------------------------------------------------------------
# AG2 launch helpers (moved from _stream_events pre-loop)
# ---------------------------------------------------------------------------

async def _launch_ag2(
    pattern,
    resumed_messages: List[Dict[str, Any]],
    initial_messages: List[Dict[str, Any]],
    max_turns: int,
    iostream_bridge,
    wf_logger,
):
    """Call AG2 resume or new-run and return the response object.

    The IOStream bridge must be set as default around the call so AG2
    routes ``StreamEvent`` chunks through our transport.
    """
    from autogen.agentchat import a_run_group_chat
    from autogen.io import IOStream

    resumed_mode = bool(resumed_messages)

    if resumed_mode:
        wf_logger.info(f" [AG2_RESUME] Using AG2 a_resume path (history={len(initial_messages)} messages)")
        wf_logger.info(f" [AG2_RESUME] Pattern type: {type(pattern).__name__} | Messages count: {len(initial_messages)}")

        for i, msg in enumerate(initial_messages):
            wf_logger.debug(f" [AG2_RESUME] Message[{i}]: {msg.get('role', 'unknown')} from {msg.get('name', 'unknown')}")

        import inspect as _inspect
        _pgc = getattr(pattern, "prepare_group_chat", None)
        if callable(_pgc):
            _sig = _inspect.signature(_pgc)
            if "max_rounds" in _sig.parameters:
                wf_logger.debug(" [AG2_RESUME] prepare_group_chat supports max_rounds -> passing it explicitly")
                prep_res = _pgc(messages=initial_messages, max_rounds=max_turns)
            else:
                prep_res = _pgc(messages=initial_messages)
        else:
            raise RuntimeError("Pattern missing prepare_group_chat callable during resume path")

        if asyncio.iscoroutine(prep_res):
            prep_res = await prep_res
        if isinstance(prep_res, (list, tuple)) and len(prep_res) == 2:
            group_manager = prep_res[1]
        else:
            group_manager = getattr(pattern, "group_manager", None)

        wf_logger.info(f" [AG2_RESUME] Group manager resolved: {type(group_manager).__name__ if group_manager else 'None'}")

        if not group_manager or not hasattr(group_manager, "a_resume"):
            wf_logger.warning(
                " [AG2_RESUME] Pattern lacks a_resume — falling back to "
                "a_run_group_chat with full message history"
            )
            # Fall back to new-run with history (e.g. after handoff_to_user)
            try:
                with IOStream.set_default(iostream_bridge):
                    response = await a_run_group_chat(
                        pattern=pattern,
                        messages=initial_messages,
                        max_rounds=max_turns,
                    )
                wf_logger.info(" [AG2_RUN] a_run_group_chat (resume fallback) initialized successfully!")
            except Exception as run_err:
                wf_logger.error(f" [AG2_RUN] a_run_group_chat (resume fallback) failed: {run_err}")
                raise
        else:
            wf_logger.info(f" [AG2_RESUME] Calling group_manager.a_resume() with {len(initial_messages)} messages, max_rounds={max_turns}")

            try:
                with IOStream.set_default(iostream_bridge):
                    response = await group_manager.a_resume(messages=initial_messages, max_rounds=max_turns)
                wf_logger.info(" [AG2_RESUME] a_resume() initialized successfully!")
            except Exception as resume_err:
                wf_logger.error(f" [AG2_RESUME] a_resume() failed: {resume_err}")
                raise
    else:
        wf_logger.info(f" [AG2_RUN] Using AG2 a_run_group_chat for NEW chat")
        wf_logger.info(f" [AG2_RUN] Pattern type: {type(pattern).__name__} | Messages count: {len(initial_messages)} | Max rounds: {max_turns}")

        for i, msg in enumerate(initial_messages):
            wf_logger.debug(
                f" [AG2_RUN] Message[{i}]: {msg.get('role', 'unknown')} from {msg.get('name', 'unknown')} - {str(msg.get('content', ''))[:100]}"
            )

        wf_logger.info(" [AG2_RUN] Calling a_run_group_chat() NOW...")

        try:
            with IOStream.set_default(iostream_bridge):
                response = await a_run_group_chat(pattern=pattern, messages=initial_messages, max_rounds=max_turns)
            wf_logger.info(" [AG2_RUN] a_run_group_chat() initialized successfully!")
        except Exception as run_err:
            wf_logger.error(f" [AG2_RUN] a_run_group_chat() failed: {run_err}")
            raise

    return response


# ===================================================================
# GroupChatExecutor
# ===================================================================

class GroupChatExecutor:
    """Encapsulates the full AG2 prepare-and-launch lifecycle.

    Usage::

        executor = GroupChatExecutor(
            workflow_name="HelloWorld",
            app_id="app_123",
            chat_id="chat_456",
        )
        prepared = await executor.prepare_and_launch()
        # prepared.response.events is an async iterator of AG2 events
    """

    def __init__(
        self,
        workflow_name: str,
        app_id: str,
        chat_id: str,
        user_id: Optional[str] = None,
        initial_message: Optional[str] = None,
        initial_agent_name_override: Optional[str] = None,
        agents_factory: Optional[Callable] = None,
        context_factory: Optional[Callable] = None,
        handoffs_factory: Optional[Callable] = None,
        **kwargs,
    ):
        self.workflow_name = workflow_name
        self.app_id = app_id
        self.chat_id = chat_id
        self.user_id = user_id
        self.initial_message = initial_message
        self.initial_agent_name_override = initial_agent_name_override
        self.agents_factory = agents_factory
        self.context_factory = context_factory
        self.handoffs_factory = handoffs_factory
        self.kwargs = kwargs

        self.workflow_name_upper = workflow_name.upper()
        self.wf_logger = get_workflow_logger(workflow_name, chat_id=chat_id, app_id=app_id)
        self.wf_lifecycle_logger = get_workflow_logger(workflow_name, chat_id=chat_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def prepare_and_launch(self) -> PreparedRun:
        """Execute the full prepare → launch lifecycle and return a :class:`PreparedRun`.

        The caller iterates ``prepared_run.response.events`` through the event pipeline.
        """
        wf_logger = self.wf_logger
        wfu = self.workflow_name_upper

        # Infrastructure --------------------------------------------------
        persistence_manager = AG2PersistenceManager()

        from mozaiksai.transport.websocket.handler import SimpleTransport
        transport = await SimpleTransport.get_instance()
        if not transport:
            raise RuntimeError(f"SimpleTransport instance not available for {self.workflow_name} workflow")

        termination_handler = create_termination_handler(
            chat_id=self.chat_id,
            app_id=self.app_id,
            workflow_name=self.workflow_name,
            transport=transport,
        )

        trace_id_hex = uuid.uuid4().hex
        logger.debug(f"Generated trace_id for workflow {self.workflow_name}: {trace_id_hex}")

        perf_mgr = await get_performance_manager()
        await perf_mgr.initialize()
        await perf_mgr.record_workflow_start(self.chat_id, self.app_id, self.workflow_name, self.user_id or "unknown")
        await perf_mgr.attach_trace_id(self.chat_id, trace_id_hex)

        # 1) Load configuration -------------------------------------------
        cfg = _load_workflow_config(self.workflow_name)
        config = cfg["config"]
        max_turns = cfg["max_turns"]
        orchestration_pattern = cfg["orchestration_pattern"]
        startup_mode = cfg["startup_mode"]
        human_in_loop = cfg["human_in_loop"]
        initial_agent_name = cfg["initial_agent_name"]

        if self.initial_agent_name_override:
            initial_agent_name = str(self.initial_agent_name_override)

        try:
            wf_logger.info(
                f" [{wfu}] CONFIG: startup_mode={startup_mode} human_in_loop={human_in_loop} "
                f"pattern={orchestration_pattern} initial_agent={initial_agent_name}"
            )
        except Exception:
            pass

        # 2) Resume or start chat ------------------------------------------
        resumed_messages, initial_messages = await _resume_or_initialize_chat(
            persistence_manager=persistence_manager,
            termination_handler=termination_handler,
            config=config,
            chat_id=self.chat_id,
            app_id=self.app_id,
            workflow_name=self.workflow_name,
            user_id=self.user_id,
            initial_message=self.initial_message,
            wf_logger=wf_logger,
        )

        # 3) LLM config ---------------------------------------------------
        try:
            cache_seed = await persistence_manager.get_or_assign_cache_seed(self.chat_id, self.app_id)
        except Exception as seed_err:
            cache_seed = None
            wf_logger.debug(f" [{wfu}] cache_seed assignment failed for chat {self.chat_id}: {seed_err}")
        llm_config = await _load_llm_config(self.workflow_name, wf_logger, wfu, cache_seed=cache_seed)

        # 3.5) Structured outputs preload ----------------------------------
        try:
            from mozaiksai.engine.outputs.structured import load_workflow_structured_outputs as _preload_so
            _preload_so(self.workflow_name)
            wf_logger.info(f" [{wfu}] Structured outputs preloaded")
        except Exception as so_err:
            wf_logger.warning(f" [{wfu}] Structured outputs preload failed: {so_err}")

        # 4) Context build -------------------------------------------------
        frontend_context = None
        try:
            if transport and hasattr(transport, 'connections') and self.chat_id in transport.connections:
                frontend_context = transport.connections[self.chat_id].frontend_context
                if frontend_context:
                    wf_logger.info(f" [{wfu}] Found frontend context: {list(frontend_context.keys())}")
        except Exception as fc_lookup_err:
            wf_logger.debug(f" [{wfu}] Frontend context lookup failed: {fc_lookup_err}")

        context = await _build_context_blocking(
            context_factory=self.context_factory,
            workflow_name=self.workflow_name,
            app_id=self.app_id,
            chat_id=self.chat_id,
            user_id=self.user_id,
            wf_logger=wf_logger,
            workflow_name_upper=wfu,
            frontend_context=frontend_context,
        )

        # Merge persisted session metadata (extra_fields) into context
        try:
            if context is not None:
                extra_ctx = await persistence_manager.fetch_chat_session_extra_context(
                    chat_id=self.chat_id, app_id=self.app_id
                )
                if isinstance(extra_ctx, dict) and extra_ctx:
                    for k, v in extra_ctx.items():
                        try:
                            existing = None
                            if hasattr(context, "get"):
                                existing = context.get(k)
                            elif hasattr(context, "data") and isinstance(getattr(context, "data"), dict):
                                existing = getattr(context, "data").get(k)
                            if existing is None:
                                if hasattr(context, "set"):
                                    context.set(k, v)
                                elif hasattr(context, "__setitem__"):
                                    context[k] = v
                        except Exception:
                            continue

                    try:
                        parent_chat_id = extra_ctx.get("parent_chat_id")
                        if parent_chat_id and hasattr(context, "get") and not context.get("is_child_workflow"):
                            context.set("is_child_workflow", True)
                    except Exception:
                        pass
        except Exception as _seed_err:
            wf_logger.debug(f" [{wfu}] Failed merging persisted extra context: {_seed_err}")

        # has_children flag
        try:
            if context is not None:
                from mozaiksai.kernel.pack.config import workflow_has_journeys
                context.set("has_children", bool(workflow_has_journeys(self.workflow_name)))
        except Exception:
            pass

        # 6) Agents creation -----------------------------------------------
        agents = await _create_agents(self.agents_factory, self.workflow_name, context_variables=context, cache_seed=cache_seed)
        agents = agents or {}
        if not agents:
            raise RuntimeError(f"No agents defined for workflow '{self.workflow_name}'")

        # Derived context manager
        derived_context_manager: Optional[DerivedContextManager] = DerivedContextManager(self.workflow_name, agents, context)
        if derived_context_manager.has_variables():
            derived_context_manager.seed_defaults()

            def _derived_listener(payload: Dict[str, Any]):
                try:
                    var_name = payload.get('variable')
                    value = payload.get('value')
                    if not var_name:
                        return
                    if transport:
                        evt = {'kind': 'context_update', 'variable': var_name, 'value': value}
                        asyncio.create_task(transport.send_event_to_ui(evt, self.chat_id))
                except Exception as _dl_err:
                    wf_logger.debug(f"Derived listener emit failed: {_dl_err}")

            try:
                derived_context_manager.add_listener(_derived_listener)
            except Exception as _lerr:
                wf_logger.debug(f"Failed registering derived listener: {_lerr}")
        else:
            derived_context_manager = None

        if transport and derived_context_manager and hasattr(transport, "register_derived_context_manager"):
            try:
                transport.register_derived_context_manager(self.chat_id, derived_context_manager)
            except Exception as _reg_err:
                wf_logger.debug(f"Failed registering derived context manager with transport: {_reg_err}")

        # Tool binding
        from mozaiksai.engine.agents.tools import load_agent_tool_functions
        agent_tools = load_agent_tool_functions(self.workflow_name)
        total_tool_count = sum(len(funcs) for funcs in agent_tools.values())
        wf_logger.info(f" [{wfu}] Tools bound across agents: {total_tool_count}")

        try:
            wf_logger.info(f" [WORKFLOW_SETUP] {self.workflow_name}: agents={list(agents.keys())} tools={len(agent_tools)}")
        except Exception:
            pass

        # Hooks readiness snapshot
        try:
            from mozaiksai.engine.agents import list_hooks_for_workflow as _list_hooks
            hooks_snapshot = _list_hooks(agents)
            total_hooks = sum(len(funcs) for agent_hooks in hooks_snapshot.values() for funcs in agent_hooks.values())
            wf_logger.info(f" [{wfu}] Hooks registered across agents: {total_hooks}")
        except Exception:
            pass

        # Store agents on transport
        try:
            if transport and hasattr(transport, 'connections') and self.chat_id in transport.connections:
                transport.connections[self.chat_id].agents = agents
                if context is not None:
                    transport.connections[self.chat_id].context = context
        except Exception:
            pass

        # Ensure user proxy
        agents, user_proxy_agent, human_in_loop = _ensure_user_proxy(
            agents=agents,
            config=config,
            startup_mode=startup_mode,
            llm_config=llm_config,
            human_in_loop=human_in_loop,
        )

        # 7) Initiating agent -----------------------------------------------
        initiating_agent = _resolve_initiating_agent(
            agents=agents,
            initial_agent_name=initial_agent_name,
            workflow_name=self.workflow_name,
        )
        wf_logger.info(f" [{wfu}] Initial agent resolved: {getattr(initiating_agent, 'name', None)}")

        # 8) Strict resume prep --------------------------------------------
        initial_messages = _normalize_to_strict_ag2(initial_messages, default_user_name="user")
        if any(m.get("role") == "user" for m in initial_messages):
            human_in_loop = True

        # 9) Pattern creation -----------------------------------------------
        pattern, ag2_context = await _create_ag2_pattern(
            orchestration_pattern=orchestration_pattern,
            workflow_name=self.workflow_name,
            agents=agents,
            initiating_agent=initiating_agent,
            user_proxy_agent=user_proxy_agent,
            human_in_loop=human_in_loop,
            context_variables=context,
            llm_config=llm_config,
            handoffs_factory=self.handoffs_factory,
            wf_logger=wf_logger,
            chat_id=self.chat_id,
            app_id=self.app_id,
            user_id=self.user_id,
        )

        # Derived context bridging ------------------------------------------
        self._wire_derived_context(derived_context_manager, pattern, ag2_context, wf_logger)

        # 10.5) Lifecycle: before_chat trigger ------------------------------
        lifecycle_manager = None
        try:
            from mozaiksai.engine.execution.lifecycle import get_lifecycle_manager
            lifecycle_manager = get_lifecycle_manager(self.workflow_name)
            await lifecycle_manager.trigger_before_chat(context_variables=ag2_context)
            wf_logger.info(f" [{wfu}] Lifecycle before_chat triggers completed")
        except Exception as lc_err:
            wf_logger.debug(f" [{wfu}] Lifecycle before_chat failed: {lc_err}")

        # 11) IOStream bridge + AG2 launch ----------------------------------
        from mozaiksai.engine.streaming import create_iostream_bridge

        iostream_bridge = create_iostream_bridge(chat_id=self.chat_id, transport=transport)
        wf_logger.info(f" [IOSTREAM_BRIDGE] Token streaming bridge active for chat {self.chat_id}")

        self.wf_lifecycle_logger.info(
            f" [{wfu}] Starting AG2 workflow execution",
            agent_count=len(agents),
            tool_count=sum(len(getattr(agent, 'tool_names', [])) for agent in agents.values()),
            pattern_name=orchestration_pattern,
            message_count=len(initial_messages),
            max_turns=max_turns,
            is_resume=bool(resumed_messages),
        )

        response = await _launch_ag2(
            pattern=pattern,
            resumed_messages=resumed_messages,
            initial_messages=initial_messages,
            max_turns=max_turns,
            iostream_bridge=iostream_bridge,
            wf_logger=wf_logger,
        )

        return PreparedRun(
            response=response,
            pattern=pattern,
            agents=agents,
            iostream_bridge=iostream_bridge,
            resumed_messages=resumed_messages,
            initial_messages=initial_messages,
            max_turns=max_turns,
            workflow_name=self.workflow_name,
            workflow_name_upper=wfu,
            app_id=self.app_id,
            chat_id=self.chat_id,
            user_id=self.user_id,
            transport=transport,
            persistence_manager=persistence_manager,
            perf_mgr=perf_mgr,
            termination_handler=termination_handler,
            derived_context_manager=derived_context_manager,
            lifecycle_manager=lifecycle_manager,
            ag2_context=ag2_context,
            wf_logger=wf_logger,
            config=config,
            orchestration_pattern=orchestration_pattern,
            startup_mode=startup_mode,
            human_in_loop=human_in_loop,
            cache_seed=cache_seed,
            trace_id=trace_id_hex,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wire_derived_context(
        derived_context_manager: Optional[DerivedContextManager],
        pattern,
        ag2_context,
        wf_logger,
    ):
        """Register AG2 context providers on the derived context manager."""
        if not derived_context_manager:
            return

        if hasattr(pattern, "group_manager"):
            group_manager = getattr(pattern, "group_manager", None)
            if group_manager and hasattr(group_manager, "context_variables"):
                pattern_context_vars = getattr(group_manager, "context_variables")
                derived_context_manager.register_additional_provider(pattern_context_vars)
                try:
                    wf_logger.info(
                        f" [DERIVED_CONTEXT] Registered group_manager context_variables provider | "
                        f"id={id(pattern_context_vars)} keys={list(getattr(pattern_context_vars, 'data', {}).keys())}"
                    )
                except Exception:
                    wf_logger.info(" [DERIVED_CONTEXT] Registered group_manager context_variables as provider (keys unavailable)")

        pattern_context = getattr(pattern, "context_variables", None)
        if pattern_context:
            derived_context_manager.register_additional_provider(pattern_context)
            try:
                wf_logger.info(
                    f" [DERIVED_CONTEXT] Registered pattern.context_variables provider | "
                    f"id={id(pattern_context)} keys={list(getattr(pattern_context, 'data', {}).keys())}"
                )
            except Exception:
                wf_logger.info(" [DERIVED_CONTEXT] Registered pattern context_variables as provider")

        if ag2_context:
            derived_context_manager.register_additional_provider(ag2_context)
            try:
                wf_logger.info(
                    f" [DERIVED_CONTEXT] Registered ag2_context provider | "
                    f"id={id(ag2_context)} keys={list(getattr(ag2_context, 'data', {}).keys())}"
                )
            except Exception:
                wf_logger.info(" [DERIVED_CONTEXT] Registered ag2_context as primary provider")

        derived_context_manager.seed_defaults()

        provider_count = len(derived_context_manager.providers) if hasattr(derived_context_manager, 'providers') else 0
        try:
            details = []
            for idx, prov in enumerate(getattr(derived_context_manager, 'providers', [])):
                keys = []
                if hasattr(prov, 'data') and isinstance(getattr(prov, 'data'), dict):
                    keys = list(getattr(prov, 'data').keys())
                elif hasattr(prov, 'to_dict'):
                    try:
                        keys = list(prov.to_dict().keys())
                    except Exception:
                        keys = []
                details.append({"idx": idx, "id": id(prov), "key_count": len(keys)})
            wf_logger.info(f" [DERIVED_CONTEXT] Final provider count: {provider_count} | providers={details}")
        except Exception:
            wf_logger.info(f" [DERIVED_CONTEXT] Final provider count: {provider_count}")
