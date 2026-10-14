"""Compose admitted app/pack UI declarations into the existing runtime roots.

Only static imports, re-exports and registerComponent calls are admitted here.
Component implementations remain opaque, unchanged files; JavaScript is parsed,
never executed. This is a build-time projection, not another runtime registry.
"""
from __future__ import annotations

import ast
import json
import posixpath
import re
from dataclasses import dataclass
from typing import Any

from mozaiksai.core.app_context.context_graph import _tree_sitter_parser
from mozaiksai.core.runtime.app.auth_contract import APP_AUTH_COMPONENTS

_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")


@dataclass(frozen=True)
class ComponentRegistration:
    key: str
    path: str
    export: str
    options: tuple[tuple[str, str | bool], ...] = ()


def _text(node: Any) -> str:
    return node.text.decode("utf-8") if node is not None else ""


def _children(node: Any) -> list[Any]:
    return [child for child in node.named_children if child.type != "comment"]


def _parse(source: str, label: str) -> Any:
    parser = _tree_sitter_parser("javascript")
    if parser is None:
        raise ValueError("UI composition requires the installed JavaScript parser")
    root = parser.parse(source.encode("utf-8")).root_node
    if root.has_error:
        raise ValueError(f"{label}: invalid JavaScript")
    return root


def _string(node: Any) -> str:
    if node is None or node.type != "string":
        raise ValueError("UI declarations require literal strings")
    try:
        value = ast.literal_eval(_text(node))
    except (SyntaxError, ValueError) as exc:
        raise ValueError("Invalid UI string literal") from exc
    if not isinstance(value, str):
        raise ValueError("UI declarations require literal strings")
    return value


def _target(source: str, base: str, files: dict[str, str]) -> str:
    if not source.startswith(".") or any(char in source for char in ("\\", ":", "?", "#", "\x00")):
        raise ValueError(f"UI import must be an app-local relative path: {source!r}")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), source))
    if path.startswith("../") or path.startswith("/"):
        raise ValueError(f"UI import escapes the app: {source!r}")
    candidates = [path] if posixpath.splitext(path)[1] else [path + suffix for suffix in (".jsx", ".js")]
    existing = [candidate for candidate in candidates if candidate in files]
    if len(existing) != 1:
        raise ValueError(f"UI import must resolve to one existing file: {source!r}")
    return existing[0]


def _import_bindings(node: Any, files: dict[str, str], base: str = "ui/index.js") -> dict[str, tuple[str, str]]:
    path = _target(_string(node.child_by_field_name("source")), base, files)
    clause = next((child for child in _children(node) if child.type == "import_clause"), None)
    if clause is None:
        raise ValueError("Side-effect imports are not UI registration declarations")
    bindings: dict[str, tuple[str, str]] = {}
    for child in _children(clause):
        if child.type == "identifier":
            bindings[_text(child)] = (path, "default")
        elif child.type == "named_imports":
            for specifier in _children(child):
                name = _text(specifier.child_by_field_name("name"))
                alias = _text(specifier.child_by_field_name("alias")) or name
                if not _IDENTIFIER.fullmatch(name) or not _IDENTIFIER.fullmatch(alias):
                    raise ValueError("UI named imports require identifier bindings")
                bindings[alias] = (path, name)
        else:
            raise ValueError("UI registrations support default and named imports only")
    return bindings


