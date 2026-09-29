"""Normalize model-authored backend code around the code-rendered module layers.

Code renders a persistent module's canonical repository functions and, when an
action declares one, its canonical write event. What a model wrote beside them
is reconciled here instead of reaching runtime:

- a model-authored definition in ``backend/repo.py`` that no business logic
  references (a leftover repository class, a duplicate CRUD helper) is removed
  and the removal is logged; a referenced repository class, or referenced code
  calling a Motor-only method, is rejected with the site and the replacement;
- a ``ctx.emit`` literal naming a declared event under another spelling is
  rewritten to the declared type, and a write hook emitting the event its
  canonical write already emits is dropped.
"""
from __future__ import annotations

import ast
import logging
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet

from mozaiksai.core.runtime.app.module_loader import CANONICAL_EVENT_PREFIXES

from .module_action_inventory import canonical_write_event_for

logger = logging.getLogger(__name__)

# Motor cursor/collection API that the runtime PersistenceCollection does not provide.
MOTOR_ONLY_METHODS = frozenset({
    "find", "count_documents", "find_one_and_update", "find_one_and_delete", "estimated_document_count", "to_list",
})
PERSISTENCE_COLLECTION_METHODS = (
    "find_one, find_many (returns a list), count, aggregate, insert_one, update_one, delete_one or delete_many"
)
_HOOK = re.compile(r"^(before|after)_(create|update|delete)_([A-Za-z0-9_]+)$")


def _top_level_definitions(tree: ast.Module) -> dict[str, ast.stmt]:
    definitions: dict[str, ast.stmt] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            if len(names) == len(targets):
                definitions.update(dict.fromkeys(names, node))
    return {name: node for name, node in definitions.items() if not (name.startswith("__") and name.endswith("__"))}


def _names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def repository_references(sources: Mapping[str, str]) -> tuple[dict[str, str], bool]:
    """Map each ``backend/repo.py`` name the given sources use to its first use site.

    The flag is False when a source uses the repository opaquely (a star import,
    the module object passed around or reflected on, or unparseable source), so
    no definition can be proven unused.
    """
    sites: dict[str, str] = {}
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return sites, False
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imports_repo_names = (node.level == 1 and module == "repo") or module.endswith(".backend.repo")
                imports_repo_module = (node.level == 1 and not module) or module.endswith(".backend")
                for alias in node.names:
                    if imports_repo_names:
                        if alias.name == "*":
                            return sites, False
                        sites.setdefault(alias.name, f"{path}:{node.lineno}")
                    elif imports_repo_module and alias.name == "repo":
                        aliases.add(alias.asname or "repo")
            elif isinstance(node, ast.Import) and any(alias.name.endswith(".backend.repo") for alias in node.names):
                return sites, False
        if not aliases:
            continue
        bases = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
                sites.setdefault(node.attr, f"{path}:{node.lineno}")
            elif isinstance(node, ast.Name) and node.id in aliases and id(node) not in bases:
                return sites, False
    return sites, True


