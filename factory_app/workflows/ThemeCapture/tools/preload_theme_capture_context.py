"""Deterministic before_chat evidence loader for ThemeCapture."""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3,8})\b|rgba?\([^)]*\)|hsla?\([^)]*\)")
_FONT_RE = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)
_CUSTOM_PROPERTY_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;}{]+)")
_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_STYLESHEET_HREF_RE = re.compile(
    r"<link[^>]+rel=[\"'][^\"']*stylesheet[^\"']*[\"'][^>]+href=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_THEME_COLOR_RE = re.compile(
    r"<meta[^>]+name=[\"']theme-color[\"'][^>]+content=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_GOOGLE_FONT_FAMILY_RE = re.compile(r"family=([^:&\"' )]+)", re.IGNORECASE)


def _ctx_store(context_variables: Any) -> Any:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return context_variables
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data
    return context_variables


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    store = _ctx_store(context_variables)
    if isinstance(store, dict):
        return store.get(key, default)
    getter = getattr(store, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            return default
    return default


def _ctx_set(context_variables: Any, key: str, value: Any) -> None:
    store = _ctx_store(context_variables)
    if isinstance(store, dict):
        store[key] = value
        return
    try:
        store[key] = value
    except Exception:
        setattr(store, key, value)


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            path = Path(text).expanduser()
            if path.exists() and path.is_file():
                try:
                    parsed = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return {}
    return {}


def _coerce_path(value: Any) -> Path | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.exists() and path.is_file():
        return path.resolve()
    return None


def _load_related_shell_config(theme_config_value: Any, parent_shell_value: Any = None) -> dict[str, Any]:
    explicit_shell = _coerce_mapping(parent_shell_value)
    if explicit_shell:
        return explicit_shell

    theme_path = _coerce_path(theme_config_value)
    if theme_path is None:
        return {}

    candidates = [theme_path.parent / "shell.json"]
    if len(theme_path.parents) > 1:
        candidates.append(theme_path.parents[1] / "app" / "config" / "shell.json")
        candidates.append(theme_path.parents[1] / "config" / "shell.json")

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            try:
                parsed = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return {}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _top_items(items: list[str], limit: int = 8) -> list[str]:
    counts = Counter(item.strip() for item in items if str(item).strip())
    return [item for item, _count in counts.most_common(limit)]


def _normalize_font_names(raw_values: list[str]) -> list[str]:
    normalized: list[str] = []
    generic_families = {
        "serif",
        "sans-serif",
        "monospace",
        "system-ui",
        "ui-sans-serif",
        "ui-serif",
        "ui-monospace",
        "cursive",
        "fantasy",
        "emoji",
    }
    for value in raw_values:
        for part in str(value).split(","):
            name = part.strip(" '\"")
            if not name:
                continue
            if name.lower() in generic_families:
                continue
            normalized.append(name)
            break
    return _dedupe(normalized)


def _hex_to_luminance(value: str) -> float | None:
    text = str(value or "").strip()
    if not text.startswith("#"):
        return None
    raw = text[1:]
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    if len(raw) < 6:
        return None
    try:
        r = int(raw[0:2], 16) / 255
        g = int(raw[2:4], 16) / 255
        b = int(raw[4:6], 16) / 255
    except ValueError:
        return None

    def _channel(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def _infer_appearance(colors: list[str], css_variables: dict[str, str]) -> str | None:
    candidates = [
        css_variables.get("--color-background"),
        css_variables.get("--background"),
        css_variables.get("--bg-color"),
        css_variables.get("--surface"),
    ] + colors
    luminances = [value for value in (_hex_to_luminance(item) for item in candidates) if value is not None]
    if not luminances:
        return None
    return "dark" if (sum(luminances) / len(luminances)) < 0.45 else "light"


def _infer_layout_hints(html: str, css_text: str) -> list[str]:
    combined = f"{html}\n{css_text}".lower()
    hints: list[str] = []
    if "sidebar" in combined or "<aside" in combined:
        hints.append("sidebar")
    if "<header" in combined or "top-nav" in combined or "navbar" in combined:
        hints.append("top-bar")
    if "<footer" in combined or "footer" in combined:
        hints.append("footer")
    if "glass" in combined or "backdrop-filter" in combined or "blur(" in combined:
        hints.append("glass")
    return _dedupe(hints)


def _summarize_css_snapshot(css_text: str) -> dict[str, Any]:
    colors = _top_items(_COLOR_RE.findall(css_text), limit=10)
    font_values = list(_FONT_RE.findall(css_text))
    for match in _GOOGLE_FONT_FAMILY_RE.findall(css_text):
        family = match.split(":", 1)[0].replace("+", " ").strip()
        if family:
            font_values.append(family)
    fonts = _normalize_font_names(font_values)
    css_variables = {
        key: value.strip()
        for key, value in list(_CUSTOM_PROPERTY_RE.findall(css_text))[:60]
    }
    appearance = _infer_appearance(colors, css_variables)
    layout_hints = _infer_layout_hints("", css_text)

    snapshot_lines = [
        "Parsed deterministic CSS snapshot.",
        f"Detected appearance: {appearance or 'unknown'}",
        f"Top colors: {', '.join(colors[:5]) or 'none detected'}",
        f"Top fonts: {', '.join(fonts[:4]) or 'none detected'}",
    ]
    if layout_hints:
        snapshot_lines.append(f"Layout hints: {', '.join(layout_hints)}")
    if css_variables:
        preview = ", ".join(f"{key}={value}" for key, value in list(css_variables.items())[:6])
        snapshot_lines.append(f"CSS variables: {preview}")

    return {
        "source": "css_snapshot",
        "appearance": appearance,
        "colors": colors,
        "fonts": fonts,
        "css_variables": css_variables,
        "layout_hints": layout_hints,
        "snapshot": "\n".join(snapshot_lines),
    }


def _summarize_theme_mapping(theme_config: dict[str, Any], shell_config: dict[str, Any] | None = None) -> dict[str, Any]:
    theme = theme_config.get("theme") or {}
    identity = theme_config.get("identity") or {}
    fonts = theme_config.get("fonts") or {}
    colors = theme_config.get("colors") or {}
    ui = shell_config or {}
    appearance = theme.get("appearance")
    primary = ((colors.get("primary") or {}).get("main"))
    secondary = ((colors.get("secondary") or {}).get("main"))
    accent = ((colors.get("accent") or {}).get("main"))
    body_font = ((fonts.get("body") or {}).get("family"))
    heading_font = ((fonts.get("heading") or {}).get("family"))
    layout_hints = _dedupe(
        [
            "top-bar" if ui.get("header") else "",
            "footer" if ui.get("footer") else "",
        ]
    )

    snapshot_lines = [
        f"Parent theme config loaded for {identity.get('app_name') or theme.get('branding', {}).get('app_name') or 'existing app'}.",
        f"Appearance: {appearance or 'unspecified'}",
        f"Primary/secondary/accent colors: {primary or 'n/a'}, {secondary or 'n/a'}, {accent or 'n/a'}",
        f"Body/heading fonts: {body_font or 'n/a'}, {heading_font or 'n/a'}",
    ]
    if layout_hints:
        snapshot_lines.append(f"Shell layout hints: {', '.join(layout_hints)}")

    return {
        "source": "parent_theme_config",
        "app_name": identity.get("app_name") or theme.get("branding", {}).get("app_name"),
        "appearance": appearance,
        "colors": _dedupe([item for item in [primary, secondary, accent] if item]),
        "fonts": _dedupe([item for item in [body_font, heading_font] if item]),
        "css_variables": {},
        "layout_hints": layout_hints,
        "snapshot": "\n".join(snapshot_lines),
    }


async def _fetch_text(url: str) -> str | None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        return None
    return response.text


async def _load_url_theme_snapshot(app_url: str) -> dict[str, Any]:
    html = await _fetch_text(app_url)
    if not html:
        return {"success": False, "error": f"Failed to fetch {app_url}"}

    css_chunks = _STYLE_BLOCK_RE.findall(html)
    base_origin = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(app_url))
    stylesheet_urls = []
    for href in _STYLESHEET_HREF_RE.findall(html):
        absolute = urljoin(app_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc == urlparse(base_origin).netloc or "fonts.googleapis.com" in parsed.netloc:
            stylesheet_urls.append(absolute)
    stylesheet_urls = _dedupe(stylesheet_urls)[:6]

    for stylesheet_url in stylesheet_urls:
        css_text = await _fetch_text(stylesheet_url)
        if css_text:
            css_chunks.append(css_text)

    combined_css = "\n".join(css_chunks)
    colors = _top_items(_COLOR_RE.findall(f"{combined_css}\n{html}"), limit=10)
    fonts = _top_items(_FONT_RE.findall(combined_css), limit=6)
    normalized_fonts: list[str] = []
    for value in fonts:
        parts = [part.strip(" '\"") for part in value.split(",")]
        if parts:
            normalized_fonts.append(parts[0])
    css_variables = {
        key: value.strip()
        for key, value in list(_CUSTOM_PROPERTY_RE.findall(combined_css))[:40]
    }
    appearance = _infer_appearance(colors, css_variables)
    layout_hints = _infer_layout_hints(html, combined_css)
    title_match = _TITLE_RE.search(html)
    meta_theme_color = _META_THEME_COLOR_RE.search(html)
    title = title_match.group(1).strip() if title_match else None
    if meta_theme_color:
        colors = _dedupe([meta_theme_color.group(1).strip(), *colors])

    snapshot_lines = [
        f"Fetched theme evidence from {app_url}.",
        f"Detected appearance: {appearance or 'unknown'}",
        f"Top colors: {', '.join(colors[:5]) or 'none detected'}",
        f"Top fonts: {', '.join(_dedupe(normalized_fonts)[:3]) or 'none detected'}",
    ]
    if layout_hints:
        snapshot_lines.append(f"Layout hints: {', '.join(layout_hints)}")
    if css_variables:
        variable_preview = ", ".join(f"{key}={value}" for key, value in list(css_variables.items())[:6])
        snapshot_lines.append(f"CSS variables: {variable_preview}")

    return {
        "success": True,
        "source": "app_url",
        "app_name": title,
        "appearance": appearance,
        "colors": colors,
        "fonts": _dedupe(normalized_fonts),
        "css_variables": css_variables,
        "layout_hints": layout_hints,
        "snapshot": "\n".join(snapshot_lines),
    }


async def collect_prechat_theme_context(context_variables: Any | None = None) -> dict[str, Any]:
    """Populate ThemeCapture context with deterministic evidence before chat."""
    ctx = context_variables or {}
    app_url = _ctx_get(ctx, "app_url")
    css_snapshot = _ctx_get(ctx, "css_snapshot")
    screenshot_description = _ctx_get(ctx, "screenshot_description")
    parent_theme_config = _ctx_get(ctx, "parent_theme_config")
    parent_shell_config = _ctx_get(ctx, "parent_shell_config")
    frontend_repo_summary = _ctx_get(ctx, "frontend_repo_summary") or {}

    evidence_sources: list[dict[str, Any]] = []
    evidence_snapshots: list[str] = []
    structured_evidence: dict[str, Any] = {
        "sources": [],
        "colors": [],
        "fonts": [],
        "layout_hints": [],
        "appearance": None,
        "css_variables": {},
    }

    parent_theme = _coerce_mapping(parent_theme_config)
    if parent_theme:
        parent_shell = _load_related_shell_config(parent_theme_config, parent_shell_config)
        summary = _summarize_theme_mapping(parent_theme, parent_shell)
        evidence_sources.append({"kind": "parent_theme_config", "success": True})
        evidence_snapshots.append(summary["snapshot"])
        structured_evidence["sources"].append(summary["source"])
        structured_evidence["colors"].extend(summary["colors"])
        structured_evidence["fonts"].extend(summary["fonts"])
        structured_evidence["layout_hints"].extend(summary["layout_hints"])
        structured_evidence["appearance"] = structured_evidence["appearance"] or summary["appearance"]
        structured_evidence["css_variables"].update(summary["css_variables"])
        if summary.get("app_name") and not _ctx_get(ctx, "app_name"):
            _ctx_set(ctx, "app_name", summary["app_name"])

    if app_url:
        try:
            url_summary = await _load_url_theme_snapshot(str(app_url))
        except Exception as exc:
            url_summary = {"success": False, "error": str(exc)}
        evidence_sources.append({"kind": "app_url", "location": str(app_url), "success": bool(url_summary.get("success"))})
        if url_summary.get("success"):
            evidence_snapshots.append(url_summary["snapshot"])
            structured_evidence["sources"].append(url_summary["source"])
            structured_evidence["colors"].extend(url_summary["colors"])
            structured_evidence["fonts"].extend(url_summary["fonts"])
            structured_evidence["layout_hints"].extend(url_summary["layout_hints"])
            structured_evidence["appearance"] = structured_evidence["appearance"] or url_summary["appearance"]
            structured_evidence["css_variables"].update(url_summary["css_variables"])
            if url_summary.get("app_name") and not _ctx_get(ctx, "app_name"):
                _ctx_set(ctx, "app_name", url_summary["app_name"])
        else:
            evidence_snapshots.append(f"App URL evidence could not be fetched from {app_url}: {url_summary.get('error', 'unknown error')}")

    if css_snapshot:
        css_summary = _summarize_css_snapshot(str(css_snapshot))
        evidence_sources.append({"kind": "css_snapshot", "success": True})
        evidence_snapshots.append(css_summary["snapshot"])
        structured_evidence["sources"].append(css_summary["source"])
        structured_evidence["colors"].extend(css_summary["colors"])
        structured_evidence["fonts"].extend(css_summary["fonts"])
        structured_evidence["layout_hints"].extend(css_summary["layout_hints"])
        structured_evidence["appearance"] = structured_evidence["appearance"] or css_summary["appearance"]
        structured_evidence["css_variables"].update(css_summary["css_variables"])

    if screenshot_description:
        evidence_sources.append({"kind": "screenshot_description", "success": True})
        evidence_snapshots.append(f"Screenshot description:\n{screenshot_description}")
        structured_evidence["sources"].append("screenshot_description")

    if frontend_repo_summary:
        tech_stack = frontend_repo_summary.get("inferred_tech_stack") or frontend_repo_summary.get("repo_name")
        if tech_stack:
            evidence_sources.append({"kind": "frontend_repo_summary", "success": True})
            evidence_snapshots.append(f"Frontend repo summary: {tech_stack}")
            structured_evidence["sources"].append("frontend_repo_summary")

    structured_evidence["colors"] = _dedupe(structured_evidence["colors"])
    structured_evidence["fonts"] = _dedupe(structured_evidence["fonts"])
    structured_evidence["layout_hints"] = _dedupe(structured_evidence["layout_hints"])

    successful_sources = [item for item in evidence_sources if item.get("success")]
    preload_status = "ready" if successful_sources else "none"
    if evidence_sources and successful_sources and len(successful_sources) < len(evidence_sources):
        preload_status = "partial"

    preload_summary = "\n\n".join(_dedupe(evidence_snapshots)) if evidence_snapshots else "No deterministic theme evidence was preloaded."
    if not css_snapshot and preload_summary and preload_summary != "No deterministic theme evidence was preloaded.":
        _ctx_set(ctx, "css_snapshot", preload_summary)

    _ctx_set(ctx, "theme_capture_evidence", structured_evidence)
    _ctx_set(ctx, "preloaded_context_ready", bool(successful_sources))
    _ctx_set(ctx, "preload_status", preload_status)
    _ctx_set(ctx, "preload_summary", preload_summary)

    logger.info(
        "[ThemeCapture] before_chat preload complete: status=%s sources=%s",
        preload_status,
        ", ".join(item.get("kind", "unknown") for item in evidence_sources) or "none",
    )

    return {
        "success": True,
        "preload_status": preload_status,
        "successful_sources": len(successful_sources),
        "total_sources": len(evidence_sources),
    }