def _has_export(path: str, name: str, files: dict[str, str], seen: frozenset[tuple[str, str]] = frozenset()) -> bool:
    if (path, name) in seen:
        raise ValueError(f"Cyclic UI export: {path}:{name}")
    seen = seen | {(path, name)}
    root = _parse(files[path], path)

    def local_binding(binding: str) -> bool:
        for statement in _children(root):
            declaration = statement.child_by_field_name("declaration") if statement.type == "export_statement" else statement
            if declaration is not None:
                if declaration.type in {"function_declaration", "class_declaration"} and _text(declaration.child_by_field_name("name")) == binding:
                    return True
                if declaration.type in {"lexical_declaration", "variable_declaration"} and any(
                    _text(child.child_by_field_name("name")) == binding for child in _children(declaration)
                ):
                    return True
        for statement in _children(root):
            if statement.type == "import_statement" and re.search(rf"\b{re.escape(binding)}\b", _text(statement).split(" from ", 1)[0]):
                target = _import_bindings(statement, files, path).get(binding)
                if target is not None:
                    return _has_export(*target, files, seen)
        return False

    star_forwarding = False
    for node in _children(root):
        if node.type != "export_statement":
            continue
        if name == "default" and any(child.type == "default" for child in node.children):
            value = node.child_by_field_name("value")
            return local_binding(_text(value)) if value is not None and value.type == "identifier" else True
        declaration = node.child_by_field_name("declaration")
        if declaration is not None:
            if _text(declaration.child_by_field_name("name")) == name:
                return True
            if declaration.type in {"lexical_declaration", "variable_declaration"}:
                if any(_text(child.child_by_field_name("name")) == name for child in _children(declaration)):
                    return True
        source = node.child_by_field_name("source")
        if any(child.type == "namespace_export" for child in _children(node)):
            # `export * as Namespace` exports the namespace object, not the
            # named components of its source module.
            continue
        clause = next((child for child in _children(node) if child.type == "export_clause"), None)
        if clause is not None:
            for specifier in _children(clause):
                original = _text(specifier.child_by_field_name("name"))
                # tree-sitter exposes the keyword `default` as an unnamed child.
                if not original and any(child.type == "default" for child in specifier.children):
                    original = "default"
                exported = _text(specifier.child_by_field_name("alias")) or original
                if exported == name:
                    return local_binding(original) if source is None else _has_export(_target(_string(source), path, files), original, files, seen)
        elif source is not None and name != "default":
            star_forwarding = True
    if star_forwarding:
        raise ValueError(f"{path}: component export-star forwarding is unsupported; use an explicit named export")
    return False


def _options(node: Any) -> tuple[tuple[str, str | bool], ...]:
    if node.type != "object":
        raise ValueError("Registration options must be a literal object")
    result: dict[str, str | bool] = {}
    for pair in _children(node):
        if pair.type != "pair":
            raise ValueError("Registration options cannot contain spreads or methods")
        key_node, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
        key = _string(key_node) if key_node.type == "string" else _text(key_node)
        if key in result or key not in {"description", "core", "override"}:
            raise ValueError(f"Unsupported or repeated registration option: {key}")
        if key == "description":
            result[key] = _string(value)
        elif value.type in {"true", "false"}:
            result[key] = value.type == "true"
        else:
            raise ValueError(f"Registration option {key} must be a literal boolean")
    return tuple(sorted(result.items()))


