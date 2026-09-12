from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.module_tools import dispatch_workflow_module_action


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    if hasattr(context_variables, "get"):
        try:
            return detach(context_variables.get(key, default))
        except TypeError:
            try:
                return detach(context_variables.get(key))
            except Exception:
                return default
        except Exception:
            return default
    return default


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
        except Exception:
            return


def _normalize_findings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


async def record_security_findings(
    findings: list[dict[str, Any]] | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    """Record findings through the live run's permissioned module dispatch."""

    resolved_findings = _normalize_findings(
        findings
        if findings is not None
        else _context_get(context_variables, "security_readiness_findings", [])
    )
    # Findings attach to the generated app's identity, not the executing
    # factory session app_id.
    from factory_app.workflows._shared.build_identity import resolve_generated_app_id

    app_id = str(resolve_generated_app_id(context_variables) or "").strip()
    build_id = str(_context_get(context_variables, "build_id", "") or "").strip() or None
    artifact_version_id = (
        str(_context_get(context_variables, "artifact_version_id", "") or "").strip() or None
    )

    build_registry_id = str(_context_get(context_variables, "build_registry_id", "") or "").strip() or None
    persisted = False
    result: dict[str, Any] = {"success": True, "persisted": False, "findings": resolved_findings}
    if app_id and resolved_findings:
        params = {
            "app_id": app_id,
            "source": "validation",
            "findings": [
                {
                    **{key: item[key] for key in (
                        "finding_id", "title", "description", "severity", "status", "control_area",
                    ) if key in item},
                    **({"evidence_ref": item["evidence"]["path"]} if isinstance(item.get("evidence"), dict) and item["evidence"].get("path") else {}),
                    **({"remediation": item["recommendation"]} if item.get("recommendation") else {}),
                }
                for item in resolved_findings
            ],
        }
        params.update({key: value for key, value in {
            "build_id": build_id, "build_registry_id": build_registry_id,
            "artifact_version_id": artifact_version_id,
        }.items() if value})
        try:
            dispatched = await dispatch_workflow_module_action("security_readiness", "record_assessment", params)
            persisted = dispatched.success and bool((dispatched.data or {}).get("success"))
            if persisted:
                result.update(dispatched.data)
            else:
                result.update(success=False, persistence_error=dispatched.error_code or "security_findings_record_failed")
        except PermissionError as exc:
            result.update(success=False, persistence_error=str(exc))
        except Exception:
            result.update(success=False, persistence_error="security_findings_record_failed")
    elif not app_id and resolved_findings:
        result.update(success=False, persistence_error="security_findings_app_id_missing")
    result["persisted"] = persisted

    inspected = _context_get(context_variables, "security_readiness_summary", {})
    checked_file_count = inspected.get("checked_file_count", 0) if isinstance(inspected, dict) else 0
    summary = {
        "status": "attention_required" if resolved_findings else "passed" if checked_file_count else "not_assessed",
        "mode": str(
            _context_get(context_variables, "security_readiness_mode", "advisory") or "advisory"
        ),
        "persisted": persisted,
        "finding_count": len(resolved_findings),
        "checked_file_count": checked_file_count,
        "findings": resolved_findings,
    }
    if result.get("persistence_error"):
        summary["persistence_error"] = result["persistence_error"]
    _context_set(context_variables, "security_readiness_summary", summary)
    _context_set(context_variables, "security_readiness_recorded", True)
    return result
