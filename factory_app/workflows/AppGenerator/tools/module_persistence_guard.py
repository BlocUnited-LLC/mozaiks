"""Reject generated module code that escapes the injected persistence contract.

This is a bounded source admission check, not a Python sandbox. It follows
ordinary import/assignment aliases and collection-returning repo helpers.
SecurityReadiness reuses the same resolver to read which collections module
code addresses.
"""
from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

import yaml

from mozaiksai.core.runtime.app.module_loader import ModuleRuntimeExtensionsManifest

_CONSTRUCTORS = {"MongoPersistenceContext", "MongoPersistenceCollection", "ModuleContext", "PersistencePrincipal", "AppData"}
_AUTHORITY_FACTORIES = {"bind_persistence_principal", "current_persistence_principal"}
_CORE_BACKENDS = {"handler", "base_handler", "service", "repo", "policy", "schemas", "account_data_handler"}
PERSISTENCE = "<persistence>"
COLLECTION = "<collection>"
APP_DATA = "<app_data>"
_PRINCIPAL = "<principal>"
_CURSOR = "<cursor>"
_CURSOR_FACTORY = "<cursor_factory>"
# A bound collection method, keyed by the handle it belongs to and its name.
COLLECTION_METHODS = {
    (PERSISTENCE, "collection"): "<persistence_collection>",
    (PERSISTENCE, "literal_collection"): "<persistence_literal_collection>",
    (APP_DATA, "collection"): "<app_data_collection>",
    (APP_DATA, "literal_collection"): "<app_data_literal_collection>",
}
_COLLECTION_FACTORIES = frozenset(COLLECTION_METHODS.values())
_PROTECTED = {PERSISTENCE, COLLECTION, APP_DATA, _PRINCIPAL, _CURSOR}
_PRIVATE_STORAGE = {
    "_client_handle", "_owner_scope", "_scope_metadata", "_ownership", "_collections", "_restrict_aggregation",
    "_client", "_principal", "_app_id", "_app_slug", "_database_name", "_bindings", "_options", "_collection_resolver",
}


def constant_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = constant_text(node.left), constant_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


