"""AppGenerator on_complete lifecycle tool: record module cooccurrence patterns.

After a build completes, extracts the set of generated module IDs from
context_variables["generated_files"] and POSTs them to the hosted
build_intelligence module so pairwise module cooccurrence patterns can be
scored.

This tool is a no-op in OSS/unauthenticated runs where app_backend_url or
build_registry_id is absent. It never raises or blocks the build.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

# Regex to extract module_id from generated file paths.
# Matches: modules/{module_id}/... or app/modules/{module_id}/...
_MODULE_PATH_RE = re.compile(r"(?:^|/)modules/([a-zA-Z0-9_-]+)/")


def _extract_module_ids(generated_files: Any) -> list[str]:
    """Extract sorted, deduplicated module IDs from generated_files keys."""
    if not isinstance(generated_files, dict):
        return []
    seen: set[str] = set()
    for path in generated_files:
        match = _MODULE_PATH_RE.search(str(path))
        if match:
            seen.add(match.group(1))
    return sorted(seen)


def _build_endpoint(app_backend_url: str) -> str:
    return f"{app_backend_url.rstrip('/')}/api/modules/build_intelligence/record_module_cooccurrence"


async def record_module_patterns(
    context_variables: Optional[dict[str, Any]] = None,
    **_: Any,
) -> dict[str, Any]:
    """Fire-and-forget module cooccurrence recorder. Never raises."""
    ctx = context_variables or {}

    build_registry_id: str = (ctx.get("build_registry_id") or "").strip()
    app_backend_url: str = (ctx.get("app_backend_url") or "").strip()

    if not build_registry_id or not app_backend_url:
        return {"skipped": True, "reason": "no_registry_id_or_backend_url"}

    generated_files = ctx.get("generated_files") or {}
    module_ids = _extract_module_ids(generated_files)

    if len(module_ids) < 2:
        # Need at least 2 modules for a cooccurrence pair.
        return {"skipped": True, "reason": "insufficient_modules", "module_count": len(module_ids)}

    endpoint = _build_endpoint(app_backend_url)
    payload = {
        "build_registry_id": build_registry_id,
        "module_ids": module_ids,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code not in (200, 201, 202, 204):
                log.debug(
                    "module_pattern_recorder: platform returned %d — %s",
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception as exc:
        log.debug("module_pattern_recorder: non-blocking error: %s", exc)

    return {"recorded": True, "module_count": len(module_ids), "modules": module_ids}
