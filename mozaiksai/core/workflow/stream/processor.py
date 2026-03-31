# ==============================================================================
# FILE: mozaiksai/core/workflow/stream/processor.py
# DESCRIPTION: Main event stream processor for AG2 orchestration
# ==============================================================================

"""
Event Stream Processor

Main entry point for processing AG2 event streams using handler dispatch.
Replaces the monolithic _stream_events() function with a clean architecture.

Usage:
    from mozaiksai.core.workflow.stream import (
        EventStreamProcessor,
        StreamContext,
        StreamState,
    )

    processor = EventStreamProcessor()
    ctx = StreamContext(chat_id=chat_id, app_id=app_id, ...)
    state = StreamState()
    result = await processor.process_stream(response, ctx, state)
"""

import logging
import os
import time
from typing import Any, Dict, Optional

from .context import StreamContext, StreamState
from .registry import EventHandlerRegistry
from .handlers import (
    TextEventHandler,
    InputRequestHandler,
    SelectSpeakerHandler,
    ToolCallHandler,
    ToolResponseHandler,
    CompletionHandler,
    UsageSummaryHandler,
    TransitionHandler,
    GroupChatRunHandler,
    GroupChatResumeHandler,
    ErrorHandler,
    DefaultEventHandler,
    StreamingEventHandler,
)

logger = logging.getLogger(__name__)

# Lifecycle trigger enum
try:
    from mozaiksai.core.workflow.execution.lifecycle import LifecycleTrigger
    HAS_LIFECYCLE = True
except ImportError:
    HAS_LIFECYCLE = False
    LifecycleTrigger = None  # type: ignore


