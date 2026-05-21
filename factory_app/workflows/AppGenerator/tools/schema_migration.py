"""
Schema Migration — diff and migration generation for AppGenerator refinement runs.

Compares prior database intent (from the artifact being refined) against the
new database intent produced by DatabaseAgent and generates a typed migration
file. The migration is staged under config/database_migrations/ for later
runtime/platform application instead of being applied by AppGenerator.

Change class behaviour:
  patch   — diff schemas; apply additive changes only; warn on destructive
  design  — schema unchanged; skip DatabaseAgent entirely
  feature — expect additive only (new collections/fields); error on destructive
  core    — new upstream concept revision; no in-place diff needed
  null    — greenfield; no prior intent to diff
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

SchemaDiff = Dict[str, Any]   # structured diff output
Migration  = Dict[str, Any]   # migration file written to generated bundle


# ---------------------------------------------------------------------------
# Schema diffing
# ---------------------------------------------------------------------------

def _collection_field_map(collection: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return {field_name: field_def} for a collection definition."""
    return {col["name"]: col for col in collection.get("columns", [])}


def _index_set(collection: Dict[str, Any], key: str) -> Set[str]:
    """Return a set of index/constraint names for comparison."""
    return {str(entry) for entry in collection.get(key, [])}


def diff_schemas(
    old_schema: Optional[Dict[str, Any]],
    new_schema: Dict[str, Any],
) -> SchemaDiff:
    """
    Diff two database intent/schema objects.

    Returns a structured diff with:
      new_collections      — collections added in new_schema
      removed_collections  — collections in old that are absent in new (destructive)
      modified_collections — per-collection field/index diffs
      is_additive_only     — True if no destructive changes
      has_changes          — True if anything changed at all
    """
    if old_schema is None:
        # No previous schema — treat as greenfield, no diff needed
        return {
            "has_changes": True,
            "is_additive_only": True,
            "new_collections": [c["name"] for c in new_schema.get("collections", [])],
            "removed_collections": [],
            "modified_collections": [],
            "destructive_warnings": [],
        }

    old_cols: Dict[str, Dict] = {c["name"]: c for c in old_schema.get("collections", [])}
    new_cols: Dict[str, Dict] = {c["name"]: c for c in new_schema.get("collections", [])}

    new_collection_names = [n for n in new_cols if n not in old_cols]
    removed_collection_names = [n for n in old_cols if n not in new_cols]
    shared_names = [n for n in new_cols if n in old_cols]

    modified: List[Dict[str, Any]] = []
    for name in shared_names:
        old_c = old_cols[name]
        new_c = new_cols[name]

        old_fields = _collection_field_map(old_c)
        new_fields = _collection_field_map(new_c)

        added_fields   = [f for f in new_fields if f not in old_fields]
        removed_fields = [f for f in old_fields if f not in new_fields]
        modified_fields = [
            f for f in new_fields
            if f in old_fields and new_fields[f] != old_fields[f]
        ]
        added_indexes    = list(_index_set(new_c, "indices") - _index_set(old_c, "indices"))
        removed_indexes  = list(_index_set(old_c, "indices") - _index_set(new_c, "indices"))
        added_constraints    = list(_index_set(new_c, "constraints") - _index_set(old_c, "constraints"))
        removed_constraints  = list(_index_set(old_c, "constraints") - _index_set(new_c, "constraints"))

        has_collection_changes = any([
            added_fields, removed_fields, modified_fields,
            added_indexes, removed_indexes,
            added_constraints, removed_constraints,
        ])
        if has_collection_changes:
            modified.append({
                "name": name,
                "added_fields":         [new_fields[f] for f in added_fields],
                "removed_fields":       removed_fields,
                "modified_fields":      [new_fields[f] for f in modified_fields],
                "added_indexes":        added_indexes,
                "removed_indexes":      removed_indexes,
                "added_constraints":    added_constraints,
                "removed_constraints":  removed_constraints,
            })

    destructive_warnings: List[str] = []
    for name in removed_collection_names:
        destructive_warnings.append(f"Collection '{name}' removed — existing data will be orphaned.")
    for col_diff in modified:
        for field in col_diff["removed_fields"]:
            destructive_warnings.append(
                f"Field '{field}' removed from '{col_diff['name']}' — existing data in this field will be lost."
            )

    has_changes = bool(new_collection_names or removed_collection_names or modified)
    is_additive_only = not bool(removed_collection_names or
                                any(d["removed_fields"] for d in modified))

    return {
        "has_changes": has_changes,
        "is_additive_only": is_additive_only,
        "new_collections":      new_collection_names,
        "removed_collections":  removed_collection_names,
        "modified_collections": modified,
        "destructive_warnings": destructive_warnings,
    }


