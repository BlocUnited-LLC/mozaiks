"""Snapshot and patchset helpers for generated app code exports."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_zip_path(path: str) -> str | None:
    normalized = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not normalized:
        return None
    parsed = PurePosixPath(normalized)
    if parsed.is_absolute() or any(part == ".." for part in parsed.parts):
        return None
    return str(parsed)


def extract_files_from_zip_bundle(bundle_path: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    with zipfile.ZipFile(bundle_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            safe_path = _safe_zip_path(info.filename)
            if not safe_path:
                continue
            raw = zf.read(info)
            files.append(
                {
                    "path": safe_path,
                    "contentBase64": base64.b64encode(raw).decode("ascii"),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "sizeBytes": len(raw),
                }
            )
    return files


def _normalize_file(entry: dict[str, Any]) -> dict[str, Any] | None:
    path = _safe_zip_path(str(entry.get("path") or ""))
    if not path:
        return None
    content_b64 = entry.get("contentBase64")
    sha = entry.get("sha256")
    size = entry.get("sizeBytes")
    if isinstance(content_b64, str):
        try:
            raw = base64.b64decode(content_b64.encode("ascii"), validate=True)
            sha = hashlib.sha256(raw).hexdigest()
            size = len(raw)
        except Exception:
            pass
    return {
        "path": path,
        "contentBase64": content_b64 if isinstance(content_b64, str) else None,
        "sha256": sha if isinstance(sha, str) else None,
        "sizeBytes": size if isinstance(size, int) else 0,
    }


def build_snapshot_document(
    *,
    app_id: str,
    session_id: str | None,
    workflow_type: str,
    source: str,
    files: list[dict[str, Any]],
    structured_outputs: dict[str, Any] | None,
    repo_url: str | None = None,
    base_commit_sha: str | None = None,
) -> dict[str, Any]:
    now = _now()
    normalized = [item for item in (_normalize_file(f) for f in files if isinstance(f, dict)) if item]
    snapshot_id = f"snap_{uuid4().hex}"
    return {
        "snapshotId": snapshot_id,
        "snapshot_id": snapshot_id,
        "app_id": app_id,
        "appId": app_id,
        "session_id": session_id,
        "sessionId": session_id,
        "workflow_type": workflow_type,
        "workflowType": workflow_type,
        "source": source,
        "files": normalized,
        "structured_outputs": structured_outputs or {},
        "structuredOutputs": structured_outputs or {},
        "repo_url": repo_url,
        "repoUrl": repo_url,
        "base_commit_sha": base_commit_sha,
        "baseCommitSha": base_commit_sha,
        "created_at_utc": now,
        "createdAt": now,
    }


def build_snapshot_document_from_hashes(
    *,
    app_id: str,
    session_id: str | None,
    workflow_type: str,
    source: str,
    files: list[dict[str, Any]],
    structured_outputs: dict[str, Any] | None,
    repo_url: str | None = None,
    base_commit_sha: str | None = None,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for entry in files if isinstance(files, list) else []:
        if not isinstance(entry, dict):
            continue
        path = _safe_zip_path(str(entry.get("path") or ""))
        sha = entry.get("sha256")
        if path and isinstance(sha, str):
            normalized.append({"path": path, "sha256": sha, "sizeBytes": entry.get("sizeBytes") or 0})
    return build_snapshot_document(
        app_id=app_id,
        session_id=session_id,
        workflow_type=workflow_type,
        source=source,
        files=normalized,
        structured_outputs=structured_outputs,
        repo_url=repo_url,
        base_commit_sha=base_commit_sha,
    )


def _file_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("files") if isinstance(snapshot.get("files"), list) else []:  # type: ignore[union-attr]
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            out[item["path"]] = item
    return out


def compute_patchset_document(
    *,
    app_id: str,
    base_snapshot: dict[str, Any],
    target_snapshot: dict[str, Any],
    repo_file_shas: dict[str, str],
    base_commit_sha: str,
    repo_url: str,
    workflow_type: str,
) -> dict[str, Any]:
    base_files = _file_map(base_snapshot)
    target_files = _file_map(target_snapshot)
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    all_paths = sorted(set(base_files.keys()) | set(target_files.keys()))
    for path in all_paths:
        base = base_files.get(path)
        target = target_files.get(path)
        base_sha = base.get("sha256") if base else None
        target_sha = target.get("sha256") if target else None
        if base_sha == target_sha:
            continue

        operation = "add" if base is None else "delete" if target is None else "modify"
        change: dict[str, Any] = {"path": path, "operation": operation}
        if target and isinstance(target.get("contentBase64"), str):
            change["contentBase64"] = target["contentBase64"]
        changes.append(change)

        repo_sha = repo_file_shas.get(path)
        if operation in {"modify", "delete"} and repo_sha and base_sha and repo_sha != base_sha:
            conflicts.append({"path": path, "baseSha256": base_sha, "repoSha256": repo_sha, "operation": operation})

    now = _now()
    patch_id = f"patch_{uuid4().hex}"
    return {
        "patchId": patch_id,
        "patch_id": patch_id,
        "app_id": app_id,
        "appId": app_id,
        "workflow_type": workflow_type,
        "workflowType": workflow_type,
        "baseSnapshotId": base_snapshot.get("snapshotId"),
        "targetSnapshotId": target_snapshot.get("snapshotId"),
        "baseCommitSha": base_commit_sha,
        "repoUrl": repo_url,
        "changes": changes,
        "conflicts": conflicts,
        "created_at_utc": now,
        "createdAt": now,
    }


async def _collection(name: str):
    pm = AG2PersistenceManager()
    await pm.persistence._ensure_client()
    assert pm.persistence.client is not None, "Mongo client not initialized"
    return pm.persistence.client["mozaiksai"][name]


async def persist_snapshot(*, snapshot_doc: dict[str, Any]) -> str:
    coll = await _collection("CodeSnapshots")
    await coll.insert_one(snapshot_doc)
    return str(snapshot_doc.get("snapshotId") or snapshot_doc.get("snapshot_id") or "")


async def get_snapshot(*, app_id: str, snapshot_id: str) -> dict[str, Any] | None:
    coll = await _collection("CodeSnapshots")
    return await coll.find_one({"app_id": app_id, "snapshotId": snapshot_id})  # type: ignore[no-any-return]


async def get_latest_snapshot(*, app_id: str, workflow_type: str) -> dict[str, Any] | None:
    coll = await _collection("CodeSnapshots")
    cursor = coll.find({"app_id": app_id, "workflow_type": workflow_type}).sort("_id", -1).limit(1)
    docs = await cursor.to_list(length=1)
    return docs[0] if docs else None


async def persist_patchset(*, patchset_doc: dict[str, Any]) -> str:
    coll = await _collection("CodePatchsets")
    await coll.insert_one(patchset_doc)
    return str(patchset_doc.get("patchId") or patchset_doc.get("patch_id") or "")


__all__ = [
    "build_snapshot_document",
    "build_snapshot_document_from_hashes",
    "compute_patchset_document",
    "extract_files_from_zip_bundle",
    "get_latest_snapshot",
    "get_snapshot",
    "persist_patchset",
    "persist_snapshot",
]