class EventStreamProcessor:
    """
    Main entry point for processing AG2 event streams.

    Replaces the monolithic _stream_events() function with a handler-based
    architecture that uses AG2's modern event patterns.

    Features:
        - Handler dispatch based on event type
        - Automatic state management
        - Context diff tracking (verbose mode)
        - Lifecycle trigger integration
        - Error resilient (handlers can fail without crashing stream)

    Usage:
        processor = EventStreamProcessor()
        ctx = StreamContext(...)
        state = StreamState()
        result = await processor.process_stream(response, ctx, state)
    """

    def __init__(self, registry: Optional[EventHandlerRegistry] = None):
        """
        Initialize processor with optional custom registry.

        Args:
            registry: Custom handler registry. If None, uses default handlers.
        """
        self.registry = registry or self._create_default_registry()

    def _create_default_registry(self) -> EventHandlerRegistry:
        """Create registry with all standard handlers."""
        registry = EventHandlerRegistry()

        # Register handlers in priority order
        registry.register(StreamingEventHandler())  # Priority 10 - token streaming (highest)
        registry.register(TransitionHandler())      # Priority 10 - handoff detection
        registry.register(ErrorHandler())           # Priority 20 - error handling
        registry.register(CompletionHandler())      # Priority 50 - completion
        registry.register(UsageSummaryHandler())    # Priority 100 - usage tracking
        registry.register(InputRequestHandler())    # Priority 50 - input prompts
        registry.register(SelectSpeakerHandler())   # Priority 50 - turn transitions
        registry.register(ToolCallHandler())        # Priority 50 - tool calls
        registry.register(ToolResponseHandler())    # Priority 50 - tool responses
        registry.register(GroupChatRunHandler())    # Priority 100 - group chat init
        registry.register(GroupChatResumeHandler()) # Priority 100 - resume boundary
        registry.register(TextEventHandler())       # Priority 50 - messages

        # Default handler for unknown events
        registry.set_default_handler(DefaultEventHandler())

        return registry

    async def process_stream(
        self,
        response: Any,
        ctx: StreamContext,
        initial_state: Optional[StreamState] = None,
    ) -> Dict[str, Any]:
        """
        Process AG2 event stream using handler dispatch pattern.

        This is the main entry point that replaces the monolithic
        _stream_events() function.

        Args:
            response: AG2 response object. Supports either:
                - response.events async iterator
                - direct async iterator (AsyncRunIterResponse)
            ctx: Immutable stream context with services and configuration
            initial_state: Optional pre-initialized state

        Returns:
            Dict with final stream state for orchestration layer
        """
        state = initial_state or StreamState()
        state.response = response

        # Initialize verbose context tracking if enabled
        verbose_ctx = os.getenv("CONTEXT_VERBOSE_DEBUG", "0").strip() in {"1", "true", "True"}
        state.verbose_ctx = verbose_ctx
        if verbose_ctx:
            state.prev_ctx_snapshot = ctx.get_context_snapshot()
            ctx.wf_logger.info(
                f" [CONTEXT_VERBOSE] Baseline snapshot captured | "
                f"keys={len(state.prev_ctx_snapshot)}"
            )

        # Register input request registry with transport
        try:
            if ctx.transport:
                ctx.transport.register_orchestration_input_registry(
                    ctx.chat_id, state.pending_input_requests
                )
        except Exception as e:
            ctx.wf_logger.debug(
                f"Failed to register orchestration input registry for {ctx.chat_id}: {e}"
            )

        ctx.wf_logger.info(
            f"[EVENT_STREAM] Starting event processing loop for chat {ctx.chat_id}"
        )

        try:
            # Main event loop - AG2 modern pattern
            event_stream = getattr(response, "events", None)
            if event_stream is None and hasattr(response, "__aiter__"):
                event_stream = response
            if event_stream is None:
                raise TypeError(
                    f"Unsupported AG2 response type for stream processing: {type(response).__name__}"
                )

            async for event in event_stream:
                # Increment sequence
                state.sequence_counter += 1

                # First event logging
                if not state.first_event_logged:
                    ctx.wf_logger.info(
                        f" [{ctx.workflow_name_upper}] First event received: "
                        f"{event.__class__.__name__} chat_id={ctx.chat_id}"
                    )
                    state.first_event_logged = True

                # Event trace logging
                self._log_event_trace(event, ctx)

                # Dispatch to handler
                payload = await self.registry.dispatch(event, ctx, state)

                # Send to transport if payload returned
                if payload and ctx.transport:
                    # Check for visibility flags before sending
                    if not self._should_suppress_payload(payload, ctx, state):
                        await self._send_to_transport(payload, ctx)

                # Check for stream termination
                if self.registry.should_break(event, state):
                    ctx.wf_logger.debug(
                        f" [{ctx.workflow_name_upper}] Handler requested stream break "
                        f"after {event.__class__.__name__}"
                    )
                    break

                # Context diff tracking (post-event)
                if state.verbose_ctx:
                    await self._track_context_diff(ctx, state)

        except Exception as loop_err:
            ctx.wf_logger.error(
                f" [{ctx.workflow_name_upper}] Event loop failure: {loop_err}"
            )
            state.run_completed = False

        finally:
            # Final turn completion if needed
            if state.turn_agent and state.turn_started is not None:
                await self._complete_final_turn(ctx, state)

            # Lifecycle: after_chat trigger
            await self._trigger_after_chat(ctx, state)

            # Cancel zombie AG2 task if handoff_to_user
            if state.handoff_to_user:
                self._cancel_ag2_task(response, ctx)

        return state.to_result_dict()

    def _log_event_trace(self, event: Any, ctx: StreamContext) -> None:
        """Log event for debugging."""
        event_class = event.__class__.__name__
        ctx.wf_logger.debug(f" [EVENT_TRACE] {event_class} event received")

    def _should_suppress_payload(
        self,
        payload: Dict[str, Any],
        ctx: StreamContext,
        state: StreamState,
    ) -> bool:
        """Check if payload should be suppressed from UI."""
        # Check for hide flag from derived context hooks
        if payload.get("_mozaiks_hide"):
            return True
        return False

    async def _send_to_transport(
        self,
        payload: Dict[str, Any],
        ctx: StreamContext,
    ) -> None:
        """Send event payload to UI transport."""
        try:
            await ctx.transport.send_event_to_ui(payload, ctx.chat_id)
        except Exception as e:
            ctx.wf_logger.debug(
                f"Failed to send event to UI for {ctx.chat_id}: {e}"
            )

    async def _track_context_diff(
        self,
        ctx: StreamContext,
        state: StreamState,
    ) -> None:
        """Track and log context variable changes."""
        try:
            current_snapshot = ctx.get_context_snapshot()

            # Compute diff
            added = [k for k in current_snapshot.keys() if k not in state.prev_ctx_snapshot]
            removed = [k for k in state.prev_ctx_snapshot.keys() if k not in current_snapshot]
            changed = [
                k for k in current_snapshot.keys()
                if k in state.prev_ctx_snapshot and current_snapshot[k] != state.prev_ctx_snapshot[k]
            ]

            # Trigger on_context_change lifecycle for changed variables
            if changed and ctx.lifecycle_manager and HAS_LIFECYCLE:
                for context_key in changed:
                    try:
                        old_value = state.prev_ctx_snapshot.get(context_key)
                        new_value = current_snapshot.get(context_key)

                        await ctx.lifecycle_manager.execute_trigger(
                            trigger=LifecycleTrigger.ON_CONTEXT_CHANGE,
                            workflow_name=ctx.workflow_name,
                            chat_id=ctx.chat_id,
                            app_id=ctx.app_id,
                            context_key=context_key,
                            old_value=old_value,
                            new_value=new_value,
                            context_variables=ctx.context_variables,
                        )
                    except Exception as lc_err:
                        ctx.wf_logger.debug(
                            f" [{ctx.workflow_name_upper}] on_context_change lifecycle "
                            f"failed for {context_key}: {lc_err}"
                        )

            # Log diff
            if added or removed or changed:
                ctx.wf_logger.info(
                    f" [CONTEXT_VERBOSE] Diff | added={added} removed={removed} changed={changed}"
                )

            # Update previous snapshot
            state.prev_ctx_snapshot = current_snapshot

        except Exception as diff_err:
            ctx.wf_logger.debug(f"Context diff tracking failed: {diff_err}")

    async def _complete_final_turn(
        self,
        ctx: StreamContext,
        state: StreamState,
    ) -> None:
        """Complete the final agent's turn if needed."""
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
            ctx.wf_logger.debug(
                f"Failed to record final turn for {state.turn_agent}: {perf_err}"
            )

        # Execute after_agent lifecycle
        if ctx.lifecycle_manager and HAS_LIFECYCLE:
            try:
                await ctx.lifecycle_manager.trigger_after_agent(
                    agent_name=str(state.turn_agent),
                    context_variables=ctx.context_variables,
                )
            except Exception as lc_err:
                ctx.wf_logger.debug(
                    f" [{ctx.workflow_name_upper}] Final after_agent lifecycle failed: {lc_err}"
                )

    async def _trigger_after_chat(
        self,
        ctx: StreamContext,
        state: StreamState,
    ) -> None:
        """Execute after_chat lifecycle tools."""
        if not ctx.lifecycle_manager or not HAS_LIFECYCLE:
            return

        try:
            await ctx.lifecycle_manager.execute_trigger(
                trigger=LifecycleTrigger.AFTER_CHAT,
                workflow_name=ctx.workflow_name,
                chat_id=ctx.chat_id,
                app_id=ctx.app_id,
                context_variables=ctx.context_variables,
            )
        except Exception as e:
            ctx.wf_logger.warning(
                f" [{ctx.workflow_name_upper}] after_chat lifecycle failed: {e}"
            )

    def _cancel_ag2_task(self, response: Any, ctx: StreamContext) -> None:
        """Cancel the internal AG2 task that may block on IOStream.input()."""
        task = getattr(response, "_task", None)
        if task is None:
            task = getattr(response, "task", None)

        if task is not None and hasattr(task, "cancel"):
            task.cancel()
            ctx.wf_logger.info(
                f" [{ctx.workflow_name_upper}] Cancelled zombie AG2 task after handoff_to_user"
            )
        else:
            ctx.wf_logger.debug(
                f" [{ctx.workflow_name_upper}] No AG2 task to cancel "
                f"(response type: {type(response).__name__})"
            )
