"""mozaiksai.orchestration — AI workflow orchestration façade.

This module provides the public API that platform / self-hosted apps use to
obtain a workflow orchestration runner:

    from mozaiks.orchestration import create_ai_workflow_runner

    runner = create_ai_workflow_runner()      # → async callable
    app    = core_create_app(ai_engine=runner)

Internally the runner is currently ``run_workflow_orchestration`` from the
core workflow module.  Wrapping it in a factory allows the orchestration
layer to evolve independently (pluggable engines, rate-limiting, tracing)
without breaking the platform contract.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


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


__all__ = ["create_ai_workflow_runner"]
