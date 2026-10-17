"""Verified source and optional binary assets from a committed app bundle."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Any, Literal, overload

from mozaiksai.control_plane.contracts import safe_artifact_relpath
from mozaiksai.core.artifacts.content_store import read_verified_artifact_bundle
from mozaiksai.core.artifacts.models import BuildRecord

_BINARY_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".pdf", ".mp3", ".mp4"}
_MAX_FILE_BYTES = 8_000_000
APP_BUNDLE_MAX_TOTAL_BYTES = 64_000_000
APP_BUNDLE_MAX_FILES = 4096


@overload
async def read_artifact_bundle(
    artifact: BuildRecord, *, include_binary: Literal[False] = False, retain_svg_text: bool = False,
) -> tuple[dict[str, str], list[dict[str, Any]]]: ...


@overload
async def read_artifact_bundle(
    artifact: BuildRecord, *, include_binary: Literal[True], retain_svg_text: bool = False,
) -> tuple[dict[str, str | bytes], list[dict[str, Any]]]: ...


@overload
async def read_artifact_bundle(
    artifact: BuildRecord, *, include_binary: bool, retain_svg_text: bool = False,
) -> tuple[dict[str, str | bytes], list[dict[str, Any]]]: ...


async def read_artifact_bundle(
    artifact: BuildRecord, *, include_binary: bool = False, retain_svg_text: bool = False,
) -> tuple[dict[str, str], list[dict[str, Any]]] | tuple[dict[str, str | bytes], list[dict[str, Any]]]:
    metadata = artifact.commit_metadata.metadata
    raw = await read_verified_artifact_bundle(artifact, max_bytes=APP_BUNDLE_MAX_TOTAL_BYTES)
    prefix = f"{metadata['bundle_name']}/"
    files: dict[str, str | bytes] = {}
    diagnostics: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_bytes = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for info in archive.infolist():
            path = safe_artifact_relpath(info.filename)
            if path is None:
                diagnostics.append({"path": info.filename, "code": "unsafe_path", "blocking": True})
                continue
            reason = None
            if stat.S_ISLNK(info.external_attr >> 16):
                reason = "symlink"
            elif not path.startswith(prefix):
                if info.is_dir() and path == metadata["bundle_name"]:
                    continue
                reason = "outside_bundle_root"
            elif info.is_dir():
                continue
            else:
                path = path[len(prefix):]
                if path.casefold() in seen:
                    reason = "duplicate_path"
                elif len(seen) >= APP_BUNDLE_MAX_FILES:
                    reason = "file_limit"
                elif info.file_size > _MAX_FILE_BYTES:
                    reason = "file_too_large"
                elif total_bytes + info.file_size > APP_BUNDLE_MAX_TOTAL_BYTES:
                    reason = "total_size_limit"
            if reason:
                diagnostics.append({"path": info.filename, "code": reason, "blocking": True})
                continue
            seen.add(path.casefold())
            total_bytes += info.file_size
            data = archive.read(info)
            suffix = PurePosixPath(path).suffix.lower()
            if suffix == ".svg":
                try:
                    svg_text = data.decode("utf-8")
                    if "\x00" in svg_text:
                        raise UnicodeError
                except UnicodeError:
                    diagnostics.append({"path": path, "code": "non_text_source", "blocking": True})
                    continue
                if retain_svg_text and not include_binary:
                    files[path] = svg_text
                    continue
            if suffix in _BINARY_ASSET_SUFFIXES:
                if include_binary:
                    files[path] = data
                else:
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
