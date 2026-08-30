from __future__ import annotations

from typing import Any

from factory_app.refinement_harness.tools._context_graph import load_context_graph_for_tool
from factory_app.workflows._shared.context_graph.prompt_pack import (
    build_context_graph_prompt_pack,
    build_context_graph_unavailable_pack,
)
from mozaiksai.control_plane.app_context import (
    get_current_app_context_graph,
    get_current_app_context_summary,
    get_current_app_intelligence_snapshot,
    get_current_source_context_bundle,
)
from mozaiksai.control_plane.context_graph import build_context_graph_catalog
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.app_context import build_app_intelligence_catalog, build_source_corpus_catalog


async def load_context_graph_context(context_variables: Any = None) -> dict[str, Any]:
    """Load a compact Context Graph pack into workflow context before chat starts."""
    data = _context_data(context_variables)
    app_id = _text(data.get("app_id"))
    if not app_id:
        _set_app_intelligence_unavailable(context_variables, reason="missing_app_id", warnings=["missing_app_id"])
        _set_context_value(
            context_variables,
            "context_graph_pack",
            build_context_graph_unavailable_pack(reason="missing_app_id", warnings=["missing_app_id"]),
        )
        _set_context_value(context_variables, "context_graph_catalog", None)
        _set_context_value(context_variables, "context_graph_status", "unavailable")
        _set_context_value(context_variables, "context_graph_reason", "missing_app_id")
        _set_context_value(context_variables, "context_graph_warnings", ["missing_app_id"])
        _set_context_value(
            context_variables,
            "context_graph_health",
            {"source": "workflow_startup", "status": "unavailable", "reason": "missing_app_id"},
        )
        return {"present": False, "reason": "missing_app_id"}

    request_text = _request_text(data)
    artifact_version_id = _text(data.get("artifact_version_id"))
    current_summary = await get_current_app_context_summary(app_id=app_id)
    _set_context_value(context_variables, "app_context_summary", current_summary.model_dump(mode="json"))
    _set_context_value(context_variables, "current_app_context_version_id", current_summary.context_version_id)
    _set_context_value(
        context_variables,
        "app_context_required",
        _requires_current_app_context(data),
    )
    await _load_current_app_intelligence_context(
        app_id=app_id,
        app_context_summary=current_summary,
        context_variables=context_variables,
    )

    if artifact_version_id:
        loaded = await load_context_graph_for_tool(
            context=ControlPlaneToolContext(
                checkpoint="workflow_startup",
                app_id=app_id,
                user_id=_text(data.get("user_id")),
                build_family=_text(data.get("artifact_kind")),
                build_key=_text(data.get("artifact_key")),
                build_record_id=artifact_version_id,
                requested_workflow_id=_text(data.get("workflow_name")),
                source_surface=_text(data.get("screen")),
                raw_user_request=request_text,
            )
        )
        if loaded.get("present"):
            _set_artifact_workspace_intelligence_context(context_variables, loaded)
            catalog = build_context_graph_catalog(
                graph=loaded["graph"],
                raw_user_request=request_text,
                file_map=loaded["file_map"],
            )
            artifact = loaded["artifact"]
            health = loaded["workspace"].get("scan_health") or {
                "source": loaded["workspace"]["source"],
                "selected_file_count": len(loaded["file_map"]),
                "node_count": catalog.get("node_count"),
                "edge_count": catalog.get("edge_count"),
                "warnings": list(loaded.get("warnings") or []),
            }
            catalog = {
                **catalog,
                "source": loaded["workspace"]["source"],
                "artifact_version_id": artifact.id,
                "artifact_kind": artifact.artifact_kind,
                "artifact_key": artifact.artifact_key,
                "warnings": list(loaded.get("warnings") or []),
                "scan_health": health,
            }
            pack = build_context_graph_prompt_pack(
                catalog=catalog,
                source=loaded["workspace"]["source"],
                warnings=list(loaded.get("warnings") or []),
            )
            _set_context_value(context_variables, "context_graph_pack", pack)
            _set_context_value(context_variables, "context_graph_catalog", catalog)
            _set_context_value(context_variables, "context_graph_status", "loaded")
            _set_context_value(context_variables, "context_graph_reason", None)
            _set_context_value(context_variables, "context_graph_warnings", list(loaded.get("warnings") or []))
            _set_context_value(context_variables, "context_graph_health", health)
            return {"present": True, "source": "artifact_workspace", "graph_id": pack.get("graph_id")}

    lookup = await get_current_app_context_graph(app_id=app_id)
    if lookup.graph is None:
        _set_context_value(
            context_variables,
            "context_graph_pack",
            build_context_graph_unavailable_pack(
                reason="current_app_context_graph_unavailable",
                warnings=list(lookup.warnings),
                source="current_app_context_graph",
            ),
        )
        _set_context_value(context_variables, "context_graph_catalog", None)
        _set_context_value(context_variables, "context_graph_status", "unavailable")
        _set_context_value(context_variables, "context_graph_reason", "current_app_context_graph_unavailable")
        _set_context_value(context_variables, "context_graph_warnings", list(lookup.warnings))
        _set_context_value(
            context_variables,
            "context_graph_health",
            {
                "source": "current_app_context_graph",
                "status": "unavailable",
                "reason": "current_app_context_graph_unavailable",
                "warnings": list(lookup.warnings),
            },
        )
        return {"present": False, "reason": "current_app_context_graph_unavailable", "warnings": list(lookup.warnings)}

    catalog = build_context_graph_catalog(
        graph=lookup.graph,
        raw_user_request=request_text,
        file_map={},
    )
    health = {
        "source": "current_app_context_graph",
        "selected_file_count": catalog.get("file_count"),
        "node_count": catalog.get("node_count"),
        "edge_count": catalog.get("edge_count"),
        "warnings": list(lookup.warnings),
    }
    catalog.update(
        {
            "source": "current_app_context_graph",
            "warnings": list(lookup.warnings),
            "scan_health": health,
        }
    )
    pack = build_context_graph_prompt_pack(
        catalog=catalog,
        source="current_app_context_graph",
        warnings=list(lookup.warnings),
    )
    _set_context_value(context_variables, "context_graph_pack", pack)
    _set_context_value(context_variables, "context_graph_catalog", catalog)
    _set_context_value(context_variables, "context_graph_status", "loaded")
    _set_context_value(context_variables, "context_graph_reason", None)
    _set_context_value(context_variables, "context_graph_warnings", list(lookup.warnings))
    _set_context_value(context_variables, "context_graph_health", health)
    return {"present": True, "source": "current_app_context_graph", "graph_id": pack.get("graph_id")}


