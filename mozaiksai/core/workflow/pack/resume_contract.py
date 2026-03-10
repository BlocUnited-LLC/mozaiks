from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable

MFJ_RESUME_PENDING_KEY = "_mfj_resume_pending"
MFJ_RESUME_TARGET_KEY = "_mfj_resume_target_agent"
MFJ_RESUME_ENTRY_KEY = "_mfj_resume_entry_agent"
MFJ_RESUME_NONCE_KEY = "_mfj_resume_nonce"
MFJ_RESUME_CONSUMED_NONCE_KEY = "_mfj_resume_consumed_nonce"
MFJ_RESUME_TRIGGER_ID_KEY = "_mfj_resume_trigger_id"
MFJ_RESUME_CYCLE_KEY = "_mfj_resume_cycle"
MFJ_RESUME_INJECT_AS_KEY = "_mfj_resume_inject_as"
MFJ_RESUME_SUCCEEDED_COUNT_KEY = "_mfj_resume_succeeded_count"
MFJ_RESUME_FAILED_COUNT_KEY = "_mfj_resume_failed_count"
MFJ_RESUME_TS_KEY = "_mfj_resume_timestamp"

MFJ_RUNTIME_CONTEXT_PREFIXES: tuple[str, ...] = ("_mfj_", "mfj_")

MFJ_RESUME_CONTEXT_KEYS: tuple[str, ...] = (
    MFJ_RESUME_PENDING_KEY,
    MFJ_RESUME_TARGET_KEY,
    MFJ_RESUME_ENTRY_KEY,
    MFJ_RESUME_NONCE_KEY,
    MFJ_RESUME_CONSUMED_NONCE_KEY,
    MFJ_RESUME_TRIGGER_ID_KEY,
    MFJ_RESUME_CYCLE_KEY,
    MFJ_RESUME_INJECT_AS_KEY,
    MFJ_RESUME_SUCCEEDED_COUNT_KEY,
    MFJ_RESUME_FAILED_COUNT_KEY,
    MFJ_RESUME_TS_KEY,
)


def is_runtime_resume_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return key.startswith(MFJ_RUNTIME_CONTEXT_PREFIXES)


def build_resume_context_payload(
    *,
    trigger_id: str,
    cycle: int,
    inject_as: str,
    resume_entry_agent: str,
    resume_target_agent: str,
    resume_nonce: str,
    succeeded_count: int,
    failed_count: int,
) -> Dict[str, Any]:
    return {
        MFJ_RESUME_PENDING_KEY: True,
        MFJ_RESUME_TARGET_KEY: str(resume_target_agent),
        MFJ_RESUME_ENTRY_KEY: str(resume_entry_agent),
        MFJ_RESUME_NONCE_KEY: str(resume_nonce),
        MFJ_RESUME_TRIGGER_ID_KEY: str(trigger_id),
        MFJ_RESUME_CYCLE_KEY: int(max(1, cycle)),
        MFJ_RESUME_INJECT_AS_KEY: str(inject_as),
        MFJ_RESUME_SUCCEEDED_COUNT_KEY: int(max(0, succeeded_count)),
        MFJ_RESUME_FAILED_COUNT_KEY: int(max(0, failed_count)),
        MFJ_RESUME_TS_KEY: datetime.now(timezone.utc).isoformat(),
    }


def mark_resume_consumed(context_variables: Any) -> Dict[str, Any]:
    nonce = _context_get(context_variables, MFJ_RESUME_NONCE_KEY)
    updates: Dict[str, Any] = {MFJ_RESUME_PENDING_KEY: False}
    if isinstance(nonce, str) and nonce.strip():
        updates[MFJ_RESUME_CONSUMED_NONCE_KEY] = nonce.strip()
    _apply_updates(context_variables, updates)
    return updates


def _context_get(context_variables: Any, key: str) -> Any:
    try:
        if hasattr(context_variables, "get"):
            return context_variables.get(key)
    except Exception:
        pass
    try:
        data = getattr(context_variables, "data", None)
        if isinstance(data, dict):
            return data.get(key)
    except Exception:
        pass
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def _apply_updates(context_variables: Any, updates: Dict[str, Any]) -> None:
    if not updates:
        return
    if isinstance(context_variables, dict):
        context_variables.update(updates)
        return
    for key, value in updates.items():
        applied = False
        try:
            if hasattr(context_variables, "set"):
                context_variables.set(key, value)
                applied = True
        except Exception:
            applied = False
        if applied:
            continue
        try:
            if hasattr(context_variables, "__setitem__"):
                context_variables[key] = value
                applied = True
        except Exception:
            applied = False
        if applied:
            continue
        try:
            data = getattr(context_variables, "data", None)
            if isinstance(data, dict):
                data[key] = value
        except Exception:
            continue


__all__ = [
    "MFJ_RESUME_PENDING_KEY",
    "MFJ_RESUME_TARGET_KEY",
    "MFJ_RESUME_ENTRY_KEY",
    "MFJ_RESUME_NONCE_KEY",
    "MFJ_RESUME_CONSUMED_NONCE_KEY",
    "MFJ_RESUME_TRIGGER_ID_KEY",
    "MFJ_RESUME_CYCLE_KEY",
    "MFJ_RESUME_INJECT_AS_KEY",
    "MFJ_RESUME_SUCCEEDED_COUNT_KEY",
    "MFJ_RESUME_FAILED_COUNT_KEY",
    "MFJ_RESUME_TS_KEY",
    "MFJ_RUNTIME_CONTEXT_PREFIXES",
    "MFJ_RESUME_CONTEXT_KEYS",
    "is_runtime_resume_key",
    "build_resume_context_payload",
    "mark_resume_consumed",
]
