"""Compose admitted app/pack UI declarations into the existing runtime roots.

Only static imports, re-exports and registerComponent calls are admitted here.
Component implementations remain opaque, unchanged files; JavaScript is parsed,
never executed. This is a build-time projection, not another runtime registry.
"""
from __future__ import annotations

import ast
import json
import logging
import posixpath
import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from mozaiksai.core.app_context.context_graph import _tree_sitter_parser
from mozaiksai.core.runtime.app.auth_contract import APP_AUTH_COMPONENTS

logger = logging.getLogger(__name__)

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


def _candidates(source: str, base: str) -> list[str]:
    if not source.startswith(".") or any(char in source for char in ("\\", ":", "?", "#", "\x00")):
        raise ValueError(f"UI import must be an app-local relative path: {source!r}")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), source))
    if path.startswith("../") or path.startswith("/"):
        raise ValueError(f"UI import escapes the app: {source!r}")
    return [path] if posixpath.splitext(path)[1] else [path + suffix for suffix in (".jsx", ".js")]


def _target(source: str, base: str, files: dict[str, str]) -> str:
    existing = [candidate for candidate in _candidates(source, base) if candidate in files]
    if len(existing) != 1:
        raise ValueError(f"UI import must resolve to one existing file: {source!r}")
    return existing[0]


def _tombstone(source: str, base: str, files: dict[str, str], deleted: frozenset[str]) -> str | None:
    """The deleted file an import names, when it names only files the admitted change deleted."""
    candidates = _candidates(source, base)
    if any(candidate in files for candidate in candidates):
        return None
    return next((candidate for candidate in candidates if candidate in deleted), None)


def _import_names(node: Any) -> dict[str, str]:
    clause = next((child for child in _children(node) if child.type == "import_clause"), None)
    if clause is None:
        raise ValueError("Side-effect imports are not UI registration declarations")
    bindings: dict[str, str] = {}
    for child in _children(clause):
        if child.type == "identifier":
            binding = _text(child)
            if binding in bindings:
                raise ValueError(f"UI declarations contain repeated import binding: {binding}")
            bindings[binding] = "default"
        elif child.type == "named_imports":
            for specifier in _children(child):
                name = _text(specifier.child_by_field_name("name"))
                alias = _text(specifier.child_by_field_name("alias")) or name
                if not _IDENTIFIER.fullmatch(name) or not _IDENTIFIER.fullmatch(alias):
                    raise ValueError("UI named imports require identifier bindings")
                if alias in bindings:
                    raise ValueError(f"UI declarations contain repeated import binding: {alias}")
                bindings[alias] = name
        else:
            raise ValueError("UI registrations support default and named imports only")
    return bindings


def _import_bindings(node: Any, files: dict[str, str], base: str = "ui/index.js") -> dict[str, tuple[str, str]]:
    path = _target(_string(node.child_by_field_name("source")), base, files)
    return {binding: (path, export) for binding, export in _import_names(node).items()}


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