# ---------------------------------------------------------------------------
# Migration file generation
# ---------------------------------------------------------------------------

def generate_migration(
    diff: SchemaDiff,
    *,
    app_id: str,
    change_class: Optional[str],
    new_schema: Dict[str, Any],
    old_schema: Optional[Dict[str, Any]] = None,
) -> Migration:
    """
    Generate a migration document from a schema diff.

    The migration is written to:
      config/database_migrations/migration_{timestamp}.json

    The document is runtime-compatible (passes _validate_migration) and
    contains an ``operations`` list with ``ensure_collection`` entries for
    every new collection in the diff. The ``metadata`` field carries the
    human-readable diff for auditing purposes.

    It is NOT applied here — call apply_migration_safe() or pass it to
    apply_schema_migration() in backend_tools.py.
    """
    now = datetime.now(timezone.utc)
    migration_id = f"m_{now.strftime('%Y%m%d_%H%M%S')}"

    migration: Migration = {
        "migration_id":   migration_id,
        # schema_version satisfies the runtime _validate_migration check which
        # requires either "version" or "schema_version" to be present.
        "schema_version": "mozaiks.migration.v1",
        # operations is the runtime-executable list; ensure_collection is a
        # no-op in the current runtime (collections are created lazily) but
        # the field must be present and be a list.
        "operations":     _build_ensure_collection_ops(diff, new_schema),
        # metadata carries the human-readable diff for auditing; it is not
        # processed by the runtime migration loader.
        "metadata": {
            "app_id":        app_id,
            "change_class":  change_class or "unknown",
            "created_at":    now.isoformat(),
            "safety": {
                "is_additive_only":    diff["is_additive_only"],
                "destructive_changes": diff["removed_collections"] + [
                    f"{d['name']}.{f}"
                    for d in diff["modified_collections"]
                    for f in d["removed_fields"]
                ],
                "warnings": diff["destructive_warnings"],
            },
            "changes": {
                "new_collections":      _new_collection_definitions(diff, new_schema),
                "removed_collections":  diff["removed_collections"],
                "modified_collections": diff["modified_collections"],
            },
        },
    }
    return migration


