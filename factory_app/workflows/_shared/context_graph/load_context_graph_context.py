from __future__ import annotations

from typing import Any

from factory_app.refinement_harness.tools._context_graph import load_context_graph_for_tool
from factory_app.workflows._shared.context_graph.prompt_pack import (
    build_context_graph_prompt_pack,
    build_context_graph_unavailable_pack,
)
from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows.AppGenerator.tools.hydrate_app_revision_context import (
    revision_baseline_required,
    revision_source_artifact_id,
)
from mozaiksai.control_plane.app_context import (
    get_current_app_context_graph,
    get_current_app_context_summary,
    get_current_app_intelligence_snapshot,
    get_current_source_context_bundle,
)
from mozaiksai.control_plane.context_graph import build_context_graph_catalog
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.control_plane.implementations.dependency_graph_agent import DependencyGraphAgent
from mozaiksai.core.app_context import build_app_intelligence_catalog, build_source_corpus_catalog
from mozaiksai.core.usage.context import AuxiliaryUsageContext


async def load_context_graph_context(context_variables: Any = None) -> dict[str, Any]:
    """Load a compact Context Graph pack into workflow context before chat starts."""
    data = _context_data(context_variables)
    binding = require_build_binding(context_variables)
    app_id = binding.target_app_id

    request_text = _request_text(data)
    artifact_version_id = revision_source_artifact_id(data)
    _set_context_value(context_variables, "app_context_summary", None)
    _set_context_value(context_variables, "current_app_context_version_id", None)
    _set_context_value(context_variables, "app_context_required", _requires_current_app_context(data))
    _set_app_intelligence_unavailable(context_variables, reason="source_not_loaded", warnings=[])
    _set_graph_unavailable(context_variables, reason="source_not_loaded", warnings=[])

    if not artifact_version_id and (
        data.get("artifact_version_id") or data.get("revision_source_artifact_version_id")
        or revision_baseline_required(data)
    ):
        return _set_graph_unavailable(context_variables, reason="no_revision_baseline", warnings=[])

    if artifact_version_id:
        pinned = bool(data.get("revision_source_artifact_version_id"))
        tool_context = ControlPlaneToolContext(
            checkpoint="dependency_graph_requested",
            app_id=_text(data.get("app_id")),
            target_app_id=app_id,
            user_id=_text(data.get("user_id")),
            build_family="app_bundle" if pinned else _text(data.get("artifact_kind")),
            build_key="app_bundle" if pinned else _text(data.get("artifact_key")),
            build_record_id=artifact_version_id,
            requested_workflow_id=_text(data.get("workflow_name")),
            source_surface=_text(data.get("screen")),
            raw_user_request=request_text,
        )
        loaded = await load_context_graph_for_tool(context=tool_context, require_verified=True)
        if not loaded.get("present"):
            return _set_graph_unavailable(
                context_variables,
                reason=loaded.get("reason") or "artifact_source_unavailable",
                warnings=list(loaded.get("warnings") or []),
            )
        artifact = loaded["artifact"]
        commit = artifact.commit_metadata
        metadata = commit.metadata
        if (
            binding.phase != "refinement" or not tool_context.user_id
            or commit.author_user_id != tool_context.user_id
            or metadata.get("target_app_id") != binding.target_app_id
            or metadata.get("build_registry_id") != binding.build_registry_id
            or artifact.lifecycle_status in {"archived", "deleted"}
        ):
            return _set_graph_unavailable(context_variables, reason="artifact_owner_mismatch", warnings=[])
        _set_artifact_workspace_intelligence_context(context_variables, loaded)
        catalog = build_context_graph_catalog(
            graph=loaded["graph"], raw_user_request=request_text, file_map=loaded["file_map"],
        )
        tool_context = tool_context.model_copy(update={"build_family": artifact.build_family, "build_key": artifact.build_key})
        health = loaded["workspace"].get("scan_health") or {}
        catalog.update({
            "source": loaded["workspace"]["source"],
            **loaded["provenance"],
            "warnings": list(loaded.get("warnings") or []),
            "scan_health": health,
        })
        pack = build_context_graph_prompt_pack(
            catalog=catalog, source=loaded["workspace"]["source"],
            warnings=list(loaded.get("warnings") or []),
        )
        if (
            request_text and tool_context.app_id and tool_context.user_id
            and data.get("workflow_name") in {"AppGenerator", "AgentGenerator"}
        ):
            assessment = await DependencyGraphAgent().analyze(
                context=tool_context,
                usage_context=AuxiliaryUsageContext(
                    app_id=tool_context.app_id, user_id=tool_context.user_id,
                    chat_id=_text(data.get("chat_id")),
                    workflow_name=_text(data.get("workflow_name")),
                    run_build_binding=binding,
                ),
                run_build_binding=binding,
            )
            if assessment is not None:
                advisory = assessment.model_dump(mode="json")
                identity = ("app_id", "build_record_id", "build_family", "build_key", "bundle_digest", "graph_hash")
                if all(advisory.get(key) == pack.get(key) for key in identity):
                    pack["dependency_graph"] = advisory
        _set_graph_loaded(context_variables, catalog=catalog, pack=pack, health=health)
        return {"present": True, "source": "artifact_workspace", "graph_id": pack.get("graph_id"), "build_record_id": artifact.id}

    current_summary = await get_current_app_context_summary(app_id=app_id)
    if current_summary.app_id not in {None, app_id}:
        return _set_graph_unavailable(context_variables, reason="current_context_target_mismatch", warnings=[])
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

    lookup = await get_current_app_context_graph(app_id=app_id)
    if lookup.graph is not None and lookup.graph.app_id != app_id:
        _set_app_intelligence_unavailable(context_variables, reason="current_context_target_mismatch", warnings=[])
        return _set_graph_unavailable(context_variables, reason="current_context_target_mismatch", warnings=[])
    if lookup.graph is None:
        return _set_graph_unavailable(
            context_variables, reason="current_app_context_graph_unavailable",
            warnings=list(lookup.warnings), source="current_app_context_graph",
        )

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
    _set_graph_loaded(context_variables, catalog=catalog, pack=pack, health=health)
    return {"present": True, "source": "current_app_context_graph", "graph_id": pack.get("graph_id")}