def _registrations(
    source: str, files: dict[str, str], owner: str, deleted: frozenset[str] = frozenset(),
) -> tuple[list[ComponentRegistration], dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """Parse one barrel; `deleted` lets an app barrel set aside registrations of deleted files.

    The third result maps each set-aside key to its deleted file and import source.
    """
    root = _parse(source, f"{owner}:ui/index.js")
    imports: dict[str, tuple[str, str]] = {}
    tombstoned: dict[str, tuple[str, str]] = {}
    removed: dict[str, tuple[str, str]] = {}
    reexports: dict[str, tuple[str, str]] = {}
    register = None
    for node in _children(root):
        if node.type == "import_statement":
            # Only app barrels receive `deleted`: their registration of a file
            # the admitted change deleted is set aside, not resolved.
            import_source = _string(node.child_by_field_name("source"))
            removed_file = _tombstone(import_source, "ui/index.js", files, deleted) if deleted else None
            incoming: dict[str, tuple[str, str]] = {} if removed_file else _import_bindings(node, files)
            names = set(_import_names(node)) if removed_file else set(incoming)
            if (imports.keys() | tombstoned.keys()) & names:
                raise ValueError(f"{owner}: repeated import binding")
            imports.update(incoming)
            if removed_file:
                tombstoned.update(dict.fromkeys(names, (removed_file, import_source)))
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
            if not key or key in keys or arguments[1].type != "identifier" or binding not in imports.keys() | tombstoned.keys():
                raise ValueError(f"{owner}: repeated key or unresolved registration binding: {key!r}")
            keys.add(key)
            used.add(binding)
            if binding in tombstoned:
                removed[key] = tombstoned[binding]
                continue
            path, export = imports[binding]
            registrations.append(ComponentRegistration(key, path, export, _options(arguments[2]) if len(arguments) == 3 else ()))
    if imports.keys() | tombstoned.keys() != used:
        raise ValueError(f"{owner}: UI barrel contains unused imports")
    for registration in registrations:
        if not _has_export(registration.path, registration.export, files):
            raise ValueError(f"{owner}: missing component export {registration.path}:{registration.export}")
    for path, export in reexports.values():
        if not _has_export(path, export, files):
            raise ValueError(f"{owner}: missing barrel export {path}:{export}")
    return registrations, reexports, removed


def _manifest(source: str, owner: str) -> dict[str, Any]:
    manifest = json.loads(source)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list):
        raise ValueError(f"{owner}: route manifest must declare pages")
    for route in manifest["pages"]:
        if not isinstance(route, dict) or not all(isinstance(route.get(key), str) and route[key] for key in ("id", "path", "component")):
            raise ValueError(f"{owner}: routes require id, path and component")
    return manifest


_Declaration = tuple[str, bool, dict[str, str], list[ComponentRegistration], dict[str, tuple[str, str]], dict[str, Any] | None]


@dataclass(frozen=True)
class _RevisionBaseline:
    entries: frozenset[ComponentRegistration]
    exports: frozenset[tuple[str, tuple[str, str]]]
    routes: dict[str, list[dict[str, Any]]]
    sequence: list[str]
    metadata: dict[str, Any]


def _apply_admitted_overlay(
    declarations: list[_Declaration], overlay_owners: set[str], revision: _RevisionBaseline | None,
) -> list[_Declaration]:
    """Let the newest admitted app layer replace older app declarations.

    Without a pack, last-writer-wins lets the accumulated repair overlay replace
    snapshot and task roots. Here it replaces only the component keys, barrel
    exports, route ids, relative route positions and manifest metadata that it
    declares. Patched task declarations keep their owner, so task/task and
    task/pack collisions still fail; pack declarations are never patched.
    In a revision a current task takes only the overlay values that differ from
    the verified baseline. An unchanged copy, such as a full root an earlier
    repair or the auth scaffold saved, carries no admitted change: it still
    replaces older snapshot values, but it can neither revert a task's approved
    change nor make competing task declarations look carried.
    """
    entries: dict[str, ComponentRegistration] = {}
    exports: dict[str, tuple[str, str]] = {}
    routes: dict[str, dict[str, Any]] = {}
    sequence: list[str] = []
    metadata: dict[str, Any] = {}
    restated: set[tuple[str, str]] = set()
    task_positioned: set[str] = set()
    for owner, _, _, overlay_entries, overlay_exports, manifest in declarations:
        if owner not in overlay_owners:
            continue
        for entry in overlay_entries:
            if entries.setdefault(entry.key, entry) != entry:
                raise ValueError(f"Conflicting component registration: {entry.key}")
            if revision is not None and entry in revision.entries:
                restated.add(("component", entry.key))
        for name, binding in overlay_exports.items():
            if exports.setdefault(name, binding) != binding:
                raise ValueError(f"Conflicting UI barrel export: {name}")
            if revision is not None and (name, binding) in revision.exports:
                restated.add(("export", name))
        for key, value in (manifest or {}).items():
            if key == "pages":
                continue
            if metadata.setdefault(key, value) != value:
                raise ValueError(f"Conflicting route manifest metadata: {key}")
            if revision is not None and key in revision.metadata and revision.metadata[key] == value:
                restated.add(("metadata", key))
        pages = (manifest or {}).get("pages", [])
        for route in pages:
            if routes.setdefault(route["id"], route) != route:
                raise ValueError(f"Conflicting route id: {route['id']}")
            if route["id"] not in sequence:
                sequence.append(route["id"])
            if revision is not None and route in revision.routes.get(route["id"], []):
                restated.add(("route", route["id"]))
            else:
                task_positioned.add(route["id"])
        # A task's relative order yields only to an overlay order that differs from the baseline's.
        known = [route["id"] for route in pages if revision is not None and route["id"] in revision.routes]
        if revision is None or known != [route_id for route_id in revision.sequence if route_id in known]:
            task_positioned.update(route["id"] for route in pages)
    patched: list[_Declaration] = []
    for owner, baseline, contents, own_entries, own_exports, manifest in declarations:
        if owner not in overlay_owners and not owner.startswith("pack:"):
            # A carried snapshot takes every overlay value; a current task keeps
            # its own value wherever the overlay merely restates the baseline.
            keeps = set() if baseline else restated
            own_entries = [
                entries[entry.key] if entry.key in entries and ("component", entry.key) not in keeps else entry
                for entry in own_entries
            ]
            own_exports = {
                name: exports[name] if name in exports and ("export", name) not in keeps else binding
                for name, binding in own_exports.items()
            }
            if manifest is not None:
                pages = list(manifest["pages"])
                own_routes = {route["id"]: route for route in pages}
                moved: Collection[str] = routes.keys() if baseline else task_positioned
                slots = [index for index, route in enumerate(pages) if route["id"] in moved]
                for index, route_id in zip(slots, sorted((pages[index]["id"] for index in slots), key=sequence.index), strict=True):
                    pages[index] = own_routes[route_id] if ("route", route_id) in keeps else routes[route_id]
                manifest = {**{
                    key: metadata[key] if key in metadata and ("metadata", key) not in keeps else value
                    for key, value in manifest.items()
                }, "pages": pages}
        patched.append((owner, baseline, contents, own_entries, own_exports, manifest))
    return patched


