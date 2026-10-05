"""The records a module's actions can reach, read from the bundle's contracts and code.

Runtime persistence does not bind an app module to its own collections. With a
data contract present, ``ctx.persistence.collection(module_id, name)`` accepts
a collection the contract declares for any module, and a literal or alias name
reaches any collection that no ownership scopes. A module's reach is therefore
the collections the contract declares for it or shares with it, and every
collection its code addresses, including code it imports from elsewhere in the
app.

The code is read with AppGenerator's persistence resolver, bounded. The reading
proves reach only from forms it recognises: the public persistence handle API,
imports it can map to bundle files, and string constants no app code can
rebind. An argument it cannot read as a constant stands for every collection
the contract lets that call reach. Anything else that could reach records, in
the module's code or in code it follows, makes the reach unscoped: a handle it
cannot follow, reflection over objects or namespaces, module state changed at
run time, code loaded or run another way, and database clients outside the
injected persistence. It is a static reading, not a sandbox.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import posixpath
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from factory_app.workflows.AppGenerator.tools.module_persistence_guard import (
    _AUTHORITY_FACTORIES,
    _CONSTRUCTORS,
    _PRINCIPAL,
    _PRIVATE_STORAGE,
    APP_DATA,
    COLLECTION_METHODS,
    PERSISTENCE,
    PersistenceResolver,
    constant_text,
    scan_module_persistence,
)
from mozaiksai.core.runtime.composition.extensions import _module_package_root
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
_DYNAMIC_IMPORTS = frozenset({"__import__", "builtins.__import__", "importlib.import_module", "importlib.__import__"})
_DYNAMIC_CODE = frozenset(prefix + name for prefix in ("", "builtins.") for name in ("exec", "eval", "compile"))
_NAMESPACES = frozenset(prefix + name for prefix in ("", "builtins.") for name in ("vars", "locals"))
_GLOBALS = frozenset({"globals", "builtins.globals"})
_WRITERS = frozenset({
    "setattr", "delattr", "builtins.setattr", "builtins.delattr", "object.__setattr__", "object.__delattr__",
})
# Callables that act on a name chosen at run time. Used other than by a direct
# call, what they act on is not followed.
_REFLECTIVE_CALLABLES = _GETATTR | _DYNAMIC_IMPORTS | _DYNAMIC_CODE | _NAMESPACES | _GLOBALS | _WRITERS
# Attributes that expose a namespace, a frame, another object's attributes by
# name, or code to run.
_REFLECTIVE_ATTRIBUTES = frozenset({
    "__dict__", "__globals__", "__builtins__", "__getattribute__", "__closure__", "__code__", "__subclasses__",
    "__loader__", "__spec__", "f_locals", "f_globals", "f_builtins", "f_back", "tb_frame", "gi_frame", "cr_frame",
    "ag_frame",
})
_REFLECTIVE_NAMES = frozenset({"__builtins__", "__loader__", "__spec__"})
_MODULE_WRITERS = frozenset({"__setattr__", "__delattr__"})
# Modules and functions that load or run code, reach objects by reference or
# frame, or change the import system. Each can also rebind any module's names.
_REFLECTION = (
    "sys.modules", "sys.path", "sys.meta_path", "sys.path_hooks", "sys._getframe", "sys.settrace", "sys.setprofile",
    "importlib.util", "importlib.machinery", "importlib.abc", "importlib.reload", "importlib._bootstrap",
    "importlib._bootstrap_external", "imp", "runpy", "pkgutil", "zipimport", "inspect.currentframe", "inspect.stack",
    "inspect.trace", "inspect.getouterframes", "inspect.getinnerframes", "inspect.getframeinfo", "inspect.getmembers",
    "inspect.getmembers_static", "inspect.getattr_static", "inspect.getmodule", "gc.get_objects", "gc.get_referrers",
    "gc.get_referents", "operator.attrgetter", "operator.methodcaller", "ctypes", "types.FunctionType",
    "types.CodeType", "marshal", "pickle.load", "pickle.loads", "pickle.Unpickler", "code", "codeop",
)
# Other processes run code the reading never sees.
_PROCESSES = (
    "subprocess", "multiprocessing", "pty", "os.system", "os.popen", "os.fork", "os.forkpty", "os.posix_spawn",
    "os.posix_spawnp", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
    "concurrent.futures.ProcessPoolExecutor",
)
_PROCESS_PREFIXES = ("os.exec", "os.spawn")
# Database clients outside the injected persistence. Records they reach are not
# scoped by the data contract.
_DATABASE_DRIVERS = frozenset({
    "motor", "pymongo", "mongoengine", "beanie", "odmantic", "sqlalchemy", "sqlmodel", "psycopg", "psycopg2",
    "asyncpg", "aiopg", "aiosqlite", "sqlite3", "pymysql", "aiomysql", "MySQLdb", "mysql", "pymssql", "pyodbc",
    "oracledb", "cx_Oracle", "duckdb", "redis", "aioredis", "cassandra", "elasticsearch", "opensearchpy", "neo4j",
    "peewee", "tortoise", "databases", "couchbase", "firebase_admin", "supabase", "clickhouse_driver",
})
_DATABASE_PACKAGES = (
    "google.cloud.firestore", "google.cloud.datastore", "google.cloud.bigtable", "azure.cosmos", "azure.data.tables",
)
# Runtime persistence internals the AppGenerator guard keeps out of module code.
_RAW_CLIENT_NAMES = frozenset({"get_mongo_client", "request_scope", *(_CONSTRUCTORS - {"ModuleContext"}), *_AUTHORITY_FACTORIES})
_RUNTIME_INTERNALS = ("mozaiksai.core.runtime.persistence", "mozaiksai.core.core_config")
_PRIVATE_ATTRIBUTES = frozenset({*_PRIVATE_STORAGE, "_collection"})
_OWN = frozenset({"self", "cls"})
_CHOICES = 64


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
        aliases_from_data_contract(contract)
        _ContractCollections(contract)
    except (TypeError, ValueError, AttributeError, KeyError, RecursionError):
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
    # Per address, the module-level constants each argument was read from.
    constants: tuple[tuple[frozenset[str], ...], ...] = ()
    # Attribute names this source may set on another object; None when it may set any.
    writes: frozenset[str] | None = frozenset()


# Source that does not parse never runs, so it rebinds nothing.
_UNPARSED = _SourceReach((), frozenset(), (), True)


def _string_values(nodes: list[ast.AST]) -> dict[str, frozenset[str]]:
    """Names bound only to literal strings: by assignment, or as a loop target over literal strings."""
    if any(isinstance(node, ast.ImportFrom) and any(item.name == "*" for item in node.names) for node in nodes):
        return {}
    values: dict[str, set[str]] = {}
    other: set[str] = set()
    counted: set[int] = set()

    def bind(target: ast.Name, found: Iterable[str | None]) -> None:
        counted.add(id(target))
        texts = set(found)
        if None in texts:
            other.add(target.id)
        else:
            values.setdefault(target.id, set()).update(text for text in texts if text is not None)

    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bind(target, [constant_text(node.value)])
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bind(node.target, [constant_text(node.value) if node.value else None])
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and isinstance(node.target, ast.Name):
            items = node.iter.elts if isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)) else None
            bind(node.target, [constant_text(item) for item in items] if items else [None])
    for node in nodes:
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load) and id(node) not in counted:
            other.add(node.id)
        elif isinstance(node, ast.arg):
            other.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            other.add(node.name)
        elif isinstance(node, ast.alias):
            other.add(node.asname or node.name.split(".")[0])
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            other.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            other.add(node.rest)
    return {name: frozenset(found) for name, found in values.items() if name not in other}


def _import_origin(node: ast.ImportFrom, item: ast.alias) -> str:
    """What a from-import binds; a relative one keeps its leading dots."""
    return "." * node.level + (f"{node.module}." if node.module else "" if node.level else ".") + item.name


def _dynamic_import(call: ast.Call, text: Callable[[ast.AST], str | None]) -> str | None:
    """The module a dynamic import names, or None when it is chosen at run time.

    A name relative to the importing package stays inside the module, whose
    files are read anyway; it is returned as written.
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
        return module
    base = text(package)
    try:
        return importlib.util.resolve_name(module, base) if base else None
    except (ImportError, ValueError):
        return None


