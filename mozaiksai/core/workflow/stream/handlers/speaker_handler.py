# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/speaker_handler.py
# DESCRIPTION: Handler for SelectSpeakerEvent (turn transitions)
# ==============================================================================

"""
Speaker Selection Event Handler

Handles SelectSpeakerEvent when the conversation transitions between agents.

Responsibilities:
- Forward speaker selection to UI (thinking bubbles)
- Execute after_agent lifecycle tools for previous speaker
- Execute before_agent lifecycle tools for new speaker
- Record agent turn performance metrics
- Update turn tracking state
"""

import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

from autogen.events.agent_events import SelectSpeakerEvent

# Lifecycle trigger enum
try:
    from mozaiksai.core.workflow.execution.lifecycle import LifecycleTrigger
    HAS_LIFECYCLE = True
except ImportError:
    HAS_LIFECYCLE = False
    LifecycleTrigger = None  # type: ignore


class SelectSpeakerHandler(BaseEventHandler):
    """
    Handler for SelectSpeakerEvent.

    Manages turn transitions between agents including:
    - Performance metric recording for completed turns
    - Lifecycle trigger execution (before_agent, after_agent)
    - Realtime token logger context updates
    - UI notification for thinking bubbles
    """

    def event_types(self) -> Set[Type]:
        """Handle SelectSpeakerEvent."""
        return {SelectSpeakerEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle SelectSpeakerEvent for turn transitions.

        Args:
            event: SelectSpeakerEvent instance
            ctx: Stream context
            state: Stream state (turn tracking will be modified)

        Returns:
            select_speaker payload for UI
        """
        # Extract the new agent
        new_agent = getattr(event, "agent", None) or getattr(event, "sender", None)
        new_agent_name = None
        if new_agent:
            new_agent_name = getattr(new_agent, "name", None) or str(new_agent)

        # Handle previous agent's turn completion
        if state.turn_agent and state.turn_started is not None:
            await self._complete_previous_turn(ctx, state)

        # Execute before_agent lifecycle for new agent
        if new_agent_name and ctx.lifecycle_manager and HAS_LIFECYCLE:
            await self._trigger_before_agent(new_agent_name, ctx)

        # Update turn state
        previous_agent = state.update_turn(new_agent_name, time.perf_counter())

        ctx.wf_logger.debug(
            f"[{ctx.workflow_name_upper}] New turn started with agent={new_agent_name} "
            f"seq={state.sequence_counter} chat_id={ctx.chat_id}"
        )

        # Update realtime token logger context
        await self._update_realtime_context(new_agent_name, ctx)

        # Update streaming context for token attribution
        if new_agent_name:
            try:
                from mozaiksai.core.workflow.streaming import update_streaming_agent
                update_streaming_agent(new_agent_name)
            except Exception:
                pass  # Streaming may not be installed

        # Log handoff trace (debug)
        candidates = getattr(event, "agents", None)
        ctx.wf_logger.debug(
            f"[HANDOFF_TRACE] SelectSpeakerEvent candidates={candidates} selected={new_agent_name}"
        )

        # Build UI payload
        return {
            "kind": "select_speaker",
            "agent": new_agent_name,
            "previous_agent": previous_agent,
            "chat_id": ctx.chat_id,
            "sequence": state.sequence_counter,
        }

    async def _complete_previous_turn(
        self,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> None:
        """
        Complete the previous agent's turn.

        Records performance metrics and executes after_agent lifecycle.
        """
        duration = max(0.0, time.perf_counter() - (state.turn_started or 0))

        # Record turn performance
        try:
            await ctx.perf_mgr.record_agent_turn(
                chat_id=ctx.chat_id,
                agent_name=state.turn_agent,
                duration_sec=duration,
                model=None,
            )
        except Exception as perf_err:
            ctx.wf_logger.warning(
                f"Failed to record turn for {state.turn_agent}: {perf_err}"
            )

        # Execute after_agent lifecycle
        if ctx.lifecycle_manager and HAS_LIFECYCLE:
            try:
                await ctx.lifecycle_manager.trigger_after_agent(
                    agent_name=str(state.turn_agent),
                    context_variables=ctx.context_variables,
                )
            except Exception as lc_err:
                ctx.wf_logger.warning(
                    f" [{ctx.workflow_name_upper}] after_agent lifecycle tools failed "
                    f"for {state.turn_agent}: {lc_err}"
                )

    async def _trigger_before_agent(
        self,
        agent_name: str,
        ctx: "StreamContext",
    ) -> None:
        """Execute before_agent lifecycle tools for new agent."""
        try:
            await ctx.lifecycle_manager.trigger_before_agent(
                agent_name=agent_name,
                context_variables=ctx.context_variables,
            )
        except Exception as lc_err:
            ctx.wf_logger.warning(
                f" [{ctx.workflow_name_upper}] before_agent lifecycle tools failed "
                f"for {agent_name}: {lc_err}"
            )

    async def _update_realtime_context(
        self,
        agent_name: Optional[str],
        ctx: "StreamContext",
    ) -> None:
        """Update realtime token logger context for new agent."""
        if not agent_name:
            return
        try:
            from mozaiksai.core.observability.realtime_token_logger import (
                get_realtime_token_logger,
            )
            realtime_logger = get_realtime_token_logger()
            realtime_logger.set_active_agent(agent_name)
            ctx.wf_logger.debug(
                f"[REALTIME_TOKENS] Context updated for agent: {agent_name}"
            )
        except Exception as ctx_err:
            ctx.wf_logger.debug(f"Failed to update realtime token context: {ctx_err}")

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """SelectSpeakerEvent does not terminate the stream."""
        return False

    def priority(self) -> int:
        """Standard priority."""
        return 50
