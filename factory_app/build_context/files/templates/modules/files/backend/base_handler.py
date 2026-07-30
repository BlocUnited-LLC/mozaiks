"""
OSS-generated base handler for the files module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).
"""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.module_context import ModuleContext

from .service import FilesService


class FilesBaseHandler:
    def __init__(self, service: FilesService | None = None) -> None:
        self._service = service or FilesService()

    async def upload_file(
        self,
        ctx: ModuleContext,
        *,
        filename: str,
        mime_type: str,
        size_bytes: int,
        storage_url: str,
        is_public: bool = False,
        metadata: dict[str, Any] | None = None,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.upload_file(
            ctx,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_url=storage_url,
            is_public=is_public,
            metadata=metadata,
        )

    async def get_file(
        self,
        ctx: ModuleContext,
        *,
        file_id: str,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.get_file(ctx, file_id=file_id)

    async def list_files(
        self,
        ctx: ModuleContext,
        *,
        is_public: bool | None = None,
        mime_type: str | None = None,
        limit: int = 50,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.list_files(
            ctx,
            is_public=is_public,
            mime_type=mime_type,
            limit=limit,
        )

    async def delete_file(
        self,
        ctx: ModuleContext,
        *,
        file_id: str,
        **_: object,
    ) -> dict[str, Any]:
        return await self._service.delete_file(ctx, file_id=file_id)