def _set_graph_loaded(context_variables: Any, *, catalog: dict[str, Any], pack: dict[str, Any], health: dict[str, Any]) -> None:
    for key, value in {
        "context_graph_pack": pack, "context_graph_catalog": catalog,
        "context_graph_status": "loaded", "context_graph_reason": None,
        "context_graph_warnings": catalog.get("warnings", []), "context_graph_health": health,
    }.items():
        _set_context_value(context_variables, key, value)


def _set_graph_unavailable(context_variables: Any, *, reason: str, warnings: list[str], source: str = "artifact_workspace") -> dict[str, Any]:
    pack = build_context_graph_unavailable_pack(reason=reason, warnings=warnings, source=source)
    for key, value in {
        "context_graph_pack": pack, "context_graph_catalog": None,
        "context_graph_status": "unavailable", "context_graph_reason": reason,
        "context_graph_warnings": warnings,
        "context_graph_health": {"source": source, "status": "unavailable", "reason": reason, "warnings": warnings},
    }.items():
        _set_context_value(context_variables, key, value)
    return {"present": False, "reason": reason, "warnings": warnings}


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
    if (
        intelligence.snapshot is not None and intelligence.snapshot.app_id != app_id
        or source.bundle is not None and source.bundle.app_id != app_id
    ):
        _set_app_intelligence_unavailable(context_variables, reason="current_context_target_mismatch", warnings=[])
        return
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
    if hasattr(context_variables, "set"):
        context_variables.set(key, value)
        return
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