class _ReachResolver(PersistenceResolver):
    """The guard's resolver, bounded, with three forms the reach reading also follows.

    A dynamic import with a constant name holds the module it names;
    ``getattr`` with a name drawn from known strings holds those attributes;
    ``globals().get(name)`` and ``globals()[name]`` with a constant name hold
    that name, as generated CRUD services read their optional hooks.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.strings = _string_values(list(ast.walk(tree)))
        self.constants = {name: next(iter(found)) for name, found in self.strings.items() if len(found) == 1}
        super().__init__(tree, bounded=True)

    def _propagate(self) -> None:
        # The guard's resolver drops a relative import's level; keep it, so
        # ``from .code import x`` is not read as the standard library.
        for node in self.nodes:
            if isinstance(node, ast.ImportFrom) and node.level:
                for item in node.names:
                    held = self.aliases[item.asname or item.name]
                    held.discard(f"{node.module or ''}.{item.name}")
                    held.add(_import_origin(node, item))
        super()._propagate()

    def text(self, node: ast.AST) -> tuple[str | None, frozenset[str]]:
        """A constant string, and the module-level names it was read from."""
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            value = self.constants.get(node.id)
            return (value, frozenset({node.id})) if value is not None else (None, frozenset())
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            (left, left_names), (right, right_names) = self.text(node.left), self.text(node.right)
            if left is None or right is None:
                return None, frozenset()
            return left + right, left_names | right_names
        return constant_text(node), frozenset()

    def choices(self, node: ast.AST) -> frozenset[str] | None:
        """Every string an attribute name may hold, or None when it is not known."""
        value = constant_text(node)
        if value is not None:
            return frozenset({value})
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            return self.strings.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.choices(node.left), self.choices(node.right)
            if left is None or right is None or len(left) * len(right) > _CHOICES:
                return None
            return frozenset(first + second for first in left for second in right)
        return None

    def global_name(self, node: ast.AST | None) -> str | None:
        """The constant name that ``globals().get(name)`` or ``globals()[name]`` reads."""
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and self._globals(node.value):
            return self.text(node.slice)[0]
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and self._globals(node.func.value) and 1 <= len(node.args) <= 2 and not node.keywords
            and not any(isinstance(item, ast.Starred) for item in node.args)
        ):
            return self.text(node.args[0])[0]
        return None

    def _globals(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call) and not node.args and not node.keywords
            and bool(self.resolve(node.func) & _GLOBALS)
        )

    def _name(self, name: str) -> set[str]:
        return self.aliases[name] if name in self.aliases else {name}

    def resolve(self, node: ast.AST) -> set[str]:
        if isinstance(node, ast.Call):
            functions = self.resolve(node.func)
            if functions & _DYNAMIC_IMPORTS:
                module = _dynamic_import(node, lambda item: self.text(item)[0])
                return {module} if module else set()
            if functions & _GETATTR and len(node.args) >= 2 and constant_text(node.args[1]) is None:
                names = self.choices(node.args[1])
                values = self.resolve(node.args[0])
                return set().union(*(self.attribute(values, name) for name in names)) if names else set()
            name = self.global_name(node)
            if name is not None:
                return self._name(name)
        elif isinstance(node, ast.Subscript):
            name = self.global_name(node)
            if name is not None:
                return self._name(name)
        return super().resolve(node)


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
    node: ast.expr, values: set[str], resolver: _ReachResolver, parents: Mapping[ast.AST, ast.AST],
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
            names = resolver.choices(call.args[1]) if len(call.args) > 1 else None
            return names is not None and all(_api(values, name) for name in names)
        return bool(functions & _INTROSPECTION)
    return False


def _named(name: str, items: Iterable[str]) -> bool:
    return any(name == item or name.startswith(f"{item}.") for item in items)


def read_source(source: str) -> _SourceReach:
    """The collections one Python source addresses, the code it imports, and what it may rebind."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, OverflowError, RecursionError, MemoryError):
        return _UNPARSED
    try:
        return _read(tree)
    except (RecursionError, MemoryError):
        return replace(_UNPARSED, writes=None)


