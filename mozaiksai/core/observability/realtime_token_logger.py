# ==============================================================================
# FILE: realtime_token_logger.py
# DESCRIPTION: 
# ==============================================================================

# === MOZAIKS-CORE-HEADER ===

"""Real-time token tracking for AG2 runtime logging.

Provides a single BaseLogger implementation that forwards to AG2's
runtime_logging backend while streaming usage deltas into Mozaiks
observability (performance + persistence).

Robust loop handling:
- Captures the running asyncio loop at start(), so later callbacks invoked
    from non-async contexts (e.g., autogen logger threads) can schedule work
    safely via call_soon_threadsafe.
- Falls back to a background thread with asyncio.run if no loop is available.
"""

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
from collections.abc import Coroutine
import sqlite3

from autogen.logger.base_logger import BaseLogger
from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.tokens.manager import TokenManager

import logging
logger = logging.getLogger("core.observability.realtime_token_logger")


class RealtimeTokenLogger(BaseLogger):
    """Composite logger used as the single runtime_logging delegate."""

    def __init__(self) -> None:
        super().__init__()
        self._delegate: Optional[BaseLogger] = None
        self._session_id: Optional[str] = None
        self._chat_id: Optional[str] = None
        self._app_id: Optional[str] = None
        self._workflow_name: Optional[str] = None
        self._user_id: Optional[str] = None
        self._current_agent: Optional[str] = None
        self._session_totals: Dict[str, float] = {}
        self._persistence = AG2PersistenceManager()
        self._reset_totals()
        # Store the event loop that was active when the session started
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------
    def configure(
        self,
        *,
        chat_id: str,
        workflow_name: str,
        app_id: Optional[str],
        user_id: Optional[str],
        delegate: Optional[BaseLogger],
    ) -> None:
        """Set session metadata and optional downstream delegate."""
        self._chat_id = chat_id
        self._workflow_name = workflow_name
        self._app_id = app_id
        self._user_id = user_id
        self._delegate = delegate
        self._reset_totals()
        logger.debug(
            "realtime_logger_configured",
            extra={
                "chat_id": chat_id,
                "workflow": workflow_name,
                "app": app_id,
                "user": user_id,
                "delegate": delegate.__class__.__name__ if delegate else None,
            }
        )

    def set_active_agent(self, agent_name: Optional[str]) -> None:
        self._current_agent = agent_name

    def set_user(self, user_id: Optional[str]) -> None:
        self._user_id = user_id

    def _reset_totals(self) -> None:
        self._session_totals = {
            "prompt_tokens": 0.0,
            "completion_tokens": 0.0,
            "total_cost": 0.0,
        }

    # ------------------------------------------------------------------
    # BaseLogger overrides
    # ------------------------------------------------------------------
    def start(self) -> str:
        if self._delegate:
            try:
                self._session_id = self._delegate.start()
            except Exception as err:  # pragma: no cover - defensive
                logger.warning(f"Delegate logger failed to start: {err}")
                self._session_id = None
        if not self._session_id:
            token = self._chat_id or uuid.uuid4().hex
            self._session_id = f"realtime_token_session_{token}"
        # Capture the running loop for later thread-safe scheduling
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        logger.debug("realtime_logger_started", extra={"session_id": self._session_id, "chat_id": self._chat_id})
        return self._session_id

    def stop(self) -> None:
        try:
            if self._delegate:
                self._delegate.stop()
        finally:
            self._flush_session_totals()
            logger.debug("realtime_logger_stopped", extra={"session_id": self._session_id})

    def log_chat_completion(
        self,
        invocation_id: uuid.UUID,
        client_id: int,
        wrapper_id: int,
        source: Any,
        request: Dict[str, Any],
    response: Union[str, Any],
        is_cached: int,
        cost: float,
        start_time: str,
    ) -> None:
        if self._delegate:
            try:
                self._delegate.log_chat_completion(
                    invocation_id,
                    client_id,
                    wrapper_id,
                    source,
                    request,
                    response,
                    is_cached,
                    cost,
                    start_time,
                )
            except Exception as err:  # pragma: no cover
                logger.debug(f"Delegate log_chat_completion failed: {err}")

        if not self._chat_id:
            logger.debug("No chat context for realtime logging; skipping usage delta")
            return

        agent_from_source = self._extract_agent_name_from_source(source)
        if agent_from_source:
            self._current_agent = agent_from_source
        agent_label = self._current_agent or agent_from_source or "unknown"

        request_dict = self._maybe_mapping(request)
        response_dict = self._maybe_mapping(response)
        usage = self._extract_usage(response_dict)
        prompt_tokens = usage["prompt_tokens"]
        completion_tokens = usage["completion_tokens"]
        total_tokens = usage["total_tokens"]
        model_name = self._extract_model_name(request_dict, response_dict)
        cost_value = float(cost or 0.0)
        cached_flag = bool(is_cached)
        # Cached responses should not be counted as new usage (measurement only).
        if cached_flag:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            cost_value = 0.0

        self._session_totals["prompt_tokens"] += prompt_tokens
        self._session_totals["completion_tokens"] += completion_tokens
        self._session_totals["total_cost"] += cost_value

        # Prefer precise duration and end timestamp from sqlite if available
        duration_sec, event_ts = self._duration_and_event_ts(invocation_id, start_time)

        if prompt_tokens or completion_tokens or cost_value or duration_sec:
            logger.info(
                "realtime_usage_delta",
                extra={
                    "chat_id": self._chat_id,
                    "agent": agent_label,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "cost": cost_value,
                    "duration_sec": duration_sec,
                    "cached": bool(is_cached),
                    "model": model_name,
                },
            )

        self._schedule_async_task(
            self._record_agent_metrics(
                invocation_id=str(invocation_id) if invocation_id else None,
                agent_name=agent_label,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost_value,
                duration_sec=duration_sec,
                model_name=model_name,
                event_ts=event_ts,
                cached=cached_flag,
            )
        )

    def log_new_agent(self, agent: Any, init_args: Dict[str, Any]) -> None:
        if self._delegate:
            try:
                self._delegate.log_new_agent(agent, init_args)
            except Exception:  # pragma: no cover
                logger.debug("Delegate log_new_agent failed", exc_info=True)

    def log_event(self, source: Any, name: str, **kwargs: Dict[str, Any]) -> None:
        if self._delegate:
            try:
                self._delegate.log_event(source, name, **kwargs)
            except Exception:  # pragma: no cover
                logger.debug("Delegate log_event failed", exc_info=True)

    def log_new_wrapper(self, wrapper: Any, init_args: Dict[str, Any]) -> None:
        if self._delegate:
            try:
                # Sanitize init_args to remove logging reserved keys that might cause conflicts
                sanitized_args = {k: v for k, v in init_args.items() 
                                 if k not in {'exc_info', 'stack_info', 'stacklevel', 'extra'}}
                self._delegate.log_new_wrapper(wrapper, sanitized_args)
            except Exception:  # pragma: no cover
                logger.debug("Delegate log_new_wrapper failed", exc_info=True)

    def log_new_client(self, client: Any, wrapper: Any, init_args: Dict[str, Any]) -> None:
        if self._delegate:
            try:
                # Sanitize init_args to remove logging reserved keys that might cause conflicts
                sanitized_args = {k: v for k, v in init_args.items() 
                                 if k not in {'exc_info', 'stack_info', 'stacklevel', 'extra'}}
                self._delegate.log_new_client(client, wrapper, sanitized_args)
            except Exception:  # pragma: no cover
                logger.debug("Delegate log_new_client failed", exc_info=True)

    def log_function_use(self, source: Any, function: Any, args: Dict[str, Any], returns: Any) -> None:
        if self._delegate:
            try:
                self._delegate.log_function_use(source, function, args, returns)
            except Exception:  # pragma: no cover
                logger.debug("Delegate log_function_use failed", exc_info=True)

    def get_connection(self) -> Optional[Any]:
        if self._delegate:
            try:
                return self._delegate.get_connection()
            except Exception:  # pragma: no cover
                logger.debug("Delegate get_connection failed", exc_info=True)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_latency_seconds(self, start_time: str) -> float:
        if not start_time:
            return 0.0
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            return max(0.0, (datetime.now(timezone.utc) - start_dt).total_seconds())
        except Exception:
            return 0.0

    def _duration_and_event_ts(self, invocation_id: uuid.UUID, start_time: str) -> tuple[float, datetime]:
        # Try sqlite first
        try:
            if self._delegate is not None:
                conn = None
                try:
                    conn = self._delegate.get_connection()  # type: ignore[attr-defined]
                except Exception:
                    conn = None
                if isinstance(conn, sqlite3.Connection):
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT start_time, end_time FROM chat_completions WHERE invocation_id = ? ORDER BY id DESC LIMIT 1",
                        (str(invocation_id),),
                    )
                    row = cur.fetchone()
                    if row:
                        st_raw, et_raw = row[0], row[1]
                        st_dt = self._parse_ts(st_raw)
                        et_dt = self._parse_ts(et_raw)
                        if st_dt and et_dt:
                            return max(0.0, (et_dt - st_dt).total_seconds()), et_dt
        except Exception:
            pass
        # Fallback to latency from start_time and current UTC timestamp
        return self._compute_latency_seconds(start_time), datetime.now(timezone.utc)

    def _parse_ts(self, v: Any) -> Optional[datetime]:
        try:
            if isinstance(v, datetime):
                return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(float(v), tz=timezone.utc)
            if isinstance(v, str):
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:
            return None
        return None

    def _schedule_async_task(self, coro: Coroutine[Any, Any, None]) -> None:
        """Schedule a coroutine to run on the captured event loop when possible.

        Priority:
        1) If we captured a running loop at start(), schedule via call_soon_threadsafe.
        2) Else, if a running loop exists in this thread, use asyncio.create_task.
        3) Else, run in a short-lived background thread with asyncio.run.
        """
        loop = self._loop
        if loop and loop.is_running():
            try:
                loop.call_soon_threadsafe(asyncio.create_task, coro)
                return
            except Exception:
                pass
        try:
            cur_loop = asyncio.get_running_loop()
            if cur_loop.is_running():
                cur_loop.create_task(coro)
                return
        except RuntimeError:
            pass

        def _runner():
            try:
                asyncio.run(coro)
            except Exception:
                pass

        threading.Thread(target=_runner, name="rt_tokens_fallback", daemon=True).start()

    async def _record_agent_metrics(
        self,
        *,
        invocation_id: Optional[str],
        agent_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
        duration_sec: float,
        model_name: Optional[str],
        event_ts: datetime,
        cached: bool,
    ) -> None:

        try:
            await self._persistence.update_session_metrics(
                chat_id=self._chat_id or "unknown",
                app_id=self._app_id or "unknown",
                user_id=self._user_id or "unknown",
                workflow_name=self._workflow_name or "unknown",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                agent_name=agent_name or "unknown",
                event_ts=event_ts,
                duration_sec=duration_sec,
            )
        except Exception as err:  # pragma: no cover
            logger.warning(f"Failed to persist realtime usage delta: {err}")
            return

        # Emit factual usage delta event (no pricing/gating/enforcement).
        try:
            if (prompt_tokens or completion_tokens) and self._chat_id and self._app_id and self._user_id and self._workflow_name:
                await TokenManager.emit_usage_delta(
                    chat_id=self._chat_id,
                    app_id=self._app_id,
                    user_id=self._user_id,
                    workflow_name=self._workflow_name,
                    agent_name=agent_name or None,
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=(prompt_tokens + completion_tokens),
                    cached=cached,
                    duration_sec=duration_sec,
                    invocation_id=invocation_id,
                    event_ts=event_ts,
                )
        except Exception:
            logger.debug("Failed to emit usage delta event", exc_info=True)

    def _extract_agent_name_from_source(self, source: Any) -> Optional[str]:
        if isinstance(source, str):
            value = source.strip()
            return value or None
        for attr in ("name", "agent_name"):
            try:
                candidate = getattr(source, attr, None)
            except Exception:
                candidate = None
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    def _maybe_mapping(self, payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return None
        for attr in ("model_dump", "dict", "to_dict"):
            if hasattr(payload, attr):
                try:
                    candidate = getattr(payload, attr)()
                    if isinstance(candidate, dict):
                        return candidate
                except Exception:
                    continue
        return None

    def _extract_usage(self, response_dict: Optional[Dict[str, Any]]) -> Dict[str, int]:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        if isinstance(response_dict, dict):
            usage = response_dict.get("usage")
            if isinstance(usage, dict):
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
        if not total_tokens:
            total_tokens = prompt_tokens + completion_tokens
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_model_name(
        self,
        request_dict: Optional[Dict[str, Any]],
        response_dict: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        for data in (request_dict, response_dict):
            if not isinstance(data, dict):
                continue
            for key in ("model", "deployment_name", "engine"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            config = data.get("config")
            if isinstance(config, dict):
                for key in ("model", "deployment_name", "engine"):
                    value = config.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return None

    def _flush_session_totals(self) -> None:
        if not self._chat_id or self._session_totals["total_cost"] <= 0:
            return
        logger.info(
            "realtime_session_totals",
            extra={
                "chat_id": self._chat_id,
                "prompt_tokens": int(self._session_totals["prompt_tokens"]),
                "completion_tokens": int(self._session_totals["completion_tokens"]),
                "total_cost": float(self._session_totals["total_cost"]),
            },
        )


# ------------------------------------------------------------------
# Singleton helper
# ------------------------------------------------------------------

_realtime_logger: Optional[RealtimeTokenLogger] = None


def get_realtime_token_logger() -> RealtimeTokenLogger:
    global _realtime_logger
    if _realtime_logger is None:
        _realtime_logger = RealtimeTokenLogger()
    return _realtime_logger