def compose_ui_registry(
    contributions: list[dict[str, Any]], files: dict[str, str], *, revision_baseline: dict[str, str] | None = None,
    deleted_paths: Collection[str] = (),
) -> dict[str, str]:
    """Compose shared roots from admitted worker files and verified pack files.

    The caller admits workers, verifies pack digests, and reads revision_baseline
    from the bound immutable archive. Current snapshots cannot grant carryover.
    A contribution marked ``admitted_overlay`` is the accumulated admitted repair
    layer. ``deleted_paths`` are admitted tombstones, already absent from files;
    app declarations (never pack ones) drop registrations of those files.
    """
    deleted = frozenset(deleted_paths)
    declarations: list[_Declaration] = []
    set_aside: list[dict[str, tuple[str, str]]] = []
    overlay_owners: set[str] = set()
    baseline_entries: set[ComponentRegistration] = set()
    baseline_exports: set[tuple[str, tuple[str, str]]] = set()
    baseline_routes: dict[str, list[dict[str, Any]]] = {}
    baseline_sequence: list[str] = []
    baseline_metadata: dict[str, Any] = {}
    if revision_baseline:
        if "ui/index.js" in revision_baseline:
            entries, exports, _ = _registrations(revision_baseline["ui/index.js"], revision_baseline, "revision baseline")
            baseline_entries.update(entries)
            baseline_exports.update(exports.items())
        if "ui/route_manifest.json" in revision_baseline:
            saved_manifest = _manifest(revision_baseline["ui/route_manifest.json"], "revision baseline")
            baseline_metadata.update((key, value) for key, value in saved_manifest.items() if key != "pages")
            for route in saved_manifest["pages"]:
                baseline_routes.setdefault(route["id"], []).append(route)
                baseline_sequence.append(route["id"])
    for contribution in contributions:
        owner = str(contribution["owner"])
        overlay = contribution.get("admitted_overlay") is True
        if overlay and owner.startswith("pack:"):
            raise ValueError(f"{owner}: a pack contribution cannot be the admitted app overlay")
        if overlay:
            overlay_owners.add(owner)
        # The overlay is carried: it never claims an active owner.
        baseline = contribution.get("baseline") is True or overlay
        contents = {item["filename"]: item["content"] for item in contribution["files"]}
        entries, exports, removed = (
            _registrations(contents["ui/index.js"], files, owner, frozenset() if owner.startswith("pack:") else deleted)
            if "ui/index.js" in contents else ([], {}, {})
        )
        manifest = _manifest(contents["ui/route_manifest.json"], owner) if "ui/route_manifest.json" in contents else None
        declarations.append((owner, baseline, contents, entries, exports, manifest))
        set_aside.append(removed)
    # Admitted deletions are newer than task output, so a task's stale copy of a
    # registration drops like a carried one. A task that writes the deleted file
    # itself is current page work: only an admitted barrel that no longer
    # registers the file supersedes it, otherwise the conflict fails here.
    repair_barrels = [
        removed for (owner, _, contents, *_), removed in zip(declarations, set_aside, strict=True)
        if owner in overlay_owners and "ui/index.js" in contents
    ]
    for (owner, baseline, contents, *_), removed in zip(declarations, set_aside, strict=True):
        for key, (path, source) in sorted(removed.items()):
            if not baseline and path in contents and not any(barrel.get(key, ("", ""))[0] != path for barrel in repair_barrels):
                raise ValueError(
                    f"ui/index.js: UI import must resolve to one existing file: {source!r}; {owner} re-emits {path}, "
                    "which an admitted repair deleted, and no admitted ui/index.js drops its registration"
                )
    revision = _RevisionBaseline(
        frozenset(baseline_entries), frozenset(baseline_exports), baseline_routes, baseline_sequence, baseline_metadata,
    ) if revision_baseline else None
    declarations = _apply_admitted_overlay(declarations, overlay_owners, revision) if overlay_owners else declarations
    # Composition is additive: an omitted carried declaration stays. Only an
    # admitted deletion of its component file removes an app registration, and
    # the app routes to that component leave with it. Pack declarations never
    # drop; they, and app declarations of files absent without an admitted
    # deletion, still fail on a missing file or unregistered component.
    registered = {entry.key for _, _, _, entries, _, _ in declarations for entry in entries}
    dropped_components = {key for removed in set_aside for key in removed} - registered
    if dropped_components:
        dropped_routes = {
            (route["id"], owner) for owner, _, _, _, _, manifest in declarations
            if manifest is not None and not owner.startswith("pack:")
            for route in manifest["pages"] if route["component"] in dropped_components
        }
        owners = {owner for _, owner in dropped_routes} | {
            owner for (owner, *_), removed in zip(declarations, set_aside, strict=True) if removed.keys() & dropped_components
        }
        logger.info(
            "UI composition dropped registrations and routes of admitted-deleted files: components=%s routes=%s owners=%s",
            sorted(dropped_components), sorted({route_id for route_id, _ in dropped_routes}), sorted(owners),
        )

    # Only admitted app declarations supersede saved app snapshots. Verified
    # pack declarations remain current owners, including when carried by a task.
    app_declarations = [item for item in declarations if not item[1] and not item[0].startswith("pack:")]
    changed_components = {entry.key for _, _, _, entries, _, _ in app_declarations for entry in entries if entry not in baseline_entries}
    changed_exports = {name for _, _, _, _, exports, _ in app_declarations for name, binding in exports.items() if (name, binding) not in baseline_exports}
    changed_routes = {route["id"] for _, _, _, _, _, manifest in app_declarations if manifest is not None for route in manifest["pages"] if route not in baseline_routes.get(route["id"], [])}
    reordered_routes = set()
    for _, _, _, _, _, manifest in app_declarations:
        if manifest is None:
            continue
        sequence = [route["id"] for route in manifest["pages"] if route["id"] in baseline_routes]
        if sequence != [route_id for route_id in baseline_sequence if route_id in sequence]:
            reordered_routes.update(sequence)
        for route in manifest["pages"]:
            order = (route.get("order"), (route.get("navigation") or {}).get("order"))
            if any(order != (prior.get("order"), (prior.get("navigation") or {}).get("order")) for prior in baseline_routes.get(route["id"], [])):
                reordered_routes.add(route["id"])

    registrations: dict[str, set[tuple[str, bool, ComponentRegistration]]] = {}
    reexports: dict[str, tuple[str, str]] = {}
    export_owners: dict[str, set[str]] = {}
    routes: dict[str, dict[str, Any]] = {}
    route_paths: dict[str, str] = {}
    route_owners: dict[str, set[str]] = {}
    route_sequence: set[tuple[str, str]] = set()
    manifest_metadata: dict[str, Any] = {}
    has_index = has_manifest = False
    for owner, baseline, contents, entries, exports, manifest in declarations:
        app_owner = not owner.startswith("pack:")
        if "ui/index.js" in contents:
            has_index = True
            for entry in entries:
                carried = baseline or (app_owner and entry in baseline_entries)
                if carried and entry.key in changed_components:
                    continue
                registrations.setdefault(entry.key, set()).add((owner, carried, entry))
            for name, binding in exports.items():
                carried = baseline or (app_owner and (name, binding) in baseline_exports)
                if carried and name in changed_exports:
                    continue
                if name in reexports and reexports[name] != binding:
                    raise ValueError(f"Conflicting UI barrel export: {name}")
                reexports[name] = binding
                if not carried:
                    export_owners.setdefault(name, set()).add(owner)
                    if len(export_owners[name]) > 1:
                        raise ValueError(f"Competing owners for UI barrel export: {name}")
        if manifest is None:
            continue
        has_manifest = True
        for key, value in manifest.items():
            if key != "pages":
                if key in manifest_metadata and manifest_metadata[key] != value:
                    raise ValueError(f"Conflicting route manifest metadata: {key}")
                manifest_metadata[key] = value
        sequence = []
        seen_route_ids = set()
        for route in manifest["pages"]:
            route_id, path = route["id"], route["path"]
            if route_id in seen_route_ids:
                raise ValueError(f"{owner}: repeated route id: {route_id}")
            seen_route_ids.add(route_id)
            if app_owner and route["component"] in dropped_components:
                # An app route leaves with its deleted component; the remaining
                # entries keep their relative positions.
                continue
            if not baseline or route_id not in reordered_routes:
                sequence.append(route_id)
            if not path.startswith("/"):
                raise ValueError(f"{owner}: route paths must start with /")
            carried = baseline or (app_owner and route in baseline_routes.get(route_id, []))
            if carried and route_id in changed_routes:
                continue
            if route_id in routes and routes[route_id] != route:
                raise ValueError(f"Conflicting route id: {route_id}")
            if path in route_paths and route_paths[path] != route_id:
                raise ValueError(f"Conflicting route path: {path}")
            if not carried:
                route_owners.setdefault(route_id, set()).add(owner)
                if len(route_owners[route_id]) > 1:
                    raise ValueError(f"Competing owners for route: {route_id}")
            routes[route_id], route_paths[path] = route, route_id
        route_sequence.update(zip(sequence, sequence[1:], strict=False))
    selected: dict[str, ComponentRegistration] = {}
    for key, records in registrations.items():
        candidates = {entry for _, _, entry in records}
        active_owners = {owner for owner, baseline, _ in records if not baseline}
        overrides = {entry for entry in candidates if dict(entry.options).get("override") is True}
        if len(candidates) == 1:
            if len(active_owners) > 1:
                raise ValueError(f"Competing owners for component registration: {key}")
            selected[key] = next(iter(candidates))
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
            alias = f"UiComponent{index}"
            specifier = alias if entry.export == "default" else f"{{ {entry.export} as {alias} }}"
            imports.append(f"import {specifier} from {json.dumps('./' + posixpath.relpath(entry.path, 'ui'))};")
            calls.append(f"  registerComponent({json.dumps(key)}, {alias}, {json.dumps(dict(entry.options), sort_keys=True, ensure_ascii=False)});")
        reexport_lines = [
            f"export {{ {export} as {name} }} from {json.dumps('./' + posixpath.relpath(path, 'ui'))};"
            for name, (path, export) in sorted(reexports.items())
        ]
        result["ui/index.js"] = "\n".join([
            *imports, *reexport_lines, "", "export function register(registerComponent) {",
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
