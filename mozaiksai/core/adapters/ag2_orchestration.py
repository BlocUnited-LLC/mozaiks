# FILE: mozaiksai/core/adapters/ag2_orchestration.py
# DESCRIPTION: AG2-specific implementation of OrchestrationPort.
#
# This is one of the narrow files allowed to import AG2 types directly.
# When AG2 ships bidirectional streaming or a new engine is added, the adapter
# boundary changes instead of transport/runtime callers.
#
# Consumers (transport, runtime routes, workflow callers) interact
# exclusively through the OrchestrationPort protocol and never import AG2.
# ==============================================================================
"""AG2OrchestrationAdapter — wraps AG2 Network execution behind OrchestrationPort.

Responsibilities:
    - run()    → delegates to orchestration_patterns.run_workflow_orchestration()
    - resume() → same call with initial_agent_name_override set to resume_agent
    - cancel() → delegates to SimpleTransport.pause_background_workflow()
    - capabilities() → reports engine version and supported features

This adapter does not own workflow-specific task planning. AG2 beta provides the
agent and network execution substrate; Mozaiks owns deterministic contracts
around AG2, such as workflow YAML loading, structured-output validation, context
persistence, artifact materialization, and task-batch dependency contracts.

Architecture (Layer 1.5):

    ┌─────────────┐      ┌────────────────────────────────┐
    │  Transport   │─────►│  OrchestrationPort (protocol)  │
    │ (websocket)  │      │    run / resume / cancel        │
    └─────────────┘      └───────────────┬────────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  AG2OrchestrationAdapter     │
                          │  (this file)                 │
                          │  ┌────────────────────────┐  │
                          │  │ orchestration_patterns  │  │
                          │  │ + AG2NetworkRunner      │  │
                          │  └────────────────────────┘  │
                          └──────────────┬──────────────┘
                                         │ emits events
                          ┌──────────────▼──────────────┐
                          │ UnifiedEventDispatcher       │
                          │  ├─ Task batch observers     │
                          │  ├─ Workflow sequence hooks  │
                          │  └─ AutoToolHandler          │
                          └─────────────────────────────┘
"""

from __future__ import annotations

import asyncio
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.ports.orchestration import (
    ResumeRequest,
    RunRequest,
    RunResult,
    RunStatus,
)

logger = get_core_logger("ag2_orchestration_adapter")


def _ag2_version() -> str:
    """Return the installed AG2 version string."""
    import autogen

    return str(getattr(autogen, "__version__", "unknown"))


