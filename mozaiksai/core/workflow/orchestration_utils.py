# ==============================================================================
# FILE: core/workflow/orchestration_utils.py
# DESCRIPTION: Utility functions extracted from orchestration_patterns.py
# ==============================================================================

"""
Orchestration Utilities

Helper functions for workflow orchestration, extracted to reduce the size
of orchestration_patterns.py and improve maintainability.

Functions:
    - get_run_registry_summary: Health endpoint support
    - _cancel_ag2_task: Cancel zombie AG2 tasks
    - _normalize_human_in_the_loop: Normalize HIL config values
    - _load_workflow_config: Load workflow configuration
    - _safe_float_value: Convert AG2 values to float
    - _reconcile_final_usage: Reconcile usage metrics with AG2
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, UTC
import logging

logger = logging.getLogger(__name__)


# ===================================================================
# HEALTH ENDPOINT SUPPORT
# ===================================================================

def get_run_registry_summary() -> Dict[str, Any]:
    """Simple health endpoint response - no actual registry tracking."""
    return {
        'active_count': 0,
        'total_runs': 0,
        'runs': [],
        'note': 'Registry tracking disabled for simplicity'
    }


# ===================================================================
# AG2 TASK MANAGEMENT
# ===================================================================

def _cancel_ag2_task(response: Any, *, logger: Any = None, label: str = "") -> None:
    """Cancel classic AG2 run tasks that may block waiting for user input.

    Iterator-based AG2 runs clean themselves up when the event loop breaks.
    Resume responses still use the classic async run response shape, which may
    leave an internal task blocked on IOStream.input() after handoff-to-user.
    """
    import logging as _logging
    _log = logger or _logging.getLogger(__name__)
    task = getattr(response, "_task", None)
    if task is None:
        task = getattr(response, "task", None)
    if task is not None and hasattr(task, "cancel"):
        task.cancel()
        _log.info(f" [{label}] Cancelled zombie AG2 task after handoff_to_user")
    else:
        _log.debug(f" [{label}] No AG2 task to cancel (response type: {type(response).__name__})")


# ===================================================================
# CONFIG NORMALIZATION
# ===================================================================

def _normalize_human_in_the_loop(value) -> bool:
    """Normalize human_in_the_loop config values to a strict boolean.

    Accepts booleans directly, and common string/int representations:
    - True-y: "true", "yes", "1", "on", "always"
    - False-y: "false", "no", "0", "of", "never"
    Any other value defaults to False.
    """
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
    """Load workflow configuration block."""
    from .workflow_manager import workflow_manager
    config = workflow_manager.get_config(workflow_name)
    return {
        "config": config,
        "max_turns": config.get("max_turns", 50),
        "orchestration_pattern": config.get("orchestration_pattern", "AutoPattern"),
        "startup_mode": config.get("startup_mode", "AgentDriven"),
        "human_in_loop": _normalize_human_in_the_loop(config.get("human_in_the_loop", False)),
        "initial_agent_name": config.get("initial_agent", None),
    }


# ===================================================================
# USAGE RECONCILIATION
# ===================================================================

def _safe_float_value(value: Any) -> float:
    """Convert mixed autogen values to float safely."""
    try:
        if isinstance(value, dict):
            if "total_cost" in value:
                return float(value.get("total_cost", 0.0))
            values = list(value.values())
            if values:
                return float(values[0])
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def _reconcile_final_usage(
    agents: Dict[str, Any],
    persistence_manager: Any,
    chat_id: str,
    app_id: str,
    user_id: Optional[str],
    workflow_name: str,
    wf_logger,
) -> None:
    """
    Reconcile final usage metrics using AG2's gather_usage_summary.

    Compares AG2 authoritative usage data against persisted counters
    and updates if deltas are detected.
    """
    try:
        from autogen import gather_usage_summary

        agent_list = list(agents.values())
        if not agent_list:
            return

        final_summary = gather_usage_summary(agent_list)
        ag2_total_cost = _safe_float_value(final_summary.get("total_cost", 0.0))
        ag2_usage_including_cached = final_summary.get("usage_including_cached", {})
        ag2_usage_excluding_cached = final_summary.get("usage_excluding_cached", {})

        wf_logger.info(
            "[AG2_FINAL_SUMMARY] Authoritative usage data | "
            f"total_cost=${ag2_total_cost:.4f} | "
            f"with_cache={ag2_usage_including_cached} | "
            f"without_cache={ag2_usage_excluding_cached}"
        )

        # Compare AG2 totals vs persisted ChatSessions counters
        persisted_prompt = 0
        persisted_completion = 0
        persisted_cost = 0.0
        try:
            coll = await persistence_manager._coll()
            persisted = await coll.find_one(
                {"_id": chat_id, "app_id": app_id},
                {
                    "usage_prompt_tokens_final": 1,
                    "usage_completion_tokens_final": 1,
                    "usage_total_cost_final": 1,
                },
            )
            if isinstance(persisted, dict):
                persisted_prompt = int(persisted.get("usage_prompt_tokens_final") or 0)
                persisted_completion = int(persisted.get("usage_completion_tokens_final") or 0)
                persisted_cost = float(persisted.get("usage_total_cost_final") or 0.0)
        except Exception as read_err:
            wf_logger.debug(f"[FINAL_RECONCILIATION] Failed to read persisted usage: {read_err}")

        ag2_prompt_total = int(ag2_usage_excluding_cached.get("prompt_tokens", 0) or 0)
        ag2_completion_total = int(ag2_usage_excluding_cached.get("completion_tokens", 0) or 0)

        final_cost_delta = max(0.0, ag2_total_cost - persisted_cost)
        final_prompt_delta = max(0, ag2_prompt_total - persisted_prompt)
        final_completion_delta = max(0, ag2_completion_total - persisted_completion)

        if final_cost_delta > 0.01 or final_prompt_delta or final_completion_delta:
            wf_logger.warning(
                "[FINAL_RECONCILIATION] Delta detected | "
                f"ag2_total=${ag2_total_cost:.4f} persisted_total=${persisted_cost:.4f} | "
                f"delta=${final_cost_delta:.4f} prompt_delta={final_prompt_delta} completion_delta={final_completion_delta}"
            )
            await persistence_manager.update_session_metrics(
                chat_id=chat_id,
                app_id=app_id,
                user_id=user_id or "unknown",
                workflow_name=workflow_name,
                prompt_tokens=final_prompt_delta,
                completion_tokens=final_completion_delta,
                cost_usd=final_cost_delta,
                agent_name="ag2_final_reconciliation",
                event_ts=datetime.now(UTC)
            )
        else:
            wf_logger.info(
                "[FINAL_RECONCILIATION] Usage tracking accurate | "
                f"ag2=${ag2_total_cost:.4f} persisted=${persisted_cost:.4f} | "
                f"delta=${final_cost_delta:.4f}"
            )

        # Log per-agent usage summaries for visibility
        for agent_name, agent in agents.items():
            try:
                if hasattr(agent, 'print_usage_summary'):
                    wf_logger.debug(f" [AGENT_USAGE] {agent_name} summary logged to stdout")
            except Exception as agent_summary_err:
                wf_logger.debug(f"Failed to log usage summary for {agent_name}: {agent_summary_err}")

    except ImportError:
        wf_logger.warning(" [FINAL_RECONCILIATION] autogen.gather_usage_summary not available")
    except Exception as reconcile_err:
        wf_logger.error(f" [FINAL_RECONCILIATION] Failed: {reconcile_err}")


# ===================================================================
# CONVERSATION LOGGING
# ===================================================================

async def log_conversation_to_agent_chat_file(
    conversation_history,
    chat_id: str,
    app_id: str,
    workflow_name: str
) -> None:
    """Log the complete AG2 conversation to the agent chat log file."""
    from logs.logging_config import get_workflow_logger

    try:
        agent_chat_logger = get_workflow_logger(
            "agent_messages",
            base_logger=logging.getLogger("mozaiks.workflow.agent_messages"),
        )

        if not conversation_history:
            agent_chat_logger.info(f" [{workflow_name}] No conversation history to log for chat {chat_id}")
            return

        msg_count = len(conversation_history) if hasattr(conversation_history, '__len__') else 0
        agent_chat_logger.info(f" [{workflow_name}] Logging {msg_count} messages to agent chat file for chat {chat_id}")

        for i, message in enumerate(conversation_history):
            try:
                sender_name = "Unknown"
                content = ""

                if isinstance(message, dict):
                    if 'name' in message and message['name']:
                        sender_name = message['name']
                    elif 'sender' in message and message['sender']:
                        sender_name = message['sender']
                    elif 'from' in message and message['from']:
                        sender_name = message['from']

                    if 'content' in message and message['content'] is not None:
                        content = message['content']
                    elif 'message' in message and message['message'] is not None:
                        content = message['message']
                    elif 'text' in message and message['text'] is not None:
                        content = message['text']
                elif isinstance(message, str):
                    content = message
                elif hasattr(message, 'name') and hasattr(message, 'content'):
                    sender_name = getattr(message, 'name', 'Unknown')
                    content = getattr(message, 'content', '')
                elif hasattr(message, 'sender') and hasattr(message, 'message'):
                    sender_name = getattr(message, 'sender', 'Unknown')
                    content = getattr(message, 'message', '')
                else:
                    content = str(message)

                clean_content = content if isinstance(content, str) else str(content)
                clean_content = clean_content.strip() if clean_content else ""

                if clean_content:
                    agent_chat_logger.info(
                        f"AGENT_MESSAGE | Chat: {chat_id} | App: {app_id} | Agent: {sender_name} | Message #{i+1}: {clean_content}"
                    )
                    # Skip user proxy messages to prevent echo back to UI
                    message_role = message.get('role') if isinstance(message, dict) else None
                    if not (sender_name.lower() in ("user", "userproxy", "userproxyagent") or message_role == 'user'):
                        try:
                            from mozaiksai.core.transport.simple_transport import SimpleTransport
                            transport = await SimpleTransport.get_instance()
                            if transport:
                                await transport.send_chat_message(
                                    message=clean_content,
                                    agent_name=sender_name,
                                    chat_id=chat_id,
                                    metadata={"source": "ag2_conversation", "message_index": i+1}
                                )
                        except Exception as ui_error:
                            logger.debug(f"UI forwarding failed for message {i+1}: {ui_error}")
                else:
                    agent_chat_logger.debug(f"EMPTY_MESSAGE | Chat: {chat_id} | Agent: {sender_name} | Message #{i+1}: (empty)")

            except Exception as msg_error:
                agent_chat_logger.error(f" Failed to log message {i+1} in chat {chat_id}: {msg_error}")

        agent_chat_logger.info(f" [{workflow_name}] Successfully logged {msg_count} messages for chat {chat_id}")

    except Exception as e:
        logger.error(f" Failed to log conversation to agent chat file for {chat_id}: {e}")
