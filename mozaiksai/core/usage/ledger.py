from __future__ import annotations

"""Neutral runtime usage ledger.

The ledger stores factual usage measurements so local OSS and Studio dashboards
can query token usage from AG2 1.0 beta usage events. It is not a billing authority
and does not enforce subscriptions or entitlements.
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id
from mozaiksai.core.usage.pricing import estimate_token_cost, pricing_catalog_health

logger = get_core_logger("runtime_usage_ledger")

USAGE_COLLECTION = RuntimeCollections.RUNTIME_USAGE_EVENTS


def _int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _now() -> datetime:
    return datetime.now(UTC)


def _display_cost_for_doc(doc: dict[str, Any], *, prompt: int, completion: int, cached_prompt: int) -> tuple[float, str]:
    stored_cost = _float_value(doc.get("estimated_cost_usd"))
    stored_source = _text(doc.get("cost_source"))

    if stored_source == "provided" or (stored_source is None and stored_cost > 0):
        return stored_cost, stored_source or "provided"

    estimate = estimate_token_cost(
        model_name=_text(doc.get("model_name")),
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_prompt_tokens=cached_prompt,
    )
    if estimate.cost_source != "not_configured":
        return float(estimate.estimated_cost_usd), estimate.cost_source

    if stored_source and stored_source != "not_configured":
        return stored_cost, stored_source
    if stored_cost > 0:
        return stored_cost, stored_source or "provided"
    return 0.0, "not_configured"


class RuntimeUsageLedger:
    def __init__(self) -> None:
        self._indexes_ready = False

    async def _coll(self):
        client = get_mongo_client()
        coll = client[SYSTEM_DATABASE][USAGE_COLLECTION]
        if not self._indexes_ready:
            try:
                await coll.create_index("event_id", name="usage_event_id", unique=True)
                await coll.create_index([("app_id", 1), ("event_ts", -1)], name="usage_app_time")
                await coll.create_index([("app_id", 1), ("user_id", 1), ("event_ts", -1)], name="usage_app_user_time")
                await coll.create_index([("app_id", 1), ("chat_id", 1)], name="usage_app_chat")
            except Exception as exc:
                logger.debug("usage ledger index ensure skipped: %s", exc)
            self._indexes_ready = True
        return coll

    async def record_usage_delta(self, payload: dict[str, Any], *, source: str = "usage_event") -> None:
        app_id = coalesce_app_id(app_id=payload.get("app_id"))
        chat_id = _text(payload.get("chat_id"))
        workflow_name = _text(payload.get("workflow_name"))
        user_id = _text(payload.get("user_id")) or "anonymous"
        tenant_id = _text(payload.get("tenant_id"))
        workspace_id = _text(payload.get("workspace_id"))
        if not app_id or not chat_id or not workflow_name:
            return

        prompt_tokens = _int_value(payload.get("prompt_tokens") or payload.get("input_tokens"))
        completion_tokens = _int_value(payload.get("completion_tokens") or payload.get("output_tokens"))
        total_tokens = _int_value(payload.get("total_tokens") or (prompt_tokens + completion_tokens))
        cached_prompt_tokens = min(_int_value(payload.get("cached_prompt_tokens") or payload.get("cached_tokens")), prompt_tokens)
        model_name = _text(payload.get("model_name"))
        estimate = estimate_token_cost(
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            explicit_cost_usd=payload.get("estimated_cost_usd") or payload.get("cost_usd"),
        )

        event_id = _text(payload.get("event_id")) or (
            f"{source}:{app_id}:{chat_id}:{workflow_name}:{user_id}:"
            f"{model_name or 'unknown'}:{prompt_tokens}:{completion_tokens}:{payload.get('invocation_id') or ''}"
        )
        try:
            event_ts = payload.get("event_ts")
            if isinstance(event_ts, datetime):
                parsed_ts = event_ts
            elif isinstance(event_ts, str) and event_ts.strip():
                parsed_ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
            else:
                parsed_ts = _now()
        except Exception:
            parsed_ts = _now()
        if parsed_ts.tzinfo is None:
            parsed_ts = parsed_ts.replace(tzinfo=UTC)

        doc = {
            "_id": event_id,
            "event_id": event_id,
            "event_ts": parsed_ts,
            "source": source,
            "app_id": str(app_id),
            "chat_id": chat_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "workflow_name": workflow_name,
            "build_id": _text(payload.get("build_id")),
            "agent_name": _text(payload.get("agent_name")),
            "model_name": model_name,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_prompt_tokens": cached_prompt_tokens,
            "cached": bool(payload.get("cached", False)),
            "duration_sec": _float_value(payload.get("duration_sec")),
            "estimated_cost_usd": float(estimate.estimated_cost_usd),
            "cost_source": estimate.cost_source,
            "created_at": _now(),
        }

        coll = await self._coll()
        await coll.update_one({"_id": event_id}, {"$setOnInsert": doc}, upsert=True)

    async def purge_usage_for_app(self, *, app_id: str) -> int:
        """Delete all usage events for a specific app. Called on app delete."""
        resolved = coalesce_app_id(app_id=app_id)
        if not resolved:
            return 0
        coll = await self._coll()
        result = await coll.delete_many(build_app_scope_filter(str(resolved)))
        deleted = int(getattr(result, "deleted_count", 0) or 0)
        if deleted:
            logger.info("[usage_ledger] purged %d events for app_id=%s", deleted, resolved)
        return deleted

    async def purge_orphaned_usage(self, *, known_app_ids: list[str]) -> int:
        """Delete usage events for app_ids not in the provided list of known apps."""
        if not known_app_ids:
            return 0
        normalized = [str(aid) for aid in known_app_ids if aid]
        coll = await self._coll()
        result = await coll.delete_many({"app_id": {"$nin": normalized}})
        deleted = int(getattr(result, "deleted_count", 0) or 0)
        if deleted:
            logger.info("[usage_ledger] purged %d orphaned events (not in %d known apps)", deleted, len(normalized))
        return deleted

    async def query_usage(
        self,
        *,
        app_id: str | None = None,
        user_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        resolved_app_id = coalesce_app_id(app_id=app_id)
        query: dict[str, Any] = {}
        if resolved_app_id:
            query.update(build_app_scope_filter(str(resolved_app_id)))
        if user_id:
            query["user_id"] = str(user_id)

        bounded_limit = max(1, min(int(limit or 1), 1000))
        coll = await self._coll()
        docs = await coll.find(query).sort("event_ts", -1).limit(bounded_limit).to_list(length=bounded_limit)
        return summarize_usage_events(docs, app_id=str(resolved_app_id) if resolved_app_id else None, user_id=user_id)


def summarize_usage_events(
    docs: list[dict[str, Any]],
    *,
    app_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_prompt_tokens": 0,
        "estimated_cost_usd": 0.0,
        "llm_calls": 0,
    }
    cost_sources: set[str] = set()
    cost_source_counts: dict[str, int] = defaultdict(int)
    used_models: set[str] = set()
    unpriced_models: set[str] = set()
    default_table_models: set[str] = set()
    by_workflow: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "workflow_name": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_prompt_tokens": 0,
        "estimated_cost_usd": 0.0,
        "llm_calls": 0,
        "runs": set(),
    })
    by_run: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "chat_id": "",
        "app_id": "",
        "workflow_name": "",
        "user_id": "",
        "event_ts": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_prompt_tokens": 0,
        "estimated_cost_usd": 0.0,
        "llm_calls": 0,
    })
    display_events: list[dict[str, Any]] = []

    for doc in docs:
        prompt = _int_value(doc.get("prompt_tokens"))
        completion = _int_value(doc.get("completion_tokens"))
        total = _int_value(doc.get("total_tokens") or prompt + completion)
        cached_prompt = _int_value(doc.get("cached_prompt_tokens"))
        cost, cost_source = _display_cost_for_doc(
            doc,
            prompt=prompt,
            completion=completion,
            cached_prompt=cached_prompt,
        )
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["total_tokens"] += total
        totals["cached_prompt_tokens"] += cached_prompt
        totals["estimated_cost_usd"] += cost
        totals["llm_calls"] += 1
        cost_sources.add(cost_source)
        cost_source_counts[cost_source] += 1
        model_name = str(doc.get("model_name") or "unknown")
        used_models.add(model_name)
        if cost_source == "not_configured":
            unpriced_models.add(model_name)
        elif cost_source == "default_table":
            default_table_models.add(model_name)

        workflow = str(doc.get("workflow_name") or "Unknown")
        wf = by_workflow[workflow]
        wf["workflow_name"] = workflow
        wf["prompt_tokens"] += prompt
        wf["completion_tokens"] += completion
        wf["total_tokens"] += total
        wf["cached_prompt_tokens"] += cached_prompt
        wf["estimated_cost_usd"] += cost
        wf["llm_calls"] += 1
        app_scope = str(doc.get("app_id") or "")
        chat_id = str(doc.get("chat_id") or "")
        if chat_id:
            wf_run_key = f"{app_scope}:{chat_id}" if app_scope else chat_id
            wf["runs"].add(wf_run_key)

        if chat_id:
            run_key = f"{app_scope}:{chat_id}" if app_scope else chat_id
            run = by_run[run_key]
            run["chat_id"] = chat_id
            run["app_id"] = app_scope
            run["workflow_name"] = workflow
            run["user_id"] = str(doc.get("user_id") or "")
            run["prompt_tokens"] += prompt
            run["completion_tokens"] += completion
            run["total_tokens"] += total
            run["cached_prompt_tokens"] += cached_prompt
            run["estimated_cost_usd"] += cost
            run["llm_calls"] += 1
            # Keep the most recent event_ts for this run
            doc_ts = doc.get("event_ts")
            if doc_ts is not None and (run["event_ts"] is None or doc_ts > run["event_ts"]):
                run["event_ts"] = doc_ts

        display_events.append(
            {
                **doc,
                "estimated_cost_usd": cost,
                "cost_source": cost_source,
            }
        )

    workflow_rows = []
    for row in by_workflow.values():
        workflow_rows.append({**row, "runs": len(row["runs"])})
    workflow_rows.sort(key=lambda item: int(item.get("total_tokens") or 0), reverse=True)
    run_rows = sorted(by_run.values(), key=lambda item: int(item.get("total_tokens") or 0), reverse=True)

    # Summarize cost_source across all events
    if not cost_sources or cost_sources == {"not_configured"}:
        summary_cost_source = "not_configured"
    elif len(cost_sources) == 1:
        summary_cost_source = next(iter(cost_sources))
    elif "not_configured" in cost_sources:
        summary_cost_source = "mixed"
    elif len(cost_sources) > 1:
        summary_cost_source = "mixed"
    else:
        summary_cost_source = "default_table"

    return {
        "app_id": app_id,
        "user_id": user_id,
        "totals": totals,
        "by_workflow": workflow_rows,
        "by_run": run_rows,
        "events": display_events,
        "source": "runtime_usage_events",
        "cost_source": summary_cost_source,
        "pricing_health": pricing_catalog_health(
            used_models=sorted(used_models),
            unpriced_models=sorted(unpriced_models),
            default_table_models=sorted(default_table_models),
            cost_source_counts=dict(cost_source_counts),
        ),
    }


_global_ledger: RuntimeUsageLedger | None = None


def get_runtime_usage_ledger() -> RuntimeUsageLedger:
    global _global_ledger
    if _global_ledger is None:
        _global_ledger = RuntimeUsageLedger()
    return _global_ledger


__all__ = ["RuntimeUsageLedger", "get_runtime_usage_ledger", "summarize_usage_events", "purge_orphaned_usage"]


async def purge_orphaned_usage(known_app_ids: list[str]) -> int:
    """Module-level helper — purge usage events for apps not in known_app_ids."""
    return await get_runtime_usage_ledger().purge_orphaned_usage(known_app_ids=known_app_ids)
