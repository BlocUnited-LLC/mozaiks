"""Engine-scoped AG2 runtime patch helpers.

Transport/startup code can call these helpers without importing AG2 directly.
"""

from __future__ import annotations

from typing import Any

from logs.logging_config import get_workflow_logger


def patch_ag2_file_logger() -> None:
    """Monkey-patch AG2 FileLogger so it tolerates non-serializable objects."""
    wf_logger = get_workflow_logger("engine.runtime_patches")
    try:
        from autogen.logger import file_logger as _file_logger
        from autogen.logger.logger_utils import get_current_ts as _logger_ts
    except Exception as patch_err:
        wf_logger.debug(f"Skipped AG2 file_logger patch: {patch_err}")
        return

    file_logger_cls: Any = _file_logger.FileLogger
    if getattr(file_logger_cls, "_mozaiks_safe_json", False):
        return

    import json
    import threading

    safe_serialize = _file_logger.safe_serialize

    def _serialize_wrapper_payload(wrapper, session_id, thread_id, init_args):
        return json.dumps(
            {
                "wrapper_id": id(wrapper),
                "session_id": session_id,
                "json_state": safe_serialize(init_args or {}),
                "timestamp": _logger_ts(),
                "thread_id": thread_id,
            }
        )

    def _serialize_client_payload(client, wrapper, session_id, thread_id, init_args):
        return json.dumps(
            {
                "client_id": id(client),
                "wrapper_id": id(wrapper),
                "session_id": session_id,
                "class": type(client).__name__,
                "json_state": safe_serialize(init_args or {}),
                "timestamp": _logger_ts(),
                "thread_id": thread_id,
            }
        )

    def _patched_log_new_wrapper(self, wrapper, init_args=None):
        thread_id = threading.get_ident()
        try:
            payload = _serialize_wrapper_payload(wrapper, self.session_id, thread_id, init_args)
            self.logger.info(payload)
        except Exception as exc:
            self.logger.error(f"[file_logger] Failed to log event {exc}")

    def _patched_log_new_client(self, client, wrapper, init_args):
        thread_id = threading.get_ident()
        try:
            payload = _serialize_client_payload(client, wrapper, self.session_id, thread_id, init_args)
            self.logger.info(payload)
        except Exception as exc:
            self.logger.error(f"[file_logger] Failed to log event {exc}")

    file_logger_cls.log_new_wrapper = _patched_log_new_wrapper  # type: ignore[attr-defined]
    file_logger_cls.log_new_client = _patched_log_new_client  # type: ignore[attr-defined]
    setattr(file_logger_cls, "_mozaiks_safe_json", True)
    wf_logger.info("Patched AG2 FileLogger for safe JSON serialization")


__all__ = ["patch_ag2_file_logger"]
