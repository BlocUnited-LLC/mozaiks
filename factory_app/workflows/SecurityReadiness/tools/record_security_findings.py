from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.module_tools import dispatch_workflow_module_action

from .build_artifact import (
    context_get as _context_get,
)
from .build_artifact import (
    context_set as _context_set,
)
from .build_artifact import (
    resolve_security_artifact,
    source_exception,
    source_failure,
)


async def record_security_findings(
    findings: list[dict[str, Any]] | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    """Record findings through the live run's permissioned module dispatch."""

    _context_set(context_variables, "security_readiness_recorded", False)
    inspected = _context_get(context_variables, "security_readiness_summary", {})
    if inspected.get("source_error"):
        return dict(inspected)
    try:
        binding, artifact = await resolve_security_artifact(context_variables)
    except Exception as exc:
        return source_exception(context_variables, exc)
    if not inspected.get("success") or not inspected.get("checked_file_count"):
        return source_failure(context_variables, "security_source_not_inspected")
    if inspected.get("artifact_version_id") != artifact.id or any(
        inspected.get(key) != value for key, value in binding.model_dump().items()
    ):
        return source_failure(context_variables, "security_source_assessment_stale")
    resolved_findings = _context_get(context_variables, "security_readiness_findings", [])
    if findings is not None and detach(findings) != resolved_findings:
        return source_failure(context_variables, "security_source_findings_mismatch")

    # The module's persistence plane belongs to the host; verified lineage
    # identifies the generated target within that plane.
    app_id = active_workflow_tool_run()[1]
    persisted = False
    result: dict[str, Any] = {"success": True, "persisted": False, "findings": resolved_findings}
    if resolved_findings:
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
        params.update({
            "build_id": binding.build_id, "build_registry_id": binding.build_registry_id,
            "artifact_version_id": artifact.id,
        })
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
    result["persisted"] = persisted

    checked_file_count = inspected["checked_file_count"]
    summary = {
        **inspected,
        "success": result["success"],
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
    _context_set(context_variables, "security_readiness_recorded", result["success"])
    return result
