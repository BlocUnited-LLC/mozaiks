"""AppGenerator on_complete lifecycle tool: populate build domain tags.

After a build completes, extracts domain tags from the selected capability_packs
in context_variables and POSTs them to the hosted build_intelligence module so
the build entry's domain_tags field is populated before pattern scoring runs.

Domain tags are derived from capability pack IDs (e.g. "mozaikspay" → "mozaikspay").
This ensures gate_failure and refinement_hotspot patterns are domain-specific
rather than always falling back to "global".

This tool is a no-op in OSS/unauthenticated runs where app_backend_url or
build_registry_id is absent. It never raises or blocks the build.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

_SAFE_TAG_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _derive_domain_tags(capability_packs: Any) -> list[str]:
    """Extract normalized domain tag strings from the capability_packs context value.

    Accepts a list of pack dicts (with pack_id key) or plain strings.
    """
    if not isinstance(capability_packs, list):
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for pack in capability_packs:
        raw: str | None = None
        if isinstance(pack, dict):
            raw = str(pack.get("pack_id") or pack.get("id") or "").strip()
        elif isinstance(pack, str):
            raw = pack.strip()
        if raw and _SAFE_TAG_RE.match(raw) and raw not in seen:
            seen.add(raw)
            tags.append(raw)
    return tags


def _build_endpoint(app_backend_url: str) -> str:
    return f"{app_backend_url.rstrip('/')}/api/modules/build_intelligence/set_build_domain_tags"


async def record_domain_tags(
    context_variables: Optional[dict[str, Any]] = None,
    **_: Any,
) -> dict[str, Any]:
    """Fire-and-forget domain tag recorder. Never raises."""
    ctx = context_variables or {}

    build_registry_id: str = (ctx.get("build_registry_id") or "").strip()
    app_backend_url: str = (ctx.get("app_backend_url") or "").strip()

    if not build_registry_id or not app_backend_url:
        return {"skipped": True, "reason": "no_registry_id_or_backend_url"}

    capability_packs = ctx.get("capability_packs") or []
    domain_tags = _derive_domain_tags(capability_packs)

    if not domain_tags:
        return {"skipped": True, "reason": "no_domain_tags_derived", "pack_count": len(capability_packs)}

    endpoint = _build_endpoint(app_backend_url)
    payload = {
        "build_registry_id": build_registry_id,
        "domain_tags": domain_tags,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code not in (200, 201, 202, 204):
                log.debug(
                    "domain_tags_recorder: platform returned %d — %s",
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception as exc:
        log.debug("domain_tags_recorder: non-blocking error: %s", exc)

    return {"recorded": True, "domain_tags": domain_tags}
