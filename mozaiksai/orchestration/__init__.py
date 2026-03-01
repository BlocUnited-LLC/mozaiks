"""mozaiksai.orchestration — AI workflow orchestration façade.

This module provides the public API that platform / self-hosted apps use to
obtain a workflow orchestration runner:

    from mozaiks.orchestration import create_ai_workflow_runner

    runner = create_ai_workflow_runner()      # → async callable
    app    = core_build_runtime(ai_engine=runner)

Internally the runner is currently ``run_workflow_orchestration`` from the
core workflow module.  Wrapping it in a factory allows the orchestration
layer to evolve independently (pluggable engines, rate-limiting, tracing)
without breaking the platform contract.

Phase 4 additions:

    from mozaiks.orchestration import get_universal_orchestrator

    orchestrator = get_universal_orchestrator()  # → UniversalOrchestrator
    # Implements OrchestrationPort (run, resume, cancel, capabilities)

The UniversalOrchestrator sits at Layer 1.5: between single-GroupChat
execution and cross-workflow pack orchestration.  It adds decomposition
(pause → spawn N parallel sub-GroupChats → merge → resume parent).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

# Phase 4: UniversalOrchestrator public API
from mozaiksai.orchestration.decomposition import (
    AgentSignalDecomposition,
    ConfigDrivenDecomposition,
    DecompositionContext,
    DecompositionPlan,
    DecompositionStrategy,
    ExecutionMode,
    SubTask,
)
from mozaiksai.orchestration.groupchat_pool import GroupChatPool
from mozaiksai.orchestration.merge import (
    ChildResult,
    ConcatenateMerge,
    MergeContext,
    MergeResult,
    MergeStrategy,
    StructuredMerge,
)
from mozaiksai.orchestration.universal import (
    OrchestratorRun,
    RunState,
    UniversalOrchestrator,
    get_universal_orchestrator,
    reset_universal_orchestrator,
)


def create_ai_workflow_runner(
    *,
    hooks: Optional[dict[str, Any]] = None,
) -> Callable[..., Any]:
    """Return the default async workflow runner.

    Parameters
    ----------
    hooks : dict, optional
        Lifecycle hooks (``on_start``, ``on_complete``, ``on_fail``) that the
        orchestration layer will invoke around each workflow execution.

    Returns
    -------
    callable
        An async function with the same signature as
        ``mozaiksai.core.workflow.run_workflow_orchestration``.
    """
    from mozaiksai.core.workflow import run_workflow_orchestration

    if hooks:
        # Thin wrapper that fires hooks around the real runner
        import functools

        @functools.wraps(run_workflow_orchestration)
        async def _hooked_runner(*args: Any, **kwargs: Any) -> Any:
            on_start = hooks.get("on_start")
            on_complete = hooks.get("on_complete")
            on_fail = hooks.get("on_fail")

            if callable(on_start):
                await on_start(*args, **kwargs) if _is_coro(on_start) else on_start(*args, **kwargs)

            try:
                result = await run_workflow_orchestration(*args, **kwargs)
            except Exception as exc:
                if callable(on_fail):
                    await on_fail(exc, *args, **kwargs) if _is_coro(on_fail) else on_fail(exc, *args, **kwargs)
                raise
            else:
                if callable(on_complete):
                    await on_complete(result, *args, **kwargs) if _is_coro(on_complete) else on_complete(result, *args, **kwargs)
            return result

        return _hooked_runner

    return run_workflow_orchestration


def _is_coro(fn: Any) -> bool:
    import asyncio
    return asyncio.iscoroutinefunction(fn)


__all__ = [
    # Original
    "create_ai_workflow_runner",
    # Phase 4 — Decomposition
    "DecompositionStrategy",
    "DecompositionPlan",
    "DecompositionContext",
    "SubTask",
    "ExecutionMode",
    "ConfigDrivenDecomposition",
    "AgentSignalDecomposition",
    # Phase 4 — Merge
    "MergeStrategy",
    "MergeResult",
    "MergeContext",
    "ChildResult",
    "ConcatenateMerge",
    "StructuredMerge",
    # Phase 4 — Pool
    "GroupChatPool",
    # Phase 4 — Orchestrator
    "UniversalOrchestrator",
    "OrchestratorRun",
    "RunState",
    "get_universal_orchestrator",
    "reset_universal_orchestrator",
]
