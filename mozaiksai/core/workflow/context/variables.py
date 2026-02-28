# ==============================================================================
# FILE: core/workflow/context_variables.py
# DESCRIPTION: Context variable loading for the Mozaiks runtime (agent-centric schema)
# ==============================================================================

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .adapter import create_context_container
from .data_entity import DataEntityManager
from .db_adapters import get_db_adapter
from .schema import (
    ContextVariablesPlan,
    ContextVariableDefinition,
    load_context_variables_config,
)
from ..workflow_manager import workflow_manager
from logs.logging_config import get_workflow_logger

business_logger = get_workflow_logger("context_variables")

_TRUE_FLAG_VALUES = {"1", "true", "yes", "on"}
TRUNCATE_CHARS = int(os.getenv("CONTEXT_SCHEMA_TRUNCATE_CHARS", "4000") or 4000)
_FILE_CONTEXT_ALLOW_OUTSIDE_ROOT = os.getenv("CONTEXT_FILE_ALLOW_OUTSIDE_ROOT", "false").strip().lower() in _TRUE_FLAG_VALUES


def _find_repo_root() -> Path:
    """Best-effort repo root discovery.

    We avoid trusting Path.cwd() because runtimes can launch from arbitrary working dirs.
    This searches upward from this module for common repo markers.
    """

    try:
        here = Path(__file__).resolve()
    except Exception:  # pragma: no cover
        return Path.cwd().resolve()

    for parent in [here] + list(here.parents):
        try:
            if (parent / "workflows").is_dir() and (parent / "core").is_dir() and (parent / "requirements.txt").exists():
                return parent
        except Exception:
            continue

    # Fallback
    return Path.cwd().resolve()


