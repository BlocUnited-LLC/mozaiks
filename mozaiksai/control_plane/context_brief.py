from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .contracts import (
    ControlPlaneToolContext,
    RefinementContextBrief,
    RefinementContextToolBrief,
)

_PATH_KEYS = (
    "path",
    "source_path",
    "file_path",
)


def build_refinement_context_brief(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None,
    tool_results: dict[str, Any] | None,
) -> RefinementContextBrief:
    """Build a UI-safe summary of refinement context loaded for a checkpoint."""

    tool_context = (
        context
        if isinstance(context, ControlPlaneToolContext)
        else ControlPlaneToolContext.model_validate(dict(context or {}))
    )
    results = dict(tool_results or {})
    tool_briefs = [
        _tool_brief(tool_id=tool_id, output=output)
        for tool_id, output in results.items()
        if tool_id != "refinement_context_brief"
    ]

    app_context_summary = _app_context_summary(tool_context)
    graph_briefs = [brief for brief in tool_briefs if brief.graph_id or brief.node_count or brief.edge_count]
    primary_graph = graph_briefs[0] if graph_briefs else None
    warnings = _dedupe(
        [
            warning
            for output in results.values()
            for warning in _warnings_from_output(output)
        ]
    )

    return RefinementContextBrief(
        checkpoint=tool_context.checkpoint,
        app_id=tool_context.app_id,
        artifact_kind=tool_context.artifact_kind,
        artifact_key=tool_context.artifact_key,
        artifact_version_id=tool_context.artifact_version_id,
        requested_workflow_id=tool_context.requested_workflow_id,
        source_surface=tool_context.source_surface,
        request_preview=_preview(tool_context.raw_user_request),
        current_app_context_version_id=_first_text(
            tool_context.extra.get("current_app_context_version_id"),
            tool_context.extra.get("current_context_version_id"),
            app_context_summary.get("context_version_id"),
        ),
        app_context_available=_optional_bool(app_context_summary.get("available")),
        app_context_stale_status=_first_text(
            tool_context.extra.get("app_context_stale_status"),
            app_context_summary.get("stale_status"),
        ),
        app_intelligence_available=_tool_available(results.get("get_app_intelligence_context")),
        source_context_available=(
            _tool_available(results.get("search_app_source_context"))
            or _tool_available(results.get("read_app_source_file"))
            or _tool_available(results.get("get_related_app_source_files"))
        ),
        graph_available=primary_graph is not None,
        graph_id=primary_graph.graph_id if primary_graph is not None else None,
        graph_hash=primary_graph.graph_hash if primary_graph is not None else None,
        graph_stale_status=primary_graph.stale_status if primary_graph is not None else None,
        graph_node_count=primary_graph.node_count if primary_graph is not None else None,
        graph_edge_count=primary_graph.edge_count if primary_graph is not None else None,
        candidate_paths=_dedupe(
            [
                path
                for brief in tool_briefs
                for path in brief.candidate_paths
            ]
        )[:24],
        selected_paths=_dedupe(
            [
                path
                for path in [
                    *_safe_path_list(tool_context.extra.get("selected_file_paths")),
                    *[
                        selected
                        for brief in tool_briefs
                        for selected in brief.selected_paths
                    ],
                ]
            ]
        )[:24],
        related_paths=_dedupe(
            [
                path
                for brief in tool_briefs
                for path in brief.related_paths
            ]
        )[:24],
        readable_paths=_dedupe(
            [
                path
                for brief in tool_briefs
                for path in brief.readable_paths
            ]
        )[:24],
        warnings=warnings[:24],
        tool_briefs=tool_briefs,
    )


def _tool_brief(*, tool_id: str, output: Any) -> RefinementContextToolBrief:
    data = output if isinstance(output, dict) else {}
    context_graph = data.get("context_graph")
    graph_id = _first_text(
        data.get("graph_id"),
        context_graph.get("graph_id") if isinstance(context_graph, dict) else None,
    )
    candidate_paths = _candidate_paths(data)
    selected_paths = _safe_path_list(data.get("selected_file_paths"))
    related_paths = _safe_path_list(data.get("related_file_paths"))
    readable_paths = _readable_paths(data)
    return RefinementContextToolBrief(
        tool_id=tool_id,
        present=_optional_bool(data.get("present")),
        source=_first_text(data.get("source")),
        reason=_first_text(data.get("reason"), data.get("error")),
        graph_id=graph_id,
        graph_hash=_first_text(data.get("graph_hash")),
        stale_status=_first_text(data.get("stale_status")),
        node_count=_optional_int(data.get("node_count")),
        edge_count=_optional_int(data.get("edge_count")),
        file_count=_optional_int(data.get("file_count")),
        result_count=_result_count(data),
        candidate_paths=candidate_paths[:16],
        selected_paths=selected_paths[:16],
        related_paths=related_paths[:16],
        readable_paths=readable_paths[:16],
        warning_count=len(_warnings_from_output(data)),
    )


def _app_context_summary(context: ControlPlaneToolContext) -> dict[str, Any]:
    raw = context.extra.get("app_context_summary")
    return raw if isinstance(raw, dict) else {}


def _tool_available(output: Any) -> bool:
    if not isinstance(output, dict):
        return False
    present = output.get("present")
    if isinstance(present, bool):
        return present
    return bool(output)


def _result_count(data: dict[str, Any]) -> int | None:
    for key in ("results", "matches", "candidate_files"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _candidate_paths(data: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("candidate_files", "matches", "file_tree"):
        paths.extend(_paths_from_container(data.get(key)))
    return _dedupe(paths)


def _readable_paths(data: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    paths.extend(_paths_from_container(data.get("results")))
    paths.extend(_paths_from_container(data.get("files")))
    if data.get("present") and _first_text(data.get("path")):
        paths.append(str(data["path"]))
    return _dedupe(paths)


def _paths_from_container(value: Any) -> list[str]:
    if isinstance(value, str):
        safe = _safe_path(value)
        return [safe] if safe else []
    if isinstance(value, dict):
        direct = _first_text(*(value.get(key) for key in _PATH_KEYS))
        paths = [direct] if direct else []
        for nested_key in ("file", "metadata", "source", "preview"):
            nested = value.get(nested_key)
            if isinstance(nested, (dict, list)):
                paths.extend(_paths_from_container(nested))
        return [path for path in (_safe_path(path) for path in paths) if path]
    if isinstance(value, Iterable):
        iterable_paths: list[str] = []
        for item in value:
            iterable_paths.extend(_paths_from_container(item))
        return iterable_paths
    return []


def _safe_path_list(value: Any) -> list[str]:
    return _dedupe(_paths_from_container(value))


def _warnings_from_output(output: Any) -> list[str]:
    if not isinstance(output, dict):
        return []
    warnings = output.get("warnings")
    if isinstance(warnings, list):
        return [str(item).strip() for item in warnings if str(item).strip()]
    return []


def _safe_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.replace("\\", "/").strip().strip("/")
    if not raw or raw.startswith("~") or ":" in raw:
        return None
    parts = [part for part in raw.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _preview(value: Any, *, max_chars: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _dedupe(values: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = ["build_refinement_context_brief"]
