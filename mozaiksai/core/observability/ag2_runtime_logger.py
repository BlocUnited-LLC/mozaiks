# ==============================================================================
# FILE: ag2_runtime_logger.py
# DESCRIPTION: 
# ==============================================================================

# === MOZAIKS-CORE-HEADER ===

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Literal, cast

from autogen import runtime_logging
from autogen.logger.base_logger import BaseLogger
from autogen.logger.logger_factory import LoggerFactory

from logs.logging_config import get_observability_logger, get_ag2_runtime_log_path
from mozaiksai.core.observability.realtime_token_logger import get_realtime_token_logger

log = get_observability_logger("ag2.runtime")


class AG2RuntimeLoggingController:
    """Lightweight wrapper around autogen.runtime_logging with Mozaiks integrations."""

    def __init__(self) -> None:
        self._session_id: Optional[str] = None
        self._chat_id: Optional[str] = None
        self._workflow_name: Optional[str] = None
        self._app_id: Optional[str] = None
        self._user_id: Optional[str] = None
        self._delegate: Optional[BaseLogger] = None
        self._realtime_logger = get_realtime_token_logger()
        self._log_file_path: Optional[Path] = None
        self._active = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def log_file_path(self) -> Optional[Path]:
        return self._log_file_path

    def should_enable(self) -> bool:
        mode = os.getenv("AG2_RUNTIME_LOGGING", "file").strip().lower()
        return mode not in {"", "off", "false", "0", "disabled", "none"}

    def start_session(
        self,
        chat_id: str,
        workflow_name: str,
        app_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        if not self.should_enable():
            log.debug("AG2 runtime logging disabled via env")
            return False

        if self._active:
            log.debug("Active AG2 runtime session detected; restarting")
            self.stop_session()

        logger_type, config = self._resolve_logger_selection()
        delegate = self._build_delegate(logger_type, config)

        self._realtime_logger.configure(
            chat_id=chat_id,
            workflow_name=workflow_name,
            app_id=app_id,
            user_id=user_id,
            delegate=delegate,
        )
        self._realtime_logger.set_active_agent(workflow_name)
        if user_id:
            self._realtime_logger.set_user(user_id)

        try:
            session_id = runtime_logging.start(logger=self._realtime_logger)
        except Exception as err:  # pragma: no cover - defensive
            log.error("Failed to start autogen runtime logging", exc_info=err)
            return False

        self._session_id = session_id
        self._chat_id = chat_id
        self._workflow_name = workflow_name
        self._app_id = app_id
        self._user_id = user_id
        self._delegate = delegate
        self._active = True

        if logger_type == "sqlite":
            db_path = Path(config.get("dbname", "logs.db")).resolve()
            self._log_file_path = db_path
        else:
            self._log_file_path = config.get("__resolved_path")

        log.info(
            "ag2_runtime_logging_started",
            extra={
                "chat_id": chat_id,
                "workflow_name": workflow_name,
                "app_id": app_id,
                "logger_type": logger_type,
                "session_id": session_id,
            },
        )
        return True

    def stop_session(self) -> bool:
        if not self._active:
            return False

        try:
            runtime_logging.stop()
        except Exception as err:  # pragma: no cover - defensive
            log.warning("Failed to stop autogen runtime logging", exc_info=err)
        finally:
            log.info(
                "ag2_runtime_logging_stopped",
                extra={
                    "chat_id": self._chat_id,
                    "workflow_name": self._workflow_name,
                    "session_id": self._session_id,
                },
            )
            self._reset_state()
        return True

    def set_active_agent(self, agent_name: Optional[str]) -> None:
        self._realtime_logger.set_active_agent(agent_name)

    def set_user(self, user_id: Optional[str]) -> None:
        self._user_id = user_id
        self._realtime_logger.set_user(user_id)

    @contextmanager
    def session_context(
        self,
        chat_id: str,
        workflow_name: str,
        app_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        started = self.start_session(chat_id, workflow_name, app_id, user_id)
        try:
            yield self
        finally:
            if started:
                self.stop_session()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_logger_selection(self) -> Tuple[str, Dict[str, Any]]:
        requested = os.getenv("AG2_RUNTIME_LOGGER_TYPE", "").strip().lower()
        if requested not in {"file", "sqlite"}:
            requested = os.getenv("AG2_RUNTIME_LOGGING", "file").strip().lower()
            if requested not in {"file", "sqlite"}:
                requested = "file"

        if requested == "sqlite":
            db_path = os.getenv("AG2_RUNTIME_SQLITE_PATH")
            if not db_path:
                db_path = str(get_ag2_runtime_log_path().with_suffix(".db"))
            return "sqlite", {"dbname": db_path}

        target_path = get_ag2_runtime_log_path()
        delegate_root = Path.cwd() / "autogen_logs"
        delegate_root.mkdir(parents=True, exist_ok=True)
        relative_filename = os.path.relpath(target_path, delegate_root)
        # Store resolved path so callers can expose it without re-deriving
        return "file", {"filename": relative_filename, "__resolved_path": target_path}

    def _build_delegate(self, logger_type: str, config: Dict[str, Any]) -> Optional[BaseLogger]:
        try:
            return LoggerFactory.get_logger(logger_type=cast(Literal['sqlite', 'file'], logger_type), config=config)
        except Exception as err:  # pragma: no cover - defensive
            log.warning("Failed to construct %s logger: %s", logger_type, err)
            return None

    def _reset_state(self) -> None:
        self._session_id = None
        self._chat_id = None
        self._workflow_name = None
        self._app_id = None
        self._user_id = None
        self._delegate = None
        self._log_file_path = None
        self._active = False


# Singleton helpers ----------------------------------------------------
_controller: Optional[AG2RuntimeLoggingController] = None


def get_ag2_runtime_logger() -> AG2RuntimeLoggingController:
    global _controller
    if _controller is None:
        _controller = AG2RuntimeLoggingController()
    return _controller


def start_ag2_logging(
    chat_id: str,
    workflow_name: str,
    app_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> bool:
    return get_ag2_runtime_logger().start_session(chat_id, workflow_name, app_id, user_id)


def stop_ag2_logging() -> bool:
    return get_ag2_runtime_logger().stop_session()


@contextmanager
def ag2_logging_session(
    chat_id: str,
    workflow_name: str,
    app_id: Optional[str] = None,
    user_id: Optional[str] = None,
):
    with get_ag2_runtime_logger().session_context(chat_id, workflow_name, app_id, user_id):
        yield

