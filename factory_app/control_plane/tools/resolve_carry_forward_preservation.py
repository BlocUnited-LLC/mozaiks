"""
Carry-forward preservation resolver for AppGenerator assembly — Phase 7A.

Copies only Phase 7A allowlisted declarative module contract files from the
prior app bundle workspace into the assembled output for modules with a
``reuse`` carry_forward_decision.

Rules:
- Generated output always wins. The resolver only fills empty paths.
- Only ``module.yaml`` and ``contracts/*.yaml`` may be copied.
- Backend Python, runtime_extensions.yaml, custom React, route manifests,
  database artifacts, env files, and all non-declarative files are permanently
  forbidden.
- Never raises. All failures go to ``carry_forward_report.warnings``.
- No-ops gracefully when prior workspace is unavailable or when
  ``carry_forward_decisions`` is absent (normal for non-conceptual builds).
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Optional

from factory_app.control_plane.tools._artifact_workspace import load_artifact_workspace
from mozaiksai.core.artifacts.content_store import ArtifactContentStore, get_artifact_content_store
from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

# ---------------------------------------------------------------------------
# Phase 7A file allowlist — relative paths under modules/{module_id}/
# ---------------------------------------------------------------------------
_PHASE_7A_MODULE_ALLOWLIST: frozenset[str] = frozenset({
    "module.yaml",
    "contracts/events.yaml",
    "contracts/reactions.yaml",
    "contracts/notifications.yaml",
    "contracts/settings.yaml",
    "contracts/admin.yaml",
    "contracts/profile.yaml",
})

# ---------------------------------------------------------------------------
# Denylist — belt-and-suspenders over the allowlist. Checked against the
# full path relative to the app root (e.g. "modules/settings/module.yaml").
# ---------------------------------------------------------------------------

# Basenames that are always rejected regardless of where they appear
_DENYLIST_BASENAMES: frozenset[str] = frozenset({
    "runtime_extensions.yaml",
    ".env.example",
    "requirements.txt",
})

# Full relative paths that are always rejected
_DENYLIST_FULL_PATHS: frozenset[str] = frozenset({
    "ui/route_manifest.json",
    "ui/index.js",
    "config/database_intent.json",
})

# Path prefixes that are always rejected
_DENYLIST_PREFIXES: tuple[str, ...] = (
    "config/database_migrations/",
    "ui/pages/custom/",
    "backend/integrations/",
    "backend/routes/",
)

# Case-insensitive substrings in the path that trigger rejection
_DENYLIST_SENSITIVE: tuple[str, ...] = (
    "secret",
    "credential",
    "password",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_safe_module_id(module_id: str) -> bool:
    """Return True if module_id is a plain directory name (no traversal)."""
    if not module_id or not isinstance(module_id, str):
        return False
    clean = str(module_id).strip()
    if not clean:
        return False
    if "/" in clean or "\\" in clean or ".." in clean or clean.startswith("."):
        return False
    return True


def _is_denylist_path(path: str) -> tuple[bool, str]:
    """Return (True, reason) if path fails any denylist rule.

    This is belt-and-suspenders: the allowlist already restricts what can be
    copied, but the denylist adds an explicit rejection layer so that future
    allowlist additions cannot accidentally open forbidden file families.
    """
    if not isinstance(path, str):
        return True, "invalid_type"
    try:
        p = PurePosixPath(path)
    except Exception:
        return True, "invalid_path"
    if p.is_absolute():
        return True, "absolute_path"
    if any(part == ".." for part in p.parts):
        return True, "path_traversal"
    # Python source files — backend carry-forward is permanently forbidden
    if path.endswith(".py"):
        return True, "backend_python"
    # Sensitive substrings
    lower = path.lower()
    for indicator in _DENYLIST_SENSITIVE:
        if indicator in lower:
            return True, f"sensitive_indicator:{indicator}"
    # Basename denylist
    basename = p.name
    if basename in _DENYLIST_BASENAMES:
        return True, f"denylist_basename:{basename}"
    # Full-path denylist
    if path in _DENYLIST_FULL_PATHS:
        return True, "denylist_full_path"
    # Prefix denylist
    for prefix in _DENYLIST_PREFIXES:
        if path.startswith(prefix):
            return True, f"denylist_prefix:{prefix}"
    return False, ""


def _extract_decisions(raw_plan: Any) -> list[dict[str, Any]]:
    """Extract carry_forward_decisions list from app_build_plan value."""
    if not raw_plan:
        return []
    if isinstance(raw_plan, dict):
        raw = raw_plan.get("carry_forward_decisions")
    else:
        raw = getattr(raw_plan, "carry_forward_decisions", None)
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict)]


def _cv_get(context_variables: Any, key: str) -> Any:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key)
        except Exception:
            pass
    if isinstance(context_variables, dict):
        return context_variables.get(key)
    return None


def _cv_set(context_variables: Any, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    if isinstance(context_variables, dict):
        context_variables[key] = value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def resolve_carry_forward_preservation(
    *,
    context_variables: Optional[dict[str, Any]] = None,
    artifact_store: Optional[ArtifactStore] = None,
    content_store: Optional[ArtifactContentStore] = None,
) -> dict[str, Any]:
    """Inject allowlisted declarative module contract files from the prior app bundle.

    This is the Phase 7A carry-forward preservation resolver.  It reads
    ``app_build_plan.carry_forward_decisions`` from context, loads the prior
    app bundle workspace, and copies only ``module.yaml`` and
    ``contracts/*.yaml`` files for modules with ``decision == "reuse"`` to
    paths not already produced by generation.

    Writes:

    - ``context_variables["generated_files"]`` — merged dict (for schema path).
    - ``context_variables["carry_forward_additions"]`` — only the new preserved
      files (for the MFJ path, merged by generate_and_download).
    - ``context_variables["carry_forward_report"]`` — full report dict.

    Returns ``{"carry_forward_report": {...}}``.  Never raises.
    """
    prev_ref_raw = _cv_get(context_variables, "previous_app_bundle_ref")
    prev_ref: Optional[str] = str(prev_ref_raw).strip() if prev_ref_raw else None

    report: dict[str, Any] = {
        "previous_app_bundle_ref": prev_ref,
        "workspace_available": False,
        "workspace_source": None,
        "preserved_paths": [],
        "skipped_paths": {},
        "conflicts": {},
        "decisions_processed": [],
        "reused_modules": [],
        "adapted_modules": [],
        "regenerated_modules": [],
        "dropped_modules": [],
        "warnings": [],
    }

    raw_plan = _cv_get(context_variables, "app_build_plan")
    decisions = _extract_decisions(raw_plan)

    # No-op: no carry_forward_decisions present (normal for non-conceptual builds)
    if not decisions:
        _cv_set(context_variables, "carry_forward_report", report)
        return {"carry_forward_report": report}

    # No-op: no prior bundle reference
    if not prev_ref:
        _cv_set(context_variables, "carry_forward_report", report)
        return {"carry_forward_report": report}

    app_id = str(_cv_get(context_variables, "app_id") or "").strip()
    if not app_id:
        report["warnings"].append("app_id missing from context — preservation skipped")
        _cv_set(context_variables, "carry_forward_report", report)
        return {"carry_forward_report": report}

    # Load prior workspace using all three lookup branches
    try:
        store = artifact_store or get_artifact_store()
        cs = content_store or get_artifact_content_store()
        workspace = await load_artifact_workspace(
            artifact_store=store,
            app_id=app_id,
            artifact_version_id=prev_ref,
            content_store=cs,
        )
    except Exception as exc:
        report["warnings"].append(f"load_artifact_workspace raised unexpectedly: {exc}")
        _cv_set(context_variables, "carry_forward_report", report)
        return {"carry_forward_report": report}

    if not workspace.get("present"):
        reason = workspace.get("reason", "workspace_unavailable")
        report["warnings"].append(
            f"Prior workspace not available (reason={reason!r}) — "
            "carry-forward preservation skipped"
        )
        _cv_set(context_variables, "carry_forward_report", report)
        return {"carry_forward_report": report}

    report["workspace_available"] = True
    report["workspace_source"] = str(workspace.get("source") or "")
    prior_file_map: dict[str, str] = dict(workspace.get("file_map") or {})

    # Current assembled generated output (set by assemble_app_tasks on both paths)
    raw_generated = _cv_get(context_variables, "generated_files")
    generated_files: dict[str, str] = dict(raw_generated) if isinstance(raw_generated, dict) else {}

    preservation_additions: dict[str, str] = {}

    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            report["warnings"].append(f"Skipped malformed decision entry: {raw_decision!r}")
            continue

        module_id = str(raw_decision.get("module_id") or "").strip()
        decision_value = str(raw_decision.get("decision") or "").strip().lower()

        decision_entry: dict[str, Any] = {
            "module_id": module_id,
            "decision": decision_value,
            "outcome": "skipped",
        }

        if not _is_safe_module_id(module_id):
            decision_entry["outcome"] = "invalid_module_id"
            report["decisions_processed"].append(decision_entry)
            report["warnings"].append(
                f"Invalid module_id in carry-forward decision: {module_id!r}"
            )
            continue

        if decision_value == "reuse":
            report["reused_modules"].append(module_id)
            preserved_count = 0
            module_prefix = f"modules/{module_id}/"

            for rel_allowed in sorted(_PHASE_7A_MODULE_ALLOWLIST):
                full_path = f"{module_prefix}{rel_allowed}"

                # Belt-and-suspenders denylist check
                denied, deny_reason = _is_denylist_path(full_path)
                if denied:
                    report["skipped_paths"][full_path] = f"denylist:{deny_reason}"
                    continue

                # Path not present in prior workspace — nothing to preserve
                if full_path not in prior_file_map:
                    continue

                # Conflict: generated output already owns this path
                if full_path in generated_files or full_path in preservation_additions:
                    report["conflicts"][full_path] = "generated_output_wins"
                    continue

                preservation_additions[full_path] = prior_file_map[full_path]
                report["preserved_paths"].append(full_path)
                preserved_count += 1

            decision_entry["outcome"] = "preserved" if preserved_count > 0 else "nothing_to_preserve"
            decision_entry["preserved_count"] = preserved_count

        elif decision_value == "adapt":
            report["adapted_modules"].append(module_id)
            decision_entry["outcome"] = "no_files_copied"

        elif decision_value == "regenerate":
            report["regenerated_modules"].append(module_id)
            decision_entry["outcome"] = "no_files_copied"

        elif decision_value == "drop":
            report["dropped_modules"].append(module_id)
            decision_entry["outcome"] = "no_files_copied"
            # Warn if generated output already contains paths for the dropped module
            module_prefix = f"modules/{module_id}/"
            generated_module_paths = sorted(
                p for p in generated_files if p.startswith(module_prefix)
            )
            if generated_module_paths:
                sample = generated_module_paths[:3]
                report["warnings"].append(
                    f"Module {module_id!r} is marked drop but generated output "
                    f"contains {len(generated_module_paths)} path(s) for it "
                    f"(e.g. {', '.join(sample)}). Review AppBuildPlan drop decision."
                )

        else:
            decision_entry["outcome"] = "unknown_decision_value"
            report["warnings"].append(
                f"Unknown decision value {decision_value!r} for module {module_id!r}"
            )

        report["decisions_processed"].append(decision_entry)

    # Merge preserved files into generated_files and write both context keys
    if preservation_additions:
        updated_generated = dict(generated_files)
        updated_generated.update(preservation_additions)
        _cv_set(context_variables, "generated_files", updated_generated)

    # Write preservation_additions separately so generate_and_download can merge
    # them into files collected from agent outputs on the MFJ path.
    _cv_set(context_variables, "carry_forward_additions", dict(preservation_additions))
    _cv_set(context_variables, "carry_forward_report", report)
    return {"carry_forward_report": report}


__all__ = [
    "resolve_carry_forward_preservation",
    "_PHASE_7A_MODULE_ALLOWLIST",
    "_is_denylist_path",
    "_is_safe_module_id",
]
