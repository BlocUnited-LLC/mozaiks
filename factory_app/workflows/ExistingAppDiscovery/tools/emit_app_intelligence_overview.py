"""Emit App Intelligence UI surfaces after deterministic repository indexing.

Payload schema v2 (mozaiks.app_intelligence_overview.ui.v2):
  summary     — 2–3 sentence plain-language narrative (from agent or derived)
  app_type    — short noun phrase, e.g. "community platform"
  stack       — list of primary language/framework strings
  features    — list[{name, category, description, files}]
  services    — list[{id, label, evidence}] — deterministic known-services match
  services_unmatched — count of unrecognized integration surfaces
  graph       — {nodes, edges} — capability-level product graph
  meta        — {files, symbols, nodes, edges, truncated, file_limit}
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from mozaiksai.core.workflow.ui_tools import emit_ui_surface

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known-services static lookup (deterministic, no LLM)
# ---------------------------------------------------------------------------

_KNOWN_SERVICES: dict[str, Any] | None = None


def _load_known_services() -> dict[str, Any]:
    global _KNOWN_SERVICES
    if _KNOWN_SERVICES is None:
        path = os.path.join(os.path.dirname(__file__), "known_services.json")
        try:
            with open(path, encoding="utf-8") as fh:
                _KNOWN_SERVICES = json.load(fh)
        except Exception as exc:
            logger.warning("[ExistingAppDiscovery] Could not load known_services.json: %s", exc)
            _KNOWN_SERVICES = {}
    return _KNOWN_SERVICES


# ---------------------------------------------------------------------------
# Category classification — deterministic, closed enum
# ---------------------------------------------------------------------------

FEATURE_CATEGORIES: frozenset[str] = frozenset({
    "identity_access",
    "social_community",
    "payments_commerce",
    "content_media",
    "data_analytics",
    "communication",
    "infrastructure_ops",
    "growth_marketing",
    "ai_automation",
    "integrations_platform",
    "other",
})

# Keyword lists for each category (searched in label + connector text)
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "identity_access": [
        "auth", "login", "sign in", "sign up", "register", "password", "oauth",
        "session", "permission", "role", "account", "user management", "iam",
        "token", "sso", "jwt",
    ],
    "social_community": [
        "community", "social", "feed", "post", "comment", "follow", "friend",
        "group", "forum", "discussion", "like", "share", "profile", "network",
        "member",
    ],
    "payments_commerce": [
        "payment", "billing", "subscription", "invoice", "checkout", "stripe",
        "paypal", "cart", "order", "purchase", "transaction", "commerce",
        "wallet", "pricing", "monetiz", "revenue",
    ],
    "content_media": [
        "media", "upload", "image", "video", "audio", "storage", "asset",
        "content", "document", "cdn", "gallery", "attachment", "photo", "file",
    ],
    "data_analytics": [
        "analytics", "report", "dashboard", "metric", "chart", "insight",
        "statistics", "tracking", "monitoring", "logging", "export", "clickhouse",
        "bigquery", "telemetry",
    ],
    "communication": [
        "email", "notification", "message", "sms", "push", "alert", "chat",
        "sendgrid", "twilio", "mailgun", "smtp", "inbox", "messaging", "inbox",
    ],
    "infrastructure_ops": [
        "deploy", "hosting", "infrastructure", "docker", "kubernetes", "server",
        "health", "admin", "configuration", "settings", "cache", "queue", "redis",
        "worker", "job", "cron", "background",
    ],
    "growth_marketing": [
        "marketing", "campaign", "seo", "lead", "affiliate", "referral",
        "coupon", "discount", "promotion", "newsletter", "landing",
    ],
    "ai_automation": [
        "ai", "machine learning", "openai", "llm", "gpt", "anthropic",
        "automation", "workflow", "agent", "bot", "predict", "recommend",
        "embedding", "vector",
    ],
    "integrations_platform": [
        "api", "webhook", "integration", "connector", "sdk", "platform",
        "third party", "external",
    ],
}


def _classify_category(
    label: str,
    kind: str = "",
    connector_requirements: list[Any] | None = None,
) -> str:
    """Deterministically assign a closed-enum category to a capability."""
    connector_text = " ".join(str(c) for c in (connector_requirements or []))
    text = f"{label} {connector_text}".lower()

    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return cat

    # Kind-based fallback
    if kind == "module_boundary":
        return "infrastructure_ops"
    if kind == "service_boundary":
        return "integrations_platform"

    return "other"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        text = str(v or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"


# ---------------------------------------------------------------------------
# Service recognition — deterministic lookup, no LLM
# ---------------------------------------------------------------------------

def _resolve_services(catalog: dict[str, Any]) -> tuple[list[dict], int]:
    """Match integration surfaces against known_services.json. Returns (matched, unmatched_count)."""
    known = _load_known_services()
    found: dict[str, dict] = {}
    unmatched = 0

    integration_surfaces = _list_value(catalog.get("integration_surfaces"))
    for surf in integration_surfaces:
        label = str(surf.get("label") or "").lower()
        matched = False
        for key, service in known.items():
            if key in label or label in key:
                sid = service["id"]
                if sid not in found:
                    found[sid] = {"id": sid, "label": service["label"], "evidence": []}
                path = surf.get("path") or surf.get("root")
                if path:
                    found[sid]["evidence"].append(str(path))
                matched = True
                break
        if not matched:
            unmatched += 1

    return list(found.values()), unmatched


# ---------------------------------------------------------------------------
# Feature normalization
# ---------------------------------------------------------------------------

def _normalize_features(
    catalog: dict[str, Any],
    capability_specs: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Build the features list from catalog capabilities, enriched with agent data if available."""
    catalog_caps = _list_value(catalog.get("capabilities"))
    source_caps = catalog_caps or _list_value(capability_specs)

    # Index agent-produced specs by capability_id for O(1) lookup
    spec_by_id: dict[str, dict] = {}
    for spec in _list_value(capability_specs):
        cid = str(spec.get("capability_id") or spec.get("label") or "")
        if cid:
            spec_by_id[cid] = spec

    features: list[dict] = []
    for cap in source_caps:
        cap_id = str(cap.get("capability_id") or cap.get("label") or "")
        label = str(cap.get("label") or cap_id)
        spec = spec_by_id.get(cap_id)

        # Category: prefer agent-assigned (any domain label), else classify deterministically
        category = None
        if spec:
            raw_cat = str(spec.get("category") or "").strip()
            if raw_cat:
                category = raw_cat
        if not category:
            category = _classify_category(label, cap.get("kind", ""), cap.get("connector_requirements"))

        # Icon: agent-produced emoji for this capability's category
        icon = str(spec.get("icon") or "").strip() if spec else ""

        # Description: prefer agent-produced (plain language), else empty string
        description = str(spec.get("description") or "").strip() if spec else ""

        files = _list_value(cap.get("evidence_paths"))
        if not files and spec and spec.get("evidence_source"):
            files = [str(spec.get("evidence_source"))]

        features.append({
            "name": label,
            "category": category,
            "description": description,
            "files": files,
            "icon": icon,
        })

    return features