async def _load_current_app_intelligence_context(
    *,
    app_id: str,
    app_context_summary: Any,
    context_variables: Any,
) -> None:
    intelligence = await get_current_app_intelligence_snapshot(
        app_id=app_id,
        app_context_summary=app_context_summary,
    )
    source = await get_current_source_context_bundle(
        app_id=app_id,
        app_context_summary=app_context_summary,
    )
    warnings = [*list(intelligence.warnings), *list(source.warnings)]
    if intelligence.snapshot is None:
        _set_app_intelligence_unavailable(
            context_variables,
            reason="current_app_intelligence_unavailable",
            warnings=warnings,
        )
    else:
        catalog = build_app_intelligence_catalog(intelligence.snapshot, max_items=24)
        _set_context_value(context_variables, "app_intelligence_ready", True)
        _set_context_value(context_variables, "app_intelligence_status", "loaded")
        _set_context_value(context_variables, "app_intelligence_catalog", catalog)
        _set_context_value(context_variables, "app_intelligence_summary", _app_intelligence_summary(catalog))
        _set_context_value(context_variables, "app_intelligence_health", _app_intelligence_health(catalog, warnings))
    if source.bundle is not None:
        _set_context_value(
            context_variables,
            "source_context_catalog",
            build_source_corpus_catalog(source.bundle, max_files=40, max_chunks=8),
        )


