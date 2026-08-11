from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True)
class FunctionalGeneratedAppDiagnostic:
    code: str
    message: str
    path: str | None = None
    severity: Literal["error", "warning"] = "error"


_MODULE_ENDPOINT_RE = re.compile(r"^/api/modules/([^/?#]+)/([^/?#]+)$")
_MODULE_ENDPOINT_ANYWHERE_RE = re.compile(r"/api/modules/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_MODULE_ACTION_CALL_RE = re.compile(
    r"\bmoduleAction\s*\(\s*['\"](?P<module>[A-Za-z0-9_.-]+)['\"]\s*,\s*['\"](?P<action>[A-Za-z0-9_.-]+)['\"]"
)
_REGISTER_COMPONENT_RE = re.compile(
    r"\bregisterComponent\s*\(\s*['\"](?P<name>[A-Za-z_$][\w$.-]*)['\"]"
)
_PLACEHOLDER_RE = re.compile(
    r"\bNotImplementedError\b|\b[A-Z0-9_]*NOT_IMPLEMENTED[A-Z0-9_]*\b|\bstatus_code\s*=\s*501\b|\bHTTP_501_",
    re.IGNORECASE,
)

_BUILT_IN_ROUTE_COMPONENTS = {
    "SchemaPage",
    "DashboardPortalPage",
    "AdminPortal",
    "ProfilePage",
}