def _registrations(source: str, files: dict[str, str], owner: str) -> tuple[list[ComponentRegistration], dict[str, tuple[str, str]]]:
    root = _parse(source, f"{owner}:ui/index.js")
    imports: dict[str, tuple[str, str]] = {}
    reexports: dict[str, tuple[str, str]] = {}
    register = None
    for node in _children(root):
        if node.type == "import_statement":
            incoming = _import_bindings(node, files)
            if imports.keys() & incoming.keys():
                raise ValueError(f"{owner}: repeated import binding")
            imports.update(incoming)
        elif node.type == "export_statement":
            declaration = node.child_by_field_name("declaration")
            if declaration is not None and declaration.type == "function_declaration" and _text(declaration.child_by_field_name("name")) == "register":
                if register is not None:
                    raise ValueError(f"{owner}: repeated register function")
                register = declaration
            elif node.child_by_field_name("source") is not None:
                path = _target(_string(node.child_by_field_name("source")), "ui/index.js", files)
                clause = next((child for child in _children(node) if child.type == "export_clause"), None)
                if clause is None:
                    raise ValueError("UI barrel re-exports must name their bindings")
                for specifier in _children(clause):
                    original = _text(specifier.child_by_field_name("name"))
                    if not original and any(child.type == "default" for child in specifier.children):
                        original = "default"
                    exported = _text(specifier.child_by_field_name("alias")) or original
                    if not _IDENTIFIER.fullmatch(exported) or exported == "register" or exported in reexports:
                        raise ValueError("Invalid or repeated UI barrel export")
                    reexports[exported] = (path, original)
            else:
                raise ValueError(f"{owner}: unsupported UI barrel export")
        else:
            raise ValueError(f"{owner}: unsupported executable UI barrel statement: {node.type}")
    registrations: list[ComponentRegistration] = []
    keys: set[str] = set()
    used: set[str] = set()
    if register is not None:
        parameters = _children(register.child_by_field_name("parameters"))
        if parameters and (len(parameters) != 1 or _text(parameters[0]) != "registerComponent"):
            raise ValueError("UI register must accept registerComponent")
        for statement in _children(register.child_by_field_name("body")):
            # The existing deterministic generator emits this guard and a literal diagnostic.
            if statement.type == "if_statement" and re.fullmatch(
                r"if\s*\(\s*typeof\s+registerComponent\s*!==\s*(['\"])function\1\s*\)\s*(?:return\s*;?|\{\s*return\s*;?\s*\})",
                _text(statement),
            ):
                continue
            children = _children(statement)
            call = children[0] if statement.type == "expression_statement" and len(children) == 1 else None
            if call is None or call.type != "call_expression":
                raise ValueError(f"{owner}: register body must contain static registrations")
            function = _text(call.child_by_field_name("function"))
            arguments = _children(call.child_by_field_name("arguments"))
            if function == "console.info" and all(argument.type == "string" for argument in arguments):
                continue
            if function != "registerComponent" or len(arguments) not in {2, 3} or not parameters:
                raise ValueError(f"{owner}: unsupported UI registration call")
            key, binding = _string(arguments[0]), _text(arguments[1])
            if not key or key in keys or arguments[1].type != "identifier" or binding not in imports:
                raise ValueError(f"{owner}: repeated key or unresolved registration binding: {key!r}")
            keys.add(key)
            used.add(binding)
            path, export = imports[binding]
            registrations.append(ComponentRegistration(key, path, export, _options(arguments[2]) if len(arguments) == 3 else ()))
    if imports.keys() != used:
        raise ValueError(f"{owner}: UI barrel contains unused imports")
    for registration in registrations:
        if not _has_export(registration.path, registration.export, files):
            raise ValueError(f"{owner}: missing component export {registration.path}:{registration.export}")
    for path, export in reexports.values():
        if not _has_export(path, export, files):
            raise ValueError(f"{owner}: missing barrel export {path}:{export}")
    return registrations, reexports


