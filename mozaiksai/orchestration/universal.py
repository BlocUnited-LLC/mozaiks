# ==============================================================================
# FILE: orchestration/universal.py
# DESCRIPTION: UniversalOrchestrator — Layer 1.5 orchestration engine.
#
# Sits between single-GroupChat execution (AG2OrchestrationAdapter) and
# cross-workflow pack orchestration (WorkflowPackCoordinator).
#
# It controls GroupChats — pause, decompose, spawn parallel sub-GroupChats,
# merge results, and resume the parent.
# ==============================================================================
"""
UniversalOrchestrator
=====================

The UniversalOrchestrator implements ``OrchestrationPort`` and adds
decomposition/merge capabilities on top of the existing AG2 engine.

Lifecycle:

1. Caller invokes ``run(RunRequest)`` → async iterator of ``DomainEvent``s.
2. **Happy path** (no decomposition):
   - Delegates directly to ``AG2OrchestrationAdapter.run()``
   - Behavior is identical to the existing system.
3. **Decomposition path**:
   - Before executing, checks ``DecompositionStrategy.detect()``
   - If a plan is returned:
     a. Emits ``orchestration.decomposition_started``
     b. Creates ``GroupChatPool`` with the plan's sub-tasks
     c. Yields all sub-task lifecycle ``DomainEvent``s
     d. Merges results via ``MergeStrategy``
     e. Emits ``orchestration.merge_completed``
     f. Optionally resumes the parent GroupChat with the merged output
4. ``resume(ResumeRequest)`` delegates to the adapter.
5. ``cancel(run_id)`` terminates the active run.

State Model:

``OrchestratorRun`` tracks: parent run_id, child run_ids, current state
(INITIALIZING → RUNNING → DECOMPOSING → MERGING → COMPLETED/FAILED).

Future Streaming Evolution:

When the underlying agentic framework evolves to native async streaming,
the ``GroupChatPool`` swaps its internals. The UniversalOrchestrator itself
does NOT change — it speaks ``OrchestrationPort``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator

from mozaiksai.core.contracts.events import EVENT_SCHEMA_VERSION, DomainEvent
from mozaiksai.core.contracts.runner import ResumeRequest, RunRequest
from mozaiksai.core.ports.orchestration import OrchestrationPort

from mozaiksai.orchestration.decomposition import (
    AgentSignalDecomposition,
    ConfigDrivenDecomposition,
    DecompositionContext,
    DecompositionPlan,
    DecompositionStrategy,
)
from mozaiksai.orchestration.groupchat_pool import GroupChatPool
from mozaiksai.orchestration.merge import (
    ChildResult,
    ConcatenateMerge,
    MergeContext,
    MergeStrategy,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run state model
# ---------------------------------------------------------------------------

class RunState(str, Enum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    DECOMPOSING = "decomposing"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class OrchestratorRun:
    """Internal state tracker for one orchestrator invocation."""
    run_id: str
    parent_run_id: str | None = None
    workflow_name: str = ""
    state: RunState = RunState.INITIALIZING
    child_run_ids: list[str] = field(default_factory=list)
    decomposition_plan: DecompositionPlan | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Domain event helper
# ---------------------------------------------------------------------------

_seq_counter: int = 0


def _next_seq() -> int:
    global _seq_counter
    s = _seq_counter
    _seq_counter += 1
    return s


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    event_type: str,
    run_id: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        seq=_next_seq(),
        occurred_at=_now(),
        run_id=run_id,
        schema_version=EVENT_SCHEMA_VERSION,
        payload=payload,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# UniversalOrchestrator
# ---------------------------------------------------------------------------

class UniversalOrchestrator:
    """Layer 1.5 orchestration engine.

    Implements ``OrchestrationPort``.  Wraps the existing AG2 adapter for
    the happy path and adds decomposition/merge on top.

    Parameters
    ----------
    decomposition_strategies : list[DecompositionStrategy], optional
        Strategies to try in order.  Defaults to
        ``[ConfigDrivenDecomposition(), AgentSignalDecomposition()]``.
    merge_strategy : MergeStrategy, optional
        How to combine sub-chat results.  Defaults to ``ConcatenateMerge()``.
    auto_resume_parent : bool
        If True (default), the orchestrator resumes the parent GroupChat
        after merging sub-task results.
    """

    def __init__(
        self,
        *,
        decomposition_strategies: list[DecompositionStrategy] | None = None,
        merge_strategy: MergeStrategy | None = None,
        auto_resume_parent: bool = True,
    ) -> None:
        self._strategies: list[DecompositionStrategy] = decomposition_strategies or [
            ConfigDrivenDecomposition(),
            AgentSignalDecomposition(),
        ]
        self._merge: MergeStrategy = merge_strategy or ConcatenateMerge()
        self._auto_resume = auto_resume_parent
        self._active_runs: dict[str, OrchestratorRun] = {}

    # -------------------------------------------------------------------
    # OrchestrationPort implementation
    # -------------------------------------------------------------------

    async def run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        """Execute a workflow, with optional decomposition."""
        run = OrchestratorRun(
            run_id=request.run_id,
            workflow_name=request.workflow_name,
        )
        self._active_runs[run.run_id] = run

        try:
            # ----------------------------------------------------------
            # Step 1: Check for config-driven decomposition BEFORE running
            # ----------------------------------------------------------
            plan = self._detect_decomposition(request)

            if plan is not None:
                # ---- DECOMPOSITION PATH ----
                run.state = RunState.DECOMPOSING
                run.decomposition_plan = plan

                async for event in self._execute_decomposition(run, request, plan):
                    yield event
            else:
                # ---- HAPPY PATH (single GroupChat) ----
                run.state = RunState.RUNNING
                async for event in self._execute_single(run, request):
                    yield event

            run.state = RunState.COMPLETED
            run.completed_at = _now()

            yield _event(
                event_type="orchestration.run_completed",
                run_id=run.run_id,
                payload={
                    "workflow_name": run.workflow_name,
                    "state": run.state.value,
                    "decomposed": run.decomposition_plan is not None,
                    "child_count": len(run.child_run_ids),
                },
            )

        except Exception as exc:
            run.state = RunState.FAILED
            run.error = str(exc)
            run.completed_at = _now()
            logger.error("[ORCHESTRATOR] Run %s failed: %s", run.run_id, exc, exc_info=True)

            yield _event(
                event_type="orchestration.run_failed",
                run_id=run.run_id,
                payload={
                    "workflow_name": run.workflow_name,
                    "error": str(exc),
                },
            )

        finally:
            # Keep terminated runs for inspection, but clean up after a while
            self._active_runs.pop(run.run_id, None)

    async def resume(self, request: ResumeRequest) -> AsyncIterator[DomainEvent]:
        """Resume a paused workflow run."""
        from mozaiksai.core.ports.ag2_adapter import get_ag2_orchestration_adapter
        adapter = get_ag2_orchestration_adapter()
        async for event in adapter.resume(request):
            yield event

    async def cancel(self, run_id: str) -> None:
        """Cancel an active run."""
        run = self._active_runs.get(run_id)
        if run:
            run.state = RunState.CANCELLED
            run.completed_at = _now()
            logger.info("[ORCHESTRATOR] Run %s cancelled", run_id)
        # Delegate to adapter for cleanup
        from mozaiksai.core.ports.ag2_adapter import get_ag2_orchestration_adapter
        adapter = get_ag2_orchestration_adapter()
        await adapter.cancel(run_id)

    def capabilities(self) -> dict[str, Any]:
        return {
            "engine": "universal_orchestrator",
            "decomposition": True,
            "merge": True,
            "streaming": False,  # Will be True when native async streaming is adopted
            "cancel": True,
            "resume": True,
            "strategies": [type(s).__name__ for s in self._strategies],
            "merge_strategy": type(self._merge).__name__,
        }

    # -------------------------------------------------------------------
    # Internal: decomposition detection
    # -------------------------------------------------------------------

    def _detect_decomposition(self, request: RunRequest) -> DecompositionPlan | None:
        """Try each strategy in order.  First non-None plan wins."""
        ctx = self._build_decomposition_context(request)

        for strategy in self._strategies:
            try:
                plan = strategy.detect(ctx)
                if plan is not None and plan.task_count > 0:
                    logger.info(
                        "[ORCHESTRATOR] Decomposition detected by %s: %d sub-tasks (%s)",
                        type(strategy).__name__,
                        plan.task_count,
                        plan.execution_mode.value,
                    )
                    return plan
            except Exception as exc:
                logger.warning(
                    "[ORCHESTRATOR] Strategy %s.detect() failed: %s",
                    type(strategy).__name__,
                    exc,
                )

        return None

    def _build_decomposition_context(self, request: RunRequest) -> DecompositionContext:
        """Build a DecompositionContext from the RunRequest."""
        workflow_config = self._load_workflow_config(request.workflow_name)
        pack_config = self._load_pack_config(request.workflow_name)

        return DecompositionContext(
            run_id=request.run_id,
            workflow_name=request.workflow_name,
            app_id=request.app_id or "",
            user_id=request.user_id or "",
            workflow_config=workflow_config,
            pack_config=pack_config,
            context_variables=request.payload,
        )

    # -------------------------------------------------------------------
    # Internal: single GroupChat execution (happy path)
    # -------------------------------------------------------------------

    async def _execute_single(
        self,
        run: OrchestratorRun,
        request: RunRequest,
    ) -> AsyncIterator[DomainEvent]:
        """Delegate to the AG2 adapter for a normal single-chat run."""
        from mozaiksai.core.ports.ag2_adapter import get_ag2_orchestration_adapter
        adapter = get_ag2_orchestration_adapter()

        async for event in adapter.run(request):
            yield event

    # -------------------------------------------------------------------
    # Internal: decomposition execution
    # -------------------------------------------------------------------

    async def _execute_decomposition(
        self,
        run: OrchestratorRun,
        request: RunRequest,
        plan: DecompositionPlan,
    ) -> AsyncIterator[DomainEvent]:
        """Execute a full decompose → pool → merge → (optional resume) cycle."""

        # 1. Emit decomposition started
        yield _event(
            event_type="orchestration.decomposition_started",
            run_id=run.run_id,
            payload={
                "workflow_name": run.workflow_name,
                "task_count": plan.task_count,
                "execution_mode": plan.execution_mode.value,
                "reason": plan.reason,
                "sub_tasks": [
                    {"task_id": st.task_id, "workflow_name": st.workflow_name}
                    for st in plan.sub_tasks
                ],
            },
        )

        # 2. Execute sub-tasks via pool
        pool = GroupChatPool(
            parent_run_id=run.run_id,
            parent_app_id=request.app_id or "",
            parent_user_id=request.user_id or "",
        )

        async for event in pool.execute(plan):
            yield event
            # Track child run IDs
            if event.event_type == "process.started":
                child_id = event.payload.get("run_id")
                if child_id:
                    run.child_run_ids.append(child_id)

        # 3. Merge results
        run.state = RunState.MERGING
        child_results = pool.results

        merge_ctx = MergeContext(
            parent_run_id=run.run_id,
            parent_workflow_name=run.workflow_name,
            child_results=child_results,
            parent_context_variables=request.payload,
            strategy_metadata=plan.strategy_metadata,
        )

        merge_result = self._merge.merge(merge_ctx)

        yield _event(
            event_type="orchestration.merge_completed",
            run_id=run.run_id,
            payload={
                "workflow_name": run.workflow_name,
                "total": len(child_results),
                "succeeded": merge_result.succeeded_count,
                "failed": merge_result.failed_count,
                "all_succeeded": merge_result.all_succeeded,
                "summary_preview": merge_result.summary_message[:500],
            },
        )

        # 4. Optionally resume parent with merged output
        if self._auto_resume and plan.resume_agent:
            async for event in self._resume_parent_with_merge(
                run, request, plan.resume_agent, merge_result.summary_message
            ):
                yield event

    async def _resume_parent_with_merge(
        self,
        run: OrchestratorRun,
        original_request: RunRequest,
        resume_agent: str,
        summary_message: str,
    ) -> AsyncIterator[DomainEvent]:
        """Resume the parent GroupChat with the merged summary."""
        yield _event(
            event_type="orchestration.parent_resuming",
            run_id=run.run_id,
            payload={
                "workflow_name": run.workflow_name,
                "resume_agent": resume_agent,
                "summary_length": len(summary_message),
            },
        )

        resume_request = ResumeRequest(
            run_id=run.run_id,
            workflow_name=original_request.workflow_name,
            app_id=original_request.app_id,
            user_id=original_request.user_id,
            chat_id=original_request.chat_id,
            metadata={
                "resume_agent": resume_agent,
                "merge_summary": summary_message,
                "decomposition_run": True,
            },
        )

        from mozaiksai.core.ports.ag2_adapter import get_ag2_orchestration_adapter
        adapter = get_ag2_orchestration_adapter()

        async for event in adapter.resume(resume_request):
            yield event

    # -------------------------------------------------------------------
    # Config loading helpers
    # -------------------------------------------------------------------

    @staticmethod
    def _load_workflow_config(workflow_name: str) -> dict[str, Any] | None:
        """Load workflow YAML config via the UnifiedWorkflowManager."""
        try:
            from mozaiksai.core.workflow.workflow_manager import workflow_manager
            return workflow_manager.get_config(workflow_name)
        except Exception:
            return None

    @staticmethod
    def _load_pack_config(workflow_name: str) -> dict[str, Any] | None:
        """Load pack graph config if it exists."""
        try:
            from pathlib import Path
            import json
            path = Path("workflows") / workflow_name / "_pack" / "workflow_graph.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

_instance: UniversalOrchestrator | None = None


def get_universal_orchestrator(
    *,
    decomposition_strategies: list[DecompositionStrategy] | None = None,
    merge_strategy: MergeStrategy | None = None,
    auto_resume_parent: bool = True,
) -> UniversalOrchestrator:
    """Get or create the singleton UniversalOrchestrator."""
    global _instance
    if _instance is None:
        _instance = UniversalOrchestrator(
            decomposition_strategies=decomposition_strategies,
            merge_strategy=merge_strategy,
            auto_resume_parent=auto_resume_parent,
        )
    return _instance


def reset_universal_orchestrator() -> None:
    """Reset the singleton (for testing)."""
    global _instance, _seq_counter
    _instance = None
    _seq_counter = 0


__all__ = [
    "OrchestratorRun",
    "RunState",
    "UniversalOrchestrator",
    "get_universal_orchestrator",
    "reset_universal_orchestrator",
]
