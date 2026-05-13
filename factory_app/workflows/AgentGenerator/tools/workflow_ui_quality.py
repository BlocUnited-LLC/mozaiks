"""Workflow-local UI quality persistence and gate for AgentGenerator.

UIFileGenerator emits workflow-local Python + React files as structured output.
This module persists that output into AG2 context, audits the workflow-local
React surfaces deterministically, and converts warnings into routing state so
noisy workflow UI can be revised before bundle assembly/download.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Any, Dict, Iterable, List, Optional

from autogen.tools.dependency_injection import Field

_COPY_FLAGS = (
    "placeholder",
    "lorem",
    "coming soon",
    "todo",
    "tbd",
    "posture",
    "handoff",
    "control room",
    "kpi wall",
    "dashboard",
)
_FONT_FLAGS = ("rajdhani", "orbitron", "fagrak")
_LEGACY_COLOR_PATTERNS = (
    re.compile(r"\bbg-(gray|slate|zinc|neutral|stone|white|black)-"),
    re.compile(r"\btext-(gray|slate|zinc|neutral|stone|white|black)-"),
    re.compile(r"\bborder-(gray|slate|zinc|neutral|stone|white|black)-"),
)
_HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB_COLOR_RE = re.compile(r"\brgba?\(")
_PUBLIC_IMPORT_RE = re.compile(
    r"import\s*\{(?P<specifiers>[^}]+)\}\s*from\s*['\"]@mozaiks/chat-ui/ui['\"]"
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


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_warnings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        normalized: List[str] = []
        for item in value:
            text = item.strip() if isinstance(item, str) else str(item).strip()
            if text:
                normalized.append(text)
        return normalized
    text = str(value).strip()
    return [text] if text else []


def _normalize_code_files(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("UIToolsFilesOutput.tools must be a list")

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"UIToolsFilesOutput.tools[{index}] must be an object")
        filename = str(item.get("filename") or "").strip()
        content = item.get("content")
        if not filename:
            raise ValueError(f"UIToolsFilesOutput.tools[{index}].filename is required")
        if not isinstance(content, str):
            raise ValueError(f"UIToolsFilesOutput.tools[{index}].content must be a string")
        install_requirements = item.get("installRequirements")
        if install_requirements is None:
            install_requirements = []
        if not isinstance(install_requirements, list):
            raise ValueError(
                f"UIToolsFilesOutput.tools[{index}].installRequirements must be a list"
            )
        normalized.append(
            {
                "filename": filename,
                "content": content,
                "installRequirements": list(install_requirements),
            }
        )
    return normalized


def _expected_workflow_ui_targets(context_variables: Optional[Any]) -> Dict[str, Dict[str, str]]:
    tool_planning = _context_get(context_variables, "ToolPlanning", {})
    if not isinstance(tool_planning, dict):
        return {}
    ui_requirements = tool_planning.get("ui_requirements")
    if not isinstance(ui_requirements, list):
        return {}

    targets: Dict[str, Dict[str, str]] = {}
    for requirement in ui_requirements:
        if not isinstance(requirement, dict):
            continue
        component = str(requirement.get("component") or "").strip()
        realization = str(requirement.get("realization") or "").strip()
        primitive = str(requirement.get("workflow_primitive") or "").strip()
        if not component or primitive == "composer_reply":
            continue
        targets[component] = {
            "realization": realization,
            "workflow_primitive": primitive,
        }
    return targets


def _parse_public_imports(content: str) -> List[str]:
    imported: List[str] = []
    for match in _PUBLIC_IMPORT_RE.finditer(content):
        for raw in match.group("specifiers").split(","):
            token = raw.strip()
            if not token:
                continue
            if " as " in token:
                token = token.split(" as ", 1)[0].strip()
            imported.append(token)
    return imported


def _dedupe(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _audit_workflow_ui_files(
    code_files: List[Dict[str, Any]],
    *,
    context_variables: Optional[Any],
) -> List[str]:
    warnings: List[str] = []
    expected_targets = _expected_workflow_ui_targets(context_variables)

    ui_files = [
        item
        for item in code_files
        if str(item.get("filename") or "").startswith("ui/")
        and PurePosixPath(str(item.get("filename"))).suffix.lower() in {".jsx", ".js"}
        and str(item.get("filename")) != "ui/index.js"
    ]

    emitted_components = {
        PurePosixPath(str(item["filename"])).stem: str(item["filename"])
        for item in ui_files
    }

    for component_name, target in expected_targets.items():
        realization = target.get("realization")
        workflow_primitive = target.get("workflow_primitive")
        if realization in {"workflow_wrapper", "generated_component"} and component_name not in emitted_components:
            warnings.append(
                f"UIFileGenerator did not emit workflow-local React for {component_name} "
                f"({workflow_primitive}, realization={realization})."
            )
        if realization == "shipped_component" and component_name in emitted_components:
            warnings.append(
                f"UIFileGenerator emitted workflow-local React for shipped shared component "
                f"{component_name} ({workflow_primitive})."
            )

    for item in ui_files:
        filename = str(item["filename"])
        content = str(item["content"])
        component_name = PurePosixPath(filename).stem
        suffix = PurePosixPath(filename).suffix.lower()
        lower = content.lower()

        if suffix != ".jsx":
            warnings.append(
                f"{filename} uses {suffix or 'no extension'}; generated workflow-local React must use .jsx."
            )

        for deep_import in (
            "@mozaiks/chat-ui/ui/primitives/",
            "chat-ui/src/",
            "../../ui/",
            "../ui/",
        ):
            if deep_import in content:
                warnings.append(
                    f"{filename} uses brittle deep UI imports ({deep_import}); use the public @mozaiks/chat-ui/ui entrypoint."
                )

        imported = set(_parse_public_imports(content))
        discouraged_imports = sorted(imported & {"Card", "Stat", "Badge"})
        if discouraged_imports:
            warnings.append(
                f"{filename} imports discouraged runtime primitives: {', '.join(discouraged_imports)}."
            )

        for discouraged_tag in ("Card", "Stat", "Badge"):
            if re.search(rf"<{discouraged_tag}\b", content):
                warnings.append(
                    f"{filename} renders discouraged runtime primitive <{discouraged_tag}>."
                )

        if "fontFamily" in content or "font-family" in lower:
            warnings.append(
                f"{filename} hardcodes font-family styling; use semantic theme tokens instead."
            )

        literal_fonts = [font for font in _FONT_FLAGS if font in lower]
        if literal_fonts:
            warnings.append(
                f"{filename} references literal brand fonts ({', '.join(sorted(set(literal_fonts)))}); use semantic theme tokens instead."
            )

        if _HEX_COLOR_RE.search(content) or _RGB_COLOR_RE.search(content):
            warnings.append(
                f"{filename} hardcodes color values; use semantic theme tokens instead."
            )

        for pattern in _LEGACY_COLOR_PATTERNS:
            if pattern.search(content):
                warnings.append(
                    f"{filename} uses legacy color utility classes; use semantic theme tokens instead."
                )
                break

        matched_copy_flags = [flag for flag in _COPY_FLAGS if flag in lower]
        if matched_copy_flags:
            warnings.append(
                f"{filename} contains placeholder/internal copy ({', '.join(sorted(set(matched_copy_flags)))})."
            )

        status_pill_count = len(re.findall(r"<StatusPill\b", content))
        if status_pill_count > 2:
            warnings.append(
                f"{filename} renders {status_pill_count} StatusPill components; compact workflow UI should avoid repeated status chips."
            )

        container_count = len(re.findall(r"<(Panel|SurfaceCard)\b", content))
        if container_count > 2:
            warnings.append(
                f"{filename} renders {container_count} primary wrapper surfaces; workflow UI should keep one focused working area."
            )

        metric_count = len(re.findall(r"<Metric\b", content))
        if metric_count > 3:
            warnings.append(
                f"{filename} renders {metric_count} Metric components; avoid KPI-strip workflow UI."
            )

        summary_strip_count = len(re.findall(r"<SummaryStrip\b", content))
        if summary_strip_count > 1:
            warnings.append(
                f"{filename} renders multiple SummaryStrip components; keep workflow UI compact."
            )

        if component_name.endswith("Dashboard"):
            warnings.append(
                f"{filename} uses dashboard-style naming ({component_name}); workflow UI should describe the actual task surface."
            )

    return _dedupe(warnings)


def save_workflow_ui_files_output(
    tools: Annotated[
        List[Dict[str, Any]],
        Field(description="UIToolsFilesOutput.tools emitted by UIFileGenerator."),
    ],
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    """Persist UIFileGenerator output and compute deterministic workflow UI warnings."""

    normalized_tools = _normalize_code_files(tools)
    warnings = _audit_workflow_ui_files(
        normalized_tools,
        context_variables=context_variables,
    )

    persisted = {"tools": normalized_tools}
    _context_set(context_variables, "workflow_ui_files_output", persisted)
    _context_set(context_variables, "workflow_ui_quality_warnings", warnings)
    _context_set(context_variables, "workflow_ui_quality_status", "pending")
    _context_set(context_variables, "workflow_ui_quality_result", None)
    _context_set(context_variables, "workflow_ui_quality_revision_request", None)

    return {
        "saved": True,
        "ui_file_count": len(normalized_tools),
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def review_workflow_ui_quality(
    max_revision_attempts: Annotated[
        int,
        Field(
            description=(
                "Maximum number of UIFileGenerator revision loops allowed before "
                "blocking for user/operator review."
            )
        ),
    ] = 2,
    context_variables: Annotated[
        Optional[Any],
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> Dict[str, Any]:
    """Convert workflow UI audit warnings into deterministic handoff state."""

    warnings = _normalize_warnings(
        _context_get(context_variables, "workflow_ui_quality_warnings", [])
    )
    prior_attempts = _as_int(
        _context_get(context_variables, "workflow_ui_quality_revision_count", 0), 0
    )
    max_attempts = max(0, _as_int(max_revision_attempts, 2))

    if not warnings:
        status = "passed"
        revision_count = prior_attempts
        revision_request = None
    elif prior_attempts < max_attempts:
        status = "needs_revision"
        revision_count = prior_attempts + 1
        revision_request = (
            "Revise the workflow-local UI before continuing. Remove or simplify these workflow UI "
            "quality issues, then emit the full UIToolsFilesOutput again:\n- "
            + "\n- ".join(warnings)
        )
    else:
        status = "blocked"
        revision_count = prior_attempts
        revision_request = (
            "Workflow UI quality warnings remain after the allowed automated revision attempts. "
            "User/operator review is required before bundle delivery:\n- "
            + "\n- ".join(warnings)
        )

    result = {
        "status": status,
        "warnings": warnings,
        "revision_count": revision_count,
        "max_revision_attempts": max_attempts,
        "revision_request": revision_request,
    }

    _context_set(context_variables, "workflow_ui_quality_status", status)
    _context_set(context_variables, "workflow_ui_quality_warnings", warnings)
    _context_set(
        context_variables, "workflow_ui_quality_revision_count", revision_count
    )
    _context_set(
        context_variables, "workflow_ui_quality_revision_request", revision_request
    )
    _context_set(context_variables, "workflow_ui_quality_result", result)

    return result


__all__ = [
    "review_workflow_ui_quality",
    "save_workflow_ui_files_output",
]
