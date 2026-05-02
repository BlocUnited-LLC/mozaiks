from datetime import UTC, datetime
import re
from typing import Any, Dict, Optional

import yaml

from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager


logger = get_workflow_logger("design_docs")


_COLLECTION = "DesignDocuments"


class DesignDocKinds:
    FRONTEND: str = "frontend"
    BACKEND: str = "backend"
    DATABASE: str = "database"
    UI_SCHEMA: str = "ui_schema"


_DOC_KINDS = (
    DesignDocKinds.FRONTEND,
    DesignDocKinds.BACKEND,
    DesignDocKinds.DATABASE,
    DesignDocKinds.UI_SCHEMA,
)
_FIRST_DOC = DesignDocKinds.FRONTEND
_LAST_DOC = DesignDocKinds.UI_SCHEMA


async def _ensure_indexes(pm: AG2PersistenceManager) -> None:
    await pm.persistence._ensure_client()  # noqa: SLF001 (runtime pattern)
    assert pm.persistence.client is not None
    coll = pm.persistence.client["MozaiksAI"][_COLLECTION]
    try:
        existing = await coll.list_indexes().to_list(length=None)
        names = {i.get("name") for i in existing if isinstance(i, dict)}
        if "dd_app_kind" not in names:
            await coll.create_index([("app_id", 1), ("kind", 1)], unique=True, name="dd_app_kind")
    except Exception as err:
        logger.debug("Failed to ensure DesignDocuments indexes: %s", err)


async def _upsert_design_doc(
    *,
    pm: AG2PersistenceManager,
    app_id: str,
    user_id: Optional[str],
    kind: str,
    stage: str,
    content: str,
    source_workflow: str,
    source_chat_id: Optional[str],
    extra_fields: Optional[Dict[str, Any]] = None,
) -> None:
    await _ensure_indexes(pm)
    assert pm.persistence.client is not None
    coll = pm.persistence.client["MozaiksAI"][_COLLECTION]
    now = datetime.now(UTC)

    set_fields: Dict[str, Any] = {
        "app_id": app_id,
        "user_id": user_id,
        "kind": kind,
        "stage": stage,
        "content": content,
        "status": "succeeded",
        "source": {"workflow": source_workflow, "chat_id": source_chat_id},
        "updated_at": now,
    }
    if extra_fields:
        set_fields.update(extra_fields)

    update: Dict[str, Any] = {
        "$set": {
            **set_fields,
        },
        "$setOnInsert": {"created_at": now},
        "$push": {
            "revisions": {
                "$each": [
                    {
                        "stage": stage,
                        "content": content,
                        "workflow": source_workflow,
                        "chat_id": source_chat_id,
                        "created_at": now,
                    }
                ],
                "$slice": -5,
            }
        },
    }

    await coll.update_one({"app_id": app_id, "kind": kind}, update, upsert=True)


async def _mark_design_docs_status(
    *,
    pm: AG2PersistenceManager,
    app_id: str,
    user_id: Optional[str],
    stage: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    await _ensure_indexes(pm)
    assert pm.persistence.client is not None
    coll = pm.persistence.client["MozaiksAI"][_COLLECTION]
    now = datetime.now(UTC)
    for k in _DOC_KINDS:
        update: Dict[str, Any] = {
            "$set": {
                "app_id": app_id,
                "user_id": user_id,
                "kind": k,
                "stage": stage,
                "status": status,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        }
        if error:
            update["$set"]["error"] = error
        await coll.update_one({"app_id": app_id, "kind": k}, update, upsert=True)


def _cv_get(context_variables: Any, key: str) -> Optional[Any]:
    if context_variables is None:
        return None
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key)
        except Exception:
            return None
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key)
    return None


def _cv_set(context_variables: Any, key: str, value: Any) -> None:
    if context_variables is None:
        return
    setter = getattr(context_variables, "set", None)
    if callable(setter):
        try:
            setter(key, value)
            return
        except Exception:
            return
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        data[key] = value


