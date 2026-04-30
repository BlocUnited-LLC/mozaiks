
from datetime import UTC, datetime
from typing import Any, Dict, Optional

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
) -> None:
    await _ensure_indexes(pm)
    assert pm.persistence.client is not None
    coll = pm.persistence.client["MozaiksAI"][_COLLECTION]
    now = datetime.now(UTC)

    update: Dict[str, Any] = {
        "$set": {
            "app_id": app_id,
            "user_id": user_id,
            "kind": kind,
            "stage": stage,
            "content": content,
            "status": "succeeded",
            "source": {"workflow": source_workflow, "chat_id": source_chat_id},
            "updated_at": now,
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


def _normalize_kind(kind: str) -> Optional[str]:
    if not isinstance(kind, str):
        return None
    k = kind.strip().lower()
    if k in {DesignDocKinds.FRONTEND, DesignDocKinds.BACKEND, DesignDocKinds.DATABASE, DesignDocKinds.UI_SCHEMA}:
        return k
    return None


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
