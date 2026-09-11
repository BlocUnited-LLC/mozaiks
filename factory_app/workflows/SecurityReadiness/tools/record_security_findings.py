from __future__ import annotations

from typing import Any

from factory_app.app.modules.security_readiness.backend.service import SecurityReadinessService


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key, default)
        except TypeError:
            try:
                return context_variables.get(key)
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
    module_context: Any | None = None,
) -> dict[str, Any]:
    """Record SecurityReadiness findings when module persistence is available."""

    resolved_findings = _normalize_findings(
        findings
        if findings is not None
        else _context_get(context_variables, "security_readiness_findings", [])
    )
    app_id = str(_context_get(context_variables, "app_id", "") or "").strip()
    build_id = str(_context_get(context_variables, "build_id", "") or "").strip() or None
    artifact_version_id = (
        str(_context_get(context_variables, "artifact_version_id", "") or "").strip() or None
    )

    persisted = False
    result: dict[str, Any] = {"success": True, "persisted": False, "findings": resolved_findings}
    if module_context is not None and app_id and resolved_findings:
        service_result = await SecurityReadinessService().record_assessment(
            module_context,
            app_id=app_id,
            build_id=build_id,
            artifact_version_id=artifact_version_id,
            source="validation",
            findings=resolved_findings,
        )
        result.update(service_result)
        persisted = True
    result["persisted"] = persisted

    summary = {
        "status": "passed" if not resolved_findings else "attention_required",
        "mode": str(
            _context_get(context_variables, "security_readiness_mode", "advisory") or "advisory"
        ),
        "persisted": persisted,
        "finding_count": len(resolved_findings),
        "findings": resolved_findings,
    }
    _context_set(context_variables, "security_readiness_summary", summary)
    _context_set(context_variables, "security_readiness_recorded", True)
    return result