def _safe_path(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    normalized = raw.replace("\\", "/").strip()
    try:
        path = PurePosixPath(normalized)
    except Exception:
        return ""
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return ""
    return str(path)


def _safe_files(files: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_path, content in (files or {}).items():
        path = _safe_path(raw_path)
        if path:
            out[path] = str(content)
    return out


def _diag(
    code: str,
    message: str,
    *,
    path: str | None = None,
    severity: Literal["error", "warning"] = "error",
) -> FunctionalGeneratedAppDiagnostic:
    return FunctionalGeneratedAppDiagnostic(code=code, message=message, path=path, severity=severity)


def _load_json(path: str, content: str, diagnostics: list[FunctionalGeneratedAppDiagnostic]) -> Any:
    try:
        return json.loads(content)
    except Exception as exc:
        diagnostics.append(_diag("INVALID_JSON", f"{path} must be valid JSON: {exc}", path=path))
        return None


def _load_yaml(path: str, content: str, diagnostics: list[FunctionalGeneratedAppDiagnostic]) -> Any:
    try:
        return yaml.safe_load(content) or {}
    except Exception as exc:
        diagnostics.append(_diag("INVALID_YAML", f"{path} must be valid YAML: {exc}", path=path))
        return None


def _module_action_registry(
    files: dict[str, str],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> dict[str, dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for path, content in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if len(parts) != 3 or parts[0] != "modules" or parts[2] != "module.yaml":
            continue
        parsed = _load_yaml(path, content, diagnostics)
        if not isinstance(parsed, dict):
            continue
        module_block = parsed.get("module") if isinstance(parsed.get("module"), dict) else parsed
        module_id = str(module_block.get("id") if isinstance(module_block, dict) else "").strip() or parts[1]
        handler = str(module_block.get("handler") if isinstance(module_block, dict) else "").strip()
        if not module_id:
            diagnostics.append(_diag("MISSING_MODULE_ID", f"{path} must declare module.id.", path=path))
            continue
        for index, action in enumerate(parsed.get("actions") or []):
            if not isinstance(action, dict):
                diagnostics.append(
                    _diag("INVALID_MODULE_ACTION", f"{path} actions[{index}] must be an object.", path=path)
                )
                continue
            action_id = str(action.get("id") or "").strip()
            if not action_id:
                diagnostics.append(
                    _diag("MISSING_MODULE_ACTION_ID", f"{path} actions[{index}] must declare id.", path=path)
                )
                continue
            actions[f"{module_id}/{action_id}"] = {
                "module_id": module_id,
                "action_id": action_id,
                "handler": handler,
                "handler_method": str(action.get("handler_method") or "").strip(),
                "module_yaml": path,
            }
    return actions


def _validate_action_handlers(
    files: dict[str, str],
    actions: dict[str, dict[str, Any]],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> None:
    parsed_handlers: dict[str, ast.Module] = {}

    def parse_backend(path: str) -> ast.Module | None:
        tree = parsed_handlers.get(path)
        if tree is not None:
            return tree
        if path not in files:
            return None
        try:
            tree = ast.parse(files[path])
            parsed_handlers[path] = tree
            return tree
        except SyntaxError as exc:
            diagnostics.append(_diag("INVALID_PYTHON", f"{path} must parse as Python: {exc}", path=path))
            return None

    def class_methods(module_id: str, class_name: str, *, seen: set[str] | None = None) -> set[str] | None:
        seen = seen or set()
        if class_name in seen:
            return set()
        seen.add(class_name)
        methods: set[str] = set()
        class_node: ast.ClassDef | None = None
        for path in sorted(files):
            if not path.startswith(f"modules/{module_id}/backend/") or not path.endswith(".py"):
                continue
            tree = parse_backend(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    class_node = node
                    break
            if class_node is not None:
                break
        if class_node is None:
            return None
        methods.update(
            node.name
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        for base in class_node.bases:
            base_name = ""
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if not base_name:
                continue
            inherited = class_methods(module_id, base_name, seen=seen)
            if inherited:
                methods.update(inherited)
        return methods

    for action in actions.values():
        handler = action["handler"]
        module_id = action["module_id"]
        action_id = action["action_id"]
        handler_method = action["handler_method"]
        module_yaml = action["module_yaml"]
        if not handler or ":" not in handler:
            diagnostics.append(
                _diag(
                    "MISSING_MODULE_HANDLER",
                    f"{module_yaml} action {action_id!r} cannot resolve because module.handler is missing or invalid.",
                    path=module_yaml,
                )
            )
            continue
        if not handler_method:
            diagnostics.append(
                _diag(
                    "MISSING_MODULE_HANDLER_METHOD",
                    f"{module_yaml} action {action_id!r} must declare handler_method.",
                    path=module_yaml,
                )
            )
            continue
        handler_module, class_name = [part.strip() for part in handler.split(":", 1)]
        handler_path = f"modules/{module_id}/{handler_module.replace('.', '/')}.py"
        if handler_path not in files:
            diagnostics.append(
                _diag(
                    "MISSING_MODULE_HANDLER",
                    f"{module_yaml} action {action_id!r} declares handler {handler!r}, but {handler_path} is missing.",
                    path=handler_path,
                )
            )
            continue
        methods = class_methods(module_id, class_name)
        if methods is None:
            diagnostics.append(
                _diag(
                    "MISSING_MODULE_HANDLER",
                    f"{handler_path} does not define handler class {class_name!r}.",
                    path=handler_path,
                )
            )
            continue
        if handler_method not in methods:
            diagnostics.append(
                _diag(
                    "MISSING_MODULE_ACTION",
                    (
                        f"{module_yaml} action {action_id!r} declares handler_method "
                        f"{handler_method!r}, but {handler_path} class {class_name!r} does not implement it."
                    ),
                    path=handler_path,
                )
            )


def _route_manifest_pages(
    files: dict[str, str],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> list[dict[str, Any]]:
    content = files.get("ui/route_manifest.json")
    if content is None:
        return []
    parsed = _load_json("ui/route_manifest.json", content, diagnostics)
    if not isinstance(parsed, dict):
        return []
    pages = parsed.get("pages")
    if not isinstance(pages, list):
        diagnostics.append(
            _diag("INVALID_ROUTE_MANIFEST", "ui/route_manifest.json pages must be a list.", path="ui/route_manifest.json")
        )
        return []
    return [page for page in pages if isinstance(page, dict)]


def _page_schema_index(
    files: dict[str, str],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> set[str]:
    schemas: set[str] = set()
    for path, content in sorted(files.items()):
        pure = PurePosixPath(path)
        if len(pure.parts) < 3 or pure.parts[:2] != ("ui", "pages"):
            continue
        if pure.suffix.lower() not in {".yaml", ".yml", ".json"}:
            continue
        if "custom" in pure.parts:
            continue
        schemas.add(pure.stem)
        parsed = _load_yaml(path, content, diagnostics) if pure.suffix.lower() in {".yaml", ".yml"} else _load_json(path, content, diagnostics)
        if isinstance(parsed, dict):
            for key in ("id", "name", "page_id", "pageId"):
                value = str(parsed.get(key) or "").strip()
                if value:
                    schemas.add(value)
                    schemas.add(value.lower().replace(" ", "_").replace("-", "_"))
    return schemas


def _registered_components(files: dict[str, str]) -> set[str]:
    registered: set[str] = set()
    for path, content in files.items():
        if path == "ui/index.js" or path.endswith("/index.js") or path.endswith("/index.jsx"):
            registered.update(match.group("name") for match in _REGISTER_COMPONENT_RE.finditer(content))
    return registered


def _validate_routes(
    files: dict[str, str],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> None:
    pages = _route_manifest_pages(files, diagnostics)
    if not pages:
        return
    schemas = _page_schema_index(files, diagnostics)
    registered = _registered_components(files)
    custom_files = {
        PurePosixPath(path).stem
        for path in files
        if path.startswith("ui/pages/custom/") and PurePosixPath(path).suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}
    }
    for index, page in enumerate(pages):
        path = str(page.get("path") or page.get("route") or "").strip()
        component = str(page.get("component") or "").strip()
        route_label = f"ui/route_manifest.json pages[{index}]"
        if not path or not path.startswith("/"):
            diagnostics.append(
                _diag("INVALID_ROUTE", f"{route_label} must declare an app-relative path.", path="ui/route_manifest.json")
            )
        if not component:
            diagnostics.append(
                _diag("MISSING_ROUTE_COMPONENT", f"{route_label} path {path!r} must declare component.", path="ui/route_manifest.json")
            )
            continue
        if component == "SchemaPage":
            schema = str(page.get("schema") or page.get("page") or page.get("name") or "").strip()
            if not schema:
                diagnostics.append(
                    _diag(
                        "MISSING_SCHEMA_PAGE",
                        f"{route_label} path {path!r} uses SchemaPage but does not declare schema.",
                        path="ui/route_manifest.json",
                    )
                )
                continue
            normalized = schema.lower().replace(" ", "_").replace("-", "_")
            if schema not in schemas and normalized not in schemas:
                diagnostics.append(
                    _diag(
                        "MISSING_SCHEMA_PAGE",
                        f"{route_label} path {path!r} uses SchemaPage schema {schema!r}, but no matching ui/pages schema file exists.",
                        path="ui/route_manifest.json",
                    )
                )
            continue
        if component in _BUILT_IN_ROUTE_COMPONENTS:
            continue
        if component not in registered:
            diagnostics.append(
                _diag(
                    "MISSING_ROUTE_COMPONENT",
                    f"{route_label} path {path!r} references component {component!r}, but ui/index.js does not register it.",
                    path="ui/route_manifest.json",
                )
            )
        elif component not in custom_files and not any(
            re.search(rf"\b{re.escape(component)}\b", content)
            for route_path, content in files.items()
            if route_path == "ui/index.js" or route_path.endswith("/index.js") or route_path.endswith("/index.jsx")
        ):
            diagnostics.append(
                _diag(
                    "MISSING_ROUTE_COMPONENT",
                    f"{route_label} path {path!r} registers component {component!r}, but no generated component source is evident.",
                    path="ui/route_manifest.json",
                )
            )


def _walk_values(value: Any) -> list[Any]:
    out: list[Any] = []
    if isinstance(value, dict):
        for child in value.values():
            out.extend(_walk_values(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_walk_values(child))
    else:
        out.append(value)
    return out


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(value, dict):
        out.append(value)
        for child in value.values():
            out.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_walk_dicts(child))
    return out


def _collect_module_refs(
    files: dict[str, str],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> list[tuple[str, str, str, str]]:
    refs: list[tuple[str, str, str, str]] = []
    for path, content in sorted(files.items()):
        pure = PurePosixPath(path)
        if pure.suffix.lower() in {".yaml", ".yml", ".json"}:
            if path.endswith("module.yaml"):
                continue
            parsed = _load_yaml(path, content, diagnostics) if pure.suffix.lower() in {".yaml", ".yml"} else _load_json(path, content, diagnostics)
            if parsed is None:
                continue
            for item in _walk_dicts(parsed):
                module_id = str(item.get("module_id") or item.get("module") or "").strip()
                action_id = str(item.get("action_id") or item.get("action") or "").strip()
                if module_id and action_id:
                    refs.append((path, module_id, action_id, "module/action reference"))
            for value in _walk_values(parsed):
                if not isinstance(value, str):
                    continue
                parsed_url = urlsplit(value.strip())
                if parsed_url.query or parsed_url.fragment:
                    if value.strip().startswith("/api/modules/"):
                        diagnostics.append(
                            _diag(
                                "INVALID_MODULE_ENDPOINT",
                                f"{path} references module endpoint {value!r} with query string or fragment.",
                                path=path,
                            )
                        )
                    continue
                match = _MODULE_ENDPOINT_RE.match(parsed_url.path)
                if match:
                    refs.append((path, match.group(1), match.group(2), value.strip()))
            continue
        if pure.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
            for match in _MODULE_ACTION_CALL_RE.finditer(content):
                refs.append((path, match.group("module"), match.group("action"), "moduleAction(...)"))
            for match in _MODULE_ENDPOINT_ANYWHERE_RE.finditer(content):
                refs.append((path, match.group(1), match.group(2), match.group(0)))
    return refs


def _validate_module_refs(
    actions: dict[str, dict[str, Any]],
    refs: list[tuple[str, str, str, str]],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> None:
    for path, module_id, action_id, source in refs:
        key = f"{module_id}/{action_id}"
        if key not in actions:
            diagnostics.append(
                _diag(
                    "MISSING_MODULE_ACTION",
                    f"{path} references {source!r}, but module action {key!r} is not declared.",
                    path=path,
                )
            )


def _selected_capability_ids(capability_packs: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for pack in capability_packs or []:
        if not isinstance(pack, dict):
            continue
        for key in ("id", "capability_pack_id", "pack_id", "capability_id", "module_id"):
            value = str(pack.get(key) or "").strip()
            if value:
                ids.add(value)
    return ids


def _validate_mozaikspay_facade(
    files: dict[str, str],
    actions: dict[str, dict[str, Any]],
    capability_packs: list[dict[str, Any]],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> None:
    selected = _selected_capability_ids(capability_packs)
    if "mozaikspay" not in selected:
        return
    required_files = {
        "services/integrations/mozaikspay_client.py": "generated MozaiksPay-compatible client",
        "modules/billing_portal/module.yaml": "app-owned billing_portal facade module",
    }
    for path, label in required_files.items():
        if path not in files:
            diagnostics.append(
                _diag(
                    "CAPABILITY_FACADE_MISSING",
                    f"mozaikspay capability requires {label} at {path}.",
                    path=path,
                )
            )
    for action_id in ("list_plans", "get_subscription_status", "open_billing_portal"):
        if f"billing_portal/{action_id}" not in actions:
            diagnostics.append(
                _diag(
                    "CAPABILITY_FACADE_MISSING",
                    f"mozaikspay capability requires billing_portal action {action_id!r}.",
                    path="modules/billing_portal/module.yaml",
                )
            )


def _validate_placeholders(
    files: dict[str, str],
    diagnostics: list[FunctionalGeneratedAppDiagnostic],
) -> None:
    for path, content in sorted(files.items()):
        pure = PurePosixPath(path)
        if pure.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx", ".yaml", ".yml"}:
            continue
        if not (
            path.startswith("modules/")
            or path.startswith("services/")
            or path.startswith("ui/pages/")
            or path.startswith("workflows/")
        ):
            continue
        match = _PLACEHOLDER_RE.search(content)
        if match:
            diagnostics.append(
                _diag(
                    "PLACEHOLDER_IMPLEMENTATION",
                    f"{path} contains placeholder/not-implemented marker {match.group(0)!r}.",
                    path=path,
                )
            )


def scan_functional_generated_app(
    files: dict[str, str],
    *,
    capability_packs: list[dict[str, Any]] | None = None,
) -> list[FunctionalGeneratedAppDiagnostic]:
    """Validate cross-artifact functional coherence for a canonical app bundle.

    This intentionally stays deterministic and contract-driven. It checks
    canonical manifests and generated source references, but does not attempt to
    understand arbitrary Python or JavaScript control flow.
    """

    safe_files = _safe_files(files)
    diagnostics: list[FunctionalGeneratedAppDiagnostic] = []
    actions = _module_action_registry(safe_files, diagnostics)
    _validate_action_handlers(safe_files, actions, diagnostics)
    _validate_routes(safe_files, diagnostics)
    refs = _collect_module_refs(safe_files, diagnostics)
    _validate_module_refs(actions, refs, diagnostics)
    _validate_mozaikspay_facade(safe_files, actions, capability_packs or [], diagnostics)
    _validate_placeholders(safe_files, diagnostics)
    return diagnostics


__all__ = [
    "FunctionalGeneratedAppDiagnostic",
    "scan_functional_generated_app",
]
