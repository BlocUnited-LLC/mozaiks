from __future__ import annotations

from collections.abc import Mapping
from typing import Any

Document = dict[str, Any]


def _collection(ctx, entity_name: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection("files", entity_name)


def _document(value: Any) -> Document | None:
    return dict(value) if isinstance(value, Mapping) else None


def _documents(values: list[Any] | None) -> list[Document]:
    return [dict(item) for item in values or [] if isinstance(item, Mapping)]


async def register_file(
    ctx,
    *,
    file_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    storage_url: str,
    is_public: bool,
    created_by: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    app_id = getattr(ctx, "app_id", None)
    record = {
        "file_id": file_id,
        "app_id": app_id,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "storage_url": storage_url,
        "is_public": is_public,
        "created_by": created_by,
        "deleted": False,
        "deleted_at": None,
        "deleted_by": None,
        "metadata": dict(metadata or {}),
    }
    await _collection(ctx, "records").insert_one(record)


async def get_file(ctx, *, file_id: str) -> Document | None:
    app_id = getattr(ctx, "app_id", None)
    return _document(
        await _collection(ctx, "records").find_one(
            {"app_id": app_id, "file_id": file_id, "deleted": {"$ne": True}}
        )
    )


async def list_files(
    ctx,
    *,
    created_by: str | None = None,
    is_public: bool | None = None,
    mime_type: str | None = None,
    limit: int = 50,
) -> list[Document]:
    app_id = getattr(ctx, "app_id", None)
    query: dict[str, Any] = {"app_id": app_id, "deleted": {"$ne": True}}
    if created_by is not None:
        query["created_by"] = created_by
    if is_public is not None:
        query["is_public"] = is_public
    if mime_type is not None:
        query["mime_type"] = mime_type
    return _documents(
        await _collection(ctx, "records").find_many(
            query,
            limit=max(1, min(int(limit), 200)),
            sort=[("created_at", -1)],
        )
    )


async def soft_delete_file(ctx, *, file_id: str, deleted_by: str) -> int:
    from .schemas import timestamp_now

    app_id = getattr(ctx, "app_id", None)
    result = await _collection(ctx, "records").update_one(
        {"app_id": app_id, "file_id": file_id, "deleted": {"$ne": True}},
        {
            "$set": {
                "deleted": True,
                "deleted_at": timestamp_now(),
                "deleted_by": deleted_by,
            }
        },
    )
    return int(getattr(result, "matched_count", 0) or 0)
