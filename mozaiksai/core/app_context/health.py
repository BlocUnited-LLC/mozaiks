"""Health evaluation for AppContext source and graph coverage."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContextGraphHealthReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "warning", "blocked"] = "healthy"
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)


_CORE_PRIORITY_BUCKETS = {
    "app_modules",
    "app_services",
    "app_ui",
    "app_config",
    "workflows",
    "control_plane",
    "build_context",
    "build_context_contracts",
    "build_context_packs",
    "src",
}


def evaluate_context_graph_health(scan_health: dict[str, Any] | None) -> ContextGraphHealthReport:
    health = scan_health or {}
    selected_count = _int(health.get("selected_file_count"))
    candidate_count = _int(health.get("candidate_file_count"))
    selected_by_priority = _dict_ints(health.get("selected_by_priority"))
    selected_by_extension = _dict_ints(health.get("selected_by_extension"))
    skipped = _dict_ints(health.get("skipped"))
    limit_reached = bool(health.get("limit_reached"))
    core_count = sum(selected_by_priority.get(bucket, 0) for bucket in _CORE_PRIORITY_BUCKETS)

    blockers: list[str] = []
    warnings: list[str] = []
    if selected_count <= 0:
        blockers.append("context_graph_no_indexed_files")
    if limit_reached and core_count <= 0:
        blockers.append("context_graph_limit_reached_before_core_code")
    if limit_reached:
        warnings.append("context_graph_file_or_char_limit_reached")
    if skipped.get("sensitive_path", 0) > 0:
        warnings.append("context_graph_sensitive_paths_skipped")
    if selected_count > 0 and core_count <= 0:
        warnings.append("context_graph_no_mozaiks_core_surfaces_indexed")

    parser_status = health.get("parser_status") if isinstance(health.get("parser_status"), dict) else {}
    parser_warning = _parser_fallback_warning(
        parser_status=parser_status,  # type: ignore[arg-type]
        selected_by_extension=selected_by_extension,
    )
    if parser_warning:
        warnings.append(parser_warning)

    status: Literal["healthy", "warning", "blocked"] = "healthy"
    if blockers:
        status = "blocked"
    elif warnings:
        status = "warning"

    return ContextGraphHealthReport(
        status=status,
        blockers=blockers,
        warnings=warnings,
        coverage={
            "selected_file_count": selected_count,
            "candidate_file_count": candidate_count,
            "core_surface_file_count": core_count,
            "limit_reached": limit_reached,
            "sensitive_path_skipped_count": skipped.get("sensitive_path", 0),
            "selected_by_priority": selected_by_priority,
            "selected_by_extension": selected_by_extension,
        },
    )


def _parser_fallback_warning(
    *,
    parser_status: dict[str, Any],
    selected_by_extension: dict[str, int],
) -> str | None:
    js_like_count = sum(selected_by_extension.get(ext, 0) for ext in (".js", ".jsx", ".ts", ".tsx"))
    python_count = selected_by_extension.get(".py", 0)
    if js_like_count <= python_count:
        return None
    raw_languages = parser_status.get("languages")
    languages = raw_languages if isinstance(raw_languages, dict) else {}
    raw_javascript = languages.get("javascript")
    javascript = raw_javascript if isinstance(raw_javascript, dict) else {}
    if str(javascript.get("active_parser") or "") == "regex":
        return "context_graph_javascript_parser_fallback"
    return None


def _dict_ints(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, item in value.items():
        text = str(key or "").strip()
        if text:
            out[text] = _int(item)
    return out


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = ["ContextGraphHealthReport", "evaluate_context_graph_health"]
