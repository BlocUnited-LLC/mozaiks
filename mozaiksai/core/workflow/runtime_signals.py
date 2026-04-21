"""Internal runtime coordination signals.

These strings are runtime-owned control markers used to resume execution after
non-text UI interactions. They are persisted only where resume/replay requires
them and must never surface as user-facing content.
"""

SYSTEM_RESUME_SIGNAL = "[SYSTEM_RESUME_SIGNAL] Continue workflow execution after UI tool response."


__all__ = ["SYSTEM_RESUME_SIGNAL"]