"""
Read-only carry-forward module contract reader.

Allows AppPlanAgent (during ``conceptual_replan``) to inspect specific
contract files from the previous app_bundle artifact for carry-forward
candidates before deciding whether to reuse, adapt, or regenerate them.

This tool is **read-only**:
- No file writes.
- No artifact writes.
- No LLM calls.
- No backend Python source is readable — only declared module contract files.
- No file copy or merge behavior.

Callable by AppPlanAgent as an AG2 tool (via ``context_variables``)
and by test harnesses via direct argument passing.

Input:
- ``module_id`` — the module to inspect (must exist in the previous bundle).
- ``files`` — optional list of contract filenames to read from the allowed
  set. When ``None``, all allowed files that exist in the workspace are
  returned.
- ``context_variables`` — dict containing at minimum:
  - ``app_id``: the app being replanned.
  - ``previous_app_bundle_ref``: artifact version id of the prior bundle.
- ``artifact_store`` — optional; injected in tests; resolved from runtime
  when omitted.

Output (always a dict, never raises):
- ``module_id`` — echoed back.
- ``files`` — ``{filename: content}`` dict for the returned files.
- ``available_files`` — all allowed contract files present in the workspace
  for this module.
- ``missing_files`` — requested files not found in the workspace.
- ``warnings`` — diagnostic messages for missing refs, disallowed paths, or
  load failures.

Allowed files (relative to ``modules/{module_id}/``):
- ``module.yaml``
- ``runtime_extensions.yaml``
- ``contracts/events.yaml``
- ``contracts/reactions.yaml``
- ``contracts/notifications.yaml``
- ``contracts/policy_hooks.yaml``
- ``contracts/settings.yaml``
- ``contracts/admin.yaml``
- ``contracts/profile.yaml``
- ``contracts/relationships.yaml``

Disallowed:
- ``backend/*.py`` — backend source is not exposed in this phase.
- Any path outside ``modules/{module_id}/`` — path traversal is rejected.
- Arbitrary filenames not in the allowed set.
"""
from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

from ._artifact_workspace import load_artifact_workspace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed contract files (relative to modules/{module_id}/)
# ---------------------------------------------------------------------------

_ALLOWED_CONTRACT_FILES: frozenset[str] = frozenset({
    "module.yaml",
    "runtime_extensions.yaml",
    "contracts/events.yaml",
    "contracts/reactions.yaml",
    "contracts/notifications.yaml",
    "contracts/policy_hooks.yaml",
    "contracts/settings.yaml",
    "contracts/admin.yaml",
    "contracts/profile.yaml",
    "contracts/relationships.yaml",
})


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

