from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4


class FileRecord(TypedDict, total=False):
    file_id: str
    app_id: str
    filename: str
    mime_type: str
    size_bytes: int
    storage_url: str
    is_public: bool
    created_by: str
    created_at: str
    deleted: bool
    deleted_at: str | None
    deleted_by: str | None
    metadata: dict[str, Any]


def timestamp_now() -> str:
    return datetime.now(UTC).isoformat()


def new_file_id() -> str:
    return f"file_{uuid4().hex}"


def build_file_record(
    *,
    app_id: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    storage_url: str,
    is_public: bool = False,
    created_by: str,
    metadata: dict[str, Any] | None = None,
) -> FileRecord:
    now = timestamp_now()
    return {
        "file_id": new_file_id(),
        "app_id": app_id,
        "filename": str(filename or "").strip(),
        "mime_type": str(mime_type or "").strip(),
        "size_bytes": int(size_bytes),
        "storage_url": str(storage_url or "").strip(),
        "is_public": bool(is_public),
        "created_by": created_by,
        "created_at": now,
        "deleted": False,
        "deleted_at": None,
        "deleted_by": None,
        "metadata": dict(metadata or {}),
    }


def safe_file_record(doc: Any) -> dict[str, Any]:
    """Return a safe dict of file fields, excluding internal storage fields."""
    if not doc:
        return {}
    return {
        "file_id": doc.get("file_id"),
        "filename": doc.get("filename"),
        "mime_type": doc.get("mime_type"),
        "size_bytes": doc.get("size_bytes"),
        "storage_url": doc.get("storage_url"),
        "is_public": doc.get("is_public", False),
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at"),
    }
