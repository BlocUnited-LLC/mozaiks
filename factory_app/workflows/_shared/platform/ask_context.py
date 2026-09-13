# ==============================================================================
# FILE: factory_app/workflows/_shared/platform/ask_context.py
# DESCRIPTION: Studio ask_context hook — summarizes the user's workspace apps
#              and their in-flight build so ask-mode answers are grounded in
#              real registry state.
# ==============================================================================
from __future__ import annotations

from typing import Any

_RECENT_APP_LIMIT = 5

# Registry lifecycle states (see app_registry policy.APP_LIFECYCLE_STATES) that
# mean work is still in flight. Studio build sessions are bound to a generated
# target app, so they live in a target-scoped session document the runtime's own
# default-scope snapshot never sees — the registry record is the reliable source
# for "what is this user building now".
_IN_FLIGHT_STATES = frozenset({"building", "configuring", "deploying"})


async def studio_ask_context(*, app_id: str, user_id: str) -> dict[str, Any]:
    """Summarize the user's app registry for ask-mode workspace context.

    Registered on the Studio host through the platform ``ask_context`` hook.
    Best-effort: any failure is handled by the hook registry (logged, skipped),
    so this function only needs to describe what the registry actually holds.
    """
    _ = app_id  # workspace apps are owner-scoped, not host-app-scoped
    from factory_app.app.modules.app_registry.backend.service import AppRegistryService

    apps = (await AppRegistryService().list_apps(owner_user_id=user_id)).get("apps") or []
    if not isinstance(apps, list):
        return {}

    total = len(apps)
    if not total:
        return {"Workspace apps": "none created yet"}

    state_counts: dict[str, int] = {}
    recent: list[str] = []
    active_build: str | None = None

    for record in apps:
        if not isinstance(record, dict):
            continue
        state = str(record.get("lifecycle_state") or "unknown").strip() or "unknown"
        state_counts[state] = state_counts.get(state, 0) + 1

        label = str(record.get("name") or record.get("app_id") or "").strip()
        if label and len(recent) < _RECENT_APP_LIMIT:
            recent.append(f"{label} ({state})" if state != "unknown" else label)

        # Records are sorted newest-updated first, so the first in-flight build
        # with a bound workflow is the one the user is currently working in.
        if active_build is None and state in _IN_FLIGHT_STATES:
            workflow_id = str(record.get("active_workflow_id") or "").strip()
            if workflow_id:
                active_build = f"{label or record.get('app_id') or 'app'} — {workflow_id} ({state})"

    counts = ", ".join(f"{count} {state}" for state, count in sorted(state_counts.items()))
    context: dict[str, Any] = {"Workspace apps": f"{total} total — {counts}"}
    if recent:
        context["Most recently updated apps"] = "; ".join(recent)
    if active_build:
        context["Build in progress"] = active_build
    return context
