"""
Read-only control-plane tool: get_carry_forward_candidates.

Inspects the previous app_bundle artifact workspace and returns a structured
module inventory that the refinement router can use to populate
``carry_forward_modules`` for ``conceptual_replan`` sequences.

This tool is **read-only**:
- No file writes.
- No artifact writes.
- No LLM calls.
- Filesystem access only through :func:`._artifact_workspace.load_artifact_workspace`.

Available at the ``route_requested`` checkpoint because:
- ``conceptual_replan`` routing is determined at ``route_requested`` — earlier
  checkpoints (``request_submitted``) cannot know whether carry-forward is
  relevant.
- Phase 3 will call this tool programmatically from ``_build_context_seed()``
  inside ``RefinementTriggerRouteResolver``, which runs at ``route_requested``.
- ``scope_requested`` and ``coding_requested`` are patch-class-only checkpoints
  that ``conceptual_replan`` sequences never reach.

Input (via ``ControlPlaneToolContext``):
- ``context.app_id`` — the app being refined.
- ``context.extra["previous_app_bundle_ref"]`` — artifact version id of the
  previous app bundle. If absent or empty, returns an empty result + warning.

Output (always a dict, never raises):
- ``modules`` — list of ``ModuleInventoryEntry`` dicts.
- ``count`` — number of candidate modules found.
- ``source_artifact_version_id`` — artifact version id that was read, or None.
- ``warnings`` — list of diagnostic strings; non-empty when workspace was
  missing, unreadable, or contained no recognizable modules.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

from ._artifact_workspace import load_artifact_workspace
from ._module_inventory import extract_module_inventory
from ._shared import normalize_context

logger = logging.getLogger(__name__)


async def get_carry_forward_candidates(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: Optional[ArtifactStore] = None,
) -> dict[str, Any]:
    """Return candidate modules from the previous app_bundle artifact.

    Read-only. Never raises. Returns empty modules + warnings on any failure.
    """
    ctx = normalize_context(context)
    app_id = str(ctx.app_id or "").strip()
    previous_app_bundle_ref = str(ctx.extra.get("previous_app_bundle_ref") or "").strip()

    if not app_id:
        return _empty("missing_app_id: context.app_id is required")

    if not previous_app_bundle_ref:
        return _empty(
            "missing_previous_app_bundle_ref: "
            "context.extra['previous_app_bundle_ref'] was not supplied; "
            "carry_forward_modules cannot be auto-populated without it"
        )

    store = artifact_store or get_artifact_store()

    try:
        workspace = await load_artifact_workspace(
            artifact_store=store,
            app_id=app_id,
            artifact_version_id=previous_app_bundle_ref,
        )
    except Exception as exc:
        logger.warning(
            "get_carry_forward_candidates: workspace load raised for app_id=%s version=%s: %s",
            app_id,
            previous_app_bundle_ref,
            exc,
        )
        return _empty(
            f"workspace_load_error: failed to load artifact workspace "
            f"(artifact_version_id={previous_app_bundle_ref}): {exc}"
        )

    if not workspace.get("present"):
        reason = workspace.get("reason", "unknown")
        return _empty(
            f"workspace_unavailable: {reason} "
            f"(artifact_version_id={previous_app_bundle_ref})",
            source_artifact_version_id=previous_app_bundle_ref,
        )

    file_map: dict[str, str] = workspace.get("file_map") or {}
    artifact = workspace.get("artifact")
    resolved_version_id: str = (
        str(artifact.id) if artifact is not None else previous_app_bundle_ref
    )

    warnings: list[str] = []

    try:
        entries = extract_module_inventory(file_map)
    except Exception as exc:
        logger.warning(
            "get_carry_forward_candidates: extract_module_inventory raised for app_id=%s version=%s: %s",
            app_id,
            resolved_version_id,
            exc,
        )
        return _empty(
            f"inventory_extraction_error: {exc}",
            source_artifact_version_id=resolved_version_id,
        )

    if not entries:
        warnings.append(
            "no_modules_found: the app bundle workspace contained no recognizable "
            "modules (modules/{module_id}/module.yaml not found in file_map)"
        )

    return {
        "modules": [entry.model_dump() for entry in entries],
        "count": len(entries),
        "source_artifact_version_id": resolved_version_id,
        "warnings": warnings,
    }


def _empty(
    warning: str,
    *,
    source_artifact_version_id: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "modules": [],
        "count": 0,
        "source_artifact_version_id": source_artifact_version_id,
        "warnings": [warning],
    }


__all__ = ["get_carry_forward_candidates"]
