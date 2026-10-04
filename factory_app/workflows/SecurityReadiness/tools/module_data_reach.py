"""The records a module's actions can reach, read from the bundle's contracts and code.

Runtime persistence does not bind an app module to its own collections. With a
data contract present, ``ctx.persistence.collection(module_id, name)`` accepts
a collection the contract declares for any module, and a literal or alias name
reaches any collection that no ownership scopes. A module's reach is therefore
the collections the contract declares for it or shares with it, and every
collection its code addresses, including code it imports from elsewhere in the
app.

The code is read with AppGenerator's persistence resolver. It is a bounded
reading, not a sandbox: an argument it cannot read as a constant stands for
every collection the contract lets that call reach, and a persistence handle it
cannot follow, or code that escapes the persistence contract, makes the reach
unscoped.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import posixpath
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from factory_app.workflows.AppGenerator.tools.module_persistence_guard import (
    APP_DATA,
    COLLECTION_METHODS,
    PERSISTENCE,
    PersistenceResolver,
    constant_text,
    scan_module_persistence,
)
from mozaiksai.core.runtime.persistence.app_data import aliases_from_data_contract
from mozaiksai.core.runtime.persistence.intent_loader import index_data_contract_by_entity
from mozaiksai.core.runtime.persistence.ownership import collection_bindings, collection_ownership
from mozaiksai.core.runtime.persistence.platform_modules import _shared_grants

# Reach states, from least to most exposed.
NONE = "none"
OWNED = "owned"
APP_WIDE = "app_wide"
UNSCOPED = "unscoped"
_ORDER = (NONE, OWNED, APP_WIDE, UNSCOPED)

# Storage names embed the app id; whether a declared collection is owned does not.
_CONTRACT_APP_ID = "security-readiness"
_PARAMETERS = {
    COLLECTION_METHODS[(PERSISTENCE, "collection")]: ("module_id", "collection_name"),
    COLLECTION_METHODS[(PERSISTENCE, "literal_collection")]: ("collection_name",),
    COLLECTION_METHODS[(APP_DATA, "collection")]: ("alias",),
    COLLECTION_METHODS[(APP_DATA, "literal_collection")]: ("collection_name",),
}
_HANDLES = frozenset({PERSISTENCE, APP_DATA, *COLLECTION_METHODS.values()})
# The public API of each handle. Any other attribute, such as a client or a
# database, is a way out of the persistence contract this reading cannot follow.
_HANDLE_API = {
    PERSISTENCE: frozenset({
        "collection", "literal_collection", "collection_name", "principal", "app_id", "database_name",
        "scope_filter", "ensure_indexes",
    }),
    APP_DATA: frozenset({"collection", "collection_name_for_alias", "aliases"}),
}
_GETATTR = frozenset({"getattr", "builtins.getattr"})
_INTROSPECTION = frozenset(
    prefix + name
    for prefix in ("", "builtins.")
    for name in ("hasattr", "isinstance", "callable", "id", "bool")
)
_DYNAMIC_IMPORTS = frozenset({"__import__", "builtins.__import__", "importlib.import_module"})
_DYNAMIC_CODE = frozenset({"exec", "eval", "builtins.exec", "builtins.eval"})


def join(states: Iterable[str]) -> str:
    return max(states, key=_ORDER.index, default=NONE)


def valid_data_contract(text: str | None) -> dict[str, Any] | None:
    """The data contract the runtime would load and bind persistence to, or None."""
    if not text:
        return None
    try:
        contract = json.loads(text)
        if not isinstance(contract, dict):
            return None
        version = contract.get("version")
        if not isinstance(version, str) or not version.strip() or not isinstance(contract.get("surfaces"), list):
            return None
        index_data_contract_by_entity(contract)
        collection_bindings(contract)
        collection_ownership(contract, app_id=_CONTRACT_APP_ID, app_slug=None)
        _shared_grants(contract)
    except (TypeError, ValueError, RecursionError):
        return None
    return contract


def _row_state(row: Mapping[str, Any]) -> str:
    # The runtime scopes a collection only with both a scoped tenancy and an owner field.
    if row.get("tenancy") in ("per_user", "per_workspace") and row.get("owner_field"):
        return OWNED
    return APP_WIDE if row.get("tenancy") == "app_wide" else UNSCOPED


class _ContractCollections:
    """The reach state of each collection a valid contract declares."""

    def __init__(self, contract: dict[str, Any]) -> None:
        self.contract = contract
        self.aliases = aliases_from_data_contract(contract)
        rows: dict[tuple[str, str], list[str]] = {}
        literals: dict[str, list[str]] = {}
        for (owner, entity), row in index_data_contract_by_entity(contract).items():
            state = _row_state(row)
            rows.setdefault((owner, str(row.get("name") or entity)), []).append(state)
            literal = row.get("mongo_collection") or row.get("collection")
            if literal:
                literals.setdefault(str(literal), []).append(state)
        self.bindings = {
            key: join(rows.get((key[0], name), [UNSCOPED])) for key, name in collection_bindings(contract).items()
        }
        self.literals = {name: join(states) for name, states in literals.items()}

    def binding(self, module_id: str | None, name: str | None) -> str:
        """``collection(module_id, name)``: the runtime refuses a pair the contract does not declare."""
        return join(
            state for (owner, reference), state in self.bindings.items()
            if module_id in (None, owner) and name in (None, reference)
        )

    def literal(self, name: str | None) -> str:
        """A literal name: refused for an owned collection, unscoped when undeclared."""
        if name is None:
            return UNSCOPED
        state = self.literals.get(name, UNSCOPED)
        return NONE if state == OWNED else state

    def alias(self, alias: str | None) -> str:
        """An alias resolves to its literal collection; an unknown alias is refused."""
        if alias is None:
            return join(self.literal(name) for name in self.aliases.values())
        return self.literal(self.aliases[alias]) if alias in self.aliases else NONE

    def address(self, method: str, arguments: tuple[str | None, ...]) -> str:
        if method == COLLECTION_METHODS[(PERSISTENCE, "collection")]:
            return self.binding(*arguments)
        if method == COLLECTION_METHODS[(APP_DATA, "collection")]:
            return self.alias(arguments[0])
        return self.literal(arguments[0])

    def declared_for(self, module_id: str) -> str:
        """Collections declared under the module, and collections a share grants it."""
        states = [state for (owner, _reference), state in self.bindings.items() if owner == module_id]
        for grant in _shared_grants(self.contract):
            if module_id not in grant.grantees:
                continue
            if grant.collection is not None:
                states.append(self.binding(*grant.collection))
            states.extend(self.literal(name) for name in grant.literals)
        return join(states)


@dataclass(frozen=True)
class _SourceReach:
    addresses: tuple[tuple[str, tuple[str | None, ...]], ...]
    references: frozenset[str]
    relative_imports: tuple[tuple[int, str | None, tuple[str, ...]], ...]
    unresolved: bool


def _string_constants(nodes: list[ast.AST]) -> dict[str, str]:
    """Names bound only by assignments of one constant string, wherever they occur."""
    if any(isinstance(node, ast.ImportFrom) and any(item.name == "*" for item in node.names) for node in nodes):
        return {}
    values: dict[str, set[str | None]] = {}
    simple: set[int] = set()
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    simple.add(id(target))
                    values.setdefault(target.id, set()).add(constant_text(node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            simple.add(id(node.target))
            values.setdefault(node.target.id, set()).add(constant_text(node.value) if node.value else None)
    rebound: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and id(node) not in simple:
            rebound.add(node.id)
        elif isinstance(node, ast.arg):
            rebound.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebound.add(node.name)
        elif isinstance(node, ast.alias):
            rebound.add(node.asname or node.name.split(".")[0])
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            rebound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            rebound.add(node.rest)
    constants: dict[str, str] = {}
    for name, found in values.items():
        value = next(iter(found)) if len(found) == 1 else None
        if value is not None and name not in rebound:
            constants[name] = value
    return constants


def _in_truth_test(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> bool:
    """A boolean operand whose result is only tested, never kept."""
    while isinstance(parents.get(node), (ast.BoolOp, ast.UnaryOp)):
        node = parents[node]
    parent = parents.get(node)
    if isinstance(parent, (ast.If, ast.While, ast.Assert, ast.IfExp)):
        return parent.test is node
    return isinstance(parent, (ast.Compare, ast.comprehension)) and node is not getattr(parent, "iter", None)


def _api(values: set[str], name: str | None) -> bool:
    """Whether every handle among the values exposes the attribute as public persistence API."""
    return all(name in _HANDLE_API.get(value, frozenset()) for value in values & _HANDLES)


def _followed(
    node: ast.expr, values: set[str], resolver: PersistenceResolver, parents: Mapping[ast.AST, ast.AST],
) -> bool:
    """Whether the resolver keeps track of a handle-valued expression where it is used."""
    parent = parents.get(node)
    if isinstance(parent, ast.Call) and parent.func is node:
        return True
    if isinstance(parent, ast.Attribute):
        return _api(values, parent.attr)
    if isinstance(parent, (ast.Await, ast.Return, ast.Expr, ast.FormattedValue, ast.Compare)):
        return True
    if isinstance(parent, ast.Assign):
        return parent.value is node and all(resolver.key(target) for target in parent.targets)
    if isinstance(parent, ast.AnnAssign):
        return parent.value is node and resolver.key(parent.target) is not None
    if isinstance(parent, ast.UnaryOp):
        return isinstance(parent.op, ast.Not)
    if isinstance(parent, (ast.If, ast.While, ast.Assert, ast.IfExp)):
        return parent.test is node
    if isinstance(parent, ast.BoolOp):
        return _in_truth_test(parent, parents)
    call = parents.get(parent) if isinstance(parent, ast.keyword) else parent
    if isinstance(call, ast.Call):
        functions = resolver.resolve(call.func)
        if functions & _GETATTR and call.args[:1] == [node]:
            return len(call.args) > 1 and _api(values, constant_text(call.args[1]))
        return bool(functions & _INTROSPECTION)
    return False


def _dynamic_import(call: ast.Call, text: Callable[[ast.AST], str | None]) -> str | None:
    """The absolute module a dynamic import names, or None when it is chosen at run time.

    A relative name resolved against the importing package stays inside the
    module, whose files are read anyway; it returns an empty name.
    """
    module = text(call.args[0]) if call.args else None
    extra = call.args[1:] + [keyword.value for keyword in call.keywords if keyword.arg != "name"]
    if module is None or (not module.startswith(".") and not extra):
        return module
    package = call.args[1] if len(call.args) == 2 and not call.keywords else next(
        (keyword.value for keyword in call.keywords if keyword.arg == "package" and not call.args[1:]), None,
    )
    if package is None or not module.startswith("."):
        return None
    if isinstance(package, ast.Name) and package.id in {"__name__", "__package__"}:
        return ""
    base = text(package)
    try:
        return importlib.util.resolve_name(module, base) if base else None
    except (ImportError, ValueError):
        return None


def read_source(source: str) -> _SourceReach:
    """The collections one Python source addresses and the code it imports."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return _SourceReach((), frozenset(), (), True)
    resolver = PersistenceResolver(tree)
    constants = _string_constants(resolver.nodes)
    parents = {child: parent for parent in resolver.nodes for child in ast.iter_child_nodes(parent)}

    def text(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            return constants.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = text(node.left), text(node.right)
            return left + right if left is not None and right is not None else None
        return constant_text(node)

    def returns_handle(values: set[str]) -> bool:
        return any(resolver.returns.get(value.rsplit(".", 1)[-1], set()) & _HANDLES for value in values)

    addresses: list[tuple[str, tuple[str | None, ...]]] = []
    references: set[str] = set()
    relative: list[tuple[int, str | None, tuple[str, ...]]] = []
    unresolved = False
    for node in resolver.nodes:
        if isinstance(node, ast.Import):
            references.update(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = tuple(item.name for item in node.names)
            if node.level:
                relative.append((node.level, node.module, names))
            elif node.module:
                references.update({node.module, *(f"{node.module}.{name}" for name in names)})
        if not isinstance(node, ast.expr) or isinstance(getattr(node, "ctx", None), (ast.Store, ast.Del)):
            continue
        values = resolver.resolve(node)
        if isinstance(node, (ast.Name, ast.Attribute)):
            references.update(value for value in values if not value.startswith("<"))
        if isinstance(node, ast.Call):
            functions = resolver.resolve(node.func)
            for method in sorted(functions & _PARAMETERS.keys()):
                names = _PARAMETERS[method]
                if any(isinstance(item, ast.Starred) for item in node.args) or any(
                    keyword.arg is None for keyword in node.keywords
                ):
                    arguments: list[str | None] = [None] * len(names)
                else:
                    arguments = [text(item) for item in node.args[: len(names)]]
                    arguments += [None] * (len(names) - len(arguments))
                    for keyword in node.keywords:
                        if keyword.arg in names:
                            arguments[names.index(keyword.arg)] = text(keyword.value)
                addresses.append((method, tuple(arguments)))
            if functions & _DYNAMIC_IMPORTS:
                module = _dynamic_import(node, text)
                if module is None:
                    unresolved = True
                else:
                    references.add(module)
            if functions & _DYNAMIC_CODE:
                unresolved = True
        parent = parents.get(node)
        handle = values & _HANDLES or (
            isinstance(node, (ast.Name, ast.Attribute)) and returns_handle(values)
            and not (isinstance(parent, ast.Call) and parent.func is node)
        )
        if handle and not _followed(node, values, resolver, parents):
            unresolved = True
    return _SourceReach(tuple(addresses), frozenset(references), tuple(relative), unresolved)


def _dotted(path: str) -> str:
    stem = path.removesuffix(".py")
    return stem.removesuffix("/__init__").replace("/", ".")


class BundleDataReach:
    """Reach of each module in an app root's files, keyed by module directory."""

    def __init__(self, files: Mapping[str, str], contract: dict[str, Any] | None) -> None:
        self._files = {path: source for path, source in files.items() if path.endswith(".py")}
        self._extensions = {
            path: source for path, source in files.items()
            if path.startswith("modules/") and path.endswith("/runtime_extensions.yaml")
        }
        self._collections = _ContractCollections(contract) if contract is not None else None
        self._by_name: dict[str, set[str]] = {}
        for path in self._files:
            self._by_name.setdefault(_dotted(path), set()).add(path)
        self._sources: dict[str, _SourceReach] = {}

    def _source(self, path: str) -> _SourceReach:
        if path not in self._sources:
            reach = read_source(self._files[path])
            # Code the persistence guard rejects escapes the injected persistence contract.
            if path.startswith("modules/") and scan_module_persistence({path: self._files[path], **self._extensions}):
                reach = _SourceReach(reach.addresses, reach.references, reach.relative_imports, True)
            self._sources[path] = reach
        return self._sources[path]

    def _imports(self, path: str) -> set[str]:
        source = self._source(path)
        found: set[str] = set()
        for reference in source.references:
            parts = reference.split(".")
            if parts[0] == "app":
                parts = parts[1:]
            for end in range(1, len(parts) + 1):
                found |= self._by_name.get(".".join(parts[:end]), set())
        for level, module, names in source.relative_imports:
            base = posixpath.dirname(path)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            target = posixpath.join(base, *(module or "").split(".")) if module else base
            for candidate in (target, *(posixpath.join(target, name) for name in names)):
                found |= {item for item in (f"{candidate}.py", f"{candidate}/__init__.py") if item in self._files}
        return found

    def for_module(self, module_id: str, module_dir: str) -> str:
        """The reach state of a module's actions."""
        if self._collections is None:
            return UNSCOPED
        pending = [path for path in self._files if path.startswith(f"{module_dir}/")]
        seen = set(pending)
        states = [self._collections.declared_for(module_id)]
        while pending:
            path = pending.pop()
            source = self._source(path)
            if source.unresolved:
                return UNSCOPED
            states.extend(self._collections.address(method, arguments) for method, arguments in source.addresses)
            for imported in sorted(self._imports(path) - seen):
                seen.add(imported)
                pending.append(imported)
        return join(states)
