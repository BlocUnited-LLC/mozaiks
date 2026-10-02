"""Normalize model-authored backend code around the code-rendered module layers.

Code renders a persistent module's canonical repository functions and, when an
action declares one, its canonical write event. What a model wrote beside them
is reconciled here instead of reaching runtime:

- a model-authored top-level function or class in ``backend/repo.py`` that
  neither business logic nor any import-time statement reaches (a leftover
  repository class, a duplicate CRUD helper) is removed and the removal is
  logged; decorated definitions, registrations and dynamic imports keep code; a referenced repository class, or referenced code
  calling a Motor-only method, is rejected with the site and the replacement;
- a ``ctx.emit`` literal naming a declared event under another spelling is
  rewritten to the declared type, and a write hook emitting the event its
  canonical write already emits is dropped.

Model-authored Python that does not parse is rejected with its path and line
before any of this runs (``reject_unparseable_python``). These cleanups are
optional: one whose own result would not parse keeps the model's file
unchanged and logs a warning. Required renderings parse their result through
``parse_rendered_python``, which names the path, line and rendered snippet.
"""
from __future__ import annotations

import ast
import io
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
_SNIPPET_CONTEXT_LINES = 3


class RenderedPythonError(RuntimeError):
    """Code rendering produced Python that does not parse: a builder defect, never model work."""


def source_lines(source: str) -> list[str]:
    """Split ``source`` at the line breaks ``ast`` counts, keeping them.

    ``str.splitlines`` also breaks at form feeds, vertical tabs, ``\\x1c``-``\\x1e``,
    ``\\x85``, ``\\u2028`` and ``\\u2029``, which Python source treats as ordinary
    characters. Indexing that list by an AST line number edits the wrong line.
    """
    return io.StringIO(source, newline="").readlines()


def syntax_error_diagnostic(path: str, source: str, exc: SyntaxError | ValueError) -> str:
    """``<path>:<line>: <msg>`` followed by the offending source line."""
    if not isinstance(exc, SyntaxError) or exc.lineno is None:
        return f"{path}: {exc}"
    lines = source_lines(source)
    offending = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else (exc.text or "")
    message = f"{path}:{exc.lineno}: {exc.msg}"
    return f"{message}\n    {offending.strip()}" if offending.strip() else message


def reject_unparseable_python(files: Mapping[str, str]) -> None:
    """Reject every ``.py`` file that does not parse, all of them in one message."""
    errors: list[str] = []
    for path, source in sorted(files.items()):
        if not path.endswith(".py"):
            continue
        try:
            ast.parse(source, filename=path)
        except (SyntaxError, ValueError) as exc:  # ValueError: a null byte on Python 3.11
            errors.append(syntax_error_diagnostic(path, source, exc))
    if errors:
        raise ValueError("\n".join(errors))


def parse_rendered_python(path: str, source: str) -> ast.Module:
    """Parse code-rendered Python; a failure names the path, line and rendered snippet."""
    try:
        return ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as exc:
        lineno = getattr(exc, "lineno", None) or 1
        lines = source_lines(source)
        start = max(1, lineno - _SNIPPET_CONTEXT_LINES)
        snippet = "".join(
            f"{number:>5} | {lines[number - 1].rstrip()}\n"
            for number in range(start, min(len(lines), lineno + _SNIPPET_CONTEXT_LINES) + 1)
        )
        raise RenderedPythonError(
            f"code rendering produced Python that does not parse: "
            f"{syntax_error_diagnostic(path, source, exc).splitlines()[0]}\n{snippet}".rstrip()
        ) from exc


def _top_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    return {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not (node.name.startswith("__") and node.name.endswith("__"))
    }


def _import_time_roots(tree: ast.Module) -> set[str]:
    """Names live at import: used by any top-level statement other than an undecorated definition.

    Assignments, subscript registrations (``HANDLERS["x"] = f``), module-level
    ``if`` blocks and decorated definitions all run when the module is imported,
    so everything they touch is used even when no business logic names it.
    """
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.decorator_list:
                continue
            roots.add(node.name)
        roots |= _names(node)
    return roots


