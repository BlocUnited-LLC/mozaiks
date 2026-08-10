"""Opt-in framework build telemetry client.

Sends anonymized build outcome payloads to a configured endpoint.
No user content, generated code, or reversible app/operator/customer identity
is sent by the generic OSS emitter.

The OSS telemetry contract is intentionally narrow: it may describe one build
workflow's framework-level outcome, but it must not become an Operator
Intelligence channel. Cross-app learning, customer feedback analysis,
production outcomes, and commercial optimization belong behind explicitly
reviewed hosted receivers and schemas.

Environment variables
---------------------
MOZAIKS_TELEMETRY_ENABLED   : Set to "false" to disable entirely (default: true if endpoint set)
MOZAIKS_TELEMETRY_ENDPOINT  : HTTPS URL of the telemetry receiver. Unset = off.
MOZAIKS_TELEMETRY_SECRET    : Shared HMAC secret for payload signing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA_VERSION = "mozaiks.telemetry.v1"
_TIMEOUT_S = 3.0
_ALLOWED_TELEMETRY_FIELDS = (
    "schema_version",
    "workflow_name",
    "final_status",
    "build_id_hash",
    "refinement_cycles",
    "quality_gate_blocks",
    "duration_seconds",
    "domain_tags",
    "mozaiks_version",
    "timestamp",
    # Existing hosted Build Intelligence consumes this explicit reviewed shape.
    "event",
    "rating",
    "sequence_id",
)
_IDENTITY_FIELD_FRAGMENTS = (
    "app_id",
    "build_id",
    "build_registry_id",
    "chat_id",
    "customer",
    "email",
    "operator",
    "organization",
    "org_id",
    "repo",
    "tenant",
    "user_id",
    "workspace",
)


def _endpoint() -> str:
    return os.getenv("MOZAIKS_TELEMETRY_ENDPOINT", "").strip()


def _enabled() -> bool:
    if not _endpoint():
        return False
    return os.getenv("MOZAIKS_TELEMETRY_ENABLED", "true").strip().lower() != "false"


def _secret() -> str:
    return os.getenv("MOZAIKS_TELEMETRY_SECRET", "").strip()


def _sign(payload_bytes: bytes) -> str:
    """HMAC-SHA256 signature over the raw payload bytes."""
    secret = _secret()
    if not secret:
        return ""
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


def _anonymize_build_id(build_registry_id: str) -> str:
    """One-way hash — not reversible, safe to send."""
    return hashlib.sha256(build_registry_id.encode()).hexdigest()[:16]


def build_workflow_payload(
    *,
    workflow_name: str,
    final_status: str,
    build_registry_id: str = "",
    refinement_cycles: int = 0,
    quality_gate_blocks: int = 0,
    duration_seconds: float = 0.0,
    domain_tags: list[str] | None = None,
    mozaiks_version: str = "",
) -> dict[str, Any]:
    """Assemble an anonymized per-workflow telemetry payload."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "workflow_name": workflow_name,
        "final_status": final_status,
        "build_id_hash": _anonymize_build_id(build_registry_id) if build_registry_id else "",
        "refinement_cycles": max(0, int(refinement_cycles)),
        "quality_gate_blocks": max(0, int(quality_gate_blocks)),
        "duration_seconds": max(0.0, float(duration_seconds)),
        "domain_tags": list(domain_tags or []),
        "mozaiks_version": mozaiks_version or _get_mozaiks_version(),
        "timestamp": _iso_now(),
    }


def build_satisfaction_payload(
    *,
    rating: int,
    build_registry_id: str = "",
    sequence_id: str = "",
) -> dict[str, Any]:
    """Assemble an anonymized user satisfaction payload."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "event": "build_satisfaction",
        "rating": max(1, min(5, int(rating))),
        "build_id_hash": _anonymize_build_id(build_registry_id) if build_registry_id else "",
        "sequence_id": sequence_id,
        "mozaiks_version": _get_mozaiks_version(),
        "timestamp": _iso_now(),
    }


def sanitize_framework_telemetry_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded OSS framework telemetry shape sent over the network."""
    raw = payload if isinstance(payload, dict) else {}
    out: dict[str, Any] = {"schema_version": str(raw.get("schema_version") or _SCHEMA_VERSION)}
    for key in _ALLOWED_TELEMETRY_FIELDS:
        if key == "schema_version" or key not in raw:
            continue
        if _looks_identity_field(key):
            continue
        value = raw.get(key)
        if key == "domain_tags":
            out[key] = _safe_domain_tags(value)
        elif key in {"refinement_cycles", "quality_gate_blocks"}:
            out[key] = max(0, int(value or 0))
        elif key == "duration_seconds":
            out[key] = max(0.0, float(value or 0.0))
        elif key == "rating":
            out[key] = max(1, min(5, int(value or 0)))
        elif isinstance(value, str):
            clean = value.strip()
            if clean:
                out[key] = clean[:160]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
    return out


async def emit_build_telemetry(payload: dict[str, Any]) -> None:
    """Fire-and-forget telemetry emit. Never raises. Never blocks the build."""
    if not _enabled():
        return
    try:
        import httpx
        safe_payload = sanitize_framework_telemetry_payload(payload)
        payload_bytes = json.dumps(safe_payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-Mozaiks-Signature": _sign(payload_bytes)}
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            await client.post(_endpoint(), content=payload_bytes, headers=headers)
    except Exception as exc:  # noqa: BLE001
        log.debug("Telemetry emit failed (non-blocking): %s", exc)


def emit_build_telemetry_sync(payload: dict[str, Any]) -> None:
    """Sync fire-and-forget for lifecycle tool contexts that are not async."""
    if not _enabled():
        return
    try:
        import httpx
        safe_payload = sanitize_framework_telemetry_payload(payload)
        payload_bytes = json.dumps(safe_payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-Mozaiks-Signature": _sign(payload_bytes)}
        with httpx.Client(timeout=_TIMEOUT_S) as client:
            client.post(_endpoint(), content=payload_bytes, headers=headers)
    except Exception as exc:  # noqa: BLE001
        log.debug("Telemetry emit failed (non-blocking): %s", exc)


def _get_mozaiks_version() -> str:
    try:
        from mozaiksai.version import __version__
        return __version__
    except Exception:
        return "unknown"


def _iso_now() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


def _looks_identity_field(key: str) -> bool:
    lowered = str(key or "").lower()
    if lowered.endswith("_hash"):
        return False
    return any(fragment in lowered for fragment in _IDENTITY_FIELD_FRAGMENTS)


def _safe_domain_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if not text or "@" in text or "/" in text or "\\" in text:
            continue
        normalized = "".join(char for char in text if char.isalnum() or char in {"_", "-", "."}).strip("._-")
        if normalized and normalized not in tags:
            tags.append(normalized[:48])
        if len(tags) >= 10:
            break
    return tags