def _set_artifact_workspace_intelligence_context(context_variables: Any, loaded: dict[str, Any]) -> None:
    catalog = loaded.get("app_intelligence_catalog")
    source_catalog = loaded.get("source_context_catalog")
    warnings = list(loaded.get("warnings") or [])
    if isinstance(catalog, dict):
        _set_context_value(context_variables, "app_intelligence_ready", True)
        _set_context_value(context_variables, "app_intelligence_status", "loaded")
        _set_context_value(context_variables, "app_intelligence_catalog", catalog)
        _set_context_value(context_variables, "app_intelligence_summary", _app_intelligence_summary(catalog))
        _set_context_value(context_variables, "app_intelligence_health", _app_intelligence_health(catalog, warnings))
    if isinstance(source_catalog, dict):
        _set_context_value(context_variables, "source_context_catalog", source_catalog)


def _set_app_intelligence_unavailable(context_variables: Any, *, reason: str, warnings: list[str]) -> None:
    _set_context_value(context_variables, "app_intelligence_ready", False)
    _set_context_value(context_variables, "app_intelligence_status", reason)
    _set_context_value(context_variables, "app_intelligence_summary", "")
    _set_context_value(
        context_variables,
        "app_intelligence_health",
        {"source": "workflow_startup", "status": "unavailable", "reason": reason, "warnings": list(warnings)},
    )
    _set_context_value(context_variables, "app_intelligence_catalog", None)
    _set_context_value(context_variables, "source_context_catalog", None)


def _app_intelligence_summary(catalog: dict[str, Any]) -> str:
    _cov = catalog.get("coverage")
    coverage: dict[str, Any] = _cov if isinstance(_cov, dict) else {}
    _arch = catalog.get("architecture")
    architecture: dict[str, Any] = _arch if isinstance(_arch, dict) else {}
    file_count = int(coverage.get("file_count") or 0)
    symbol_count = int(coverage.get("symbol_count") or 0)
    edge_count = int(coverage.get("edge_count") or 0)
    module_count = len(architecture.get("module_roots") or [])
    service_count = len(architecture.get("service_roots") or [])
    return (
        "App Intelligence indexed "
        f"{file_count} files, {symbol_count} symbols, {edge_count} graph edges, "
        f"{module_count} module roots, and {service_count} service roots."
    )


def _app_intelligence_health(catalog: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    _cov2 = catalog.get("coverage")
    coverage: dict[str, Any] = _cov2 if isinstance(_cov2, dict) else {}
    return {
        "source": "app_intelligence",
        "status": "loaded",
        "file_count": coverage.get("file_count"),
        "symbol_count": coverage.get("symbol_count"),
        "import_count": coverage.get("import_count"),
        "node_count": coverage.get("node_count"),
        "edge_count": coverage.get("edge_count"),
        "scan_health": coverage.get("scan_health"),
        "warnings": list(warnings),
    }


def _requires_current_app_context(data: dict[str, Any]) -> bool:
    build_mode = _text(data.get("build_mode"))
    trigger_source = _text(data.get("trigger_source"))
    workflow_sequence = _text(data.get("workflow_sequence"))
    return (
        build_mode == "revision"
        or trigger_source == "refinement"
        or bool(workflow_sequence and workflow_sequence.endswith("_revision"))
    )


def _context_data(context_variables: Any) -> dict[str, Any]:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return context_variables
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data
    if hasattr(context_variables, "to_dict"):
        try:
            value = context_variables.to_dict()
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    return {}


def _set_context_value(context_variables: Any, key: str, value: Any) -> None:
    if context_variables is None:
        return
    try:
        if hasattr(context_variables, "set"):
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


def _request_text(data: dict[str, Any]) -> str:
    candidates = [
        data.get("refinement_request"),
        data.get("pivot_description"),
        data.get("raw_user_request"),
        data.get("concept_overview"),
    ]
    meta = data.get("refinement_request_meta")
    if isinstance(meta, dict):
        candidates.insert(0, meta.get("raw_user_request"))
    for candidate in candidates:
        text = _text(candidate)
        if text:
            return text
    return ""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