async def read_carry_forward_module_contract(
    module_id: str,
    files: list[str] | None = None,
    *,
    context_variables: dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Return selected contract files from the previous app_bundle for one module.

    Read-only. Never raises. Returns empty files + warnings on any failure.

    Args:
        module_id: Module directory name to inspect.
        files: Optional list of allowed filenames to read. ``None`` returns
            all allowed files that are present in the workspace.
        context_variables: AG2 workflow context (or plain dict). Must contain
            ``app_id`` and ``previous_app_bundle_ref``.
        artifact_store: Optional injected store; resolved from runtime when None.

    Returns:
        Dict with keys: ``module_id``, ``files``, ``available_files``,
        ``missing_files``, ``warnings``.
    """
    warnings: list[str] = []

    # --- Validate module_id ---
    module_id_clean = _validate_module_id(module_id)
    if not module_id_clean:
        return _empty(
            module_id=str(module_id or ""),
            warning=f"invalid_module_id: module_id {module_id!r} contains invalid characters or is empty",
        )

    # --- Extract context fields ---
    ctx = dict(context_variables) if context_variables else {}
    app_id = str(ctx.get("app_id") or "").strip()
    previous_app_bundle_ref = str(ctx.get("previous_app_bundle_ref") or "").strip()

    if not app_id:
        return _empty(
            module_id=module_id_clean,
            warning="missing_app_id: context_variables['app_id'] is required",
        )

    if not previous_app_bundle_ref:
        return _empty(
            module_id=module_id_clean,
            warning=(
                "missing_previous_app_bundle_ref: "
                "context_variables['previous_app_bundle_ref'] was not supplied; "
                "cannot inspect prior module without it"
            ),
        )

    # --- Validate requested file names ---
    requested_files, file_warnings = _validate_requested_files(files)
    warnings.extend(file_warnings)

    # --- Load workspace ---
    store = artifact_store or get_artifact_store()

    try:
        workspace = await load_artifact_workspace(
            artifact_store=store,
            app_id=app_id,
            artifact_version_id=previous_app_bundle_ref,
        )
    except Exception as exc:
        logger.warning(
            "read_carry_forward_module_contract: workspace load raised for "
            "app_id=%s version=%s module_id=%s: %s",
            app_id,
            previous_app_bundle_ref,
            module_id_clean,
            exc,
        )
        return _empty(
            module_id=module_id_clean,
            warning=(
                f"workspace_load_error: failed to load artifact workspace "
                f"(artifact_version_id={previous_app_bundle_ref}): {exc}"
            ),
        )

    if not workspace.get("present"):
        reason = workspace.get("reason", "unknown")
        return _empty(
            module_id=module_id_clean,
            warning=(
                f"workspace_unavailable: {reason} "
                f"(artifact_version_id={previous_app_bundle_ref})"
            ),
        )

    file_map: dict[str, str] = workspace.get("file_map") or {}
    prefix = f"modules/{module_id_clean}/"

    # --- Determine which allowed files exist for this module ---
    available_files: list[str] = sorted(
        fname
        for fname in _ALLOWED_CONTRACT_FILES
        if (prefix + fname) in file_map
    )

    if not available_files:
        warnings.append(
            f"module_not_found_or_empty: no allowed contract files found under "
            f"modules/{module_id_clean}/ in the previous app bundle workspace"
        )

    # --- Resolve which files to read ---
    to_read: list[str] = requested_files if requested_files is not None else available_files

    # --- Build result ---
    result_files: dict[str, str] = {}
    missing_files: list[str] = []

    for fname in to_read:
        key = prefix + fname
        if key in file_map:
            result_files[fname] = file_map[key]
        else:
            missing_files.append(fname)

    return {
        "module_id": module_id_clean,
        "files": result_files,
        "available_files": available_files,
        "missing_files": missing_files,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_module_id(raw: Any) -> str | None:
    """Return a sanitized module_id or None if invalid."""
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    # Reject path traversal, slashes, or dots at the start
    if "/" in cleaned or "\\" in cleaned or ".." in cleaned or cleaned.startswith("."):
        return None
    return cleaned


def _validate_requested_files(
    files: list[str] | None,
) -> tuple[list[str] | None, list[str]]:
    """Return (validated_list_or_None, warnings).

    ``None`` means "all available" (files parameter was None).
    Entries that are not in the allowed set are dropped with a warning.
    """
    if files is None:
        return None, []

    if not isinstance(files, list):
        return None, [
            f"invalid_files_param: 'files' must be a list or null, got {type(files).__name__}"
        ]

    warnings: list[str] = []
    validated: list[str] = []

    for entry in files:
        if not isinstance(entry, str):
            warnings.append(
                f"disallowed_file: non-string entry {entry!r} skipped"
            )
            continue
        fname = entry.strip()
        if fname in _ALLOWED_CONTRACT_FILES:
            validated.append(fname)
        else:
            warnings.append(
                f"disallowed_file: '{fname}' is not in the allowed contract file set "
                f"(backend source files are not readable in this phase; "
                f"allowed: {sorted(_ALLOWED_CONTRACT_FILES)})"
            )

    return validated, warnings


def _empty(
    *,
    module_id: str,
    warning: str,
) -> dict[str, Any]:
    return {
        "module_id": module_id,
        "files": {},
        "available_files": [],
        "missing_files": [],
        "warnings": [warning],
    }


__all__ = ["read_carry_forward_module_contract", "_ALLOWED_CONTRACT_FILES"]
