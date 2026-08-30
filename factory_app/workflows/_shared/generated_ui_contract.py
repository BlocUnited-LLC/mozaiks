"""Deterministic quality checks for generated frontend surfaces.

This module is intentionally shared by AppGenerator and AgentGenerator so the
React and YAML lanes cannot drift into different UI standards.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - import failures are surfaced by tests.
    yaml = None  # type: ignore[assignment]

from mozaiksai.core.runtime.app.page_schema import (
    VALID_PAGE_TYPES,
    PageSchemaValidationError,
    validate_page_schema,
)

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
VALID_CUSTOM_ROUTE_EXTENSIONS = {".jsx"}
YAML_PAGE_EXTENSIONS = {".yaml", ".yml"}

REMOVED_COLOR_PATTERNS = (
    re.compile(r"\bbg-(gray|slate|zinc|neutral|stone|white|black)-"),
    re.compile(r"\btext-(gray|slate|zinc|neutral|stone|white|black)-"),
    re.compile(r"\bborder-(gray|slate|zinc|neutral|stone|white|black)-"),
)
HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
COLOR_FUNCTION_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\(", re.IGNORECASE)
FONT_BINARY_EXTENSIONS = {".otf", ".ttf", ".woff", ".woff2"}
LOCAL_VISUAL_SYSTEM_RE = re.compile(
    r"\b(?:const|let|var)\s+"
    r"(?:palette|colors|theme|brandColors|brandPalette|THEME|COLORS|PALETTE)"
    r"\s*=\s*\{",
    re.IGNORECASE,
)
LOCAL_PRIMITIVE_CLONE_RE = re.compile(
    r"\b(?:function|const|let|var)\s+(StatusPill|MetricTile|StatCard|Badge)\b"
)
RAW_PRIMARY_BUTTON_RE = re.compile(
    r"<button\b[^>]*className\s*=\s*(?:"
    r"\"[^\"]*\bbg-primary\b[^\"]*\""
    r"|'[^']*\bbg-primary\b[^']*'"
    r"|`[^`]*\bbg-primary\b[^`]*`"
    r"|\{\s*`[^`]*\bbg-primary\b[^`]*`\s*\}"
    r")",
    re.DOTALL,
)
# Module API endpoint format: /api/modules/{module}/{action} with no extras.
# Page section api_endpoint values must match this pattern — no query strings,
# no extra path segments, no direct hosted-module or external routes.
_API_ENDPOINT_RE = re.compile(r"^/api/modules/[^/?#]+/[^/?#]+$")

CLASS_LITERAL_RE = re.compile(
    r"className\s*=\s*(?:"
    r"\"(?P<double>[^\"]*)\""
    r"|'(?P<single>[^']*)'"
    r"|`(?P<tick>[^`]*)`"
    r"|\{\s*`(?P<brace>[^`]*)`\s*\}"
    r")",
    re.DOTALL,
)
PUBLIC_IMPORT_RE = re.compile(
    r"import\s*\{(?P<specifiers>[^}]+)\}\s*from\s*['\"]@mozaiks/chat-ui/ui['\"]"
)
REGISTER_COMPONENT_RE = re.compile(
    r"\bregisterComponent\s*\(\s*['\"`](?P<name>[A-Za-z_$][\w$.-]*)['\"`]",
)
IMPORT_BINDING_RE = re.compile(
    r"import\s+(?P<default>[A-Za-z_$][\w$]*)\s+from\s+['\"](?P<default_source>[^'\"]+)['\"]"
    r"|import\s+\{(?P<named>[^}]+)\}\s+from\s+['\"](?P<named_source>[^'\"]+)['\"]",
)
LOCAL_COMPONENT_DEF_RE = re.compile(
    r"\b(?:function|class|const|let|var)\s+(?P<name>[A-Z][A-Za-z0-9_]*)\b",
)
ADMIN_ROUTE_OWNERSHIP_KEYS = {
    "component",
    "component_name",
    "custom_component",
    "file",
    "import",
    "page_file",
    "registry_key",
    "renderer",
}


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _page_primitive_names() -> set[str] | None:
    if get_page_ui_primitive_names is None:
        return None
    try:
        return set(get_page_ui_primitive_names())
    except Exception:
        return None


def _parse_public_imports(content: str) -> list[str]:
    imported: list[str] = []
    for match in PUBLIC_IMPORT_RE.finditer(content):
        for raw in match.group("specifiers").split(","):
            token = raw.strip()
            if not token:
                continue
            if " as " in token:
                token = token.split(" as ", 1)[0].strip()
            imported.append(token)
    return imported


def _class_literals(content: str) -> list[str]:
    literals: list[str] = []
    for match in CLASS_LITERAL_RE.finditer(content):
        for group_name in ("double", "single", "tick", "brace"):
            value = match.group(group_name)
            if value:
                literals.append(value)
                break
    return literals


def _looks_like_local_card_shell(class_names: str) -> bool:
    normalized = " ".join(class_names.split())
    has_rounded_panel = "rounded-" in normalized or "rounded[" in normalized
    has_border = "border" in normalized
    has_surface_background = any(
        token in normalized
        for token in ("bg-card", "bg-background", "bg-muted")
    )
    return has_rounded_panel and has_border and has_surface_background


def _file_name(item: dict[str, Any]) -> str:
    return str(item.get("filename") or item.get("path") or "").strip()


def _bundle_file_map(code_files: Sequence[dict[str, Any]]) -> dict[str, str]:
    files: dict[str, str] = {}
    for item in code_files:
        if not isinstance(item, dict):
            continue
        filename = _file_name(item).replace("\\", "/")
        content = item.get("content")
        if not filename or not isinstance(content, str):
            continue
        normalized_path = PurePosixPath(filename)
        lower_parts = {part.lower() for part in normalized_path.parts}
        if "docs" in lower_parts or "tests" in lower_parts:
            continue
        files[filename] = content
    return files


def _react_file_items(
    code_files: Sequence[dict[str, Any]],
    *,
    include_ui_index: bool,
) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for item in code_files:
        if not isinstance(item, dict):
            continue
        filename = _file_name(item).replace("\\", "/")
        content = item.get("content")
        if not filename or not isinstance(content, str):
            continue
        normalized_path = PurePosixPath(filename)
        lower_parts = {part.lower() for part in normalized_path.parts}
        # Ignore docs/tests fixtures even if they include JSX snippets.
        if "docs" in lower_parts or "tests" in lower_parts:
            continue
        suffix = normalized_path.suffix.lower()
        if suffix not in {".js", ".jsx"}:
            continue
        if not include_ui_index and normalized_path.name == "index.js":
            continue
        files.append((filename, content))
    return files


def _route_manifest_pages_from_content(content: str, *, source_label: str) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return [], [f"{source_label} route_manifest.json is not valid JSON: {exc.msg}."]
    if not isinstance(data, dict):
        return [], [f"{source_label} route_manifest.json must be an object."]
    pages = data.get("pages")
    if pages is None:
        pages = data.get("routes")
    if pages is None:
        return [], []
    if not isinstance(pages, list):
        return [], [f"{source_label} route_manifest.json pages must be a list."]
    return [page for page in pages if isinstance(page, dict)], []


def _parse_registered_components(source: str) -> set[str]:
    return {match.group("name") for match in REGISTER_COMPONENT_RE.finditer(source)}


def _parse_imports_and_local_definitions(
    source: str,
    *,
    filename: str,
) -> tuple[set[str], dict[str, str]]:
    bindings: set[str] = set()
    import_sources: dict[str, str] = {}
    base_dir = PurePosixPath(filename).parent
    for match in IMPORT_BINDING_RE.finditer(source):
        default_name = match.group("default")
        if default_name:
            bindings.add(default_name)
            source_path = str(match.group("default_source") or "")
            if source_path.startswith("."):
                import_sources[default_name] = _resolve_relative_import(base_dir, source_path)
            continue
        named = match.group("named")
        if not named:
            continue
        source_path = str(match.group("named_source") or "")
        for raw in named.split(","):
            token = raw.strip()
            if not token:
                continue
            local_name = token.split(" as ", 1)[-1].strip()
            source_name = token.split(" as ", 1)[0].strip()
            binding_name = local_name or source_name
            if binding_name:
                bindings.add(binding_name)
                if source_path.startswith("."):
                    import_sources[binding_name] = _resolve_relative_import(base_dir, source_path)
    bindings.update(match.group("name") for match in LOCAL_COMPONENT_DEF_RE.finditer(source))
    return bindings, import_sources


def _resolve_relative_import(base_dir: PurePosixPath, source_path: str) -> str:
    resolved = base_dir.joinpath(source_path)
    parts: list[str] = []
    for part in resolved.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    normalized = PurePosixPath(*parts)
    if normalized.suffix:
        return normalized.as_posix()
    return normalized.with_suffix(".jsx").as_posix()


def _import_path_exists(import_path: str, file_names: set[str]) -> bool:
    if import_path in file_names:
        return True
    path = PurePosixPath(import_path)
    if path.suffix:
        return False
    for suffix in (".jsx", ".js", ".tsx", ".ts"):
        if path.with_suffix(suffix).as_posix() in file_names:
            return True
    return False


def _parse_admin_registry(content: str, *, source_label: str) -> tuple[dict[str, Any] | None, list[str]]:
    if yaml is None:
        return None, [f"{source_label} admin_registry.yaml could not be parsed because PyYAML is unavailable."]
    try:
        data = yaml.safe_load(content) or {}
    except Exception as exc:
        return None, [f"{source_label} admin_registry.yaml is not valid YAML: {exc}."]
    if not isinstance(data, dict):
        return None, [f"{source_label} admin_registry.yaml must be an object."]
    return data, []


def _parse_yaml_mapping_content(
    content: str,
    *,
    source_label: str,
    file_label: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if yaml is None:
        return None, [f"{source_label} {file_label} could not be parsed because PyYAML is unavailable."]
    try:
        data = yaml.safe_load(content) or {}
    except Exception as exc:
        return None, [f"{source_label} {file_label} is not valid YAML: {exc}."]
    if not isinstance(data, dict):
        return None, [f"{source_label} {file_label} must be an object."]
    return data, []


def _is_module_contract_path(filename: str) -> bool:
    path = PurePosixPath(filename)
    return len(path.parts) == 3 and path.parts[0] == "modules" and path.parts[2] == "module.yaml"


def _module_contracts_from_files(
    files: dict[str, str],
    *,
    source_label: str,
) -> tuple[dict[str, dict[str, Any]], bool, list[str]]:
    contracts: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    saw_module_contract = False

    for filename, content in sorted(files.items()):
        if not _is_module_contract_path(filename):
            continue
        saw_module_contract = True
        data, parse_warnings = _parse_yaml_mapping_content(
            content,
            source_label=source_label,
            file_label=filename,
        )
        warnings.extend(parse_warnings)
        if not isinstance(data, dict):
            continue

        module_block = data.get("module") if isinstance(data.get("module"), dict) else data
        module_id = str(module_block.get("id") if isinstance(module_block, dict) else "").strip()
        if not module_id:
            parts = PurePosixPath(filename).parts
            module_id = parts[1] if len(parts) > 1 else ""
        if not module_id:
            warnings.append(f"{source_label} {filename} must declare module.id or top-level id.")
            continue

        actions = data.get("actions") or []
        if not isinstance(actions, list):
            warnings.append(f"{source_label} {filename}.actions must be a list.")
            actions = []

        action_ids: set[str] = set()
        for action in actions:
            if isinstance(action, dict):
                action_id = str(action.get("id") or action.get("name") or "").strip()
            elif isinstance(action, str):
                action_id = action.strip()
            else:
                continue
            if action_id:
                action_ids.add(action_id)

        if module_id in contracts:
            warnings.append(
                f"{source_label} {filename} duplicates module id '{module_id}' already declared by {contracts[module_id]['path']}."
            )
            contracts[module_id]["actions"].update(action_ids)
            continue
        contracts[module_id] = {"path": filename, "actions": action_ids}

    return contracts, saw_module_contract, warnings


def _route_auth_reference(route_auth: Any, *, path: str) -> dict[str, str] | None:
    if not isinstance(route_auth, dict):
        return None
    module_id = str(route_auth.get("module") or "").strip()
    action_id = str(route_auth.get("action") or "").strip()
    if not module_id or not action_id:
        return None
    return {"path": path, "module": module_id, "action": action_id}


def _audit_route_auth_action_contracts(
    route_auth_refs: Sequence[dict[str, str]],
    module_contracts: dict[str, dict[str, Any]],
    *,
    saw_module_contract: bool,
    source_label: str,
) -> list[str]:
    if not saw_module_contract:
        return []

    warnings: list[str] = []
    for ref in route_auth_refs:
        module_id = ref["module"]
        action_id = ref["action"]
        contract = module_contracts.get(module_id)
        if contract is None:
            warnings.append(
                f"{source_label} {ref['path']} references module '{module_id}' but no generated module contract with that id was found."
            )
            continue
        if action_id not in contract["actions"]:
            warnings.append(
                f"{source_label} {ref['path']} references action '{module_id}/{action_id}' but {contract['path']} does not declare that action."
            )
    return warnings


def _yaml_page_files(files: dict[str, str]) -> list[tuple[str, str]]:
    page_files: list[tuple[str, str]] = []
    for filename, content in sorted(files.items()):
        path = PurePosixPath(filename)
        if not filename.startswith("ui/pages/"):
            continue
        if path.suffix.lower() not in YAML_PAGE_EXTENSIONS:
            continue
        page_files.append((filename, content))
    return page_files


def audit_generated_react_files(
    code_files: Sequence[dict[str, Any]],
    *,
    source_label: str = "generated React",
    require_jsx: bool = False,
    include_ui_index: bool = False,
) -> list[str]:
    """Return blocking quality warnings for generated/custom React files."""

    warnings: list[str] = []
    for item in code_files:
        if not isinstance(item, dict):
            continue
        filename = _file_name(item).replace("\\", "/")
        if not filename:
            continue
        normalized_path = PurePosixPath(filename)
        lower_parts = {part.lower() for part in normalized_path.parts}
        if "docs" in lower_parts or "tests" in lower_parts:
            continue
        if (
            normalized_path.suffix.lower() in FONT_BINARY_EXTENSIONS
            and not filename.startswith("brand/fonts/")
        ):
            warnings.append(
                f"{filename} places a local font outside brand/fonts; generated font binaries must live under app/brand/fonts and be referenced through /fonts/."
            )

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

        local_primitive_clones = sorted(set(LOCAL_PRIMITIVE_CLONE_RE.findall(content)))
        if local_primitive_clones:
            warnings.append(
                f"{filename} defines local visual primitive clones ({', '.join(local_primitive_clones)}); import shared primitives from @mozaiks/chat-ui/ui instead."
            )

        if LOCAL_VISUAL_SYSTEM_RE.search(content):
            warnings.append(
                f"{filename} defines a page-local palette/theme object; express visual intent through theme_config, semantic classes, and shared primitive variants instead."
            )

        if RAW_PRIMARY_BUTTON_RE.search(content):
            warnings.append(
                f"{filename} renders a raw primary-styled <button>; use the shared Button primitive with a semantic variant."
            )

        local_card_shell_count = sum(
            1
            for class_names in _class_literals(content)
            if _looks_like_local_card_shell(class_names)
        )
        if local_card_shell_count > 2:
            warnings.append(
                f"{filename} reimplements {local_card_shell_count} local card/panel shells; use SurfaceCard, Panel, ResourceList, or SummaryStrip."
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

        if HEX_COLOR_RE.search(content) or COLOR_FUNCTION_RE.search(content):
            warnings.append(
                f"{filename} hardcodes color values; use semantic theme tokens instead."
            )

        for pattern in REMOVED_COLOR_PATTERNS:
            if pattern.search(content):
                warnings.append(
                    f"{filename} uses removed color utility classes; use semantic theme tokens instead."
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

        # Layout shell contract: admin/pages/ files are workspace/app Studio
        # surfaces and must use WorkspaceLayout or AppStudioLayout (from
        # @mozaiks/chat-ui/workspace), NOT PageFrame.
        # ui/pages/custom/ files are user-facing app pages and must use
        # PageFrame (from @mozaiks/chat-ui), NOT WorkspaceLayout.
        is_admin_page = "admin/pages/" in filename
        is_custom_route = "ui/pages/custom/" in filename
        uses_page_frame = "PageFrame" in content
        uses_workspace_layout = "WorkspaceLayout" in content or "AppStudioLayout" in content

        if is_admin_page and uses_page_frame and not uses_workspace_layout:
            warnings.append(
                f"{filename} is a workspace/app Studio page but uses PageFrame; "
                "use WorkspaceLayout (or AppStudioLayout) from @mozaiks/chat-ui/workspace so the sidebar renders."
            )

        if is_custom_route and uses_workspace_layout and not uses_page_frame:
            warnings.append(
                f"{filename} is a user-facing custom route but uses WorkspaceLayout; "
                "use PageFrame from @mozaiks/chat-ui instead."
            )

    return dedupe(warnings)


def _section_children(section: dict[str, Any]) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
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
) -> Iterable[tuple[str, dict[str, Any], tuple[str, ...]]]:
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


def _audit_runtime_page_contract(page: dict[str, Any], *, page_path: str) -> list[str]:
    try:
        validate_page_schema(page)
    except PageSchemaValidationError as exc:
        return [
            f"{page_path} violates mozaiks.app_page.v1 at {diagnostic.location}: {diagnostic.code}."
            for diagnostic in exc.diagnostics
        ]
    return []


def audit_page_schemas(
    pages: Sequence[dict[str, Any]],
    *,
    source_label: str = "AppPageSchema",
) -> list[str]:
    """Return blocking quality warnings for declarative YAML page schemas."""

    warnings: list[str] = []
    allowed_primitives = _page_primitive_names()

    for page_index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_name = str(page.get("name") or page.get("title") or f"page[{page_index}]")
        page_path = f"{source_label} '{page_name}'"
        primitive_counts: dict[str, int] = {}
        warnings.extend(_audit_runtime_page_contract(page, page_path=page_path))

        meta = page.get("meta")
        if meta is not None and not isinstance(meta, dict):
            warnings.append(f"{page_path}.meta must be an object or null.")
        elif isinstance(meta, dict):
            warnings.extend(_audit_route_auth(meta.get("routeAuth"), path=f"{page_path}.meta.routeAuth"))

        page_type = page.get("page_type")
        if not page_type:
            warnings.append(
                f"{page_path} is missing required 'page_type' field."
            )
        elif page_type not in VALID_PAGE_TYPES:
            warnings.append(
                f"{page_path} has invalid page_type '{page_type}'; must be one of: {', '.join(sorted(VALID_PAGE_TYPES))}."
            )

        if "extensions" in page:
            # Retired contract: page slot extensions were never rendered by
            # PageRenderer and are no longer part of AppPageSchema.  Custom UI
            # zones belong to custom_route_bundle pages.
            warnings.append(
                f"{page_path} declares 'extensions', a retired unsupported field. "
                "Remove it; use a custom_route_bundle page when primitives cannot "
                "express the route."
            )

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

            # Validate api_endpoint format: must be /api/modules/{module}/{action}.
            # Check both the section-level field and the nested config field.
            for _ep in (
                section.get("api_endpoint"),
                (section.get("config") or {}).get("api_endpoint"),
            ):
                if isinstance(_ep, str) and _ep.strip().startswith("/api/"):
                    if not _API_ENDPOINT_RE.match(_ep.strip()):
                        warnings.append(
                            f"{section_path}.api_endpoint '{_ep}' does not match "
                            "the required pattern '/api/modules/{{module}}/{{action}}'. "
                            "Module API endpoints must use exactly this path format "
                            "without query strings or extra path segments."
                        )

        # Wizard pages must contain at least one Form section.
        # A wizard without a Form has no submission path to a module action and
        # violates the archetype contract: ProgressTracker + Form + ActionButton.
        # Only fire when sections are present; an empty sections list is a
        # degenerate/stub schema that passes the type-enum check but is not a
        # real page — the Form check cannot apply to it.
        if page_type == "wizard" and primitive_counts and primitive_counts.get("Form", 0) == 0:
            warnings.append(
                f"{page_path} has page_type='wizard' but no Form section; "
                "wizard pages must include at least one Form section with a "
                "submit_action that binds to an app-owned module action."
            )

        # checkout_success pages must be custom React routes, not YAML primitives.
        # Post-payment redirect flows require query-param access (session_id,
        # order_id) and polling for payment confirmation — capabilities that
        # YAML primitive pages cannot express. Use custom_route_bundle instead.
        if page_type == "checkout_success" and isinstance(sections, list) and sections:
            warnings.append(
                f"{page_path} has page_type='checkout_success' with sections; "
                "checkout_success pages must be custom React routes declared via "
                "custom_route_bundle, not YAML primitive pages. Remove sections "
                "and declare this page through custom_route_bundle in the build plan."
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


def custom_route_bundle_page_files(custom_route_bundle: Any) -> list[dict[str, Any]]:
    if not isinstance(custom_route_bundle, dict):
        return []
    page_files = custom_route_bundle.get("page_files")
    if not isinstance(page_files, list):
        return []
    return [item for item in page_files if isinstance(item, dict)]


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _audit_route_auth(route_auth: Any, *, path: str) -> list[str]:
    if route_auth is None:
        return []
    warnings: list[str] = []
    if not isinstance(route_auth, dict):
        return [f"{path} must be an object or null."]
    if not _non_empty_string(route_auth.get("module")):
        warnings.append(f"{path}.module is required.")
    if not _non_empty_string(route_auth.get("action")):
        warnings.append(f"{path}.action is required.")
    params = route_auth.get("params")
    if params is not None and not isinstance(params, dict):
        warnings.append(f"{path}.params must be an object or null.")
    elif isinstance(params, dict):
        for key in params:
            if not _non_empty_string(key):
                warnings.append(f"{path}.params keys must be non-empty strings.")
                break
    return warnings


def audit_custom_route_bundle_integrity(
    custom_route_bundle: Any,
    *,
    app_manifest: dict[str, Any] | None = None,
    source_label: str = "custom_route_bundle",
) -> list[str]:
    """Return route/component registry warnings for custom full-page routes.

    AppGenerator persists custom routes from two agent-authored lists:
    route_manifest declares shell routes, while page_files declares the custom
    React implementations. The runtime only works when each route component key
    is registered by exactly one page file, so this audit keeps those lists from
    drifting before assembly.
    """

    if custom_route_bundle is None:
        return []
    if not isinstance(custom_route_bundle, dict):
        return [f"{source_label} must be an object."]

    warnings: list[str] = []
    route_manifest = custom_route_bundle.get("route_manifest")
    raw_page_files = custom_route_bundle.get("page_files")

    if not isinstance(route_manifest, list) or not route_manifest:
        warnings.append(f"{source_label}.route_manifest must be a non-empty list.")
        route_manifest = []
    if not isinstance(raw_page_files, list) or not raw_page_files:
        warnings.append(f"{source_label}.page_files must be a non-empty list.")
        raw_page_files = []

    route_by_id: dict[str, dict[str, Any]] = {}
    route_ids: set[str] = set()
    route_paths: set[str] = set()
    route_components: set[str] = set()

    for index, route in enumerate(route_manifest):
        path = f"{source_label}.route_manifest[{index}]"
        if not isinstance(route, dict):
            warnings.append(f"{path} must be an object.")
            continue

        route_id = str(route.get("id") or "").strip()
        route_path = str(route.get("path") or "").strip()
        component = str(route.get("component") or "").strip()

        if not route_id:
            warnings.append(f"{path}.id is required.")
        elif route_id in route_ids:
            warnings.append(f"{path}.id '{route_id}' is duplicated.")
        else:
            route_ids.add(route_id)
            route_by_id[route_id] = route

        if not _non_empty_string(route.get("label")):
            warnings.append(f"{path}.label is required.")

        if not route_path:
            warnings.append(f"{path}.path is required.")
        elif not route_path.startswith("/"):
            warnings.append(f"{path}.path must start with '/'.")
        elif route_path in route_paths:
            warnings.append(f"{path}.path '{route_path}' is duplicated.")
        else:
            route_paths.add(route_path)

        if not component:
            warnings.append(f"{path}.component is required.")
        elif component in route_components:
            warnings.append(f"{path}.component '{component}' is duplicated.")
        else:
            route_components.add(component)

        if not isinstance(route.get("requiresAuth"), bool):
            warnings.append(f"{path}.requiresAuth must be a boolean.")

        meta = route.get("meta")
        if meta is not None and not isinstance(meta, dict):
            warnings.append(f"{path}.meta must be an object or null.")
        elif isinstance(meta, dict):
            warnings.extend(_audit_route_auth(meta.get("routeAuth"), path=f"{path}.meta.routeAuth"))

        if not _non_empty_string(route.get("purpose")):
            warnings.append(f"{path}.purpose must explain why YAML primitives cannot own this route.")

    files_by_route_id: dict[str, list[dict[str, Any]]] = {}
    file_paths: set[str] = set()
    registry_keys: set[str] = set()

    for index, page_file in enumerate(raw_page_files):
        path = f"{source_label}.page_files[{index}]"
        if not isinstance(page_file, dict):
            warnings.append(f"{path} must be an object.")
            continue

        route_id = str(page_file.get("route_id") or "").strip()
        file_path = str(page_file.get("path") or "").strip().replace("\\", "/")
        component_name = str(page_file.get("component_name") or "").strip()
        registry_key = str(page_file.get("registry_key") or "").strip()
        content = page_file.get("content")

        if not route_id:
            warnings.append(f"{path}.route_id is required.")
        elif route_id not in route_by_id:
            warnings.append(f"{path}.route_id '{route_id}' does not match any route_manifest id.")
        else:
            files_by_route_id.setdefault(route_id, []).append(page_file)

        if not file_path:
            warnings.append(f"{path}.path is required.")
        else:
            if not file_path.startswith("ui/pages/custom/"):
                warnings.append(f"{path}.path must live under ui/pages/custom/.")
            suffix = PurePosixPath(file_path).suffix.lower()
            if suffix not in VALID_CUSTOM_ROUTE_EXTENSIONS:
                warnings.append(f"{path}.path must use .jsx for custom route React.")
            if file_path in file_paths:
                warnings.append(f"{path}.path '{file_path}' is duplicated.")
            else:
                file_paths.add(file_path)

        if not component_name:
            warnings.append(f"{path}.component_name is required.")

        if not registry_key:
            warnings.append(f"{path}.registry_key is required.")
        else:
            if registry_key in registry_keys:
                warnings.append(f"{path}.registry_key '{registry_key}' is duplicated.")
            registry_keys.add(registry_key)
            owning_route = route_by_id.get(route_id)
            if owning_route and registry_key != owning_route.get("component"):
                warnings.append(
                    f"{path}.registry_key '{registry_key}' must match route_manifest component '{owning_route.get('component')}'."
                )

        if not _non_empty_string(page_file.get("purpose")):
            warnings.append(f"{path}.purpose is required.")
        if not isinstance(content, str) or not content.strip():
            warnings.append(f"{path}.content is required.")
        elif "export default" not in content:
            warnings.append(f"{path}.content must export a default React component.")

    for route_id, route in route_by_id.items():
        component = str(route.get("component") or "").strip()
        matching_files = [
            item
            for item in files_by_route_id.get(route_id, [])
            if str(item.get("registry_key") or "").strip() == component
        ]
        if not matching_files:
            warnings.append(
                f"{source_label}.route_manifest route '{route_id}' declares component '{component}' but no page_files entry registers that key."
            )
        elif len(matching_files) > 1:
            warnings.append(
                f"{source_label}.route_manifest route '{route_id}' has multiple page_files entries for component '{component}'."
            )

    if isinstance(app_manifest, dict):
        declared_custom_routes = app_manifest.get("custom_routes")
        if isinstance(declared_custom_routes, list):
            manifest_ids = [str(item).strip() for item in declared_custom_routes if str(item).strip()]
            if set(manifest_ids) != set(route_by_id):
                warnings.append(
                    f"manifest.custom_routes must match {source_label}.route_manifest ids exactly."
                )

        default_route = app_manifest.get("default_route")
        if _non_empty_string(default_route) and route_by_id:
            page_routes = {
                str(route.get("path") or "").strip()
                for route in route_by_id.values()
                if _non_empty_string(route.get("path"))
            }
            manifest_pages = app_manifest.get("pages")
            has_declarative_pages = isinstance(manifest_pages, list) and bool(manifest_pages)
            if not has_declarative_pages and str(default_route).strip() not in page_routes:
                warnings.append(
                    "manifest.default_route must match a custom route path when no declarative pages are emitted."
                )

    return dedupe(warnings)


def audit_app_ui_bundle_integrity(
    code_files: Sequence[dict[str, Any]],
    *,
    source_label: str = "app UI bundle",
) -> list[str]:
    """Return route/component/admin registry drift warnings for app UI files.

    This is a lightweight static audit over generated or staged app files. It
    validates the emitted runtime contract without executing application code:
    `ui/route_manifest.json` owns custom full-page route declarations,
    `ui/index.js` (and any additional registration barrel included in the file
    set) registers those component keys, and `admin/admin_registry.yaml` stays
    limited to admin page metadata.
    """

    files = _bundle_file_map(code_files)
    if not files:
        return []

    warnings: list[str] = []
    file_names = set(files)
    route_pages: list[dict[str, Any]] = []
    route_auth_refs: list[dict[str, str]] = []

    route_manifest_content = files.get("ui/route_manifest.json")
    if route_manifest_content is not None:
        route_pages, manifest_warnings = _route_manifest_pages_from_content(
            route_manifest_content,
            source_label=source_label,
        )
        warnings.extend(manifest_warnings)

    module_contracts, saw_module_contract, module_warnings = _module_contracts_from_files(
        files,
        source_label=source_label,
    )
    warnings.extend(module_warnings)

    for filename, content in _yaml_page_files(files):
        page_schema, page_warnings = _parse_yaml_mapping_content(
            content,
            source_label=source_label,
            file_label=filename,
        )
        warnings.extend(page_warnings)
        if not isinstance(page_schema, dict):
            continue
        meta = page_schema.get("meta")
        if meta is not None and not isinstance(meta, dict):
            warnings.append(f"{source_label} {filename}.meta must be an object or null.")
            continue
        if not isinstance(meta, dict):
            continue
        route_auth = meta.get("routeAuth")
        warnings.extend(
            f"{source_label} {warning}"
            for warning in _audit_route_auth(route_auth, path=f"{filename}.meta.routeAuth")
        )
        ref = _route_auth_reference(route_auth, path=f"{filename}.meta.routeAuth")
        if ref is not None:
            route_auth_refs.append(ref)

    registry_files = {
        filename: content
        for filename, content in files.items()
        if filename.endswith("index.js") or filename.endswith("index.jsx")
    }
    registered_components: dict[str, list[str]] = {}
    available_bindings: dict[str, set[str]] = {}
    import_sources: dict[str, dict[str, str]] = {}

    for filename, source in registry_files.items():
        for component in _parse_registered_components(source):
            registered_components.setdefault(component, []).append(filename)
        bindings, imports = _parse_imports_and_local_definitions(source, filename=filename)
        available_bindings[filename] = bindings
        import_sources[filename] = imports

    route_components: dict[str, set[str]] = {}
    for index, page in enumerate(route_pages):
        route_path = str(page.get("path") or page.get("route") or "").strip()
        component = str(page.get("component") or "").strip()
        label = f"route_manifest.json pages[{index}]"
        meta = page.get("meta")
        if meta is not None and not isinstance(meta, dict):
            warnings.append(f"{source_label} {label}.meta must be an object or null.")
        elif isinstance(meta, dict):
            warnings.extend(
                f"{source_label} {warning}"
                for warning in _audit_route_auth(meta.get("routeAuth"), path=f"{label}.meta.routeAuth")
            )
            ref = _route_auth_reference(meta.get("routeAuth"), path=f"{label}.meta.routeAuth")
            if ref is not None:
                route_auth_refs.append(ref)
        if not component:
            continue
        route_components.setdefault(component, set()).add(route_path or "<missing path>")
        if component not in registered_components:
            warnings.append(
                f"{source_label} {label} path '{route_path or '<missing path>'}' references component '{component}' but no registerComponent('{component}', ...) call was found in ui/index.js."
            )

    for component, registry_paths in registered_components.items():
        for registry_path in registry_paths:
            bindings = available_bindings.get(registry_path, set())
            imports = import_sources.get(registry_path, {})
            if component not in bindings:
                warnings.append(
                    f"{source_label} {registry_path} registers component '{component}' but does not import or define a binding with that name."
                )
                continue
            import_path = imports.get(component)
            if import_path and not _import_path_exists(import_path, file_names):
                warnings.append(
                    f"{source_label} {registry_path} registers component '{component}' from missing file '{import_path}'."
                )

    custom_page_files = [
        filename
        for filename in files
        if filename.startswith("ui/pages/custom/")
        and PurePosixPath(filename).suffix.lower() in VALID_CUSTOM_ROUTE_EXTENSIONS
    ]
    for filename in custom_page_files:
        component_name = PurePosixPath(filename).stem
        if component_name not in registered_components:
            warnings.append(
                f"{source_label} custom page file '{filename}' exports component '{component_name}' but that component is not registered in ui/index.js."
            )

    for component, paths in route_components.items():
        if component not in registered_components:
            continue
        has_route_file = any(
            PurePosixPath(filename).stem == component
            and filename.startswith("ui/pages/custom/")
            for filename in custom_page_files
        )
        has_import = any(component in imports for imports in import_sources.values())
        if not has_route_file and not has_import:
            warnings.append(
                f"{source_label} route component '{component}' for path(s) {', '.join(sorted(paths))} has no matching custom page file or registry import."
            )

    admin_registry_content = files.get("admin/admin_registry.yaml")
    if admin_registry_content is not None:
        admin_registry, admin_warnings = _parse_admin_registry(
            admin_registry_content,
            source_label=source_label,
        )
        warnings.extend(admin_warnings)
        if isinstance(admin_registry, dict):
            if "route_manifest" in admin_registry or "custom_routes" in admin_registry:
                warnings.append(
                    f"{source_label} admin/admin_registry.yaml declares custom route ownership; use ui/route_manifest.json + ui/index.js for full-page custom routes."
                )
            if "panels" in admin_registry:
                warnings.append(
                    f"{source_label} admin/admin_registry.yaml declares top-level panels; module admin panels belong in modules/*/contracts/admin.yaml and reference admin registry page ids."
                )
            pages = admin_registry.get("pages")
            if pages is not None and not isinstance(pages, list):
                warnings.append(f"{source_label} admin/admin_registry.yaml pages must be a list.")
                pages = []
            for index, page in enumerate(pages or []):
                if not isinstance(page, dict):
                    warnings.append(f"{source_label} admin/admin_registry.yaml pages[{index}] must be an object.")
                    continue
                page_id = str(page.get("id") or f"pages[{index}]")
                forbidden_keys = sorted(set(page) & ADMIN_ROUTE_OWNERSHIP_KEYS)
                if forbidden_keys:
                    warnings.append(
                        f"{source_label} admin/admin_registry.yaml page '{page_id}' uses unsupported custom route ownership fields ({', '.join(forbidden_keys)}); full-page React routes belong in ui/route_manifest.json and ui/index.js."
                    )
                page_path = str(page.get("path") or "")
                page_scope = str(page.get("scope") or "app")
                surfaces = page.get("surfaces")
                explicit_host_surface = isinstance(surfaces, list) and bool(surfaces)
                if (
                    page_scope == "app"
                    and page_path.startswith("/apps/:appId")
                    and not explicit_host_surface
                ):
                    warnings.append(
                        f"{source_label} admin/admin_registry.yaml page '{page_id}' uses hosted Studio path '{page_path}'. Standard generated-app AdminPortal pages must use /admin paths; /apps/:appId/... belongs to first-party Studio or explicit hosted-operator surfaces."
                    )

    warnings.extend(
        _audit_route_auth_action_contracts(
            route_auth_refs,
            module_contracts,
            saw_module_contract=saw_module_contract,
            source_label=source_label,
        )
    )

    return dedupe(warnings)


__all__ = [
    "REMOVED_PRIMITIVES",
    "audit_app_ui_bundle_integrity",
    "audit_custom_route_bundle_integrity",
    "audit_generated_react_files",
    "audit_page_schemas",
    "custom_route_bundle_page_files",
    "dedupe",
]
