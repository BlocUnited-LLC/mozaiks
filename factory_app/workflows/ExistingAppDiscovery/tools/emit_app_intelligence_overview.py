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
    """Show the user the compact context available to discovery agents."""
    ctx = context_variables if context_variables is not None else {}
    preload_status = str(_ctx_get(ctx, "preload_status") or "none")
    catalog = _dict_value(_ctx_get(ctx, "app_intelligence_catalog"))

    if preload_status == "none" and not catalog:
        return {"skipped": True, "reason": "no_app_intelligence_data"}

    payload = _overview_payload(ctx=ctx, preload_status=preload_status, catalog=catalog)
    chat_id = _ctx_get(ctx, "chat_id")

    try:
        await emit_ui_surface(
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
        "status": preload_status if preload_status in {"ready", "partial", "none"} else "partial",
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
        "total_files_scanned": int(
            coverage.get("file_count")
            or source_catalog.get("file_count")
            or graph_catalog.get("indexed_file_count")
            or primary.get("total_files_scanned")
            or 0
        ),
        "app_intelligence_catalog": resolved_catalog,
        "warnings": _dedupe(
            [
                *_list_value(_ctx_get(ctx, "context_graph_warnings")),
                *_list_value(source_catalog.get("warnings")),
                *_list_value(resolved_catalog.get("warnings")),
            ]
        )[:12],
    }


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
                    "tools": ["search_repo_source_context", "read_repo_source_file"],
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


__all__ = ["emit_app_intelligence_overview_card"]