def _is_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate_resolved = candidate.resolve()
        root_resolved = root.resolve()
    except Exception:
        return False
    return candidate_resolved == root_resolved or root_resolved in candidate_resolved.parents


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _context_to_dict(container: Any) -> Dict[str, Any]:
    try:
        if hasattr(container, "to_dict"):
            return dict(container.to_dict())  # type: ignore[arg-type]
    except Exception:  # pragma: no cover
        pass
    data = getattr(container, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if isinstance(container, dict):
        return dict(container)
    return {}


def _coerce_value(definition: Optional[ContextVariableDefinition], raw_value: Any) -> Any:
    if definition is None:
        return raw_value
    if raw_value is None:
        return None
    dtype = (definition.type or "").lower()
    if dtype in {"boolean", "bool"}:
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in _TRUE_FLAG_VALUES
        return bool(raw_value)
    if dtype in {"integer", "int"}:
        try:
            return int(raw_value)
        except Exception:
            return raw_value
    return raw_value


def _resolve_config(definition: ContextVariableDefinition) -> Any:
    source = definition.source
    env_var = source.env_var
    value = os.getenv(env_var) if env_var else source.default
    if value is None and source.required:
        raise ValueError(f"Required config variable '{env_var}' is not set")
    return _coerce_value(definition, value)


def _resolve_state_default(definition: ContextVariableDefinition) -> Any:
    return _coerce_value(definition, definition.source.default)


def _resolve_file_source(definition: ContextVariableDefinition) -> Any:
    source = definition.source
    path = (source.path or "").strip()
    if not path:
        if source.required:
            raise ValueError("File context variable is required but 'path' is missing")
        return _coerce_value(definition, source.default)

    repo_root = _find_repo_root()
    p = Path(path)
    if not p.is_absolute():
        p = repo_root / p

    # Resolve symlinks/.. and enforce root boundary unless explicitly allowed.
    try:
        resolved = p.resolve()
    except Exception as err:
        if source.required:
            raise ValueError(f"Invalid file context variable path: {p}") from err
        business_logger.warning("Invalid file context variable path '%s': %s", str(p), err)
        return _coerce_value(definition, source.default)

    if not _is_within_root(resolved, repo_root) and not _FILE_CONTEXT_ALLOW_OUTSIDE_ROOT:
        msg = f"Refusing to load file context outside repo root (set CONTEXT_FILE_ALLOW_OUTSIDE_ROOT=true to override): {resolved}"
        if source.required:
            raise ValueError(msg)
        business_logger.warning(msg)
        return _coerce_value(definition, source.default)

    # Very small denylist for common secret files.
    blocked_names = {".env", ".env.local", ".env.production", ".env.development"}
    blocked_suffixes = {".pem", ".key", ".pfx", ".p12"}
    if resolved.name.lower() in blocked_names or resolved.suffix.lower() in blocked_suffixes:
        msg = f"Refusing to load likely-secret file via context variable: {resolved}"
        if source.required:
            raise ValueError(msg)
        business_logger.warning(msg)
        return _coerce_value(definition, source.default)

    if not resolved.exists():
        if source.required:
            raise ValueError(f"Required file context variable not found: {resolved}")
        return _coerce_value(definition, source.default)

    encoding = source.encoding or "utf-8"
    raw = resolved.read_text(encoding=encoding)
    fmt = (source.format or "json").lower()
    if fmt == "text":
        return _coerce_value(definition, raw)
    if fmt == "yaml":
        import yaml

        return _coerce_value(definition, yaml.safe_load(raw))
    # default json
    return _coerce_value(definition, json.loads(raw))


def _create_minimal_context(workflow_name: str, app_id: Optional[str]):
    context = create_context_container()
    if app_id:
        context.set("app_id", app_id)
    if workflow_name:
        context.set("workflow_name", workflow_name)
    business_logger.info(
        "Created minimal context",
        extra={
            "app_id": app_id,
            "workflow_name": workflow_name,
            "environment": os.getenv("ENVIRONMENT", "unknown"),
        },
    )
    return context


def _database_defaults(raw_section: Dict[str, Any]) -> Optional[str]:
    defaults = None
    if isinstance(raw_section, dict):
        defaults = raw_section.get("default_database_name")
        defaults = defaults or raw_section.get("default_database")
    return defaults


def _resolve_template_value(template: Any, context: Any, app_id: str) -> Any:
    if not isinstance(template, str) or not template.startswith("{{") or not template.endswith("}}"):
        return template
    inner = template[2:-2].strip()
    if not inner:
        return template

    # runtime scoped value (e.g., {{runtime.app_id}})
    if inner.startswith("runtime."):
        key = inner.split(".", 1)[1]
        if key == "app_id":
            return app_id
        try:
            return context.get(key)  # type: ignore[attr-defined]
        except Exception:
            return getattr(context, key, None)

    parts = inner.split(".")
    base_name = parts[0]
    try:
        base_value = context.get(base_name)  # type: ignore[attr-defined]
    except Exception:
        base_value = getattr(context, base_name, None)
    for part in parts[1:]:
        if isinstance(base_value, dict):
            base_value = base_value.get(part)
        else:
            base_value = getattr(base_value, part, None)
    return base_value


def _materialize_query_template(
    template: Optional[Dict[str, Any]],
    context: Any,
    app_id: str,
) -> Dict[str, Any]:
    if not template:
        return {"app_id": app_id}
    resolved: Dict[str, Any] = {}
    for key, value in template.items():
        resolved[key] = _resolve_template_value(value, context, app_id)
    return resolved


async def _load_data_reference_value(
    name: str,
    definition: ContextVariableDefinition,
    *,
    default_database_name: Optional[str],
    app_id: str,
    context: Any,
) -> Any:
    source = definition.source
    
    adapter = get_db_adapter(source)
    if not adapter:
        business_logger.warning(
            "Skipping data_reference variable %s (no suitable db adapter found)", name
        )
        return None

    try:
        query = _materialize_query_template(source.query_template, context, app_id)
        projection = {field: 1 for field in (source.fields or [])} or None
        
        business_logger.info(f"[DATA_REFERENCE] Resolved query for '{name}': query={query}, projection={projection}, app_id={app_id}")
        
        doc = await adapter.fetch_one(source, query, projection)

        if doc is None:
            if source.default is not None:
                return _coerce_value(definition, source.default)
            return None

        if isinstance(doc, dict) and "_id" in doc:
            doc = {k: v for k, v in doc.items() if k != "_id"}

        # If only one field was requested, extract it directly.
        if source.fields and len(source.fields) == 1:
            field_value = doc.get(source.fields[0])
            business_logger.info(f"Extracted single field '{source.fields[0]}' from document: value={'<present>' if field_value else '<missing>'}")
            coerced = _coerce_value(definition, field_value)
            business_logger.info(f"After coercion for '{name}': type={type(coerced).__name__}, length={len(str(coerced)) if coerced else 0}")
            return coerced

        business_logger.info(f"Returning full document for '{name}': keys={list(doc.keys()) if isinstance(doc, dict) else 'not_dict'}")
        return _coerce_value(definition, doc)
    except Exception as err:
        business_logger.error(f"Failed loading data_reference '{name}': {err}")
        return None


def _create_data_entity_manager(
    name: str,
    definition: ContextVariableDefinition,
    *,
    default_database_name: Optional[str],
) -> Optional[DataEntityManager]:
    source = definition.source
    db_name = source.database_name or default_database_name
    collection = source.collection
    if not db_name or not collection:
        business_logger.warning(
            "Skipping data_entity %s (database_name=%s, collection=%s)",
            name,
            db_name,
            collection,
        )
        return None

    try:
        manager = DataEntityManager(
            database_name=db_name,
            collection=collection,
            schema=source.entity_schema,
            indexes=source.indexes,
            write_strategy=source.write_strategy or "immediate",
            search_by=source.search_by,
        )
        return manager
    except Exception as err:
        business_logger.error(f"Failed creating DataEntityManager for {name}: {err}")
    return None


# ---------------------------------------------------------------------------
# Workflow config loading
# ---------------------------------------------------------------------------

def _load_workflow_plan(workflow_name: str) -> Tuple[ContextVariablesPlan, Dict[str, Any]]:
    raw_section: Dict[str, Any] = {}
    try:
        workflow_config = workflow_manager.get_config(workflow_name) or {}
        context_section = workflow_config.get("context_variables") or {}
        if isinstance(context_section, dict):
            raw_section = context_section
        if not raw_section:
            from pathlib import Path
            import json

            wf_info = getattr(workflow_manager, "_workflows", {}).get(workflow_name.lower())
            if wf_info and hasattr(wf_info, "path"):
                ext_file = Path(wf_info.path) / "context_variables.json"
                if ext_file.exists():
                    raw = ext_file.read_text(encoding="utf-8-sig")
                    data = json.loads(raw)
                    ctx_section = data.get("context_variables") or data
                    if isinstance(ctx_section, dict):
                        raw_section = ctx_section
    except Exception as err:  # pragma: no cover
        business_logger.warning(f"Unable to load context config for {workflow_name}: {err}")

    try:
        plan = load_context_variables_config(raw_section)
    except ValueError as err:
        business_logger.warning(
            f"Context variables validation failed for {workflow_name}: {err}"
        )
        plan = ContextVariablesPlan()
        raw_section = {}
    return plan, raw_section


# ---------------------------------------------------------------------------
# Schema utilities (optional, reused from legacy implementation)
# ---------------------------------------------------------------------------

async def _get_all_collections_first_docs(database_name: str) -> Dict[str, Any]:
    from mozaiksai.core.core_config import get_mongo_client  # local import

    result: Dict[str, Any] = {}
    try:
        client = get_mongo_client()
        db = client[database_name]
        try:
            names = await db.list_collection_names()
        except Exception as err:  # pragma: no cover
            business_logger.error(f"list_collection_names failed for {database_name}: {err}")
            return result
        for cname in names:
            try:
                doc = await db[cname].find_one()
                if not doc:
                    result[cname] = {"_note": "empty_collection"}
                else:
                    cleaned = {k: v for k, v in doc.items() if k != "_id"}
                    result[cname] = cleaned
            except Exception as ce:
                result[cname] = {"_error": str(ce)}
    except Exception as outer:
        business_logger.error(f"Failed collecting first docs for {database_name}: {outer}")
    return result


async def _get_database_schema_async(database_name: str) -> Dict[str, Any]:
    schema_info: Dict[str, Any] = {}

    try:
        from mozaiksai.core.core_config import get_mongo_client

        client = get_mongo_client()
        db = client[database_name]
        collection_names = await db.list_collection_names()

        schema_lines: List[str] = []
        schema_lines.append(f"DATABASE: {database_name}")
        schema_lines.append(f"TOTAL COLLECTIONS: {len(collection_names)}")
        schema_lines.append("")

        app_collections: List[str] = []
        collection_schemas: Dict[str, Dict[str, str]] = {}

        for collection_name in collection_names:
            try:
                collection = db[collection_name]
                sample_doc = await collection.find_one()
                if not sample_doc:
                    collection_schemas[collection_name] = {"note": "No sample data available"}
                    continue

                field_types: Dict[str, str] = {}
                for field_name, value in sample_doc.items():
                    if field_name == "_id":
                        continue
                    field_type = type(value).__name__
                    if field_type == "ObjectId":
                        field_type = "ObjectId"
                    field_types[field_name] = field_type
                collection_schemas[collection_name] = field_types

                if "app_id" in sample_doc:
                    app_collections.append(collection_name)
            except Exception as err:
                business_logger.debug(f"Could not analyze {collection_name}: {err}")
                collection_schemas[collection_name] = {"error": f"Analysis failed: {err}"}

        for collection_name, fields in collection_schemas.items():
            note = fields.get("note") if isinstance(fields, dict) else None
            error = fields.get("error") if isinstance(fields, dict) else None
            if note or error:
                continue
            is_app = " [app-specific]" if collection_name in app_collections else ""
            schema_lines.append(f"{collection_name.upper()}{is_app}:")
            schema_lines.append("  Fields:")
            for field_name, field_type in fields.items():
                schema_lines.append(f"    - {field_name}: {field_type}")
            schema_lines.append("")

        schema_info["schema_overview"] = "\n".join(schema_lines)
        business_logger.info(
            "Schema loaded",
            extra={
                "database": database_name,
                "collections": len(collection_names),
                "app_collections": len(app_collections),
            },
        )
    except Exception as err:
        business_logger.error(f"Database schema loading failed: {err}")
        schema_info["error"] = f"Could not load schema: {err}"

    return schema_info


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

async def _load_context_async(workflow_name: str, app_id: Optional[str]):
    business_logger.info(f"Loading context for workflow={workflow_name}")
    context = _create_minimal_context(workflow_name, app_id)
    internal_app_id = app_id or ""

    plan, raw_context_section = _load_workflow_plan(workflow_name)

    # Optional schema overview (gated by env)
    schema_capability_enabled = False
    schema_capability_db: Optional[str] = None
    if isinstance(raw_context_section, dict):
        try:
            include_schema = os.getenv("CONTEXT_INCLUDE_SCHEMA", "false").lower() in _TRUE_FLAG_VALUES
            if include_schema:
                db_name = os.getenv("CONTEXT_SCHEMA_DB")
                if not db_name:
                    schema_cfg = raw_context_section.get("schema_overview")
                    if isinstance(schema_cfg, dict):
                        db_name = schema_cfg.get("database_name")
                if db_name:
                    schema_capability_db = db_name
                    overview_info = await _get_database_schema_async(db_name)
                    overview_text = overview_info.get("schema_overview")
                    if overview_text:
                        schema_capability_enabled = True
                        if len(overview_text) > TRUNCATE_CHARS:
                            overview_text = f"{overview_text[:TRUNCATE_CHARS]}... [truncated {len(overview_text) - TRUNCATE_CHARS} chars]"
                        context.set("schema_overview", overview_text)
                    try:
                        first_docs = await _get_all_collections_first_docs(db_name)
                        context.set("collections_first_docs_full", first_docs)
                    except Exception as doc_err:
                        business_logger.debug(f"collections_first_docs_full attachment failed: {doc_err}")
        except Exception as schema_err:  # pragma: no cover
            business_logger.debug(f"Schema overview skipped: {schema_err}")

    context.set("database_schema_available", schema_capability_enabled)
    context.set("database_schema_db", schema_capability_db)

    definitions = plan.definitions or {}
    default_db = _database_defaults(raw_context_section)
    data_entity_managers: List[DataEntityManager] = []

    for name, definition in definitions.items():
        source = definition.source
        source_type = source.type

        if source_type == "config":
            value = _resolve_config(definition)
            context.set(name, value)
            business_logger.info("Loaded config variable %s", name)
        elif source_type == "data_reference":
            business_logger.info(f"[DATA_REFERENCE] Loading '{name}' for app_id={internal_app_id}")
            value = await _load_data_reference_value(
                name,
                definition,
                default_database_name=default_db,
                app_id=internal_app_id,
                context=context,
            )
            context.set(name, value)
            business_logger.info(f"[DATA_REFERENCE] Loaded '{name}' - type={type(value).__name__}, value_preview={str(value)[:100] if value else 'None'}")
            business_logger.info("Loaded data_reference %s", name)
        elif source_type == "data_entity":
            manager = _create_data_entity_manager(
                name,
                definition,
                default_database_name=default_db,
            )
            if manager:
                data_entity_managers.append(manager)
                context.set(name, manager)
                business_logger.info("Initialized data_entity manager for %s", name)
        elif source_type == "computed":
            context.set(name, None)
            business_logger.debug("Registered computed variable %s (on-demand)", name)
        elif source_type == "state":
            value = _resolve_state_default(definition)
            context.set(name, value)
            trigger_count = len(getattr(source, "triggers", []) or [])
            business_logger.info(
                "Initialized state variable %s with default=%s, triggers=%d",
                name,
                value,
                trigger_count,
            )
        elif source_type == "external":
            context.set(name, None)
            business_logger.debug("Registered external variable %s (fetched by tools)", name)
        elif source_type == "file":
            try:
                value = _resolve_file_source(definition)
            except Exception as err:
                business_logger.error("Failed loading file variable %s: %s", name, err)
                value = None
            context.set(name, value)
            business_logger.info("Loaded file variable %s", name)
        else:
            business_logger.debug("Unsupported source type for %s: %s", name, source_type)

    if data_entity_managers:
        setattr(context, "_mozaiks_data_entity_managers", data_entity_managers)

    # Expose definitions and agent plan on the context container for downstream consumers
    if definitions:
        setattr(context, "_mozaiks_context_definitions", definitions)
    if plan.agents:
        setattr(context, "_mozaiks_context_agents", plan.agents)

    # Log context summary
    try:
        keys = [k for k in context.keys() if k != "app_id"]  # type: ignore[attr-defined]
    except Exception:
        keys = list(_context_to_dict(context).keys())
    business_logger.info(
        "Context loaded",
        extra={
            "workflow": workflow_name,
            "variable_count": len(keys),
            "variables": keys,
        },
    )
    for key in keys:
        try:
            business_logger.debug(f"    {key} => {context.get(key)}")
        except Exception:
            pass

    if internal_app_id and not (hasattr(context, "contains") and context.contains("app_id")):
        context.set("app_id", internal_app_id)

    return context


__all__ = ["_create_minimal_context", "_load_context_async"]