def _normalize_kind(kind: str) -> Optional[str]:
    if not isinstance(kind, str):
        return None
    k = kind.strip().lower()
    if k in {DesignDocKinds.FRONTEND, DesignDocKinds.BACKEND, DesignDocKinds.DATABASE, DesignDocKinds.UI_SCHEMA}:
        return k
    return None


def _extract_bundle(context_variables: Any) -> Optional[Dict[str, Any]]:
    if context_variables is None:
        return None

    raw = _cv_get(context_variables, "structured_output")
    if not isinstance(raw, dict):
        raw = _cv_get(context_variables, "DesignDocsBundle")
    if not isinstance(raw, dict):
        return None

    nested = raw.get("DesignDocsBundle")
    if isinstance(nested, dict):
        return nested
    return raw


def _canonical_surface_map(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("surface_map must be an object")
    surfaces = raw.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise ValueError("surface_map.surfaces must be a non-empty list")
    for idx, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            raise ValueError(f"surface_map.surfaces[{idx}] must be an object")
    return {"surfaces": surfaces}


def _surface_map_yaml_block(surface_map: Dict[str, Any]) -> str:
    return yaml.safe_dump(
        {"surface_map": surface_map},
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).strip()


def _inject_backend_surface_map(backend_markdown: str, surface_map: Dict[str, Any]) -> str:
    doc = str(backend_markdown or "").strip()
    if not doc:
        raise ValueError("backend_markdown must be a non-empty string")

    block = "## Surface Realization Map\n\n```yaml\n" + _surface_map_yaml_block(surface_map) + "\n```"
    pattern = re.compile(
        r"^## Surface Realization Map\s+```yaml\s+.*?```(?:\s+|$)",
        flags=re.MULTILINE | re.DOTALL,
    )
    if pattern.search(doc):
        return pattern.sub(block + "\n\n", doc, count=1).strip()
    return doc.rstrip() + "\n\n" + block


def _canonicalize_ui_schema_yaml(ui_schema_yaml: str, surface_map: Dict[str, Any]) -> str:
    doc = str(ui_schema_yaml or "").strip()
    if not doc:
        raise ValueError("ui_schema_yaml must be a non-empty string")
    parsed = yaml.safe_load(doc)
    if not isinstance(parsed, dict):
        raise ValueError("ui_schema_yaml must parse to a top-level mapping")
    parsed["surface_map"] = surface_map
    return yaml.safe_dump(
        parsed,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).strip() + "\n"


async def save_design_doc(
    *,
    kind: str,
    stage: str,
    content: str,
    context_variables: Any = None,
) -> Dict[str, Any]:
    app_id = _cv_get(context_variables, "app_id")
    chat_id = _cv_get(context_variables, "chat_id")
    user_id = _cv_get(context_variables, "user_id")

    if not app_id or not isinstance(app_id, str):
        return {"ok": False, "reason": "missing_app_id"}

    normalized_kind = _normalize_kind(kind)
    if not normalized_kind:
        return {"ok": False, "reason": "invalid_kind"}

    if not isinstance(content, str) or not content.strip():
        return {"ok": False, "reason": "empty_content"}

    pm = AG2PersistenceManager()
    normalized_stage = str(stage or "draft")

    # Best-effort stage status tracking: mark running on first doc, succeeded on last.
    try:
        if normalized_kind == _FIRST_DOC:
            await _mark_design_docs_status(
                pm=pm,
                app_id=app_id,
                user_id=str(user_id) if user_id else None,
                stage=normalized_stage,
                status="running",
            )
    except Exception:
        pass

    await _upsert_design_doc(
        pm=pm,
        app_id=app_id,
        user_id=str(user_id) if user_id else None,
        kind=normalized_kind,
        stage=normalized_stage,
        content=content,
        source_workflow="DesignDocs",
        source_chat_id=str(chat_id) if chat_id else None,
    )

    # Best-effort: mark succeeded when the final doc (ui_schema) is saved.
    try:
        if normalized_kind == _LAST_DOC:
            await _mark_design_docs_status(
                pm=pm,
                app_id=app_id,
                user_id=str(user_id) if user_id else None,
                stage=normalized_stage,
                status="succeeded",
            )
    except Exception:
        pass

    return {
        "ok": True,
        "app_id": app_id,
        "kind": normalized_kind,
        "stage": normalized_stage,
        "len": len(content),
    }


async def save_design_docs_bundle(
    *,
    context_variables: Any = None,
) -> Dict[str, Any]:
    app_id = _cv_get(context_variables, "app_id")
    chat_id = _cv_get(context_variables, "chat_id")
    user_id = _cv_get(context_variables, "user_id")

    if not app_id or not isinstance(app_id, str):
        return {"ok": False, "reason": "missing_app_id"}

    bundle = _extract_bundle(context_variables)
    if not isinstance(bundle, dict):
        return {"ok": False, "reason": "missing_design_docs_bundle"}

    try:
        frontend_markdown = str(bundle.get("frontend_markdown") or "").strip()
        backend_markdown = str(bundle.get("backend_markdown") or "").strip()
        database_markdown = str(bundle.get("database_markdown") or "").strip()
        ui_schema_yaml = str(bundle.get("ui_schema_yaml") or "").strip()
        surface_map = _canonical_surface_map(bundle.get("surface_map"))
        if not frontend_markdown or not backend_markdown or not database_markdown or not ui_schema_yaml:
            raise ValueError("DesignDocsBundle must include all four document strings")
        backend_markdown = _inject_backend_surface_map(backend_markdown, surface_map)
        ui_schema_yaml = _canonicalize_ui_schema_yaml(ui_schema_yaml, surface_map)
    except Exception as err:
        return {"ok": False, "reason": "invalid_design_docs_bundle", "error": str(err)}

    pm = AG2PersistenceManager()
    normalized_stage = "draft"

    try:
        await _mark_design_docs_status(
            pm=pm,
            app_id=app_id,
            user_id=str(user_id) if user_id else None,
            stage=normalized_stage,
            status="running",
        )
    except Exception:
        pass

    docs = (
        (DesignDocKinds.FRONTEND, frontend_markdown, None),
        (DesignDocKinds.BACKEND, backend_markdown, {"surface_map": surface_map}),
        (DesignDocKinds.DATABASE, database_markdown, None),
        (DesignDocKinds.UI_SCHEMA, ui_schema_yaml, {"surface_map": surface_map}),
    )

    for kind, content, extra_fields in docs:
        await _upsert_design_doc(
            pm=pm,
            app_id=app_id,
            user_id=str(user_id) if user_id else None,
            kind=kind,
            stage=normalized_stage,
            content=content,
            source_workflow="DesignDocs",
            source_chat_id=str(chat_id) if chat_id else None,
            extra_fields=extra_fields,
        )

    try:
        await _mark_design_docs_status(
            pm=pm,
            app_id=app_id,
            user_id=str(user_id) if user_id else None,
            stage=normalized_stage,
            status="succeeded",
        )
    except Exception:
        pass

    _cv_set(context_variables, "frontend_design_document", frontend_markdown)
    _cv_set(context_variables, "backend_design_document", backend_markdown)
    _cv_set(context_variables, "database_design_document", database_markdown)
    _cv_set(context_variables, "ui_design_document", ui_schema_yaml)
    _cv_set(context_variables, "experience_spec_document", ui_schema_yaml)
    _cv_set(context_variables, "design_surface_map", surface_map)

    return {
        "ok": True,
        "app_id": app_id,
        "stage": normalized_stage,
        "kinds": list(_DOC_KINDS),
        "surface_count": len(surface_map.get("surfaces", [])),
    }
