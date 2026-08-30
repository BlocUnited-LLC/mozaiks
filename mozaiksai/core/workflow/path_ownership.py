"""Shared owned-path normalization and collision checks for workflow work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .generator_support.code_files import safe_relpath

_GLOB_CHARS = frozenset("*?[")
_SECRET_PATH_TERMS = frozenset({".env", "secret", "vault", "credential", "key", ".pem", ".p12", ".pfx"})


class PathCollisionKind(StrEnum):
    DIRECT_PATH = "direct_path"
    PARENT_CHILD = "parent_child"
    CASE_COLLISION = "case_collision"


@dataclass(frozen=True)
class PathCollision:
    kind: PathCollisionKind
    path: str
    owner_ids: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class PathCollisionReport:
    collisions: tuple[PathCollision, ...]

    @property
    def has_collisions(self) -> bool:
        return bool(self.collisions)


def normalize_owned_path(path: str) -> str:
    """Normalize one owned path and reject unsafe or ambiguous forms."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"owned path must be a non-empty string: {path!r}")
    cleaned = path.strip()
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":" and cleaned[0].isalpha()):
        raise ValueError(f"absolute path not allowed in owned path: {path!r}")
    if any(part == ".." for part in cleaned.replace("\\", "/").split("/")):
        raise ValueError(f"path traversal not allowed in owned path: {path!r}")
    if any(char in cleaned for char in _GLOB_CHARS):
        raise ValueError(f"glob characters not allowed in owned path: {path!r}")
    normalized = safe_relpath(cleaned)
    if not normalized:
        raise ValueError(f"unsafe owned path: {path!r}")
    lower = normalized.lower()
    for term in _SECRET_PATH_TERMS:
        if term in lower:
            raise ValueError(f"secret-term path not allowed in owned path: {path!r}")
    return normalized.rstrip("/")


def normalize_owned_paths(value: Any, *, reject_duplicates: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    paths: list[str] = []
    seen: set[str] = set()
    lower_seen: dict[str, str] = {}
    for item in value:
        path = normalize_owned_path(str(item or ""))
        if path in seen:
            if reject_duplicates:
                raise ValueError(f"duplicate owned path: {path!r}")
            continue
        lower = path.lower()
        if lower in lower_seen and lower_seen[lower] != path:
            raise ValueError(f"case-normalization collision: {lower_seen[lower]!r} and {path!r}")
        lower_seen[lower] = path
        seen.add(path)
        paths.append(path)
    return tuple(paths)


def path_is_within_owned(path: str, owned_paths: set[str]) -> bool:
    normalized = normalize_owned_path(path)
    return any(normalized == owned or normalized.startswith(f"{owned}/") for owned in owned_paths)


def detect_owned_path_collisions(owner_paths: dict[str, tuple[str, ...]]) -> PathCollisionReport:
    path_owners: dict[str, list[str]] = {}
    for owner_id, paths in owner_paths.items():
        for path in paths:
            path_owners.setdefault(path, []).append(owner_id)

    collisions: list[PathCollision] = []
    for path, owners in sorted(path_owners.items()):
        unique = tuple(sorted(set(owners)))
        if len(unique) > 1:
            collisions.append(
                PathCollision(
                    kind=PathCollisionKind.DIRECT_PATH,
                    path=path,
                    owner_ids=unique,
                    detail=f"path {path!r} is owned by multiple work items",
                )
            )

    all_paths = sorted(path_owners)
    for index, parent in enumerate(all_paths):
        for child in all_paths[index + 1 :]:
            if not child.startswith(f"{parent}/"):
                continue
            parent_owners = set(path_owners[parent])
            child_owners = set(path_owners[child])
            if parent_owners != child_owners:
                collisions.append(
                    PathCollision(
                        kind=PathCollisionKind.PARENT_CHILD,
                        path=child,
                        owner_ids=tuple(sorted(parent_owners | child_owners)),
                        detail=f"parent path {parent!r} and child path {child!r} have different owners",
                    )
                )

    lower_paths: dict[str, list[str]] = {}
    for path in path_owners:
        lower_paths.setdefault(path.lower(), []).append(path)
    for colliding_paths in lower_paths.values():
        if len(colliding_paths) <= 1:
            continue
        case_owner_ids: set[str] = set()
        for path in colliding_paths:
            case_owner_ids.update(path_owners[path])
        collisions.append(
            PathCollision(
                kind=PathCollisionKind.CASE_COLLISION,
                path=sorted(colliding_paths)[0],
                owner_ids=tuple(sorted(case_owner_ids)),
                detail=f"case-normalization collision: {sorted(colliding_paths)}",
            )
        )

    collisions.sort(key=lambda item: (item.kind.value, item.path, item.owner_ids))
    return PathCollisionReport(collisions=tuple(collisions))


__all__ = [
    "PathCollision",
    "PathCollisionKind",
    "PathCollisionReport",
    "detect_owned_path_collisions",
    "normalize_owned_path",
    "normalize_owned_paths",
    "path_is_within_owned",
]
