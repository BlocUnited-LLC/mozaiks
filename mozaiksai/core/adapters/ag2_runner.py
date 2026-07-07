# ==============================================================================
# FILE: mozaiksai/core/adapters/ag2_runner.py
# DESCRIPTION: AG2 isolation adapter — thin facade over AG2's chat, structured
#              outputs, and task batch configuration.
#
# Purpose: isolate AG2 API surface so a version bump or API change in AG2
# only requires updating this file, not every workflow tool.
#
# The adapter does NOT add logic. It translates Mozaiks workflow contracts
# into AG2 primitives and re-raises AG2 exceptions with context.
#
# AG2 ownership boundary (per CLAUDE.md):
#   AG2 owns: agent primitives, model calls, tools, multi-agent network mechanics
#   Mozaiks owns: structured-output contracts, workflow YAML, artifact shaping
#
# When an AG2 API changes that breaks this adapter, update HERE only.
# Track breaking AG2 changes as compatibility watchpoints in the docstring.
#
# Compatibility watchpoints:
#   AG2 0.x: ConversableAgent, GroupChat, GroupChatManager — current primary API
#   Watch: AG2 agents.py → ConversableAgent.initiate_chat signature
#   Watch: AG2 structured output model_client API
# ==============================================================================
from __future__ import annotations

import asyncio
from typing import Any

from logs.logging_config import get_core_logger

logger = get_core_logger("ag2_runner")


class AG2RunnerError(Exception):
    """Wraps AG2 execution errors with workflow context."""

    def __init__(self, message: str, *, workflow_name: str = "", chat_id: str = "") -> None:
        super().__init__(message)
        self.workflow_name = workflow_name
        self.chat_id = chat_id


class AG2RunnerAdapter:
    """Thin facade over AG2 primitives used by Mozaiks workflow runners.

    Call sites import this adapter instead of autogen directly, so the
    AG2 import surface is contained to this one file.

    Usage:
        adapter = AG2RunnerAdapter()
        result = await adapter.initiate_chat(
            initiator=orchestrator_agent,
            recipient=worker_agent,
            message=user_message,
            max_turns=10,
        )
    """

    # ------------------------------------------------------------------
    # Agent construction helpers
    # ------------------------------------------------------------------

    def build_conversable_agent(
        self,
        name: str,
        *,
        system_message: str = "",
        llm_config: dict[str, Any] | None = None,
        human_input_mode: str = "NEVER",
        max_consecutive_auto_reply: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Construct an AG2 ConversableAgent.

        Returns the AG2 agent object. Callers must not depend on AG2-specific
        attributes beyond what is exposed through this adapter's methods.
        """
        try:
            from autogen import ConversableAgent
        except ImportError as exc:
            raise AG2RunnerError("autogen (AG2) is not installed") from exc

        agent_kwargs: dict[str, Any] = {
            "name": name,
            "system_message": system_message,
            "human_input_mode": human_input_mode,
        }
        if llm_config is not None:
            agent_kwargs["llm_config"] = llm_config
        if max_consecutive_auto_reply is not None:
            agent_kwargs["max_consecutive_auto_reply"] = max_consecutive_auto_reply
        agent_kwargs.update(kwargs)

        return ConversableAgent(**agent_kwargs)

    def build_group_chat(
        self,
        agents: list[Any],
        *,
        max_round: int = 20,
        speaker_selection_method: str = "auto",
        **kwargs: Any,
    ) -> tuple[Any, Any]:
        """Construct an AG2 GroupChat + GroupChatManager pair.

        Returns (group_chat, manager) tuple.
        """
        try:
            from autogen import GroupChat, GroupChatManager
        except ImportError as exc:
            raise AG2RunnerError("autogen (AG2) is not installed") from exc

        group_chat = GroupChat(
            agents=agents,
            messages=[],
            max_round=max_round,
            speaker_selection_method=speaker_selection_method,  # type: ignore[arg-type]
            **kwargs,
        )
        manager = GroupChatManager(groupchat=group_chat)
        return group_chat, manager

    # ------------------------------------------------------------------
    # Chat execution
    # ------------------------------------------------------------------

    async def initiate_chat(
        self,
        initiator: Any,
        recipient: Any,
        *,
        message: str | dict[str, Any],
        max_turns: int | None = None,
        silent: bool = False,
        workflow_name: str = "",
        chat_id: str = "",
        **kwargs: Any,
    ) -> Any:
        """Start an AG2 conversation. Wraps the sync AG2 API in a thread executor.

        Returns the AG2 chat result object.
        Raises AG2RunnerError on failure.
        """
        try:
            loop = asyncio.get_event_loop()
            chat_kwargs: dict[str, Any] = {
                "message": message,
                "silent": silent,
            }
            if max_turns is not None:
                chat_kwargs["max_turns"] = max_turns
            chat_kwargs.update(kwargs)

            result = await loop.run_in_executor(
                None,
                lambda: initiator.initiate_chat(recipient, **chat_kwargs),
            )
            return result
        except Exception as exc:
            logger.error(
                "AG2_CHAT_ERROR workflow=%s chat_id=%s: %s",
                workflow_name, chat_id, exc, exc_info=True,
            )
            raise AG2RunnerError(
                f"AG2 chat failed: {exc}",
                workflow_name=workflow_name,
                chat_id=chat_id,
            ) from exc

    # ------------------------------------------------------------------
    # Structured output extraction
    # ------------------------------------------------------------------

    def extract_last_message(self, chat_result: Any) -> str:
        """Extract the last message text from an AG2 chat result."""
        try:
            if hasattr(chat_result, "chat_history") and chat_result.chat_history:
                last = chat_result.chat_history[-1]
                if isinstance(last, dict):
                    return str(last.get("content", ""))
            if hasattr(chat_result, "summary"):
                return str(chat_result.summary or "")
        except Exception:
            pass
        return ""

    def get_chat_history(self, chat_result: Any) -> list[dict[str, Any]]:
        """Extract the full chat history from an AG2 chat result."""
        try:
            if hasattr(chat_result, "chat_history"):
                return list(chat_result.chat_history or [])
        except Exception:
            pass
        return []

    # ------------------------------------------------------------------
    # LLM config builder
    # ------------------------------------------------------------------

    def build_llm_config(
        self,
        *,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        timeout: int = 120,
        fallback_models: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build an AG2-compatible llm_config dict with optional fallback models.

        When ``fallback_models`` is provided (or ``LLM_FALLBACK_MODELS`` env var
        is set), the returned ``config_list`` includes multiple entries so AG2
        will try them in order on provider failure.
        """
        from mozaiksai.core.adapters.llm_fallback import (
            build_fallback_llm_config,
            get_healthy_config_list,
        )

        config = build_fallback_llm_config(
            primary_model=model,
            primary_api_key=api_key,
            fallback_models=fallback_models,
            temperature=temperature,
            timeout=timeout,
            seed=seed,
            extra=extra,
        )
        # Reorder config_list so healthy (circuit-CLOSED) providers come first
        config["config_list"] = get_healthy_config_list(config["config_list"])
        return config


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_adapter: AG2RunnerAdapter | None = None


def get_ag2_runner() -> AG2RunnerAdapter:
    """Return the process-wide AG2RunnerAdapter singleton."""
    global _adapter
    if _adapter is None:
        _adapter = AG2RunnerAdapter()
    return _adapter


__all__ = ["AG2RunnerAdapter", "AG2RunnerError", "get_ag2_runner"]