class PersistenceResolver:
    """Resolve the expressions of one parsed source to the values they may hold.

    A value is a dotted name, an import origin, or one of the persistence
    markers above. Monotonic propagation catches simple aliases and repo
    helpers regardless of definition order without pretending to execute
    generated Python.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.nodes = list(ast.walk(tree))
        self.aliases: dict[str, set[str]] = {}
        self.returns: dict[str, set[str]] = {}
        self.methods = {node.name for node in self.nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in self.nodes:
            if isinstance(node, ast.Import):
                for imported in node.names:
                    self.aliases.setdefault(imported.asname or imported.name.split(".")[0], set()).add(
                        imported.name if imported.asname else imported.name.split(".")[0],
                    )
            elif isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    self.aliases.setdefault(imported.asname or imported.name, set()).add(f"{node.module or ''}.{imported.name}")
        self._propagate()

    def key(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self.key(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None

    def attribute(self, values: set[str], name: str) -> set[str]:
        if name == "persistence":
            return {PERSISTENCE}
        if name in {"collection", "literal_collection"} and values & {PERSISTENCE, APP_DATA}:
            return {COLLECTION_METHODS[(handle, name)] for handle in (PERSISTENCE, APP_DATA) if handle in values}
        if name == "principal" and PERSISTENCE in values:
            return {_PRINCIPAL}
        if name in {"find", "aggregate"} and COLLECTION in values:
            return {_CURSOR_FACTORY}
        if name in {"sort", "limit", "skip", "batch_size", "clone"} and _CURSOR in values:
            return {_CURSOR_FACTORY}
        return {f"{value}.{name}" for value in values}

    def resolve(self, node: ast.AST) -> set[str]:
        label = self.key(node)
        if label in self.aliases:
            return self.aliases[label]
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, ast.Attribute):
            return self.attribute(self.resolve(node.value), node.attr)
        if isinstance(node, ast.Await):
            return self.resolve(node.value)
        if isinstance(node, ast.Call):
            functions = self.resolve(node.func)
            if functions & _COLLECTION_FACTORIES:
                return {COLLECTION}
            if _CURSOR_FACTORY in functions:
                return {_CURSOR}
            if any(name.rsplit(".", 1)[-1] == "app_data_from_context" for name in functions):
                return {APP_DATA}
            if functions & {"getattr", "builtins.getattr"} and len(node.args) >= 2:
                name = constant_text(node.args[1])
                return self.attribute(self.resolve(node.args[0]), name) if name else set()
            return set().union(*(self.returns.get(name.rsplit(".", 1)[-1], set()) for name in functions))
        return set()

    def _propagate(self) -> None:
        for _ in range(len(self.nodes) + 1):
            changed = False
            for node in self.nodes:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
                value = getattr(node, "value", None)
                if targets and value is not None:
                    values = self.resolve(value)
                    for target in targets:
                        label = self.key(target)
                        if label and values - self.aliases.get(label, set()):
                            self.aliases.setdefault(label, set()).update(values)
                            changed = True
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    values = set().union(*(self.resolve(item.value) for item in ast.walk(node) if isinstance(item, ast.Return) and item.value))
                    if values - self.returns.get(node.name, set()):
                        self.returns.setdefault(node.name, set()).update(values)
                        changed = True
            if not changed:
                break


def _startup_files(files: dict[str, str]) -> set[str]:
    """Identify separate process-lifetime implementations, never driver exemptions."""
    allowed = set()
    for path, source in files.items():
        match = re.fullmatch(r"modules/([^/]+)/runtime_extensions\.yaml", path)
        if not match:
            continue
        try:
            manifest = ModuleRuntimeExtensionsManifest.model_validate(yaml.safe_load(source))
        except (ValueError, yaml.YAMLError):
            continue
        for extension in manifest.extensions:
            module_path = extension.entrypoint.partition(":")[0]
            if extension.kind == "startup_service" and module_path.rsplit(".", 1)[-1] not in _CORE_BACKENDS:
                allowed.add(f"modules/{match[1]}/{module_path.replace('.', '/')}.py")
    return allowed


def scan_module_persistence(files: dict[str, str]) -> list[str]:
    errors = []
    startup = _startup_files(files)
    for path, source in files.items():
        if not re.fullmatch(r"modules/[^/]+/(?:[^/]+/)*[^/]+\.py", path):
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            errors.append(f"{path}:{exc.lineno}: generated module Python must parse before persistence validation.")
            continue
        errors.extend(_scan_source(path, tree, startup))
    return errors


def _scan_source(path: str, tree: ast.Module, startup: set[str]) -> list[str]:
    resolver = PersistenceResolver(tree)
    nodes, resolve, methods = resolver.nodes, resolver.resolve, resolver.methods
    errors: set[tuple[int, str]] = set()

    def reject(node: ast.AST, message: str) -> None:
        errors.add((getattr(node, "lineno", 1), message))

    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if any(name.split(".")[0] in {"motor", "pymongo"} for name in modules):
                reject(node, "raw Mongo driver imports are forbidden; use ctx.persistence.collection(module_id, collection_name).")
            if any(item.name == "get_mongo_client" for item in node.names):
                reject(node, "get_mongo_client bypasses injected persistence.")
            imported_names = modules + [f"{getattr(node, 'module', '')}.{item.name}" for item in node.names]
            if any(".persistence.request_scope" in name for name in imported_names) or any(item.name in _AUTHORITY_FACTORIES for item in node.names):
                reject(node, "request principal binding is host-owned and cannot be imported by generated modules.")
            if isinstance(node, ast.ImportFrom) and any(item.name in _CONSTRUCTORS - {"ModuleContext"} for item in node.names):
                reject(node, "generated modules must not import persistence constructors or principal factories.")
            if isinstance(node, ast.ImportFrom) and any(item.name == "*" for item in node.names):
                if (node.module or "").startswith("mozaiksai"):
                    reject(node, "wildcard runtime imports hide persistence authority.")
            # Process-lifetime implementations are not request persistence APIs.
            if path not in startup and isinstance(node, ast.ImportFrom) and node.level:
                base = PurePosixPath(path).parent
                for _ in range(node.level - 1):
                    base = base.parent
                imported_path = base / (node.module or "").replace(".", "/")
                candidates = {f"{imported_path}.py", *(str(imported_path / item.name) + ".py" for item in node.names)}
                if candidates & startup:
                    reject(node, "request module code cannot import a startup service's database implementation.")
            if path not in startup and not (isinstance(node, ast.ImportFrom) and node.level):
                imported_paths = modules if isinstance(node, ast.Import) else imported_names
                candidates = {name.removeprefix("app.").replace(".", "/") + ".py" for name in imported_paths}
                if candidates & startup:
                    reject(node, "request module code cannot import a startup service's database implementation.")

    for node in nodes:
        if isinstance(node, ast.Attribute):
            own_helper = isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"} and node.attr in methods
            if node.attr.startswith("_") and (
                resolve(node.value) & _PROTECTED
                or node.attr in _PRIVATE_STORAGE and not own_helper
                or node.attr == "_collection" and not own_helper
            ):
                reject(node, f"private persistence attribute {node.attr!r} bypasses the runtime boundary.")
            if node.attr in _CONSTRUCTORS - {"ModuleContext"}:
                reject(node, f"{node.attr} cannot replace injected persistence authority.")
            if node.attr in _AUTHORITY_FACTORIES or node.attr == "request_scope":
                reject(node, "request principal binding is host-owned and cannot be accessed by generated modules.")
            if node.attr == "get_mongo_client":
                reject(node, "get_mongo_client bypasses injected persistence.")
            if node.attr == "literal_collection":
                reject(node, "literal collection names are host-owned; use app_data_from_context(ctx) for declared aliases.")
        if not isinstance(node, ast.Call):
            continue
        functions = resolve(node.func)
        for function in functions:
            name = function.rsplit(".", 1)[-1]
            forbidden = set(function.split(".")) & (_CONSTRUCTORS | _AUTHORITY_FACTORIES | {"get_mongo_client"})
            if forbidden:
                name = sorted(forbidden)[0]
                reject(node, f"{name} must not be constructed or called by generated module code; use injected persistence.")
            if name == "app_data_from_context":
                positional_context = len(node.args) == 1 and not isinstance(node.args[0], ast.Starred) and not node.keywords
                keyword_context = not node.args and len(node.keywords) == 1 and node.keywords[0].arg == "ctx"
                if not (positional_context or keyword_context):
                    reject(node, "app_data_from_context accepts only the injected context; alias contracts and app roots are host-owned.")
        reflection_writers = {"setattr", "builtins.setattr", "delattr", "builtins.delattr", "object.__setattr__", "object.__delattr__"}
        reflective = functions & ({"getattr", "builtins.getattr", "vars", "builtins.vars", "object.__getattribute__"} | reflection_writers)
        if reflective and node.args:
            reflected_attribute = constant_text(node.args[1]) if len(node.args) > 1 else None
            if reflected_attribute in _PRIVATE_STORAGE or reflected_attribute == "_collection":
                reject(node, "private persistence reflection bypasses the runtime boundary.")
            if reflected_attribute == "literal_collection":
                reject(node, "literal collection names are host-owned; use app_data_from_context(ctx) for declared aliases.")
            if resolve(node.args[0]) & _PROTECTED:
                if reflected_attribute is None or reflected_attribute.startswith("_") or functions & reflection_writers:
                    reject(node, "private or dynamic persistence reflection bypasses the runtime boundary.")
        if functions & {"__import__", "builtins.__import__", "importlib.import_module"} and node.args:
            module = constant_text(node.args[0]) or ""
            if module.startswith(("motor", "pymongo", "mozaiksai.core.runtime.persistence", "mozaiksai.core.core_config")):
                reject(node, "dynamic database imports bypass injected persistence.")
            if path not in startup and module.removeprefix("app.").replace(".", "/") + ".py" in startup:
                reject(node, "request module code cannot import a startup service's database implementation.")
    return [f"{path}:{line}: {message}" for line, message in sorted(errors)]
