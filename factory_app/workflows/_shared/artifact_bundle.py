"""Verified text files from a committed Factory app-bundle archive."""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Any

from mozaiksai.control_plane.contracts import safe_artifact_relpath
from mozaiksai.core.artifacts.content_store import (
    LocalArtifactContentStore,
    get_artifact_content_store,
)
from mozaiksai.core.artifacts.models import BuildRecord, resolve_canonical_bundle_entry

_BINARY_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".mp3", ".mp4"}
_MAX_FILE_BYTES = 2_000_000
_MAX_TOTAL_BYTES = 32_000_000
_MAX_FILES = 4096


async def read_artifact_bundle(artifact: BuildRecord) -> tuple[dict[str, str], list[dict[str, Any]]]:
    metadata = artifact.commit_metadata.metadata
    entry = resolve_canonical_bundle_entry(artifact)
    if metadata.get("content_ref"):
        content_store = get_artifact_content_store()
        if metadata.get("content_backend") != content_store.backend_name:
            raise ValueError("artifact_bundle_content_backend_mismatch")
        reference = metadata["content_ref"]
    else:
        content_store = LocalArtifactContentStore()
        reference = metadata.get("artifact_path")
    if not reference:
        raise ValueError("artifact_bundle_content_missing")
    raw = await content_store.get_bundle(reference)
    if hashlib.sha256(raw).hexdigest() != entry.sha256:
        raise ValueError("artifact_bundle_digest_mismatch")
    if len(raw) > _MAX_TOTAL_BYTES:
        raise ValueError("artifact_bundle_archive_too_large")
    prefix = f"{metadata['bundle_name']}/"
    files: dict[str, str] = {}
    diagnostics: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_bytes = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            path = safe_artifact_relpath(info.filename)
            reason = None
            if path is None:
                reason = "unsafe_path"
            elif stat.S_ISLNK(info.external_attr >> 16):
                reason = "symlink"
            elif info.is_dir():
                continue
            else:
                path = path.removeprefix(prefix)
                if path in seen:
                    reason = "duplicate_path"
                elif len(seen) >= _MAX_FILES:
                    reason = "file_limit"
                elif info.file_size > _MAX_FILE_BYTES:
                    reason = "file_too_large"
                elif total_bytes + info.file_size > _MAX_TOTAL_BYTES:
                    reason = "total_size_limit"
            if reason:
                diagnostics.append({"path": info.filename, "code": reason, "blocking": True})
                continue
            seen.add(path)
            total_bytes += info.file_size
            data = archive.read(info)
            if PurePosixPath(path).suffix.lower() in _BINARY_ASSET_SUFFIXES:
                diagnostics.append({"path": path, "code": "binary_asset", "blocking": False})
                continue
            try:
                text = data.decode("utf-8")
                if "\x00" in text:
                    raise UnicodeError
            except UnicodeError:
                diagnostics.append({"path": path, "code": "non_text_source", "blocking": True})
                continue
            files[path] = text
    return files, diagnostics

