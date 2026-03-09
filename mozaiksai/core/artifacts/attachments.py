"""Chat attachment storage and bundling helpers.

This module is workflow-agnostic:
- It persists uploaded file metadata to the ChatSessions document.
- It reads stored files back as bytes for downstream tools.

Workflow-specific tools decide whether attachments are treated as context-only
or included in deliverables.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from uuid import uuid4


@dataclass(frozen=True)
class AttachmentUploadResult:
    attachment: Dict[str, Any]
    stored_path: str
    bytes_written: int


def _parse_allowed_workflows(raw: str) -> Set[str]:
    if raw is None:
        return set()
    return {w.strip() for w in raw.split(",") if w and w.strip()}


def _normalize_intent(intent: Optional[str]) -> str:
    normalized = (intent or "context").strip().lower()
    if normalized not in {"context", "bundle", "deliverable"}:
        raise ValueError("intent must be one of: context, bundle, deliverable")
    return normalized


def _upload_root_from_env() -> Path:
    return Path(os.getenv("UPLOAD_STORAGE_DIR", str((Path.cwd() / "uploads").resolve()))).resolve()


def _max_bytes_from_env(env_key: str, default_bytes: int) -> int:
    raw = os.getenv(env_key)
    if raw is None:
        return int(default_bytes)
    try:
        parsed = int(str(raw).strip())
    except Exception:
        return int(default_bytes)
    return int(default_bytes) if parsed <= 0 else parsed


async def handle_chat_upload(
    *,
    chat_coll: Any,
    file_obj: Any,
    app_id: str,
    user_id: str,
    chat_id: str,
    intent: Optional[str] = None,
    bundle_path: Optional[str] = None,
    allowed_workflows_env: str = "",
) -> AttachmentUploadResult:
    """Store an uploaded file and append metadata to ChatSessions.attachments.

    Parameters
    - chat_coll: Motor collection for ChatSessions.
    - file_obj: An object with at least: filename, content_type, async read(), async close().
    """

    if not app_id or not user_id or not chat_id:
        raise ValueError("app_id, user_id, and chat_id are required")

    normalized_intent = _normalize_intent(intent)

    existing = await chat_coll.find_one(
        {"_id": chat_id, "app_id": app_id},
        {"_id": 1, "workflow_name": 1, "user_id": 1},
    )
    if not existing:
        raise LookupError("Chat session not found")

    owner_user_id = existing.get("user_id")
    if not owner_user_id or str(owner_user_id).strip() != str(user_id).strip():
        raise LookupError("Chat session not found")

    # Optional workflow allowlist gate (empty = allow all workflows)
    allowed = _parse_allowed_workflows((allowed_workflows_env or "").strip())
    doc_wf = (existing.get("workflow_name") or "").strip()
    if allowed and doc_wf and doc_wf not in allowed:
        raise LookupError("Uploads not enabled for this workflow")

    safe_name = Path(getattr(file_obj, "filename", None) or "upload.bin").name
    upload_root = _upload_root_from_env()
    dest_dir = (upload_root / app_id / chat_id).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    attachment_id = f"att_{uuid4().hex}"
    stored_name = f"{attachment_id}_{safe_name}"
    stored_path = (dest_dir / stored_name).resolve()

    max_bytes = _max_bytes_from_env("UPLOAD_MAX_BYTES", 25 * 1024 * 1024)
    bytes_written = 0

    try:
        with stored_path.open("wb") as out:
            while True:
                chunk = await file_obj.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise ValueError(f"File too large (max {max_bytes} bytes)")
                out.write(chunk)
    finally:
        try:
            close = getattr(file_obj, "close", None)
            if close is not None:
                maybe = close()
                if asyncio.iscoroutine(maybe):
                    await maybe
        except Exception:
            pass

    attachment_doc: Dict[str, Any] = {
        "attachment_id": attachment_id,
        "filename": safe_name,
        "stored_path": str(stored_path),
        "size_bytes": bytes_written,
        "content_type": getattr(file_obj, "content_type", None),
        "intent": normalized_intent,
        "bundle_path": (bundle_path or "").strip() or None,
        "uploaded_at_utc": datetime.now(UTC).isoformat(),
        "user_id": user_id,
    }

    await chat_coll.update_one(
        {"_id": chat_id, "app_id": app_id, "user_id": user_id},
        {"$push": {"attachments": attachment_doc}},
    )

    return AttachmentUploadResult(
        attachment=attachment_doc,
        stored_path=str(stored_path),
        bytes_written=bytes_written,
    )


async def iter_bundle_attachment_files(
    *,
    chat_coll: Any,
    chat_id: str,
    app_id: str,
    allowed_intents: Iterable[str] = ("bundle", "deliverable"),
    max_bytes_env: str = "UPLOAD_BUNDLE_MAX_BYTES",
    default_max_bytes: int = 10 * 1024 * 1024,
) -> List[Tuple[str, bytes]]:
    """Return list of (relative_path, bytes) for attachments tagged for bundling."""

    doc = await chat_coll.find_one(
        {"_id": chat_id, "app_id": app_id},
        {"attachments": 1},
    )
    attachments = (doc or {}).get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return []

    allowed = {a.strip().lower() for a in allowed_intents if a and str(a).strip()}
    max_bytes = _max_bytes_from_env(max_bytes_env, default_max_bytes)

    out: List[Tuple[str, bytes]] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        intent = str(att.get("intent") or "context").strip().lower()
        if intent not in allowed:
            continue

        filename = (att.get("filename") or "").strip()
        stored_path = att.get("stored_path")
        if not filename or not stored_path:
            continue

        rel_path = (att.get("bundle_path") or "").strip()
        if not rel_path:
            rel_path = f"attachments/{Path(filename).name}"
        rel_path = str(rel_path).replace("\\", "/").lstrip("/")

        try:
            fpath = Path(str(stored_path)).resolve()
            if not fpath.exists() or not fpath.is_file():
                continue
            size = fpath.stat().st_size
            if size > max_bytes:
                continue
            raw = await asyncio.to_thread(fpath.read_bytes)
        except Exception:
            continue

        out.append((rel_path, raw))

    return out


async def inject_bundle_attachments_into_payload(
    *,
    chat_coll: Any,
    payload: Dict[str, Any],
    chat_id: str,
    app_id: str,
) -> int:
    """Inject bundle-tagged attachments into payload.extra_files as raw bytes.

    Returns number of injected files.
    """

    pairs = await iter_bundle_attachment_files(
        chat_coll=chat_coll,
        chat_id=chat_id,
        app_id=app_id,
    )
    if not pairs:
        return 0

    existing = payload.get("extra_files")
    if not isinstance(existing, list):
        existing = []

    seen = {str(x.get("path") or x.get("filename")) for x in existing if isinstance(x, dict)}
    injected = 0
    for rel_path, raw in pairs:
        if rel_path in seen:
            continue
        existing.append({
            "path": rel_path,
            "content": raw,
            "purpose": "user_uploaded_bundle_attachment",
        })
        seen.add(rel_path)
        injected += 1

    payload["extra_files"] = existing
    return injected
