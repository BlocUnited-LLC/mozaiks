from __future__ import annotations

"""AG2 token watchdog bridge for Mozaiks runtime observability.

AG2 owns observer mechanics such as ``TokenMonitor`` and ``ObserverAlert``.
Mozaiks owns app/run scoping, event persistence, and UI/API projection. This
module keeps that boundary narrow: built-in AG2 observers detect budget
conditions, and a Mozaiks observer persists and forwards those alerts.
"""

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id

logger = get_core_logger("token_watchdog")

TOKEN_BUDGET_ALERT_EVENT_TYPE = "chat.token_budget_alert"
ALERTS_COLLECTION = RuntimeCollections.RUNTIME_TOKEN_BUDGET_ALERTS

DEFAULT_WARN_THRESHOLD = 50_000
DEFAULT_ALERT_THRESHOLD = 100_000


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "none" else text


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key, default)
        except Exception:
            return default
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _positive_int(value: Any, *, default: int | None = None) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _env_int(name: str, default: int) -> int:
    return _positive_int(os.getenv(name), default=default) or default


def _enabled() -> bool:
    value = os.getenv("MOZAIKS_TOKEN_WATCHDOG_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "off", "no", "disabled"}


def resolve_token_watchdog_thresholds(context_variables: Any = None) -> tuple[int, int]:
    """Resolve per-run watchdog thresholds from context or environment.

    Context values allow a workflow or host to tighten budgets for a specific
    run without adding a new workflow contract. Environment values are the
    process-wide default.
    """

    alert = (
        _positive_int(_ctx_get(context_variables, "token_watchdog_alert_tokens"))
        or _positive_int(_ctx_get(context_variables, "token_budget_alert_tokens"))
        or _positive_int(_ctx_get(context_variables, "token_budget_max_tokens"))
        or _env_int("MOZAIKS_TOKEN_WATCHDOG_ALERT_TOKENS", DEFAULT_ALERT_THRESHOLD)
    )
    warn = (
        _positive_int(_ctx_get(context_variables, "token_watchdog_warn_tokens"))
        or _positive_int(_ctx_get(context_variables, "token_budget_warn_tokens"))
        or _env_int("MOZAIKS_TOKEN_WATCHDOG_WARN_TOKENS", DEFAULT_WARN_THRESHOLD)
    )
    if warn >= alert:
        warn = max(1, int(alert * 0.8))
    return warn, alert


class RuntimeTokenBudgetAlertLedger:
    """Persist structured token watchdog alerts for user/admin usage views."""

    def __init__(self, *, database: Any | None = None) -> None:
        self._database = database
        self._indexes_ready = False

    async def _coll(self) -> Any:
        if self._database is not None:
            db = self._database
        else:
            client = get_mongo_client()
            db = client[SYSTEM_DATABASE]
        coll = db[ALERTS_COLLECTION]
        if not self._indexes_ready:
            try:
                await coll.create_index("event_id", name="token_budget_alert_event_id", unique=True)
                await coll.create_index([("app_id", 1), ("event_ts", -1)], name="token_budget_alert_app_time")
                await coll.create_index(
                    [("app_id", 1), ("user_id", 1), ("event_ts", -1)],
                    name="token_budget_alert_app_user_time",
                )
                await coll.create_index([("app_id", 1), ("chat_id", 1)], name="token_budget_alert_app_chat")
            except Exception as exc:
                logger.debug("token budget alert index ensure skipped: %s", exc)
            self._indexes_ready = True
        return coll

    async def record_budget_alert(self, payload: dict[str, Any], *, source: str = "ag2_token_watchdog") -> None:
        app_id = coalesce_app_id(app_id=payload.get("app_id"))
        chat_id = _text(payload.get("chat_id"))
        workflow_name = _text(payload.get("workflow_name"))
        if not app_id or not chat_id or not workflow_name:
            return
        event_id = _text(payload.get("event_id")) or uuid.uuid4().hex[:12]
        try:
            event_ts = payload.get("event_ts")
            if isinstance(event_ts, datetime):
                parsed_ts = event_ts
            elif isinstance(event_ts, str) and event_ts.strip():
                parsed_ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
            else:
                parsed_ts = datetime.now(UTC)
        except Exception:
            parsed_ts = datetime.now(UTC)
        if parsed_ts.tzinfo is None:
            parsed_ts = parsed_ts.replace(tzinfo=UTC)

        doc = {
            "_id": event_id,
            "event_id": event_id,
            "event_ts": parsed_ts,
            "source": source,
            "app_id": str(app_id),
            "chat_id": chat_id,
            "user_id": _text(payload.get("user_id")) or "anonymous",
            "tenant_id": _text(payload.get("tenant_id")) or None,
            "workspace_id": _text(payload.get("workspace_id")) or None,
            "workflow_name": workflow_name,
            "agent_name": _text(payload.get("agent_name")) or None,
            "observer_source": _text(payload.get("observer_source")) or None,
            "severity": _text(payload.get("severity")) or "warning",
            "message": _text(payload.get("message")) or "Token budget alert.",
            "total_tokens": _positive_int(payload.get("total_tokens"), default=0) or 0,
            "warn_threshold": _positive_int(payload.get("warn_threshold"), default=0) or 0,
            "alert_threshold": _positive_int(payload.get("alert_threshold"), default=0) or 0,
            "created_at": datetime.now(UTC),
        }
        coll = await self._coll()
        await coll.update_one({"_id": event_id}, {"$setOnInsert": doc}, upsert=True)

    async def query_alerts(
        self,
        *,
        app_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        query: dict[str, Any] = {}
        if resolved_app_id:
            query.update(build_app_scope_filter(str(resolved_app_id)))
        if user_id:
            query["user_id"] = str(user_id)

        bounded_limit = max(1, min(int(limit or 1), 500))
        coll = await self._coll()
        docs = await coll.find(query, {"_id": 0}).sort("event_ts", -1).limit(bounded_limit).to_list(length=bounded_limit)
        normalized_docs = [dict(doc) for doc in docs if isinstance(doc, dict)]
        for doc in normalized_docs:
            ts = doc.get("event_ts")
            if isinstance(ts, datetime):
                doc["event_ts"] = ts.isoformat()
            created = doc.get("created_at")
            if isinstance(created, datetime):
                doc["created_at"] = created.isoformat()
        return normalized_docs


_global_alert_ledger: RuntimeTokenBudgetAlertLedger | None = None


def get_runtime_token_budget_alert_ledger() -> RuntimeTokenBudgetAlertLedger:
    global _global_alert_ledger
    if _global_alert_ledger is None:
        _global_alert_ledger = RuntimeTokenBudgetAlertLedger()
    return _global_alert_ledger


async def emit_token_budget_alert(**payload: Any) -> None:
    payload.setdefault("event_id", uuid.uuid4().hex[:12])
    payload.setdefault("event_ts", datetime.now(UTC).isoformat())
    try:
        from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

        await get_event_dispatcher().emit(TOKEN_BUDGET_ALERT_EVENT_TYPE, dict(payload))
    except Exception as exc:  # pragma: no cover - watchdog must never break runs
        logger.debug("token budget alert emit failed: %s", exc)


def build_ag2_token_watchdog_observers(
    *,
    agent_name: str,
    workflow_name: str,
    context_variables: Any,
) -> list[Any]:
    """Build AG2 observers for runtime token watchdog alerts."""

    if not _enabled():
        return []

    try:
        from ag2.events import BaseEvent, ObserverAlert
        from ag2.observers import BaseObserver, TokenMonitor
        from ag2.watch import EventWatch
    except Exception as exc:
        logger.debug("AG2 token watchdog unavailable: %s", exc)
        return []

    warn_threshold, alert_threshold = resolve_token_watchdog_thresholds(context_variables)
    token_monitor = TokenMonitor(
        warn_threshold=warn_threshold,
        alert_threshold=alert_threshold,
        name="mozaiks-token-monitor",
    )

    class MozaiksTokenAlertObserver(BaseObserver):
        def __init__(self) -> None:
            super().__init__("mozaiks-token-alert-bridge", watch=EventWatch(ObserverAlert))

        async def process(self, events: list[BaseEvent], ctx: Any) -> None:
            for event in events:
                if not isinstance(event, ObserverAlert):
                    continue
                severity = getattr(event, "severity", "")
                severity_value = getattr(severity, "value", severity)
                await emit_token_budget_alert(
                    chat_id=_text(_ctx_get(context_variables, "chat_id", "")),
                    app_id=_text(_ctx_get(context_variables, "app_id", "")),
                    user_id=_text(_ctx_get(context_variables, "user_id", "anonymous")) or "anonymous",
                    tenant_id=_text(_ctx_get(context_variables, "tenant_id", "")) or None,
                    workspace_id=_text(_ctx_get(context_variables, "workspace_id", "")) or None,
                    workflow_name=_text(_ctx_get(context_variables, "workflow_name", workflow_name)),
                    agent_name=agent_name,
                    observer_source=_text(getattr(event, "source", "")),
                    severity=str(severity_value or "warning"),
                    message=_text(getattr(event, "message", "")),
                    total_tokens=int(getattr(token_monitor, "total_tokens", 0) or 0),
                    warn_threshold=warn_threshold,
                    alert_threshold=alert_threshold,
                )
            return None

    return [token_monitor, MozaiksTokenAlertObserver()]


__all__ = [
    "DEFAULT_ALERT_THRESHOLD",
    "DEFAULT_WARN_THRESHOLD",
    "RuntimeTokenBudgetAlertLedger",
    "TOKEN_BUDGET_ALERT_EVENT_TYPE",
    "build_ag2_token_watchdog_observers",
    "emit_token_budget_alert",
    "get_runtime_token_budget_alert_ledger",
    "resolve_token_watchdog_thresholds",
]