def prune_repository(
    path: str, source: str, *, module_id: str, code_owned: set[str], business_sources: Mapping[str, str],
) -> str:
    """Remove model-authored repo definitions no business logic references.

    ``code_owned`` names the functions code rendered into ``source``;
    ``business_sources`` are the module's other backend files. A referenced
    model-authored class, or referenced model code calling a Motor-only method,
    is rejected with every site in one message.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source  # The module implementation gate reports syntax errors.
    sites, provable = repository_references(business_sources)
    definitions = _top_level_definitions(tree)
    rendered = sorted(name for name in code_owned if name in definitions and not name.startswith("_"))
    rendered_clause = f"; code renders {', '.join(rendered)}" if rendered else ""
    errors: list[str] = []
    for name, site in sorted(sites.items()):
        node = definitions.get(name)
        if isinstance(node, ast.ClassDef) and name not in code_owned:
            errors.append(
                f"{site}: uses repo.{name}, a model-authored repository class ({path}:{node.lineno}). The "
                f"repository of persistent module {module_id!r} is module-level functions{rendered_clause}. "
                f"Write the persistence this class performs as module-level repo functions taking ctx, over "
                f"ctx.persistence.collection({module_id!r}, <collection>), and call them as repo.<function>(ctx, ...)."
            )
    reached: set[str] = set()
    pending = [name for name in {*code_owned, *sites} if name in definitions]
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.extend(used for used in _names(definitions[name]) if used in definitions and used not in reached)
    # An opaque use proves nothing dead: everything is kept, and only code a use
    # site actually reaches is held to the persistence API here.
    keep = reached if provable else set(definitions)
    for name in sorted(reached - code_owned):
        for call in ast.walk(definitions[name]):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in MOTOR_ONLY_METHODS:
                used_by = sites.get(name, "a referenced repo definition")
                errors.append(
                    f"{path}:{call.lineno}: {name} (used by {used_by}) calls {call.func.attr}, which is not a "
                    f"PersistenceCollection method; use {PERSISTENCE_COLLECTION_METHODS}."
                )
    if errors:
        raise ValueError("\n".join(dict.fromkeys(errors)))
    dead = {id(node): node for name, node in definitions.items() if name not in keep}
    # A statement binding several names stays when any of them is kept.
    kept_nodes = {id(definitions[name]) for name in keep}
    removed = sorted(name for name, node in definitions.items() if id(node) in dead and id(node) not in kept_nodes)
    if not removed:
        return source
    lines = source.splitlines(keepends=True)
    spans = []
    for node in dead.values():
        if id(node) in kept_nodes:
            continue
        decorators = getattr(node, "decorator_list", [])
        spans.append((min([node.lineno, *(item.lineno for item in decorators)]) - 1, node.end_lineno or node.lineno))
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    pruned = _drop_unused_imports("".join(lines))
    pruned = re.sub(r"\n{4,}", "\n\n\n", pruned).lstrip("\n")
    ast.parse(pruned)
    logger.warning(
        "REPO_CODE_DISCARDED: %s: removed model-authored definitions %s that no business logic references; "
        "the canonical repository of %s is code-rendered",
        path, removed, module_id,
    )
    return pruned


def _drop_unused_imports(source: str) -> str:
    tree = ast.parse(source)
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    used.update(
        node.value.id for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    )
    lines = source.splitlines(keepends=True)
    for node in reversed(tree.body):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound = [
                (alias.asname or alias.name.split(".")[0]) for alias in node.names
            ]
            if bound and all(name not in used and name != "*" for name in bound):
                del lines[node.lineno - 1:node.end_lineno or node.lineno]
    return "".join(lines)


def reconcile_emit_literals(
    path: str, source: str, *, declared: set[str], aliases: Mapping[str, str], write_events: Mapping[str, str],
    canonical_events: AbstractSet[str] = frozenset(),
) -> str:
    """Point ``ctx.emit`` literals at declared event types.

    ``write_events`` maps each canonical write action id to the event it emits;
    a hook of that write emitting the same event would publish it twice, so the
    hook's emit statement is removed. ``canonical_events`` are all canonical
    write events of the module: emitting one the module does not declare is
    rejected, because whether that event exists is the design's decision.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    lines = source.splitlines(keepends=True)
    renames: list[tuple[int, int, int, str]] = []
    removals: list[tuple[int, int, str]] = []
    notes: list[str] = []
    errors: list[str] = []
    for function in tree.body:
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        hook = _HOOK.match(function.name) if not isinstance(function, ast.ClassDef) else None
        hook_event = write_events.get(f"{hook[2]}_{hook[3]}") if hook else None
        statements = {
            id(node.value.value): node for node in ast.walk(function)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Await)
        }
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "emit"
                and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
            ):
                continue
            literal = call.args[0].value
            target = literal
            if literal not in declared:
                canonical = canonical_write_event_for(literal, aliases)
                prefixed = f"domain.{literal}"
                if canonical in declared:
                    target = canonical
                elif not literal.startswith(CANONICAL_EVENT_PREFIXES) and prefixed in declared:
                    target = prefixed
                elif canonical in canonical_events:
                    errors.append(
                        f"{path}:{call.lineno}: {function.name} emits {literal!r}, which names the canonical write "
                        f"event {canonical!r}; the module contract does not declare it. Code emits a canonical write "
                        "event itself when the approved design or module contract names it; remove this emit."
                    )
                    continue
            if hook_event is not None and target == hook_event:
                statement = statements.get(id(call))
                if statement is None:
                    errors.append(
                        f"{path}:{call.lineno}: {function.name} emits {literal!r}, which its canonical write already "
                        f"emits as {hook_event!r} after this hook returns; remove the emit from the hook."
                    )
                    continue
                removals.append((statement.lineno, statement.end_lineno or statement.lineno, " " * statement.col_offset))
                notes.append(f"{function.name} no longer emits {literal!r}; its canonical write emits {hook_event!r}")
                continue
            if target != literal:
                node = call.args[0]
                if node.lineno != node.end_lineno:
                    continue
                renames.append((node.lineno, node.col_offset, node.end_col_offset or node.col_offset, target))
                notes.append(f"{function.name} emits {literal!r} -> {target!r}")
    if errors:
        raise ValueError("\n".join(errors))
    if not renames and not removals:
        return source
    for lineno, start, end, target in sorted(renames, reverse=True):
        line = lines[lineno - 1]
        encoded = line.encode("utf-8")
        lines[lineno - 1] = (encoded[:start] + repr(target).encode("utf-8") + encoded[end:]).decode("utf-8")
    for start, end, indent in sorted(removals, reverse=True):
        lines[start - 1:end] = []
        # A hook left without statements still returns: None leaves the payload unchanged.
        try:
            ast.parse("".join(lines))
        except SyntaxError:
            lines.insert(start - 1, f"{indent}return None\n")
    rendered = "".join(lines)
    ast.parse(rendered)
    for note in notes:
        logger.info("EMIT_LITERAL_NORMALIZED: %s: %s", path, note)
    return rendered


__all__ = [
    "MOTOR_ONLY_METHODS",
    "PERSISTENCE_COLLECTION_METHODS",
    "prune_repository",
    "reconcile_emit_literals",
    "repository_references",
]
