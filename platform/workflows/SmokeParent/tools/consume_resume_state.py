from __future__ import annotations

from typing import Any, Dict

from mozaiksai.core.workflow.pack.resume_contract import mark_resume_consumed


def _set_context_value(context_variables: Any, key: str, value: Any) -> None:
    try:
        if hasattr(context_variables, "set"):
            context_variables.set(key, value)
            return
    except Exception:
        pass
    try:
        context_variables[key] = value
        return
    except Exception:
        pass
    try:
        data = getattr(context_variables, "data", None)
        if isinstance(data, dict):
            data[key] = value
    except Exception:
        return


async def consume_resume_state(summary: str, worker_name: str, context_variables: Any, **_: Any) -> Dict[str, Any]:
    updates = mark_resume_consumed(context_variables)
    _set_context_value(context_variables, "smoke_presented_summary", summary)
    _set_context_value(context_variables, "smoke_presented_worker", worker_name)
    return {
        "status": "ok",
        "message": "MFJ resume nonce consumed.",
        "updates": updates,
        "summary": summary,
        "worker_name": worker_name,
    }
