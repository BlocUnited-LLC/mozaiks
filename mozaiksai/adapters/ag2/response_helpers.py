"""Helpers for accessing AG2-specific attributes on response objects.

Keeps AG2 internal implementation details (``_task_ref``, etc.) out of the
engine layer.  The engine calls the helpers; only the adapter layer knows
about AG2's internal response structure.
"""
from __future__ import annotations

import logging
from typing import Any, Optional


def cancel_ag2_task(
    response: Any,
    *,
    logger: Optional[logging.Logger] = None,
    label: str = "",
) -> bool:
    """Cancel the AG2 internal asyncio task stored on ``response._task_ref``.

    AG2's ``a_run_group_chat()`` stores the running task as ``_task_ref`` on
    the response object.  When the orchestration loop breaks early (e.g.
    handoff_to_user), this task may block forever on ``IOStream.input()``
    and must be cancelled to prevent resource leaks.

    Parameters
    ----------
    response : Any
        The ``AsyncRunResponse`` (or equivalent) returned by AG2.
    logger : logging.Logger | None
        Optional logger for info/debug messages.
    label : str
        Short identifier used in log messages (e.g. workflow name upper).

    Returns
    -------
    bool
        ``True`` if a pending task was found and cancelled, ``False`` otherwise.
    """
    try:
        ag2_task = getattr(response, "_task_ref", None)
        if ag2_task and not ag2_task.done():
            ag2_task.cancel()
            if logger:
                logger.info(
                    f" [{label}] Cancelled frozen AG2 task after handoff_to_user"
                )
            return True
    except Exception as cancel_err:
        if logger:
            logger.debug(f" [{label}] AG2 task cancel failed: {cancel_err}")
    return False


__all__ = ["cancel_ag2_task"]
