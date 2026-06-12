"""AppGenerator on_complete / on_fail lifecycle tool: record quality gate corrections.

At workflow end, reads the final status of each quality gate from context_variables.
For any gate that ended in "blocked" status, posts a correction record to the
hosted build_intelligence module so the gate_failure pattern and corrections store
are populated.

This tool is a no-op in OSS/unauthenticated runs where app_backend_url or
build_registry_id is absent. It never raises or blocks the build.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# Maps gate context variable prefix → gate_type value posted to build_intelligence.
_GATES: list[tuple[str, str]] = [
    ("app_ui_quality", "ui_quality"),
    ("module_contract_quality", "module_contract_quality"),
    ("module_runtime_quality", "module_runtime_quality"),
]


def _build_endpoint(app_backend_url: str) -> str:
    return f"{app_backend_url.rstrip('/')}/api/modules/build_intelligence/record_quality_gate_block"


def _extract_issues(warnings: Any, gate_type: str) -> list[dict[str, Any]]:
    """Convert quality gate warning strings to structured issue dicts."""
    if not isinstance(warnings, list):
        return []
    issues: list[dict[str, Any]] = []
    for w in warnings:
        if isinstance(w, str) and w.strip():
            issues.append({"code": gate_type, "message": w.strip()})
    return issues[:50]  # cap to avoid oversized payloads


async def record_gate_corrections(
    context_variables: Optional[dict[str, Any]] = None,
    **_: Any,
) -> dict[str, Any]:
    """Fire-and-forget quality gate correction recorder. Never raises."""
    ctx = context_variables or {}

    build_registry_id: str = (ctx.get("build_registry_id") or "").strip()
    app_backend_url: str = (ctx.get("app_backend_url") or "").strip()

    if not build_registry_id or not app_backend_url:
        return {"skipped": True, "reason": "no_registry_id_or_backend_url"}

    domain_tags: list[str] = [
        t for t in (ctx.get("domain_tags") or []) if isinstance(t, str) and t.strip()
    ]
    endpoint = _build_endpoint(app_backend_url)
    posted = 0

    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            for prefix, gate_type in _GATES:
                status = (ctx.get(f"{prefix}_status") or "").strip().lower()
                if status != "blocked":
                    continue

                warnings = ctx.get(f"{prefix}_warnings") or []
                issues = _extract_issues(warnings, gate_type)
                payload = {
                    "build_registry_id": build_registry_id,
                    "gate_type": gate_type,
                    "issues": issues,
                    "issue_count": len(issues),
                    "domain_tags": domain_tags,
                }
                try:
                    resp = await client.post(endpoint, json=payload)
                    if resp.status_code not in (200, 201, 202, 204):
                        log.debug(
                            "gate_corrections_recorder: platform returned %d for %s — %s",
                            resp.status_code,
                            gate_type,
                            resp.text[:200],
                        )
                    else:
                        posted += 1
                except Exception as exc:
                    log.debug("gate_corrections_recorder: error posting %s: %s", gate_type, exc)
    except Exception as exc:
        log.debug("gate_corrections_recorder: non-blocking error: %s", exc)

    return {"recorded": True, "gates_posted": posted}
