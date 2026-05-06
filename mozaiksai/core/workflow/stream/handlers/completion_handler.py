# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/handlers/completion_handler.py
# DESCRIPTION: Handler for RunCompletionEvent and UsageSummaryEvent
# ==============================================================================

"""
Completion Event Handler

Handles workflow completion and usage summary events:
- RunCompletionEvent: Signals end of AG2 workflow execution
- UsageSummaryEvent: Reports token usage and costs

RunCompletionEvent terminates the event stream and marks the run complete.
UsageSummaryEvent logs usage statistics but doesn't terminate.

Also dispatches webhook callbacks for backend-to-backend integrations when
the chat session has a webhook_url configured.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Type

from .base import BaseEventHandler
from mozaiksai.core.data.models import WorkflowStatus

if TYPE_CHECKING:
    from ..context import StreamContext, StreamState

# Import AG2 event types
from autogen.events.agent_events import RunCompletionEvent
from autogen.events.client_events import UsageSummaryEvent


class CompletionHandler(BaseEventHandler):
    """
    Handler for RunCompletionEvent.

    When a run completes:
    1. Logs completion with execution summary
    2. Diagnoses any remaining after_work chains
    3. Marks stream state as completed
    4. Returns run_complete payload for UI
    5. Signals event loop to break
    """

    def event_types(self) -> Set[Type]:
        """Handle RunCompletionEvent."""
        return {RunCompletionEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle RunCompletionEvent.

        Logs completion diagnostics and marks stream as completed.

        Args:
            event: RunCompletionEvent instance
            ctx: Stream context
            state: Stream state (will be modified)

        Returns:
            run_complete payload for UI
        """
        # Diagnostic: Check for incomplete after_work chains
        try:
            remaining_after_work = []
            for agent_name, agent_obj in ctx.agents.items():
                try:
                    handoffs = getattr(agent_obj, "handoffs", None)
                    if handoffs and hasattr(handoffs, "after_work"):
                        target = getattr(handoffs, "after_work", None)
                        if target and agent_name not in state.executed_agents:
                            target_name = getattr(
                                getattr(target, "target", None),
                                "name",
                                getattr(target, "target", None)
                            )
                            remaining_after_work.append(f"{agent_name}->{target_name}")
                except Exception:
                    pass

            if remaining_after_work:
                ctx.wf_logger.warning(
                    f" [{ctx.workflow_name_upper}] RunCompletionEvent early. "
                    f"Executed: {sorted(state.executed_agents)} | "
                    f"Pending after_work chain: {remaining_after_work}"
                )
        except Exception as diag_err:
            ctx.wf_logger.debug(f"Early termination diagnostics failed: {diag_err}")

        awaiting_user_input = bool(state.awaiting_user_input or state.pending_input_requests)
        workflow_complete = not awaiting_user_input

        if workflow_complete:
            state.awaiting_user_input = False
            state.pending_input_requests.clear()
            try:
                await ctx.persistence_manager.clear_pending_input_request(
                    chat_id=ctx.chat_id,
                    app_id=ctx.app_id,
                )
            except Exception as clear_err:
                ctx.wf_logger.debug(
                    f"Failed clearing pending input request on completion for {ctx.chat_id}: {clear_err}"
                )

        # Log completion summary
        ctx.wf_logger.info(
            f" [{ctx.workflow_name_upper}] Run complete chat_id={ctx.chat_id} "
            f"events={state.sequence_counter} executed_agents={sorted(state.executed_agents)} "
            f"workflow_complete={workflow_complete}"
        )

        # Mark stream state
        state.run_completed = workflow_complete
        # Dispatch webhook callback if configured (fire-and-forget)
        asyncio.create_task(self._dispatch_webhook(ctx, state))

        # Build run_complete payload
        return {
            "kind": "run_complete",
            "agent": state.turn_agent or "workflow",
            "chat_id": ctx.chat_id,
            "sequence": state.sequence_counter,
            "executed_agents": list(state.executed_agents),
            "status": int(
                WorkflowStatus.COMPLETED
                if workflow_complete
                else WorkflowStatus.IN_PROGRESS
            ),
            "reason": "finished" if workflow_complete else "awaiting_user_input",
            "awaiting_user_input": awaiting_user_input,
        }

    async def _dispatch_webhook(
        self,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> None:
        """Dispatch webhook callback if chat session has webhook_url configured."""
        if not state.run_completed:
            return
        try:
            from mozaiksai.core.transport.webhook_dispatcher import dispatch_completion_webhook

            # Get app_id and user_id from context
            app_id = getattr(ctx, "app_id", None) or ctx.context_variables.get("app_id", "unknown")
            user_id = getattr(ctx, "user_id", None) or ctx.context_variables.get("user_id", "unknown")

            await dispatch_completion_webhook(
                chat_id=ctx.chat_id,
                workflow_name=ctx.workflow_name,
                app_id=app_id,
                user_id=user_id,
                status="completed",
                executed_agents=list(state.executed_agents),
                final_context=dict(ctx.context_variables) if ctx.context_variables else None,
            )
        except Exception as webhook_err:
            ctx.wf_logger.debug(f"Webhook dispatch failed (non-blocking): {webhook_err}")

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """Break on RunCompletionEvent."""
        return True


class UsageSummaryHandler(BaseEventHandler):
    """
    Handler for UsageSummaryEvent.

    Logs token usage and cost information. Does not terminate the stream.
    """

    def event_types(self) -> Set[Type]:
        """Handle UsageSummaryEvent."""
        return {UsageSummaryEvent}

    async def handle(
        self,
        event: Any,
        ctx: "StreamContext",
        state: "StreamState",
    ) -> Optional[Dict[str, Any]]:
        """
        Handle UsageSummaryEvent by logging usage statistics.

        Args:
            event: UsageSummaryEvent instance
            ctx: Stream context
            state: Stream state

        Returns:
            usage_summary payload for UI (optional logging)
        """
        try:
            content_obj = getattr(event, "content", None)
            agg_prompt = getattr(content_obj, "prompt_tokens", 0) if content_obj else 0
            agg_completion = getattr(content_obj, "completion_tokens", 0) if content_obj else 0
            agg_cost = getattr(content_obj, "cost", 0.0) if content_obj else 0.0

            ctx.wf_logger.info(
                f"[USAGE_SUMMARY] prompt={agg_prompt} completion={agg_completion} cost=${agg_cost:.4f}"
            )

            return {
                "kind": "usage_summary",
                "prompt_tokens": agg_prompt,
                "completion_tokens": agg_completion,
                "total_tokens": agg_prompt + agg_completion,
                "cost": agg_cost,
                "chat_id": ctx.chat_id,
            }
        except Exception as summary_err:
            ctx.wf_logger.debug(f"UsageSummaryEvent logging failed: {summary_err}")
            return None

    def should_break(self, event: Any, state: "StreamState") -> bool:
        """UsageSummaryEvent does not terminate the stream."""
        return False