# ---------------------------------------------------------------------------
# Graph normalization — capability-level product graph
# ---------------------------------------------------------------------------

def _build_graph(catalog: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized product-level graph from catalog. No raw AST nodes."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    # Capability nodes
    for cap in _list_value(catalog.get("capabilities")):
        cap_id = str(cap.get("capability_id") or cap.get("label") or "")
        if not cap_id or cap_id in seen_ids:
            continue
        seen_ids.add(cap_id)
        label = str(cap.get("label") or cap_id)
        category = _classify_category(label, cap.get("kind", ""), cap.get("connector_requirements"))
        nodes.append({
            "id": cap_id,
            "label": label,
            "kind": "capability",
            "component": category,
            "path": None,
        })

    # External service nodes (from integration_surfaces, capped at 8)
    for surf in _list_value(catalog.get("integration_surfaces"))[:8]:
        label = str(surf.get("label") or "")
        if not label:
            continue
        int_id = f"ext_{_slugify(label)}"
        if int_id in seen_ids:
            continue
        seen_ids.add(int_id)
        nodes.append({
            "id": int_id,
            "label": label,
            "kind": "external",
            "component": "integrations_platform",
            "path": surf.get("path") or surf.get("root"),
        })

    # Data nodes (from data_surfaces, capped at 4)
    for surf in _list_value(catalog.get("data_surfaces"))[:4]:
        label = str(surf.get("label") or surf.get("root") or "")
        if not label:
            continue
        dat_id = f"dat_{_slugify(label)}"
        if dat_id in seen_ids:
            continue
        seen_ids.add(dat_id)
        nodes.append({
            "id": dat_id,
            "label": label,
            "kind": "data",
            "component": "data_analytics",
            "path": surf.get("path") or surf.get("root"),
        })

    # Build edge index for integration nodes by label fragment
    ext_nodes = {n["label"].lower(): n["id"] for n in nodes if n["kind"] == "external"}

    seen_edges: set[str] = set()

    def _add_edge(src: str, tgt: str, kind: str = "call") -> None:
        key = f"{src}~{tgt}"
        if key not in seen_edges and src != tgt:
            seen_edges.add(key)
            edges.append({"source": src, "target": tgt, "kind": kind, "weight": 1})

    # Capability → integration edges (from connector_requirements)
    for cap in _list_value(catalog.get("capabilities")):
        cap_id = str(cap.get("capability_id") or cap.get("label") or "")
        if cap_id not in seen_ids:
            continue
        for connector in _list_value(cap.get("connector_requirements")):
            conn_low = str(connector).lower()
            for ext_label, ext_id in ext_nodes.items():
                if conn_low in ext_label or ext_label in conn_low:
                    _add_edge(cap_id, ext_id, "call")
                    break

    # Capability → data edges (from data_surfaces that share evidence paths)
    cap_evidence: dict[str, set[str]] = {}
    for cap in _list_value(catalog.get("capabilities")):
        cap_id = str(cap.get("capability_id") or cap.get("label") or "")
        cap_evidence[cap_id] = set(_list_value(cap.get("evidence_paths")))

    dat_nodes = {n["path"] or n["label"]: n["id"] for n in nodes if n["kind"] == "data"}
    for dat_key, dat_id in dat_nodes.items():
        dat_low = str(dat_key).lower()
        for cap_id, ev_paths in cap_evidence.items():
            if any(dat_low in str(p).lower() for p in ev_paths):
                _add_edge(cap_id, dat_id, "data")
                break

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Stack and meta
# ---------------------------------------------------------------------------

def _derive_stack(catalog: dict[str, Any]) -> list[str]:
    """Return primary language/framework strings in confidence order."""
    coverage = _dict_value(catalog.get("coverage"))
    framework_detection = _dict_value(coverage.get("framework_detection"))

    lang_counts = _dict_value(coverage.get("language_counts"))
    top_langs = sorted(lang_counts.items(), key=lambda x: -int(x[1] or 0))[:5]
    stack = [lang for lang, _ in top_langs if lang.lower() not in ("unknown", "other", "text")]

    primary = str(framework_detection.get("primary_framework_label") or "").strip()
    if primary and primary not in stack:
        stack.insert(0, primary)

    return stack[:6]


def _build_meta(catalog: dict[str, Any]) -> dict[str, Any]:
    """Build clean meta — no debug strings, only typed fields."""
    coverage = _dict_value(catalog.get("coverage"))
    file_count = int(coverage.get("file_count") or 0)
    # Detect file-limit truncation: if file_limit warnings exist, parse them
    truncated = False
    file_limit: int | None = None
    for w in _list_value(catalog.get("warnings")):
        w_str = str(w).lower()
        if "limit" in w_str or "truncat" in w_str or "cap" in w_str:
            truncated = True
            # Try to extract a numeric limit from the warning text
            m = re.search(r"(\d+)", w_str)
            if m:
                file_limit = int(m.group(1))
            break

    return {
        "files": file_count,
        "symbols": int(coverage.get("symbol_count") or 0),
        "nodes": int(coverage.get("node_count") or 0),
        "edges": int(coverage.get("edge_count") or 0),
        "truncated": truncated,
        "file_limit": file_limit,
    }


# ---------------------------------------------------------------------------
# Narrative summary — agents produce this; we derive a fallback
# ---------------------------------------------------------------------------

_CATEGORY_PROSE: dict[str, str] = {
    "identity_access": "identity and access control",
    "social_community": "community and social interaction",
    "payments_commerce": "payments and commerce",
    "content_media": "content and media management",
    "data_analytics": "analytics and reporting",
    "communication": "messaging and notifications",
    "infrastructure_ops": "infrastructure and operations",
    "growth_marketing": "growth and marketing",
    "ai_automation": "AI and automation",
    "integrations_platform": "platform integrations",
    "other": "general capabilities",
}


def _build_narrative(
    *,
    ctx: Any,
    catalog: dict[str, Any],
    features: list[dict],
    services: list[dict],
    meta: dict[str, Any],
    app_type: str,
    app_name: str,
) -> str:
    """Produce a plain-language summary. Agent-produced text is preferred."""
    # 1. Agent-produced analysis summary from ExistingAppAugmentationArtifact
    artifact = _dict_value(_ctx_get(ctx, "existing_app_discovery_artifact"))
    analysis_summary = str(artifact.get("analysis_summary") or _ctx_get(ctx, "analysis_summary") or "").strip()
    if len(analysis_summary) > 30 and not analysis_summary.startswith("{"):
        return analysis_summary

    # 2. Agent-produced summary from ExistingAppAugmentationArtifact
    app_summary = str(artifact.get("app_summary") or "").strip()
    if len(app_summary) > 30 and not app_summary.startswith("{"):
        return app_summary

    # 3. discovery_brief from context (agent-assembled)
    brief = str(_ctx_get(ctx, "discovery_brief") or "").strip()
    if len(brief) > 40 and not brief.startswith("{"):
        return brief

    # 4. ExistingProductSpec.app_description from context
    product_spec = _dict_value(_ctx_get(ctx, "existing_product_spec"))
    app_description = str(product_spec.get("app_description") or "").strip()
    if len(app_description) > 30:
        return app_description

    # 5. Derived from template (deterministic, no LLM)
    stack = _derive_stack(catalog)
    stack_str = ", ".join(stack[:3]) if stack else "the detected technologies"

    # Top categories by feature count
    cat_counts: dict[str, int] = {}
    for f in features:
        cat = f.get("category", "other")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    top_cats = [c for c, _ in sorted(cat_counts.items(), key=lambda x: -x[1])[:2]]
    cat_clause = ""
    if top_cats:
        prose = [_CATEGORY_PROSE.get(c, c) for c in top_cats]
        cat_clause = f" Its primary capabilities cover {' and '.join(prose)}."

    service_clause = ""
    if services:
        labels = [s["label"] for s in services[:3]]
        service_clause = f" It integrates with {', '.join(labels)}."

    files_str = f"{meta['files']:,}" if meta.get("files") else "the"
    edges_str = f"{meta['edges']:,}" if meta.get("edges") else "many"

    return (
        f"{app_name} is a {app_type} built with {stack_str}. "
        f"We read {files_str} files and mapped {edges_str} relationships to understand how it works."
        f"{cat_clause}{service_clause}"
    )


def _derive_app_type(ctx: Any, catalog: dict[str, Any], features: list[dict]) -> str:
    """Derive a short app-type noun phrase. Prefer agent output."""
    # Try agent-produced value first
    artifact = _dict_value(_ctx_get(ctx, "existing_app_discovery_artifact"))
    app_type = str(artifact.get("app_type") or "").strip()
    if app_type:
        return app_type

    # Derive from top feature category and framework detection
    cat_counts: dict[str, int] = {}
    for f in features:
        cat = f.get("category", "other")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    top_cat = max(cat_counts, key=lambda c: cat_counts[c]) if cat_counts else "other"

    coverage = _dict_value(catalog.get("coverage"))
    framework_detection = _dict_value(coverage.get("framework_detection"))
    framework_ids = _list_value(framework_detection.get("framework_ids"))

    # Framework-aware type
    has_react = any("react" in str(fid).lower() for fid in framework_ids)
    has_nextjs = any("next" in str(fid).lower() for fid in framework_ids)
    has_cli = any("cli" in str(fid).lower() for fid in framework_ids)

    APP_TYPE_MAP: dict[str, str] = {
        "payments_commerce": "e-commerce platform",
        "social_community": "community platform",
        "identity_access": "identity platform",
        "content_media": "content platform",
        "data_analytics": "analytics platform",
        "communication": "messaging platform",
        "infrastructure_ops": "infrastructure tool",
        "growth_marketing": "marketing platform",
        "ai_automation": "AI platform",
        "integrations_platform": "integration platform",
    }

    base = APP_TYPE_MAP.get(top_cat, "web application")
    if has_cli:
        return "CLI tool"
    if has_nextjs:
        return f"Next.js {base}"
    if has_react:
        return base
    return base


# ---------------------------------------------------------------------------
# App Intelligence overview card — full catalog in the artifact panel
# ---------------------------------------------------------------------------

async def emit_app_intelligence_overview_card(
    context_variables: Any | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Emit the App Intelligence overview card (v2 schema) before the agent speaks."""
    ctx = context_variables if context_variables is not None else {}
    catalog = _dict_value(_ctx_get(ctx, "app_intelligence_catalog"))
    chat_id = _ctx_get(ctx, "chat_id")
    repo_path = str(_ctx_get(ctx, "repo_path") or "").strip()
    github_repo = str(_ctx_get(ctx, "github_repo") or "").strip()
    capability_specs = _list_value(_ctx_get(ctx, "capability_specs"))

    logger.info(
        "[ExistingAppDiscovery] App Intelligence overview emit requested: chat_id=%s catalog_present=%s capability_specs=%d repo_path=%s github_repo=%s",
        chat_id or "NONE",
        bool(catalog),
        len(capability_specs),
        repo_path or "NONE",
        github_repo or "NONE",
    )

    if not catalog:
        github_repo = str(_ctx_get(ctx, "github_repo") or "").strip()
        repo_path = str(_ctx_get(ctx, "repo_path") or "").strip()
        tech_stack = _list_value(_ctx_get(ctx, "tech_stack"))
        app_name = str(_ctx_get(ctx, "app_name") or "").strip()
        capability_specs = capability_specs or _list_value(_ctx_get(ctx, "capability_specs"))
        analysis_summary = str(
            _ctx_get(ctx, "analysis_summary")
            or _dict_value(_ctx_get(ctx, "existing_app_discovery_artifact")).get("analysis_summary")
            or _dict_value(_ctx_get(ctx, "existing_app_discovery_artifact")).get("app_summary")
            or ""
        ).strip()
        if not (github_repo or repo_path or tech_stack or app_name or capability_specs or analysis_summary):
            logger.info(
                "[ExistingAppDiscovery] App Intelligence overview skipped: chat_id=%s reason=no_app_intelligence_catalog",
                chat_id or "NONE",
            )
            return {"skipped": True, "reason": "no_app_intelligence_catalog"}
        catalog = {}  # fall through with empty catalog; payload status will be "partial"

    payload = _build_overview_payload(ctx=ctx, catalog=catalog, capability_specs=None)

    # When catalog was absent, replace misleading template fallback values with
    # real context data where available.
    if not payload.get("stack"):
        tech_stack = _list_value(_ctx_get(ctx, "tech_stack"))
        if tech_stack:
            payload["stack"] = [str(t) for t in tech_stack][:6]
    if not payload.get("summary") or len(str(payload.get("summary", ""))) < 30:
        preload_summary = str(_ctx_get(ctx, "preload_summary") or "").strip()
        if preload_summary and preload_summary != "No deterministic evidence was preloaded.":
            payload["summary"] = preload_summary

    logger.info(
        "[ExistingAppDiscovery] Emitting App Intelligence overview card: chat_id=%s repo=%s features=%d nodes=%d agent_enriched=%s source=%s",
        chat_id or "NONE",
        payload.get("github_repo") or "unknown",
        len(payload.get("features") or []),
        len((payload.get("graph") or {}).get("nodes") or []),
        False,
        "catalog_only",
    )

    try:
        event_id = await emit_ui_surface(
            "AppIntelligenceOverviewCard",
            payload,
            chat_id=str(chat_id) if chat_id else None,
            workflow_name="ExistingAppDiscovery",
            agent_name="App Intelligence",
            display="artifact",
        )
        logger.info(
            "[ExistingAppDiscovery] App Intelligence overview card emitted (v2): chat_id=%s repo=%s status=%s ui_event_id=%s",
            chat_id or "NONE",
            payload.get("github_repo") or "unknown",
            payload.get("status") or "unknown",
            event_id,
        )
    except Exception as exc:
        logger.warning("[ExistingAppDiscovery] Overview card emission failed: %s", exc)
        return {"skipped": True, "reason": "emission_failed", "error": str(exc)}

    return {"success": True, "ui_event_id": event_id if "event_id" in locals() else None}


async def emit_app_intelligence_enriched_overview_card(
    context_variables: Any | None = None,
    capability_specs: list[dict] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Re-emit the overview card with agent-enriched data after discovery completes."""
    ctx = context_variables if context_variables is not None else {}
    catalog = _dict_value(_ctx_get(ctx, "app_intelligence_catalog"))
    chat_id = _ctx_get(ctx, "chat_id")
    specs = capability_specs or _list_value(_ctx_get(ctx, "capability_specs"))
    analysis_summary = str(
        _ctx_get(ctx, "analysis_summary")
        or _dict_value(_ctx_get(ctx, "existing_app_discovery_artifact")).get("analysis_summary")
        or _dict_value(_ctx_get(ctx, "existing_app_discovery_artifact")).get("app_summary")
        or ""
    ).strip()
    if not catalog and not specs and not analysis_summary:
        logger.info(
            "[ExistingAppDiscovery] Enriched App Intelligence overview skipped: chat_id=%s reason=no_app_intelligence_catalog",
            chat_id or "NONE",
        )
        return {"skipped": True, "reason": "no_app_intelligence_catalog"}

    payload = _build_overview_payload(ctx=ctx, catalog=catalog, capability_specs=specs)

    logger.info(
        "[ExistingAppDiscovery] Emitting enriched overview card: chat_id=%s repo=%s features=%d capability_specs=%d agent_enriched=%s source=%s",
        chat_id or "NONE",
        payload.get("github_repo") or "unknown",
        len(payload.get("features") or []),
        len(specs),
        True,
        "agent_enriched",
    )

    try:
        event_id = await emit_ui_surface(
            "AppIntelligenceOverviewCard",
            payload,
            chat_id=str(chat_id) if chat_id else None,
            workflow_name="ExistingAppDiscovery",
            agent_name="App Intelligence",
            display="artifact",
        )
        logger.info(
            "[ExistingAppDiscovery] Enriched overview card emitted: chat_id=%s repo=%s features=%d services=%d ui_event_id=%s",
            chat_id or "NONE",
            payload.get("github_repo") or "unknown",
            len(payload.get("features") or []),
            len(payload.get("services") or []),
            event_id,
        )
    except Exception as exc:
        logger.warning("[ExistingAppDiscovery] Enriched overview card emission failed: %s", exc)
        return {"skipped": True, "reason": "emission_failed", "error": str(exc)}

    return {"success": True, "ui_event_id": event_id if "event_id" in locals() else None}


def _build_overview_payload(
    *,
    ctx: Any,
    catalog: dict[str, Any],
    capability_specs: list[dict] | None,
) -> dict[str, Any]:
    """Build the v2 overview card payload from catalog + optional agent-enriched data."""
    # Strip raw file contents — agents use retrieval tools for that
    safe_catalog = {k: v for k, v in catalog.items() if k != "source_context_bundle_file_contents"}

    features = _normalize_features(safe_catalog, capability_specs)
    services, services_unmatched = _resolve_services(safe_catalog)
    graph = _build_graph(safe_catalog)
    meta = _build_meta(safe_catalog)
    stack = _derive_stack(safe_catalog)
    app_name = str(_ctx_get(ctx, "app_name") or "").strip() or str(_ctx_get(ctx, "github_repo") or "").strip() or "App"
    app_type = _derive_app_type(ctx, safe_catalog, features)
    summary = _build_narrative(
        ctx=ctx,
        catalog=safe_catalog,
        features=features,
        services=services,
        meta=meta,
        app_type=app_type,
        app_name=app_name,
    )
    artifact = _dict_value(_ctx_get(ctx, "existing_app_discovery_artifact"))
    augmentation_plan = _dict_value(_ctx_get(ctx, "agent_augmentation_plan"))
    product_spec = _dict_value(_ctx_get(ctx, "existing_product_spec"))

    warnings = _dedupe([
        *_list_value(_ctx_get(ctx, "context_graph_warnings")),
        *_list_value(catalog.get("warnings")),
    ])[:4]
    # Strip any internal debug identifiers from warnings
    warnings = [w for w in warnings if "context_graph" not in w and "file_limit" not in w.lower()]

    status = str(_ctx_get(ctx, "app_intelligence_status") or "partial").strip()
    if catalog.get("present"):
        status = status or "ready"

    return {
        "schema_version": "mozaiks.app_intelligence_overview.ui.v2",
        "app_name": app_name or None,
        "github_repo": str(_ctx_get(ctx, "github_repo") or "").strip() or None,
        "status": status,
        # v2 display fields
        "summary": summary,
        "app_type": app_type,
        "stack": stack,
        "analysis_summary": str(
            _ctx_get(ctx, "analysis_summary")
            or artifact.get("analysis_summary")
            or artifact.get("app_summary")
            or summary
            or ""
        ).strip(),
        "brand_theme_summary": str(product_spec.get("brand_theme_summary") or "").strip(),
        "brand_theme_evidence": product_spec.get("brand_theme_evidence") or {},
        "adoption_level": str(
            augmentation_plan.get("adoption_level")
            or _ctx_get(ctx, "adoption_level")
            or ""
        ).strip() or None,
        "migration_complexity": augmentation_plan.get("migration_complexity")
        or _ctx_get(ctx, "migration_complexity")
        or None,
        "adoption_rationale": augmentation_plan.get("adoption_rationale")
        or _ctx_get(ctx, "adoption_rationale")
        or "",
        "ui_surface_preference": augmentation_plan.get("ui_surface_preference")
        or _ctx_get(ctx, "ui_surface_preference")
        or "",
        "auth_model": str(_ctx_get(ctx, "auth_model") or _dict_value(_ctx_get(ctx, "existing_product_spec")).get("auth_model") or "").strip(),
        "auth_delegation_model": augmentation_plan.get("auth_delegation_model")
        or _ctx_get(ctx, "auth_delegation_model")
        or "",
        "theme_adaptation_strategy": augmentation_plan.get("theme_adaptation_strategy")
        or _ctx_get(ctx, "theme_adaptation_strategy")
        or "",
        "embed_theme_ready": bool(
            augmentation_plan.get("embed_theme_ready")
            if "embed_theme_ready" in augmentation_plan
            else _ctx_get(ctx, "embed_theme_ready")
        ),
        "initial_workflows": augmentation_plan.get("initial_workflows")
        or _list_value(_ctx_get(ctx, "initial_workflows")),
        "ecosystem_bindings": augmentation_plan.get("ecosystem_bindings")
        or _list_value(_ctx_get(ctx, "ecosystem_bindings")),
        "discovery_brief": str(
            _ctx_get(ctx, "discovery_brief")
            or artifact.get("discovery_brief")
            or artifact.get("analysis_summary")
            or ""
        ).strip(),
        "capability_count": len(_list_value(capability_specs or _ctx_get(ctx, "capability_specs"))),
        "ai_accessible_count": len(_list_value(augmentation_plan.get("ai_accessible_capabilities"))),
        "service_surface_count": len(_list_value(product_spec.get("service_surfaces"))),
        "route_surface_count": len(_list_value(product_spec.get("route_surfaces"))),
        "adoption_plan_available": bool(augmentation_plan),
        "capabilities": _list_value(capability_specs or _ctx_get(ctx, "capability_specs")),
        "features": features,
        "services": services,
        "services_unmatched": services_unmatched,
        "graph": graph,
        "meta": meta,
        # Forward-compat: keep catalog for any older code paths still reading it
        "app_intelligence_catalog": safe_catalog,
        "app_intelligence_progress": _dict_value(_ctx_get(ctx, "app_intelligence_progress")),
        "current_app_context_version_id": str(_ctx_get(ctx, "current_app_context_version_id") or "").strip() or None,
        "artifact_version_ids": {
            "app_context_version": _ctx_get(ctx, "app_context_version_artifact_version_id"),
            "source_context_bundle": _ctx_get(ctx, "source_context_artifact_version_id"),
            "app_context_graph": _ctx_get(ctx, "graph_artifact_version_id"),
            "app_intelligence_snapshot": _ctx_get(ctx, "app_intelligence_artifact_version_id"),
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Repo access recovery card — inline error card for private GitHub repos
# ---------------------------------------------------------------------------

async def emit_repo_access_recovery_card(
    context_variables: Any | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Show a visible recovery card when a GitHub repo cannot be read."""
    ctx = context_variables if context_variables is not None else {}
    recovery = _dict_value(_ctx_get(ctx, "repo_access_recovery"))

    if not recovery:
        return {"skipped": True, "reason": "no_repo_access_recovery"}

    payload = _repo_access_recovery_payload(ctx=ctx, recovery=recovery)
    chat_id = _ctx_get(ctx, "chat_id")

    try:
        event_id = await emit_ui_surface(
            "RepoAccessRecoveryCard",
            payload,
            chat_id=str(chat_id) if chat_id else None,
            workflow_name="ExistingAppDiscovery",
            agent_name="App Intelligence",
            display="inline",
        )
        logger.info(
            "[ExistingAppDiscovery] Repo access recovery emitted: repo=%s status=%s",
            payload.get("github_repo") or "unknown",
            payload.get("http_status") or "unknown",
        )
    except Exception as exc:
        logger.warning("[ExistingAppDiscovery] Repo access recovery emission failed: %s", exc)

    return {
        "success": True,
        "repo_access_status": payload.get("repo_access_status"),
        "github_repo": payload.get("github_repo"),
        "ui_event_id": event_id if "event_id" in locals() else None,
    }


def _repo_access_recovery_payload(*, ctx: Any, recovery: dict[str, Any]) -> dict[str, Any]:
    progress = _dict_value(_ctx_get(ctx, "app_intelligence_progress"))
    return {
        "schema_version": "mozaiks.repo_access_recovery.ui.v1",
        "repo_access_status": str(_ctx_get(ctx, "repo_access_status") or "required").strip() or "required",
        "provider": str(recovery.get("provider") or "github").strip(),
        "code": str(recovery.get("code") or "github_repo_access_required").strip(),
        "github_repo": str(recovery.get("github_repo") or _ctx_get(ctx, "github_repo") or "").strip(),
        "github_url": str(recovery.get("github_url") or "").strip(),
        "http_status": recovery.get("http_status"),
        "phase": str(recovery.get("phase") or "").strip(),
        "auth_present": bool(recovery.get("auth_present")),
        "message": str(recovery.get("message") or "Repository access is required before indexing can continue.").strip(),
        "recovery_actions": _list_value(recovery.get("recovery_actions")),
        "app_intelligence_status": str(_ctx_get(ctx, "app_intelligence_status") or "").strip(),
        "activity_type": "app_intelligence_indexing",
        "activity_agent": "App Intelligence",
        "activity_display_variant": "app_intelligence_progress",
        "activity_component_type": "AppIntelligenceProgressCard",
        "display_variant": "app_intelligence_progress",
        "progress": progress,
        "app_intelligence_progress": progress,
        "warnings": _dedupe([
            *_list_value(_ctx_get(ctx, "context_graph_warnings")),
            *_list_value(progress.get("warnings")),
        ])[:8],
    }


__all__ = [
    "emit_app_intelligence_overview_card",
    "emit_app_intelligence_enriched_overview_card",
    "emit_repo_access_recovery_card",
    "FEATURE_CATEGORIES",
    "_classify_category",
    "_normalize_features",
    "_build_graph",
    "_build_meta",
    "_derive_stack",
]