def compose_ui_registry(contributions: list[dict[str, Any]], files: dict[str, str]) -> dict[str, str]:
    """Compose shared roots from admitted worker files and verified pack files.

    Owners identify input for diagnostics, never authorize it. The caller admits
    workers and verifies pack digests before invoking this function.
    """
    registrations: dict[str, set[tuple[str, bool, ComponentRegistration]]] = {}
    reexports: dict[str, tuple[str, str]] = {}
    export_owners: dict[str, set[str]] = {}
    routes: dict[str, dict[str, Any]] = {}
    route_paths: dict[str, str] = {}
    route_owners: dict[str, set[str]] = {}
    route_sequence: set[tuple[str, str]] = set()
    manifest_metadata: dict[str, Any] = {}
    has_index = has_manifest = False
    for contribution in contributions:
        owner = str(contribution["owner"])
        baseline = contribution.get("baseline") is True
        contents = {item["filename"]: item["content"] for item in contribution["files"]}
        if "ui/index.js" in contents:
            has_index = True
            entries, exports = _registrations(contents["ui/index.js"], files, owner)
            for entry in entries:
                registrations.setdefault(entry.key, set()).add((owner, baseline, entry))
            for name, binding in exports.items():
                if name in reexports and reexports[name] != binding:
                    raise ValueError(f"Conflicting UI barrel export: {name}")
                reexports[name] = binding
                if not baseline:
                    export_owners.setdefault(name, set()).add(owner)
                    if len(export_owners[name]) > 1:
                        raise ValueError(f"Competing owners for UI barrel export: {name}")
        if "ui/route_manifest.json" not in contents:
            continue
        has_manifest = True
        manifest = json.loads(contents["ui/route_manifest.json"])
        if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list):
            raise ValueError(f"{owner}: route manifest must declare pages")
        for key, value in manifest.items():
            if key != "pages":
                if key in manifest_metadata and manifest_metadata[key] != value:
                    raise ValueError(f"Conflicting route manifest metadata: {key}")
                manifest_metadata[key] = value
        sequence = []
        for route in manifest["pages"]:
            if not isinstance(route, dict) or not all(isinstance(route.get(key), str) and route[key] for key in ("id", "path", "component")):
                raise ValueError(f"{owner}: routes require id, path and component")
            route_id, path = route["id"], route["path"]
            if route_id in sequence:
                raise ValueError(f"{owner}: repeated route id: {route_id}")
            sequence.append(route_id)
            if not path.startswith("/"):
                raise ValueError(f"{owner}: route paths must start with /")
            if route_id in routes and routes[route_id] != route:
                raise ValueError(f"Conflicting route id: {route_id}")
            if path in route_paths and route_paths[path] != route_id:
                raise ValueError(f"Conflicting route path: {path}")
            if not baseline:
                route_owners.setdefault(route_id, set()).add(owner)
                if len(route_owners[route_id]) > 1:
                    raise ValueError(f"Competing owners for route: {route_id}")
            routes[route_id], route_paths[path] = route, route_id
        route_sequence.update(zip(sequence, sequence[1:], strict=False))
    selected: dict[str, ComponentRegistration] = {}
    for key, records in registrations.items():
        entries = {entry for _, _, entry in records}
        active_owners = {owner for owner, baseline, _ in records if not baseline}
        overrides = {entry for entry in entries if dict(entry.options).get("override") is True}
        if len(entries) == 1:
            if len(active_owners) > 1:
                raise ValueError(f"Competing owners for component registration: {key}")
            selected[key] = next(iter(entries))
        elif len(overrides) == 1:
            override = next(iter(overrides))
            if len({owner for owner, baseline, entry in records if not baseline and entry == override}) > 1:
                raise ValueError(f"Competing override owners for component registration: {key}")
            selected[key] = override
        else:
            raise ValueError(f"Conflicting component registration: {key}")
    for route in routes.values():
        if route["component"] not in selected and route["component"] not in APP_AUTH_COMPONENTS:
            raise ValueError(f"Unregistered route component: {route['component']}")
    result: dict[str, str] = {}
    if has_index:
        imports, calls = [], []
        for index, (key, entry) in enumerate(sorted(selected.items())):
            binding = f"UiComponent{index}"
            specifier = binding if entry.export == "default" else f"{{ {entry.export} as {binding} }}"
            imports.append(f"import {specifier} from {json.dumps('./' + posixpath.relpath(entry.path, 'ui'))};")
            calls.append(f"  registerComponent({json.dumps(key)}, {binding}, {json.dumps(dict(entry.options), sort_keys=True, ensure_ascii=False)});")
        exports = [
            f"export {{ {export} as {name} }} from {json.dumps('./' + posixpath.relpath(path, 'ui'))};"
            for name, (path, export) in sorted(reexports.items())
        ]
        result["ui/index.js"] = "\n".join([
            *imports, *exports, "", "export function register(registerComponent) {",
            '  if (typeof registerComponent !== "function") return;', *calls, "}", "",
        ])
    if has_manifest:
        # Preserve each authored array's sequence. Independent contributions
        # have no shared array order; their explicit navigation order breaks ties.
        def order_key(route_id: str) -> tuple[int | float, str]:
            route = routes[route_id]
            navigation = route.get("navigation") or {}
            order = route.get("order", navigation.get("order", 0))
            if isinstance(order, bool) or not isinstance(order, (int, float)):
                raise ValueError(f"Route order must be numeric: {route_id}")
            return order, route_id

        pending = set(routes)
        ordered = []
        while pending:
            ready = [route_id for route_id in pending if not any(right == route_id and left in pending for left, right in route_sequence)]
            if not ready:
                raise ValueError("Conflicting route array order")
            route_id = min(ready, key=order_key)
            ordered.append(routes[route_id])
            pending.remove(route_id)
        result["ui/route_manifest.json"] = json.dumps({**manifest_metadata, "pages": ordered}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return result
