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
from typing import Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from mozaikscore.core.event_bus import event_bus

logger = logging.getLogger("mozaikscore.cross_substrate_bridge")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MOZAIKSAI_URL = os.getenv("MOZAIKSAI_URL", "http://localhost:8000").rstrip("/")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")

# Events to relay FROM mozaikscore TO mozaiksai
_OUTBOUND_EVENTS = [
    "user_action",                  # user clicked something that needs agent processing
    "report_requested",             # module fired a report generation request
    "module_executed",              # inform AI side that module work completed
    "subscription_updated",         # plan change may affect agent capabilities
]

# ---------------------------------------------------------------------------
# Outbound relay: mozaikscore → mozaiksai
# ---------------------------------------------------------------------------

async def _relay_to_mozaiksai(event_name: str, data: Dict[str, Any]):
    """POST the event to mozaiksai's /api/substrate-events endpoint."""
    url = f"{MOZAIKSAI_URL}/api/substrate-events"
    payload = {
        "source": "mozaikscore",
        "event": event_name,
        "data": data,
    }
    headers = {"Content-Type": "application/json"}
    if INTERNAL_API_KEY:
        headers["X-Internal-API-Key"] = INTERNAL_API_KEY

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            logger.warning(
                "Relay to mozaiksai failed: %s %d %s",
                event_name, resp.status_code, resp.text[:200],
            )
        else:
            logger.debug("Relayed '%s' to mozaiksai", event_name)
    except httpx.HTTPError as exc:
        # Non-fatal: mozaiksai may not be running
        logger.debug("Could not relay '%s' to mozaiksai: %s", event_name, exc)


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