class AG2OrchestrationAdapter:
    """Implements :class:`OrchestrationPort` for the AG2 (autogen ≥0.11) engine.

    Lifecycle:
        1. Singleton per process — the runtime host creates one instance at startup.
        2. Transport calls ``run()`` / ``resume()`` / ``cancel()`` on it.
        3. Events flow to listeners through the UnifiedEventDispatcher, NOT through
           return values.  ``run()`` / ``resume()`` return a summary ``RunResult``.
    """

    def __init__(self) -> None:
        self._version = _ag2_version()

    # ------------------------------------------------------------------
    # OrchestrationPort.run
    # ------------------------------------------------------------------

    async def run(self, request: RunRequest) -> RunResult:
        """Start a workflow orchestration.

        Delegates to ``orchestration_patterns.run_workflow_orchestration()``
        which creates beta Agents, opens an AG2 Network workflow channel, runs
        the compiled transition graph through AG2, and projects resulting WAL
        packets through Mozaiks persistence/transport.

        The function is long-running (blocks until the loop finishes or pauses).
        Callers should wrap in ``asyncio.create_task()`` when fire-and-forget
        semantics are desired (e.g. ``_run_workflow_background``).
        """
        try:
            from mozaiksai.core.workflow.orchestration_patterns import (
                run_workflow_orchestration,
            )

            result = await run_workflow_orchestration(
                workflow_name=request.workflow_name,
                app_id=request.app_id,
                chat_id=request.chat_id,
                user_id=request.user_id,
                initial_message=request.initial_message,
                initial_agent_name_override=request.initial_agent_name_override,
                **request.extra,
            )

            return self._interpret_result(request, result)

        except asyncio.CancelledError:
            logger.info(
                "AG2 run cancelled (paused) workflow=%s chat=%s",
                request.workflow_name,
                request.chat_id,
            )
            return RunResult(
                status=RunStatus.CANCELLED,
                chat_id=request.chat_id,
                workflow_name=request.workflow_name,
            )

        except Exception as exc:
            logger.error(
                "AG2 run failed workflow=%s chat=%s: %s",
                request.workflow_name,
                request.chat_id,
                exc,
                exc_info=True,
            )
            return RunResult(
                status=RunStatus.FAILED,
                chat_id=request.chat_id,
                workflow_name=request.workflow_name,
                error="internal_error",
            )

    # ------------------------------------------------------------------
    # OrchestrationPort.resume
    # ------------------------------------------------------------------

    async def resume(self, request: ResumeRequest) -> RunResult:
        """Resume a paused workflow.

        Under the hood this is the same orchestration entrypoint, with
        ``initial_agent_name_override`` set to ``resume_agent``.

        In the current runtime, Mozaiks re-enters the orchestration loop for the
        same ``chat_id`` after loading canonical AG2 run-stream events through
        the workflow bootstrap layer. If ``injected_context`` is provided, it is
        persisted before re-entry so the next agent sees it in
        ``context_variables``.

        The installed AG2 Network API does not expose durable channel resume, so
        this remains a runtime-managed re-entry boundary rather than a wrapper
        over ``Hub.resume()``.
        """
        try:
            # If injected_context is provided (e.g. task batch results),
            # persist it BEFORE resuming so the next agent sees it.
            if request.injected_context:
                await self._inject_context_before_resume(request)

            from mozaiksai.core.workflow.orchestration_patterns import (
                run_workflow_orchestration,
            )

            result = await run_workflow_orchestration(
                workflow_name=request.workflow_name,
                app_id=request.app_id,
                chat_id=request.chat_id,
                user_id=request.user_id,
                initial_message=None,
                initial_agent_name_override=request.resume_agent,
                **request.extra,
            )

            return self._interpret_result(
                RunRequest(
                    workflow_name=request.workflow_name,
                    app_id=request.app_id,
                    chat_id=request.chat_id,
                    user_id=request.user_id,
                ),
                result,
            )

        except asyncio.CancelledError:
            return RunResult(
                status=RunStatus.CANCELLED,
                chat_id=request.chat_id,
                workflow_name=request.workflow_name,
            )

        except Exception as exc:
            logger.error(
                "AG2 resume failed workflow=%s chat=%s: %s",
                request.workflow_name,
                request.chat_id,
                exc,
                exc_info=True,
            )
            return RunResult(
                status=RunStatus.FAILED,
                chat_id=request.chat_id,
                workflow_name=request.workflow_name,
                error="internal_error",
            )

    # ------------------------------------------------------------------
    # OrchestrationPort.cancel
    # ------------------------------------------------------------------

    async def cancel(self, run_id: str) -> None:
        """Abort a running workflow by cancelling its background task.

        ``run_id`` is the ``chat_id``.
        """
        try:
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            if transport:
                cancelled = await transport.pause_background_workflow(
                    chat_id=run_id, reason="cancel_requested"
                )
                if cancelled:
                    logger.info("Cancelled AG2 run chat=%s", run_id)
                else:
                    logger.debug("No active task to cancel for chat=%s", run_id)
        except Exception as exc:
            logger.warning("Cancel failed for chat=%s: %s", run_id, exc)

    # ------------------------------------------------------------------
    # OrchestrationPort.capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        return {
            "engine": "ag2",
            "version": self._version,
            "supports_pause": True,
            "supports_resume": True,
            "supports_task_batches": True,
            "supports_cancel": True,
            "supports_bidirectional_stream": False,  # ← flips when AG2 ships it
            "supports_structured_outputs": True,
            "supports_network_transition_graph": True,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _interpret_result(
        self, request: RunRequest, raw: dict[str, Any] | None
    ) -> RunResult:
        """Convert the raw dict from ``run_workflow_orchestration`` into a typed RunResult."""
        if raw is None:
            return RunResult(
                status=RunStatus.COMPLETED,
                chat_id=request.chat_id,
                workflow_name=request.workflow_name,
            )

        run_completed = bool(raw.get("run_completed", False))
        awaiting_user_input = bool(raw.get("awaiting_user_input", False))
        run_status = raw.get("run_status")
        normalized_run_status = str(run_status or "").strip().lower()
        failed = bool(raw.get("failed")) or normalized_run_status == "failed"

        if failed:
            return RunResult(
                status=RunStatus.FAILED,
                chat_id=request.chat_id,
                workflow_name=request.workflow_name,
                usage=raw.get("usage"),
                error=str(raw.get("error") or "") or None,
            )

        if run_completed and not awaiting_user_input and normalized_run_status not in {"0", "in_progress", "paused"}:
            status = RunStatus.COMPLETED
        elif awaiting_user_input or not run_completed:
            status = RunStatus.PAUSED
        else:
            status = RunStatus.COMPLETED

        return RunResult(
            status=status,
            chat_id=request.chat_id,
            workflow_name=request.workflow_name,
            usage=raw.get("usage"),
        )

    async def _inject_context_before_resume(self, request: ResumeRequest) -> None:
        """Persist injected context into the chat session before resume.

        This ensures the next agent's prompt middleware can read the
        injected outputs from ``context_variables``.
        """
        if not request.injected_context:
            return
        try:
            from mozaiksai.core.data.persistence import AG2PersistenceManager
            from mozaiksai.core.multitenant import build_app_scope_filter

            pm = AG2PersistenceManager()
            coll = await pm._coll()
            await coll.update_one(
                {"_id": request.chat_id, **build_app_scope_filter(request.app_id)},
                {"$set": {f"extra_context.{k}": v for k, v in request.injected_context.items()}},
            )
            logger.debug(
                "Injected %d context keys before resume chat=%s",
                len(request.injected_context),
                request.chat_id,
            )
        except Exception as exc:
            logger.warning(
                "Failed to inject context before resume chat=%s: %s",
                request.chat_id,
                exc,
            )


# ---------------------------------------------------------------------------
# Module-level singleton accessor
# ---------------------------------------------------------------------------

_adapter: AG2OrchestrationAdapter | None = None


def get_ag2_adapter() -> AG2OrchestrationAdapter:
    """Return the process-wide AG2 adapter singleton."""
    global _adapter
    if _adapter is None:
        _adapter = AG2OrchestrationAdapter()
    return _adapter


__all__ = ["AG2OrchestrationAdapter", "get_ag2_adapter"]