def _read(tree: ast.Module) -> _SourceReach:
    resolver = _ReachResolver(tree)
    parents = {child: parent for parent in resolver.nodes for child in ast.iter_child_nodes(parent)}
    origins: set[str] = set()
    for node in resolver.nodes:
        if isinstance(node, ast.Import):
            origins.update(item.name if item.asname else item.name.split(".")[0] for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            origins.update(_import_origin(node, item) for item in node.names)
        elif isinstance(node, ast.Call) and resolver.resolve(node.func) & _DYNAMIC_IMPORTS:
            origins.update(resolver.resolve(node))

    def imported(values: Iterable[str]) -> bool:
        """Whether a value is an imported module or something it holds."""
        return any(_named(value, origins) for value in values)

    def returns_handle(values: set[str]) -> bool:
        return any(resolver.returns.get(value.rsplit(".", 1)[-1], set()) & _HANDLES for value in values)

    addresses: list[tuple[str, tuple[str | None, ...]]] = []
    constants: list[tuple[frozenset[str], ...]] = []
    references: set[str] = set()
    relative: list[tuple[int, str | None, tuple[str, ...]]] = []
    # Resolution cut short by its bounds has not followed every handle.
    unresolved = resolver.truncated
    writes: set[str] | None = set()

    def reflective() -> None:
        """Reflection or loaded code: not followed, and it can rebind any module's names."""
        nonlocal unresolved, writes
        unresolved, writes = True, None

    def rebinds(name: str | None) -> None:
        nonlocal writes
        if writes is not None and name is not None:
            writes.add(name)
        elif name is None:
            writes = None

    def check(name: str) -> None:
        """A module or function the reading does not follow."""
        nonlocal unresolved
        if _named(name, _REFLECTION):
            reflective()
        elif (
            _named(name, _PROCESSES) or name.startswith(_PROCESS_PREFIXES)
            or name.split(".")[0] in _DATABASE_DRIVERS or _named(name, _DATABASE_PACKAGES)
        ):
            unresolved = True

    for node in resolver.nodes:
        if isinstance(node, ast.Import):
            references.update(item.name for item in node.names)
            for item in node.names:
                check(item.name)
                unresolved = unresolved or bool(set(item.name.split(".")) & _RAW_CLIENT_NAMES)
        elif isinstance(node, ast.ImportFrom):
            names = tuple(item.name for item in node.names)
            if node.level:
                relative.append((node.level, node.module, names))
            elif node.module:
                references.update({node.module, *(f"{node.module}.{name}" for name in names)})
                for name in (node.module, *(f"{node.module}.{name}" for name in names)):
                    check(name)
            unresolved = unresolved or bool(set((node.module or "").split(".")).union(names) & _RAW_CLIENT_NAMES)
        if isinstance(node, ast.Name) and node.id in _REFLECTIVE_NAMES:
            reflective()
        if isinstance(node, ast.Attribute):
            own = isinstance(node.value, ast.Name) and node.value.id in _OWN
            if node.attr in _REFLECTIVE_ATTRIBUTES:
                reflective()
            if node.attr in _RAW_CLIENT_NAMES or node.attr in _PRIVATE_ATTRIBUTES and not own:
                unresolved = True
            if node.attr in _MODULE_WRITERS and imported(resolver.resolve(node.value)):
                reflective()
            if not isinstance(node.ctx, ast.Load) and not own:
                # Setting an attribute of a module object rebinds its names.
                rebinds(node.attr)
                if imported(resolver.resolve(node.value)):
                    unresolved = True
        if not isinstance(node, ast.expr) or isinstance(getattr(node, "ctx", None), (ast.Store, ast.Del)):
            continue
        values = resolver.resolve(node)
        parent = parents.get(node)
        called = isinstance(parent, ast.Call) and parent.func is node
        if isinstance(node, (ast.Name, ast.Attribute)):
            references.update(value for value in values if not value.startswith("<"))
            for value in values:
                if imported({value}):
                    check(value)
            if values & _REFLECTIVE_CALLABLES and not called or values & _NAMESPACES:
                reflective()
            if values & _GLOBALS and not (called and _reads_global(parent, resolver, parents)):
                reflective()
        if isinstance(node, ast.Call):
            functions = resolver.resolve(node.func)
            for method in sorted(functions & _PARAMETERS.keys()):
                names = _PARAMETERS[method]
                read: list[tuple[str | None, frozenset[str]]]
                if any(isinstance(item, ast.Starred) for item in node.args) or any(
                    keyword.arg is None for keyword in node.keywords
                ):
                    read = [(None, frozenset())] * len(names)
                else:
                    read = [resolver.text(item) for item in node.args[: len(names)]]
                    read += [(None, frozenset())] * (len(names) - len(read))
                    for keyword in node.keywords:
                        if keyword.arg in names:
                            read[names.index(keyword.arg)] = resolver.text(keyword.value)
                addresses.append((method, tuple(value for value, _names in read)))
                constants.append(tuple(found for _value, found in read))
            if functions & _DYNAMIC_IMPORTS:
                module = _dynamic_import(node, lambda item: resolver.text(item)[0])
                if module is None:
                    reflective()
                else:
                    references.add(module)
                    check(module)
                    unresolved = unresolved or module.startswith(_RUNTIME_INTERNALS)
            if functions & _DYNAMIC_CODE:
                reflective()
            target = node.args[0] if node.args and not isinstance(node.args[0], ast.Starred) else None
            attribute = resolver.choices(node.args[1]) if len(node.args) > 1 else None
            own = isinstance(target, ast.Name) and target.id in _OWN
            if functions & _GETATTR:
                if attribute is None:
                    # A principal holds identity values only.
                    held = resolver.resolve(target) if target is not None else set()
                    unresolved = unresolved or not held or not held <= {_PRINCIPAL}
                elif attribute & _REFLECTIVE_ATTRIBUTES:
                    reflective()
                elif attribute & _RAW_CLIENT_NAMES or attribute & _PRIVATE_ATTRIBUTES and not own:
                    unresolved = True
            if functions & _WRITERS and not own:
                if target is None or imported(resolver.resolve(target)):
                    reflective()
                for written in attribute if attribute is not None else [None]:
                    rebinds(written)
        handle = values & _HANDLES or (
            isinstance(node, (ast.Name, ast.Attribute)) and returns_handle(values) and not called
        )
        if handle and not _followed(node, values, resolver, parents):
            unresolved = True
    return _SourceReach(
        tuple(addresses), frozenset(references), tuple(relative), unresolved, tuple(constants),
        frozenset(writes) if writes is not None else None,
    )


def _reads_global(call: ast.AST | None, resolver: _ReachResolver, parents: Mapping[ast.AST, ast.AST]) -> bool:
    """Whether ``globals()`` is read only by a constant name, as ``globals().get(name)`` or ``globals()[name]``."""
    parent = parents.get(call) if call is not None else None
    if isinstance(parent, ast.Subscript):
        return resolver.global_name(parent) is not None
    if isinstance(parent, ast.Attribute):
        return resolver.global_name(parents.get(parent)) is not None
    return False


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
        # The runtime imports each module directory's code as one package, named by the directory.
        self._packages: dict[str, set[str]] = {}
        for path in self._files:
            self._by_name.setdefault(_dotted(path), set()).add(path)
            parts = path.split("/")
            if parts[0] == "modules" and len(parts) > 2:
                self._packages.setdefault(_module_package_root(parts[1]), set()).add(parts[1])
        self._readings: dict[str, _SourceReach] = {}
        self._sources: dict[str, _SourceReach] = {}
        self._rebound: tuple[frozenset[str] | None] | None = None

    def _reading(self, path: str) -> _SourceReach:
        if path not in self._readings:
            self._readings[path] = read_source(self._files[path])
        return self._readings[path]

    def _source(self, path: str) -> _SourceReach:
        if path not in self._sources:
            reach = self._reading(path)
            # Code the persistence guard rejects escapes the injected persistence contract.
            if path.startswith("modules/") and not reach.unresolved:
                try:
                    rejected = bool(scan_module_persistence({path: self._files[path], **self._extensions}, bounded=True))
                except (RecursionError, MemoryError):
                    rejected = True
                if rejected:
                    reach = replace(reach, unresolved=True)
            self._sources[path] = reach
        return self._sources[path]

    def _rebindable(self) -> frozenset[str] | None:
        """Names any app code may set on a module object; None when it may set any.

        Every app file runs in one process, so code anywhere in the app can
        rebind a constant another module reads.
        """
        if self._rebound is None:
            readings = [self._reading(path).writes for path in sorted(self._files)]
            names = None if any(writes is None for writes in readings) else frozenset().union(*filter(None, readings))
            self._rebound = (names,)
        return self._rebound[0]

    def _imports(self, path: str) -> set[str]:
        source = self._source(path)
        found: set[str] = set()
        for reference in source.references:
            parts = reference.split(".")
            if parts[0] == "app":
                parts = parts[1:]
            candidates = [parts] + [
                ["modules", directory, *parts[1:]] for directory in sorted(self._packages.get(parts[0] if parts else "", ()))
            ]
            for dotted in candidates:
                for end in range(1, len(dotted) + 1):
                    found |= self._by_name.get(".".join(dotted[:end]), set())
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
        try:
            return self._reach(self._collections, module_id, module_dir)
        except (RecursionError, MemoryError):
            return UNSCOPED

    def _reach(self, collections: _ContractCollections, module_id: str, module_dir: str) -> str:
        rebound = self._rebindable()
        pending = [path for path in self._files if path.startswith(f"{module_dir}/")]
        seen = set(pending)
        states = [collections.declared_for(module_id)]
        while pending:
            path = pending.pop()
            source = self._source(path)
            if source.unresolved:
                return UNSCOPED
            for (method, arguments), names in zip(source.addresses, source.constants, strict=True):
                # A constant that app code may rebind is not a constant.
                arguments = tuple(
                    None if found and (rebound is None or found & rebound) else argument
                    for argument, found in zip(arguments, names, strict=True)
                )
                states.append(collections.address(method, arguments))
            for imported in sorted(self._imports(path) - seen):
                seen.add(imported)
                pending.append(imported)
        return join(states)
