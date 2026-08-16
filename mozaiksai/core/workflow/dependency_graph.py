"""Shared deterministic dependency graph validation for workflow work items."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class DependencyOrder:
    ordered_ids: tuple[str, ...]


def deterministic_topological_order(
    items: Iterable[T],
    *,
    item_id: Callable[[T], str],
    dependencies: Callable[[T], Iterable[str]],
) -> DependencyOrder:
    """Return dependency-first order with deterministic lexical tie-breaking.

    Raises ``ValueError`` when item IDs are empty/duplicated, a dependency is
    missing, or the graph contains a cycle.
    """
    by_id: dict[str, T] = {}
    for item in items:
        identifier = str(item_id(item) or "").strip()
        if not identifier:
            raise ValueError("dependency graph items must have non-empty IDs")
        if identifier in by_id:
            raise ValueError(f"duplicate dependency graph item id: {identifier!r}")
        by_id[identifier] = item

    dependency_ids: dict[str, tuple[str, ...]] = {}
    for identifier, item in by_id.items():
        deps = tuple(sorted({str(dep or "").strip() for dep in dependencies(item) if str(dep or "").strip()}))
        missing = [dep for dep in deps if dep not in by_id]
        if missing:
            raise ValueError(
                f"dependency graph item {identifier!r} references unknown dependencies: {missing}"
            )
        if identifier in deps:
            raise ValueError(f"dependency graph item {identifier!r} cannot depend on itself")
        dependency_ids[identifier] = deps

    in_degree = {identifier: len(deps) for identifier, deps in dependency_ids.items()}
    dependents: dict[str, list[str]] = {identifier: [] for identifier in by_id}
    for identifier, deps in dependency_ids.items():
        for dep in deps:
            dependents[dep].append(identifier)

    ready = sorted(identifier for identifier, degree in in_degree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        identifier = ready.pop(0)
        ordered.append(identifier)
        for dependent in sorted(dependents[identifier]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
                ready.sort()

    if len(ordered) != len(by_id):
        remaining = sorted(set(by_id) - set(ordered))
        raise ValueError(f"dependency cycle detected among items: {remaining}")

    return DependencyOrder(ordered_ids=tuple(ordered))


__all__ = ["DependencyOrder", "deterministic_topological_order"]
