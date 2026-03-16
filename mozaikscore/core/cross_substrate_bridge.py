# ==============================================================================
# FILE: mozaikscore/core/cross_substrate_bridge.py
# DESCRIPTION: Bidirectional event relay between mozaikscore and mozaiksai.
#
#   Outbound: Subscribes to mozaikscore event_bus events and POSTs them to
#             mozaiksai's event ingestion endpoint.
#   Inbound:  Provides a FastAPI route that mozaiksai can call to inject
#             events into mozaikscore's event_bus.
#
# Both directions are non-blocking and fault-tolerant (logged, not raised).
# ==============================================================================
import os
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from mozaikscore.core.automation_nats import (
    get_substrate_event_nats_publisher,
    use_http_transport,
    use_nats_transport,
)
from mozaikscore.core.config_loader import get_automation_event_catalog
from mozaikscore.core.event_bus import event_bus

logger = logging.getLogger("mozaikscore.cross_substrate_bridge")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MOZAIKSAI_URL = os.getenv("MOZAIKSAI_URL", "http://localhost:8000").rstrip("/")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
APP_ID = os.getenv("MOZAIKS_APP_ID", "dev_app").strip()


def _load_event_catalog() -> Dict[str, Dict[str, Any]]:
    catalog = get_automation_event_catalog()
    mapping: Dict[str, Dict[str, Any]] = {}
    for record in catalog.get("events", []):
        if not isinstance(record, dict):
            continue
        source_event = str(record.get("source_event") or "").strip()
        event_type = str(record.get("event_type") or "").strip()
        if source_event and event_type:
            mapping[source_event] = record
    return mapping


_OUTBOUND_EVENT_MAP = _load_event_catalog()
_OUTBOUND_EVENTS = sorted(_OUTBOUND_EVENT_MAP.keys())


def _sanitize_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if not str(key).startswith("_")
    }


def _build_envelope(source_event: str, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    clean = _sanitize_payload(data)
    user_id = str(clean.get("user_id") or clean.get("user") or "").strip() or None
    actor_type = "user" if user_id else "system"
    actor_id = user_id or "mozaikscore"
    correlation_id = (
        str(clean.get("correlation_id") or clean.get("chat_id") or clean.get("run_id") or "").strip()
        or None
    )

    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant": {
            "app_id": str(clean.get("app_id") or APP_ID or "").strip(),
            "user_id": user_id,
            "chat_id": str(clean.get("chat_id") or "").strip() or None,
            "run_id": str(clean.get("run_id") or "").strip() or None,
        },
        "actor": {
            "id": actor_id,
            "type": actor_type,
        },
        "source": {
            "layer": "substrate",
            "component": "cross_substrate_bridge",
            "transport": "http",
            "internal_event": source_event,
        },
        "payload": clean,
        "causation_id": str(clean.get("causation_id") or "").strip() or None,
        "correlation_id": correlation_id,
    }

# ---------------------------------------------------------------------------
# Outbound relay: mozaikscore → mozaiksai
# ---------------------------------------------------------------------------

async def _relay_to_mozaiksai(event_name: str, data: Dict[str, Any]):
    """Relay a substrate event to mozaiksai via the configured automation transport."""
    record = _OUTBOUND_EVENT_MAP.get(event_name)
    if not record:
        logger.debug("No automation catalog entry found for outbound event '%s'", event_name)
        return

    envelope = _build_envelope(
        source_event=event_name,
        event_type=str(record["event_type"]),
        data=data,
    )

    if use_nats_transport():
        try:
            publisher = get_substrate_event_nats_publisher()
            await publisher.publish(envelope)
        except Exception as exc:
            logger.warning("NATS relay to mozaiksai failed for '%s': %s", event_name, exc)
            if not use_http_transport():
                return

    if not use_http_transport():
        return

    url = f"{MOZAIKSAI_URL}/api/substrate-events"
    headers = {"Content-Type": "application/json"}
    if INTERNAL_API_KEY:
        headers["X-Internal-API-Key"] = INTERNAL_API_KEY

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(url, json=envelope, headers=headers)
        if resp.status_code != 200:
            logger.warning(
                "HTTP relay to mozaiksai failed: %s %d %s",
                event_name, resp.status_code, resp.text[:200],
            )
        else:
            logger.debug("Relayed '%s' to mozaiksai over HTTP", event_name)
    except httpx.HTTPError as exc:
        # Non-fatal: mozaiksai may not be running
        logger.debug("Could not relay '%s' to mozaiksai over HTTP: %s", event_name, exc)


def _make_outbound_handler(event_name: str):
    """Create an async callback for event_bus.subscribe()."""

    async def handler(data):
        await _relay_to_mozaiksai(event_name, data)

    handler.__name__ = f"relay_outbound_{event_name}"
    return handler


def register_outbound_relay():
    """Subscribe to outbound events on event_bus for relay to mozaiksai."""
    for event_name in _OUTBOUND_EVENTS:
        event_bus.subscribe(event_name, _make_outbound_handler(event_name))
    logger.info(
        "Cross-substrate bridge: registered %d outbound event relays to mozaiksai",
        len(_OUTBOUND_EVENTS),
    )


# ---------------------------------------------------------------------------
# Inbound relay: mozaiksai → mozaikscore
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/__mozaiks/internal", tags=["internal"])


def _validate_internal_key(request: Request):
    """Validate the internal API key on inbound relay requests."""
    if not INTERNAL_API_KEY:
        return  # Dev mode: no key required
    key = request.headers.get("X-Internal-API-Key", "")
    if not hmac_compare(key, INTERNAL_API_KEY):
        raise HTTPException(status_code=403, detail="Invalid internal API key")


def hmac_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


@router.post("/relay-event")
async def receive_relayed_event(request: Request):
    """
    Receive an event relayed from mozaiksai and publish it on mozaikscore's event_bus.

    Expected body:
        {
            "source": "mozaiksai",
            "event": "workflow_completed",
            "data": { ... }
        }
    """
    _validate_internal_key(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_name = body.get("event")
    data = body.get("data", {})
    source = body.get("source", "unknown")

    if not event_name:
        raise HTTPException(status_code=400, detail="Missing 'event' field")

    # Tag the event source so handlers know it's cross-substrate
    data["_relay_source"] = source

    event_bus.publish(event_name, data)
    logger.info("Inbound relay from %s: '%s'", source, event_name)

    return {"status": "ok", "event": event_name}
