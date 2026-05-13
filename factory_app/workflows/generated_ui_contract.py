"""Deterministic quality checks for generated frontend surfaces.

This module is intentionally shared by AppGenerator and AgentGenerator so the
React and YAML lanes cannot drift into different UI standards.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from mozaiksai.core.workflow.ui_primitives import get_page_ui_primitive_names
except Exception:  # pragma: no cover - import failures are surfaced by runtime tests.
    get_page_ui_primitive_names = None  # type: ignore[assignment]


REMOVED_PRIMITIVES = {"Badge", "Card", "Stat"}
SURFACE_PRIMITIVES = {"Panel", "SurfaceCard"}
COPY_FLAGS = (
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
FONT_FLAGS = ("rajdhani", "orbitron", "fagrak")
DEEP_IMPORT_FLAGS = (
    "@mozaiks/chat-ui/ui/primitives/",
    "chat-ui/src/",
    "../../ui/",
    "../ui/",
)

LEGACY_COLOR_PATTERNS = (
    re.compile(r"\bbg-(gray|slate|zinc|neutral|stone|white|black)-"),
    re.compile(r"\btext-(gray|slate|zinc|neutral|stone|white|black)-"),
    re.compile(r"\bborder-(gray|slate|zinc|neutral|stone|white|black)-"),
)
HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGB_COLOR_RE = re.compile(r"\brgba?\(")
PUBLIC_IMPORT_RE = re.compile(
    r"import\s*\{(?P<specifiers>[^}]+)\}\s*from\s*['\"]@mozaiks/chat-ui/ui['\"]"
)


def dedupe(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _page_primitive_names() -> Optional[set[str]]:
    if get_page_ui_primitive_names is None:
        return None
    try:
        return set(get_page_ui_primitive_names())
    except Exception:
        return None


def _parse_public_imports(content: str) -> List[str]:
    imported: List[str] = []
    for match in PUBLIC_IMPORT_RE.finditer(content):
        for raw in match.group("specifiers").split(","):
            token = raw.strip()
            if not token:
                continue
            if " as " in token:
                token = token.split(" as ", 1)[0].strip()
            imported.append(token)
    return imported


def _file_name(item: Dict[str, Any]) -> str:
    return str(item.get("filename") or item.get("path") or "").strip()


def _react_file_items(
    code_files: Sequence[Dict[str, Any]],
    *,
    include_ui_index: bool,
) -> List[Tuple[str, str]]:
    files: List[Tuple[str, str]] = []
    for item in code_files:
        if not isinstance(item, dict):
            continue
        filename = _file_name(item).replace("\\", "/")
        content = item.get("content")
        if not filename or not isinstance(content, str):
            continue
        suffix = PurePosixPath(filename).suffix.lower()
        if suffix not in {".js", ".jsx"}:
            continue
        if not include_ui_index and PurePosixPath(filename).name == "index.js":
            continue
        files.append((filename, content))
    return files


def audit_generated_react_files(
    code_files: Sequence[Dict[str, Any]],
    *,
    source_label: str = "generated React",
    require_jsx: bool = False,
    include_ui_index: bool = False,
) -> List[str]:
    """Return blocking quality warnings for generated/custom React files."""

    warnings: List[str] = []
    for filename, content in _react_file_items(
        code_files,
        include_ui_index=include_ui_index,
    ):
        component_name = PurePosixPath(filename).stem
        suffix = PurePosixPath(filename).suffix.lower()
        lower = content.lower()

        if require_jsx and suffix != ".jsx":
            warnings.append(
                f"{filename} uses {suffix or 'no extension'}; {source_label} must use .jsx."
            )

        for deep_import in DEEP_IMPORT_FLAGS:
            if deep_import in content:
                warnings.append(
                    f"{filename} uses brittle deep UI imports ({deep_import}); use the public @mozaiks/chat-ui/ui entrypoint."
                )

        imported = set(_parse_public_imports(content))
        non_canonical_imports = sorted(imported & REMOVED_PRIMITIVES)
        if non_canonical_imports:
            warnings.append(
                f"{filename} imports non-canonical component primitives: {', '.join(non_canonical_imports)}."
            )

        for primitive in sorted(REMOVED_PRIMITIVES):
            if re.search(rf"<{primitive}\b", content):
                warnings.append(
                    f"{filename} renders non-canonical component primitive <{primitive}>."
                )

        if "fontFamily" in content or "font-family" in lower:
            warnings.append(
                f"{filename} hardcodes font-family styling; use semantic theme tokens instead."
            )

        literal_fonts = [font for font in FONT_FLAGS if font in lower]
        if literal_fonts:
            warnings.append(
                f"{filename} references literal brand fonts ({', '.join(sorted(set(literal_fonts)))}); use semantic theme tokens instead."
            )

        if HEX_COLOR_RE.search(content) or RGB_COLOR_RE.search(content):
            warnings.append(
                f"{filename} hardcodes color values; use semantic theme tokens instead."
            )

        for pattern in LEGACY_COLOR_PATTERNS:
            if pattern.search(content):
                warnings.append(
                    f"{filename} uses legacy color utility classes; use semantic theme tokens instead."
                )
                break

        matched_copy_flags = [flag for flag in COPY_FLAGS if flag in lower]
        if matched_copy_flags:
            warnings.append(
                f"{filename} contains placeholder/internal copy ({', '.join(sorted(set(matched_copy_flags)))})."
            )

        status_pill_count = len(re.findall(r"<StatusPill\b", content))
        if status_pill_count > 2:
            warnings.append(
                f"{filename} renders {status_pill_count} StatusPill components; compact generated UI should avoid repeated status chips."
            )

        container_count = len(re.findall(r"<(Panel|SurfaceCard)\b", content))
        if container_count > 2:
            warnings.append(
                f"{filename} renders {container_count} primary wrapper surfaces; generated UI should keep one focused working area."
            )

        metric_count = len(re.findall(r"<Metric\b", content))
        if metric_count > 3:
            warnings.append(
                f"{filename} renders {metric_count} Metric components; avoid KPI-strip generated UI."
            )

        summary_strip_count = len(re.findall(r"<SummaryStrip\b", content))
        if summary_strip_count > 1:
            warnings.append(
                f"{filename} renders multiple SummaryStrip components; keep generated UI compact."
            )

        if component_name.endswith("Dashboard"):
            warnings.append(
                f"{filename} uses dashboard-style naming ({component_name}); generated UI should describe the actual task or product surface."
            )

    return dedupe(warnings)


def _section_children(section: Dict[str, Any]) -> List[Dict[str, Any]]:
    children: List[Dict[str, Any]] = []
    config = section.get("config")
    if not isinstance(config, dict):
        return children
    for key in ("children", "sections", "items"):
        raw = config.get(key)
        if isinstance(raw, list):
            children.extend([item for item in raw if isinstance(item, dict) and item.get("primitive")])
    return children


def _strings_from_value(value: Any, *, key_name: str = "") -> Iterable[str]:
    if isinstance(value, str):
        if key_name in {"title", "subtitle", "message", "description", "label", "empty", "placeholder"}:
            yield value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings_from_value(item, key_name=str(key))
        return
    if isinstance(value, list):
        for item in value:
            yield from _strings_from_value(item, key_name=key_name)


def _walk_sections(
    sections: Sequence[Any],
    *,
    path: str,
    parent_surfaces: Sequence[str],
) -> Iterable[Tuple[str, Dict[str, Any], Tuple[str, ...]]]:
    for index, item in enumerate(sections):
        if not isinstance(item, dict):
            continue
        current_path = f"{path}[{index}]"
        primitive = str(item.get("primitive") or "").strip()
        yield current_path, item, tuple(parent_surfaces)
        next_surfaces = (
            tuple(parent_surfaces) + (primitive,)
            if primitive in SURFACE_PRIMITIVES
            else tuple(parent_surfaces)
        )
        children = _section_children(item)
        if children:
            yield from _walk_sections(
                children,
                path=f"{current_path}.config.children",
                parent_surfaces=next_surfaces,
            )


def audit_page_schemas(
    pages: Sequence[Dict[str, Any]],
    *,
    source_label: str = "AppPageSchema",
) -> List[str]:
    """Return blocking quality warnings for declarative YAML page schemas."""

    warnings: List[str] = []
    allowed_primitives = _page_primitive_names()

    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_name = str(page.get("name") or page.get("title") or f"page[{page_index}]")
        page_path = f"{source_label} '{page_name}'"
        primitive_counts: Dict[str, int] = {}

        title = str(page.get("title") or page.get("name") or "")
        if "dashboard" in title.lower():
            warnings.append(
                f"{page_path} uses dashboard-style page naming; use the actual product surface name."
            )

        for text in _strings_from_value(page):
            lower = text.lower()
            matched_copy_flags = [flag for flag in COPY_FLAGS if flag in lower]
            if matched_copy_flags:
                warnings.append(
                    f"{page_path} contains placeholder/internal copy ({', '.join(sorted(set(matched_copy_flags)))})."
                )
                break

        sections = page.get("sections")
        if not isinstance(sections, list):
            continue

        for section_path, section, parent_surfaces in _walk_sections(
            sections,
            path=f"{page_path}.sections",
            parent_surfaces=(),
        ):
            primitive = str(section.get("primitive") or "").strip()
            if not primitive:
                continue
            primitive_counts[primitive] = primitive_counts.get(primitive, 0) + 1

            if primitive in REMOVED_PRIMITIVES:
                warnings.append(
                    f"{section_path}.primitive uses removed primitive '{primitive}'; use PageHeader, ResourceTable, SummaryStrip, Panel, SurfaceCard, or StatusPill as appropriate."
                )

            if allowed_primitives is not None and primitive not in allowed_primitives:
                warnings.append(
                    f"{section_path}.primitive uses unknown primitive '{primitive}'."
                )

            if primitive in SURFACE_PRIMITIVES and parent_surfaces:
                warnings.append(
                    f"{section_path}.primitive nests {primitive} inside {parent_surfaces[-1]}; avoid cards/panels inside cards/panels."
                )

        summary_count = primitive_counts.get("SummaryStrip", 0)
        if summary_count > 1:
            warnings.append(
                f"{page_path} uses {summary_count} SummaryStrip sections; use at most one compact summary per page."
            )

        metric_count = primitive_counts.get("Metric", 0)
        if metric_count > 4:
            warnings.append(
                f"{page_path} uses {metric_count} Metric sections; avoid KPI-grid generated pages."
            )

        status_count = primitive_counts.get("StatusPill", 0)
        if status_count > 2:
            warnings.append(
                f"{page_path} uses {status_count} StatusPill sections; do not repeat status chips across the same page."
            )

        surface_count = primitive_counts.get("Panel", 0) + primitive_counts.get("SurfaceCard", 0)
        if surface_count > 3:
            warnings.append(
                f"{page_path} uses {surface_count} wrapper surfaces; remove decorative panels and keep only purposeful sections."
            )

    return dedupe(warnings)


def custom_route_bundle_page_files(custom_route_bundle: Any) -> List[Dict[str, Any]]:
    if not isinstance(custom_route_bundle, dict):
        return []
    page_files = custom_route_bundle.get("page_files")
    if not isinstance(page_files, list):
        return []
    return [item for item in page_files if isinstance(item, dict)]


__all__ = [
    "REMOVED_PRIMITIVES",
    "audit_generated_react_files",
    "audit_page_schemas",
    "custom_route_bundle_page_files",
    "dedupe",
]
