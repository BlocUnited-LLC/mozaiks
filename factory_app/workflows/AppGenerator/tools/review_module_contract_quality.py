"""
Module contract quality gate tool for AppGenerator.

Called automatically after ModuleContractQualityAgent speaks.

Audits code_files accumulated in context for module contract correctness and sets
``module_contract_quality_status`` for AG2 handoff routing.

Status values:
  passed  — no critical issues; ModelAgent may proceed.
  blocked — critical issues found (bad schema_version, missing handler, missing id);
            user/operator review required before code generation continues.

Non-critical warnings (bad handler prefix, invalid type string, missing
handler_method on one action) are surfaced in warnings and logged but do not
block the pipeline.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

from autogen.tools.dependency_injection import Field

from factory_app.workflows.AppGenerator.tools.audit_module_contracts import (
    audit_admin_panel_page_refs,
    audit_module_contracts,
)

logger = logging.getLogger(__name__)

# Keywords that indicate a warning should block the pipeline entirely
_BLOCKING_KEYWORDS = (
    "schema_version",
    "missing 'handler'",
    "missing or empty 'id'",
    "could not parse as YAML",
    "unresolved admin page ref",
)


def _context_get(context_variables: Optional[Any], key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            value = context_variables.get(key)
            return default if value is None else value
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _context_set(context_variables: Optional[Any], key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        try:
            context_variables.set(key, value)
            return
        except Exception:
            pass
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value


def _normalize_warnings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            text = item.strip() if isinstance(item, str) else str(item).strip()
            if text:
                out.append(text)
        return out
    return [str(value).strip()] if str(value).strip() else []


def _dedupe(warnings: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def review_module_contract_quality(
    max_revision_attempts: Annotated[
        int,
        Field(
            description=(
                "Maximum automated revision loops before blocking. "
                "Reserved for future loop-back support; currently unused "
                "because ConfigMiddlewareAgent runs in task_run_mode and "
                "cannot be revisited from the main workflow."
            )
        ),
    ] = 1,
    agent_message: Annotated[
        str,
        Field(description="Concise message from ModuleContractQualityAgent (ignored by the tool)."),
    ] = "",
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    """Audit module contract files in context and set module_contract_quality_status.

    Returns a deterministic status payload:
      - passed: no blocking issues; ModelAgent may proceed.
      - blocked: critical contract issues require user/operator review.
    """

    # Merge prior warnings (from save-side hooks) with fresh audit output
    prior_warnings = _normalize_warnings(
        _context_get(context_variables, "module_contract_quality_warnings", [])
    )

    code_files = _context_get(context_variables, "code_files", []) or []
    if not isinstance(code_files, list):
        code_files = []

    audit_warnings = audit_module_contracts(code_files)

    admin_registry = _context_get(context_variables, "admin_registry") or {}
    registry_pages = admin_registry.get("pages") or [] if isinstance(admin_registry, dict) else []
    valid_page_ids = {
        str(p["id"])
        for p in registry_pages
        if isinstance(p, dict) and p.get("id") and p.get("enabled", True)
    }
    page_ref_warnings = audit_admin_panel_page_refs(code_files, valid_page_ids)

    all_warnings = _dedupe(prior_warnings + audit_warnings + page_ref_warnings)

    module_contract_count = sum(
        1
        for f in code_files
        if isinstance(f, dict) and str(f.get("filename") or "").endswith("module.yaml")
    )

    # Determine status based on whether any warning is blocking
    blocking = [w for w in all_warnings if any(kw in w for kw in _BLOCKING_KEYWORDS)]

    if not blocking:
        status = "passed"
        revision_request = None
        if all_warnings:
            logger.warning(
                "[ModuleContractQualityAgent] Non-critical warnings (not blocking): %s",
                all_warnings,
            )
    else:
        status = "blocked"
        revision_request = (
            "Module contract quality gate blocked. Critical issues found in "
            f"{module_contract_count} module contract(s):\n- "
            + "\n- ".join(all_warnings)
        )

    result: Dict[str, Any] = {
        "status": status,
        "warnings": all_warnings,
        "module_contract_count": module_contract_count,
        "revision_request": revision_request,
    }

    _context_set(context_variables, "module_contract_quality_status", status)
    _context_set(context_variables, "module_contract_quality_warnings", all_warnings)
    _context_set(context_variables, "module_contract_quality_result", result)

    return result


__all__ = ["review_module_contract_quality"]