def _imports_dynamically(tree: ast.AST) -> bool:
    """True when code can reach a module by computed name (importlib, __import__, sys.modules)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {"__import__", "import_module"}:
            return True
        if isinstance(node, ast.Attribute) and (
            node.attr == "import_module"
            or (node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys")
        ):
            return True
        if isinstance(node, ast.ImportFrom) and (
            node.module == "importlib" or (node.module == "sys" and any(a.name == "modules" for a in node.names))
        ):
            return True
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "importlib" for alias in node.names):
            return True
    return False


def _names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def repository_references(sources: Mapping[str, str]) -> tuple[dict[str, str], bool]:
    """Map each ``backend/repo.py`` name the given sources use to its first use site.

    The flag is False when a source uses the repository opaquely (a star import,
    the module object passed around or reflected on, a dynamic import through
    importlib, ``__import__`` or ``sys.modules``, or unparseable source), so no
    definition can be proven unused.
    """
    sites: dict[str, str] = {}
    for path, source in sources.items():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return sites, False
        if _imports_dynamically(tree):
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
    provable = provable and not _imports_dynamically(tree)
    functions = _top_level_functions(tree)
    rendered = sorted(name for name in code_owned if name in functions and not name.startswith("_"))
    rendered_clause = f"; code renders {', '.join(rendered)}" if rendered else ""
    errors: list[str] = []
    for name, site in sorted(sites.items()):
        node = functions.get(name)
        if isinstance(node, ast.ClassDef) and name not in code_owned:
            errors.append(
                f"{site}: uses repo.{name}, a model-authored repository class ({path}:{node.lineno}). The "
                f"repository of persistent module {module_id!r} is module-level functions{rendered_clause}. "
                f"Write the persistence this class performs as module-level repo functions taking ctx, over "
                f"ctx.persistence.collection({module_id!r}, <collection>), and call them as repo.<function>(ctx, ...)."
            )

    def closure(start: set[str]) -> set[str]:
        reached: set[str] = set()
        pending = [name for name in start if name in functions]
        while pending:
            name = pending.pop()
            if name in reached:
                continue
            reached.add(name)
            pending.extend(used for used in _names(functions[name]) if used in functions and used not in reached)
        return reached

    referenced = closure({*code_owned, *sites})
    live = closure({*code_owned, *sites, *_import_time_roots(tree)})
    # Only code a business use site reaches is held to the persistence API here.
    for name in sorted(referenced - code_owned):
        for call in ast.walk(functions[name]):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in MOTOR_ONLY_METHODS:
                used_by = sites.get(name, "a referenced repo definition")
                errors.append(
                    f"{path}:{call.lineno}: {name} (used by {used_by}) calls {call.func.attr}, which is not a "
                    f"PersistenceCollection method; use {PERSISTENCE_COLLECTION_METHODS}."
                )
    if errors:
        raise ValueError("\n".join(dict.fromkeys(errors)))
    # An opaque use proves nothing dead. Otherwise only an undecorated top-level
    # function or class that nothing live reaches is removed.
    removed = [] if not provable else sorted(
        name for name, node in functions.items() if name not in live and not node.decorator_list
    )
    if not removed:
        return source
    lines = source_lines(source)
    spans = [(functions[name].lineno - 1, functions[name].end_lineno or functions[name].lineno) for name in removed]
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    try:
        pruned = _drop_unused_imports("".join(lines))
        pruned = re.sub(r"\n{4,}", "\n\n\n", pruned).lstrip("\n")
        ast.parse(pruned, filename=path)
    except SyntaxError as exc:
        # Pruning is a cleanup; it must never turn a parseable model file into one that is not.
        logger.warning(
            "REPO_PRUNE_SKIPPED: %s: removing %s would leave Python that does not parse (%s); "
            "the model-authored file is kept unchanged",
            path, removed, exc,
        )
        return source
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
    lines = source_lines(source)
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
    lines = source_lines(source)
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
    try:
        ast.parse(rendered, filename=path)
    except SyntaxError as exc:
        # Normalization is a cleanup; it must never turn a parseable model file into one that is not.
        logger.warning(
            "EMIT_LITERAL_NORMALIZATION_SKIPPED: %s: %s would leave Python that does not parse (%s); "
            "the model-authored file is kept unchanged",
            path, "; ".join(notes), exc,
        )
        return source
    for note in notes:
        logger.info("EMIT_LITERAL_NORMALIZED: %s: %s", path, note)
    return rendered


__all__ = [
    "MOTOR_ONLY_METHODS",
    "PERSISTENCE_COLLECTION_METHODS",
    "RenderedPythonError",
    "parse_rendered_python",
    "prune_repository",
    "reconcile_emit_literals",
    "reject_unparseable_python",
    "repository_references",
    "source_lines",
    "syntax_error_diagnostic",
]
