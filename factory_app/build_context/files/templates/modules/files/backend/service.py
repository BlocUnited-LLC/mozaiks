from __future__ import annotations

import inspect
from typing import Any

from .repo import get_file, list_files, register_file, soft_delete_file
from .schemas import build_file_record, safe_file_record


class FilesService:
    async def _emit(self, ctx, event_type: str, payload: dict[str, Any]) -> None:
        emit = getattr(ctx, "emit", None)
        if emit is None:
            return
        result = emit(event_type, payload)
        if inspect.isawaitable(result):
            await result

    async def upload_file(
        self,
        ctx,
        *,
        filename: str,
        mime_type: str,
        size_bytes: int,
        storage_url: str,
        is_public: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        filename = str(filename or "").strip()
        if not filename:
            return {"success": False, "error": "filename is required"}

        mime_type = str(mime_type or "").strip()
        if not mime_type:
            return {"success": False, "error": "mime_type is required"}

        try:
            size_bytes = int(size_bytes)
        except (TypeError, ValueError):
            return {"success": False, "error": "size_bytes must be an integer"}
        if size_bytes <= 0:
            return {"success": False, "error": "size_bytes must be greater than zero"}

        storage_url = str(storage_url or "").strip()
        if not storage_url:
            return {"success": False, "error": "storage_url is required"}

        created_by = str(getattr(ctx, "user_id", None) or "anonymous").strip() or "anonymous"
        app_id = getattr(ctx, "app_id", None)

        record = build_file_record(
            app_id=app_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_url=storage_url,
            is_public=is_public,
            created_by=created_by,
            metadata=metadata,
        )

        await register_file(
            ctx,
            file_id=record["file_id"],
            filename=record["filename"],
            mime_type=record["mime_type"],
            size_bytes=record["size_bytes"],
            storage_url=record["storage_url"],
            is_public=record["is_public"],
            created_by=record["created_by"],
            metadata=record.get("metadata"),
        )

        await self._emit(
            ctx,
            "domain.files.file_uploaded",
            {
                "file_id": record["file_id"],
                "filename": record["filename"],
                "mime_type": record["mime_type"],
                "size_bytes": record["size_bytes"],
                "is_public": record["is_public"],
                "created_by": record["created_by"],
                "app_id": app_id,
            },
        )

        return {"success": True, "file": safe_file_record(record)}

    async def get_file(self, ctx, *, file_id: str) -> dict[str, Any]:
        doc = await get_file(ctx, file_id=file_id)
        return {"file": safe_file_record(doc) if doc else None}

    async def list_files(
        self,
        ctx,
        *,
        is_public: bool | None = None,
        mime_type: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        created_by = str(getattr(ctx, "user_id", None) or "").strip() or None
        docs = await list_files(
            ctx,
            created_by=created_by,
            is_public=is_public,
            mime_type=mime_type,
            limit=limit,
        )
        files = [safe_file_record(doc) for doc in docs]
        return {"files": files, "total": len(files)}

    async def delete_file(self, ctx, *, file_id: str) -> dict[str, Any]:
        deleted_by = str(getattr(ctx, "user_id", None) or "anonymous").strip() or "anonymous"
        app_id = getattr(ctx, "app_id", None)

        matched = await soft_delete_file(ctx, file_id=file_id, deleted_by=deleted_by)
        if not matched:
            return {"success": False, "error": "file not found"}

        await self._emit(
            ctx,
            "domain.files.file_deleted",
            {
                "file_id": file_id,
                "deleted_by": deleted_by,
                "app_id": app_id,
            },
        )

        return {"success": True}
