"""Filesystem containment guards shared by every writer that stages user paths.

Single owner for the "stay inside this root" policy. Copies of these checks
drift: the same guard raised three different exception types across five
modules before consolidation.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

__all__ = ["contains_symlink_component", "is_relative_to", "resolve_inside"]


def is_relative_to(child: Path, parent: Path) -> bool:
    """True when `child` sits at or under `parent`, without resolving either."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def contains_symlink_component(root: Path, relative_path: str) -> bool:
    """True when `root` or any existing component of `relative_path` is a symlink.

    Checked component by component: a symlink partway down the path escapes the
    root even when the fully resolved target appears contained.
    """
    if root.is_symlink():
        return True
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def resolve_inside(
    parent: Path,
    child: Path,
    *,
    error: type[Exception] = ValueError,
    message: str = "Refusing to write outside allowed workspace",
) -> Path:
    """Resolve `child` and require the result to stay under `parent`.

    Callers pass their own `error` so a containment breach surfaces in the
    domain that detected it rather than as a bare ValueError.
    """
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if not is_relative_to(child_resolved, parent_resolved):
        raise error(f"{message}: {child}")
    return child_resolved