def _build_ensure_collection_ops(
    diff: SchemaDiff,
    new_schema: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return ensure_collection runtime operations for every new collection.

    Each operation uses the collection's own ``module_id`` and ``entity_name``
    fields when present; otherwise it falls back to using the collection name
    for both. The runtime ``ensure_collection`` op is a no-op today (MongoDB
    creates collections lazily) but must be present in the operations list.
    """
    new_names = set(diff["new_collections"])
    ops: List[Dict[str, Any]] = []
    for col in new_schema.get("collections", []):
        if col.get("name") not in new_names:
            continue
        module_id = str(col.get("module_id") or col.get("name") or "").strip()
        entity_name = str(col.get("entity_name") or col.get("name") or "").strip()
        if not module_id or not entity_name:
            continue
        ops.append({
            "type": "ensure_collection",
            "module_id": module_id,
            "entity_name": entity_name,
        })
    return ops


def _new_collection_definitions(
    diff: SchemaDiff,
    new_schema: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return full collection definitions for collections being added (metadata only)."""
    new_names = set(diff["new_collections"])
    return [c for c in new_schema.get("collections", []) if c["name"] in new_names]


# ---------------------------------------------------------------------------
# Migration path in the generated bundle
# ---------------------------------------------------------------------------

def migration_file_path(migration_id: str) -> str:
    """Return the relative path for the migration file in the app bundle."""
    return f"config/database_migrations/{migration_id}.json"


# ---------------------------------------------------------------------------
# Safety-gated migration application helper
# ---------------------------------------------------------------------------

def apply_migration_safe(
    diff: SchemaDiff,
    *,
    change_class: Optional[str],
    allow_destructive: bool = False,
) -> Dict[str, Any]:
    """
    Determine what ops are safe to apply given the change class and diff.

    Returns:
      apply       — True if migration should proceed
      skip_reason — human-readable skip reason (if apply=False)
      safe_ops    — dict describing what will be applied
      blocked_ops — dict of ops blocked due to destructive risk
      warnings    — list of warning strings to surface to the user
    """
    if not diff["has_changes"]:
        return {
            "apply": False,
            "skip_reason": "No schema changes detected — database unchanged.",
            "safe_ops": {},
            "blocked_ops": {},
            "warnings": [],
        }

    # design class: skip DB entirely
    if change_class == "design":
        return {
            "apply": False,
            "skip_reason": "change_class=design — schema intentionally unchanged.",
            "safe_ops": {},
            "blocked_ops": {},
            "warnings": [],
        }

    warnings = list(diff["destructive_warnings"])
    blocked_ops: Dict[str, Any] = {}

    if not diff["is_additive_only"] and not allow_destructive:
        blocked_ops["removed_collections"] = diff["removed_collections"]
        blocked_ops["removed_fields"] = [
            {"collection": d["name"], "fields": d["removed_fields"]}
            for d in diff["modified_collections"]
            if d["removed_fields"]
        ]
        warnings.append(
            "Destructive changes were blocked. Set allow_destructive=True to apply them, "
            "or run a 'core' rebuild to reprovision from scratch."
        )

    # feature class: block destructive even if allow_destructive is set
    if change_class == "feature" and not diff["is_additive_only"]:
        blocked_ops = {
            "removed_collections": diff["removed_collections"],
            "removed_fields": [
                {"collection": d["name"], "fields": d["removed_fields"]}
                for d in diff["modified_collections"]
                if d["removed_fields"]
            ],
        }
        warnings.append(
            "change_class=feature only allows additive schema changes. "
            "Destructive changes are always blocked. Use 'core' to reprovision."
        )

    safe_ops = {
        "new_collections":  diff["new_collections"],
        "added_fields": [
            {"collection": d["name"], "fields": d["added_fields"]}
            for d in diff["modified_collections"]
            if d["added_fields"]
        ],
        "added_indexes": [
            {"collection": d["name"], "indexes": d["added_indexes"]}
            for d in diff["modified_collections"]
            if d["added_indexes"]
        ],
        "added_constraints": [
            {"collection": d["name"], "constraints": d["added_constraints"]}
            for d in diff["modified_collections"]
            if d["added_constraints"]
        ],
    }

    return {
        "apply": True,
        "skip_reason": None,
        "safe_ops": safe_ops,
        "blocked_ops": blocked_ops,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Bundle injection helper
# ---------------------------------------------------------------------------

def inject_migration_into_bundle(
    files_map: Dict[str, str],
    migration: Migration,
) -> None:
    """
    Write the migration JSON into the generated app bundle (files_map in-place).
    Also ensures config/database_migrations/.gitkeep exists.
    """
    path = migration_file_path(migration["migration_id"])
    files_map[path] = json.dumps(migration, indent=2)
    gitkeep = "config/database_migrations/.gitkeep"
    if gitkeep not in files_map:
        files_map[gitkeep] = ""
