"""Emit the App Intelligence overview after deterministic repository indexing."""

from __future__ import annotations

import logging
from typing import Any

from mozaiksai.core.workflow.ui_tools import emit_ui_surface

logger = logging.getLogger(__name__)


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                value = getter(key)
                return default if value is None else value
            except Exception:
                return default
        except Exception:
            return default
    return default


async def emit_app_intelligence_overview_card(
    context_variables: Any | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Show the full App Intelligence artifact available to discovery agents."""
    ctx = context_variables if context_variables is not None else {}
    preload_status = str(_ctx_get(ctx, "preload_status") or "none")
    catalog = _dict_value(_ctx_get(ctx, "app_intelligence_catalog"))

    if preload_status == "none" and not catalog:
        return {"skipped": True, "reason": "no_app_intelligence_data"}

    payload = _overview_payload(ctx=ctx, preload_status=preload_status, catalog=catalog)
    chat_id = _ctx_get(ctx, "chat_id")

    try:
        event_id = await emit_ui_surface(
            "AppIntelligenceOverviewCard",
            payload,
            chat_id=str(chat_id) if chat_id else None,
            workflow_name="ExistingAppDiscovery",
            agent_name="ExistingAppDiscovery",
            display="artifact",
        )
        logger.info(
            "[ExistingAppDiscovery] App Intelligence overview emitted: status=%s app=%s files=%s",
            preload_status,
            payload.get("app_name") or payload.get("repo_name") or payload.get("github_repo") or "unknown",
            (((payload.get("app_intelligence_catalog") or {}).get("coverage") or {}).get("file_count") or 0),
        )
    except Exception as exc:
        logger.warning("[ExistingAppDiscovery] App Intelligence overview emission failed: %s", exc)

    return {
        "success": True,
        "preload_status": preload_status,
        "app_intelligence_snapshot_id": payload.get("app_intelligence_snapshot_id"),
        "ui_event_id": event_id if "event_id" in locals() else None,
    }


async def emit_app_intelligence_inline_brief(
    context_variables: Any | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Show a durable compact App Intelligence brief in the chat transcript."""
    ctx = context_variables if context_variables is not None else {}
    preload_status = str(_ctx_get(ctx, "preload_status") or "none")
    catalog = _dict_value(_ctx_get(ctx, "app_intelligence_catalog"))

    if preload_status == "none" and not catalog:
        return {"skipped": True, "reason": "no_app_intelligence_data"}

    overview_payload = _overview_payload(ctx=ctx, preload_status=preload_status, catalog=catalog)
    payload = _inline_brief_payload(overview_payload)
    chat_id = _ctx_get(ctx, "chat_id")

    try:
        event_id = await emit_ui_surface(
            "AppIntelligenceInlineBrief",
            payload,
            chat_id=str(chat_id) if chat_id else None,
            workflow_name="ExistingAppDiscovery",
            agent_name="App Intelligence",
            display="inline",
        )
        logger.info(
            "[ExistingAppDiscovery] App Intelligence inline brief emitted: status=%s app=%s files=%s",
            preload_status,
            payload.get("app_name") or payload.get("repo_name") or payload.get("github_repo") or "unknown",
            ((payload.get("coverage") or {}).get("file_count") or 0),
        )
    except Exception as exc:
        logger.warning("[ExistingAppDiscovery] App Intelligence inline brief emission failed: %s", exc)

    return {
        "success": True,
        "preload_status": preload_status,
        "app_intelligence_snapshot_id": payload.get("app_intelligence_snapshot_id"),
        "ui_event_id": event_id if "event_id" in locals() else None,
    }


def _overview_payload(*, ctx: Any, preload_status: str, catalog: dict[str, Any]) -> dict[str, Any]:
    repo_summary = _dict_value(_ctx_get(ctx, "repo_summary"))
    frontend_repo_summary = _dict_value(_ctx_get(ctx, "frontend_repo_summary"))
    backend_repo_summary = _dict_value(_ctx_get(ctx, "backend_repo_summary"))
    primary = repo_summary or frontend_repo_summary or backend_repo_summary
    source_catalog = _dict_value(_ctx_get(ctx, "source_context_catalog"))
    graph_catalog = _dict_value(_ctx_get(ctx, "context_graph_catalog"))

    resolved_catalog = catalog or _fallback_catalog(
        ctx=ctx,
        source_catalog=source_catalog,
        graph_catalog=graph_catalog,
    )
    coverage = _dict_value(resolved_catalog.get("coverage"))

    return {
        "status": _overview_status(ctx=ctx, preload_status=preload_status, catalog=resolved_catalog),
        "app_name": str(_ctx_get(ctx, "app_name") or primary.get("repo_name") or "").strip(),
        "github_repo": str(_ctx_get(ctx, "github_repo") or "").strip(),
        "repo_name": str(primary.get("repo_name") or "").strip(),
        "source": str(primary.get("source") or "none"),
        "tech_stack": (
            str(_ctx_get(ctx, "tech_stack") or "")
            or str(primary.get("inferred_tech_stack") or "")
        ).strip(),
        "app_intelligence_snapshot_id": resolved_catalog.get("snapshot_id"),
        "source_context_bundle_id": resolved_catalog.get("source_context_bundle_id") or source_catalog.get("bundle_id"),
        "context_graph_id": resolved_catalog.get("graph_id") or graph_catalog.get("graph_id"),
        "current_app_context_version_id": str(
            _ctx_get(ctx, "current_app_context_version_id")
            or _ctx_get(ctx, "current_context_version_id")
            or ""
        ).strip(),
        "artifact_version_ids": {
            "app_context_version": str(_ctx_get(ctx, "app_context_version_artifact_version_id") or "").strip(),
            "source_context_bundle": str(_ctx_get(ctx, "source_context_artifact_version_id") or "").strip(),
            "app_context_graph": str(_ctx_get(ctx, "graph_artifact_version_id") or "").strip(),
            "app_intelligence_snapshot": str(_ctx_get(ctx, "app_intelligence_artifact_version_id") or "").strip(),
        },
        "app_intelligence_registration": _dict_value(_ctx_get(ctx, "app_intelligence_registration")),
        "total_files_scanned": int(
            coverage.get("file_count")
            or source_catalog.get("file_count")
            or graph_catalog.get("indexed_file_count")
            or primary.get("total_files_scanned")
            or 0
        ),
        "app_intelligence_ready": bool(_ctx_get(ctx, "app_intelligence_ready")),
        "app_intelligence_status": str(_ctx_get(ctx, "app_intelligence_status") or "").strip(),
        "app_intelligence_summary": str(_ctx_get(ctx, "app_intelligence_summary") or "").strip(),
        "app_intelligence_progress": _dict_value(_ctx_get(ctx, "app_intelligence_progress")),
        "app_intelligence_health": _dict_value(_ctx_get(ctx, "app_intelligence_health")),
        "app_intelligence_catalog": resolved_catalog,
        "warnings": _dedupe(
            [
                *_list_value(_ctx_get(ctx, "context_graph_warnings")),
                *_list_value(source_catalog.get("warnings")),
                *_list_value(resolved_catalog.get("warnings")),
            ]
        )[:12],
    }


def _inline_brief_payload(overview_payload: dict[str, Any]) -> dict[str, Any]:
    catalog = _dict_value(overview_payload.get("app_intelligence_catalog"))
    coverage = _dict_value(catalog.get("coverage"))
    architecture = _dict_value(catalog.get("architecture"))
    warnings = _dedupe(
        [
            *_list_value(overview_payload.get("warnings")),
            *_list_value(catalog.get("warnings")),
        ]
    )
    source_refs = [
        *_surface_samples(_list_value(architecture.get("module_roots")), limit=3),
        *_surface_samples(_list_value(architecture.get("service_roots")), limit=2),
        *_surface_samples(_list_value(architecture.get("ui_surfaces")), limit=3),
        *_surface_samples(_list_value(architecture.get("workflow_roots")), limit=2),
    ]
    risk_hints = _list_value(catalog.get("risk_hints"))[:4]
    capability_samples = _surface_samples(_list_value(catalog.get("capabilities")), limit=5)
    app_name = (
        str(
            overview_payload.get("app_name")
            or overview_payload.get("repo_name")
            or overview_payload.get("github_repo")
            or ""
        ).strip()
        or "Indexed app"
    )

    return {
        "status": str(overview_payload.get("status") or "partial"),
        "app_name": app_name,
        "github_repo": str(overview_payload.get("github_repo") or "").strip(),
        "repo_name": str(overview_payload.get("repo_name") or "").strip(),
        "source": str(overview_payload.get("source") or "none").strip(),
        "tech_stack": str(overview_payload.get("tech_stack") or "").strip(),
        "summary": _inline_summary(
            overview_payload=overview_payload,
            coverage=coverage,
            warning_count=len(warnings),
        ),
        "app_intelligence_snapshot_id": overview_payload.get("app_intelligence_snapshot_id"),
        "current_app_context_version_id": overview_payload.get("current_app_context_version_id"),
        "coverage": {
            "file_count": int(coverage.get("file_count") or overview_payload.get("total_files_scanned") or 0),
            "chunk_count": int(coverage.get("chunk_count") or 0),
            "symbol_count": int(coverage.get("symbol_count") or 0),
            "node_count": int(coverage.get("node_count") or 0),
            "edge_count": int(coverage.get("edge_count") or 0),
            "language_counts": _dict_value(coverage.get("language_counts")),
            "role_counts": _dict_value(coverage.get("role_counts")),
        },
        "architecture": {
            "module_count": len(_list_value(architecture.get("module_roots"))),
            "service_count": len(_list_value(architecture.get("service_roots"))),
            "ui_surface_count": len(_list_value(architecture.get("ui_surfaces"))),
            "workflow_count": len(_list_value(architecture.get("workflow_roots"))),
            "source_refs": _dedupe_surface_samples(source_refs)[:8],
        },
        "capabilities": capability_samples,
        "integration_count": len(_list_value(catalog.get("integration_surfaces"))),
        "data_surface_count": len(_list_value(catalog.get("data_surfaces"))),
        "risk_hints": risk_hints,
        "warnings": warnings[:4],
    }


def _inline_summary(
    *,
    overview_payload: dict[str, Any],
    coverage: dict[str, Any],
    warning_count: int,
) -> str:
    existing_summary = str(overview_payload.get("app_intelligence_summary") or "").strip()
    if existing_summary:
        return existing_summary

    file_count = int(coverage.get("file_count") or overview_payload.get("total_files_scanned") or 0)
    symbol_count = int(coverage.get("symbol_count") or 0)
    edge_count = int(coverage.get("edge_count") or 0)
    status = str(overview_payload.get("status") or "partial")
    warning_clause = f" with {warning_count} warning(s)" if warning_count else ""
    return (
        f"App Intelligence is {status}: indexed {file_count} file(s), "
        f"{symbol_count} symbol(s), and {edge_count} relationship(s){warning_clause}."
    )


def _surface_samples(items: list[Any], *, limit: int) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            label = item.strip()
            detail = ""
        elif isinstance(item, dict):
            label = str(
                item.get("label")
                or item.get("capability_id")
                or item.get("module_id")
                or item.get("service_id")
                or item.get("workflow_id")
                or item.get("path")
                or item.get("root")
                or ""
            ).strip()
            paths = _list_value(item.get("paths"))
            detail = str(item.get("path") or (paths[0] if paths else "") or "").strip()
        else:
            continue
        if not label:
            continue
        samples.append({"label": label, "detail": detail})
        if len(samples) >= limit:
            break
    return samples


def _dedupe_surface_samples(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in items:
        label = str(item.get("label") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        deduped.append(item)
    return deduped


def _overview_status(*, ctx: Any, preload_status: str, catalog: dict[str, Any]) -> str:
    app_status = str(_ctx_get(ctx, "app_intelligence_status") or "").strip()
    if app_status in {"ready", "partial", "unavailable", "failed"}:
        return "ready" if app_status == "ready" else "partial" if app_status == "partial" else "none"
    if catalog.get("present"):
        return "ready"
    return preload_status if preload_status in {"ready", "partial", "none"} else "partial"


def _fallback_catalog(
    *,
    ctx: Any,
    source_catalog: dict[str, Any],
    graph_catalog: dict[str, Any],
) -> dict[str, Any]:
    warnings = _dedupe(
        [
            *_list_value(_ctx_get(ctx, "context_graph_warnings")),
            *_list_value(source_catalog.get("warnings")),
        ]
    )
    role_counts = _dict_value(source_catalog.get("role_counts"))
    language_counts = _dict_value(source_catalog.get("language_counts"))
    return {
        "present": False,
        "schema_version": "mozaiks.app_intelligence.snapshot.v1",
        "snapshot_id": None,
        "app_id": str(_ctx_get(ctx, "app_id") or ""),
        "source_context_bundle_id": source_catalog.get("bundle_id"),
        "graph_id": graph_catalog.get("graph_id"),
        "graph_hash": graph_catalog.get("graph_hash"),
        "coverage": {
            "file_count": int(source_catalog.get("file_count") or graph_catalog.get("indexed_file_count") or 0),
            "chunk_count": int(source_catalog.get("chunk_count") or graph_catalog.get("source_context_chunk_count") or 0),
            "symbol_count": int(source_catalog.get("symbol_count") or graph_catalog.get("source_context_symbol_count") or 0),
            "import_count": int(source_catalog.get("import_count") or 0),
            "node_count": int(graph_catalog.get("node_count") or 0),
            "edge_count": int(graph_catalog.get("edge_count") or 0),
            "role_counts": role_counts,
            "language_counts": language_counts,
        },
        "architecture": {
            "entrypoints": [],
            "module_roots": [],
            "service_roots": [],
            "ui_surfaces": [],
            "workflow_roots": [],
            "top_source_roots": _list_value(graph_catalog.get("top_source_roots")),
        },
        "capabilities": [],
        "ownership": {},
        "integration_surfaces": _safe_surface_list(_ctx_get(ctx, "detected_connectors"), "integration"),
        "data_surfaces": [],
        "risk_hints": _fallback_risks(warnings),
        "agent_context_policy": {
            "policy": "retrieve_not_dump",
            "authority": {
                "facts": "SourceContextBundle and AppContextGraph",
                "summary": "partial App Intelligence overview",
                "exact_code": "source retrieval tools",
            },
            "surfaces": [
                {
                    "workflow_or_checkpoint": "ExistingAppDiscovery",
                    "default_context": ["source_context_catalog", "context_graph_catalog"],
                    "tools": ["search_preloaded_source_context", "read_preloaded_source_file"],
                }
            ],
        },
        "warnings": warnings,
    }


def _fallback_risks(warnings: list[str]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    if any("file_limit" in warning for warning in warnings):
        risks.append(
            {
                "risk_id": "source_scan_file_limit_reached",
                "severity": "medium",
                "description": "The source scan hit its configured file limit; context may be partial.",
            }
        )
    if warnings:
        risks.append(
            {
                "risk_id": "indexing_warnings_present",
                "severity": "low",
                "description": "The repository scan completed with warnings.",
                "count": len(warnings),
            }
        )
    return risks


def _safe_surface_list(value: Any, surface_type: str) -> list[dict[str, Any]]:
    surfaces = []
    for item in _list_value(value):
        if not isinstance(item, dict):
            continue
        label = str(item.get("provider_id") or item.get("label") or "").strip()
        if not label:
            continue
        surfaces.append(
            {
                "surface_type": surface_type,
                "label": label,
                "provider_type": item.get("category"),
                "readiness_status": item.get("confidence"),
            }
        )
    return surfaces[:24]


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = ["emit_app_intelligence_inline_brief", "emit_app_intelligence_overview_card"]
