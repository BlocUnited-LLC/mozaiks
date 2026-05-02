# ==============================================================================
# FILE: workflows/AgentGenerator/tools/hook_tokengate.py
# PURPOSE: Append a short footer to DownloadAgent's outbound message when on free trial or when tokens are exhausted.
# HOOK TYPE: process_message_before_send
# SIGNATURE: def your_function_name(sender: ConversableAgent, message: Union[dict[str, Any], str], recipient: Agent, silent: bool) -> Union[dict[str, Any], str]:
# ==============================================================================

from typing import Any, Union, Dict
from autogen import Agent, ConversableAgent
import os

FOOTER_TRIAL = (
    "\n\n—\n🔔 You're on a free trial and this was your final attempt. "
    "Upgrade now to continue and unlock more runs."
)

def get_free_trial_config() -> Dict[str, Any]:
    """Get free trial configuration from environment variables"""
    return {
        "enabled": os.getenv("FREE_TRIAL_ENABLED", "true").lower() == "true"
    }

def _normalize_content(value: Any) -> str:
    """Safely coerce hook message payloads to a string."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _append_footer(content: Any, free_trial: bool) -> str:
    text = _normalize_content(content)
    if not text:
        return text
    if free_trial and FOOTER_TRIAL not in text:
        return text + FOOTER_TRIAL
    return text

def add_usage_footer(
    sender: ConversableAgent,
    message: Union[str, Dict[str, Any]],
    recipient: Agent,
    silent: bool
) -> Union[str, Dict[str, Any]]:
    """
    Appends the correct footer to outgoing DownloadAgent messages
    when free trial is enabled.
    """
    # Respect silent flag - don't modify messages when silent
    if silent:
        return message
    
    # Check if free trial is enabled from environment config
    free_trial_config = get_free_trial_config()
    free_trial_enabled = free_trial_config.get("enabled", False)

    if isinstance(message, dict):
        content = message.get("content")
        message["content"] = _append_footer(content, free_trial_enabled)
        return message

    return _append_footer(message, free_trial_enabled)