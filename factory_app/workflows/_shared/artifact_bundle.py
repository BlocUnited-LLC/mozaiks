"""Verified source and optional binary assets from a committed app bundle."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Any, Literal, overload

from mozaiksai.control_plane.contracts import safe_artifact_relpath
from mozaiksai.core.artifacts.content_store import read_verified_artifact_bundle
from mozaiksai.core.artifacts.models import BuildRecord, validate_canonical_bundle_name
from mozaiksai.core.runtime.app.paths import APP_BINARY_ASSET_SUFFIXES

APP_BUNDLE_MAX_FILE_BYTES = 8_000_000
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
    raw = await read_verified_artifact_bundle(artifact, max_bytes=APP_BUNDLE_MAX_TOTAL_BYTES)
    return _unpack_bundle(
        raw, roots={artifact.commit_metadata.metadata["bundle_name"]: ""},
        include_binary=include_binary, retain_svg_text=retain_svg_text,
    )


def add_preview_workflow_files(
    files: dict[str, str | bytes], record: BuildRecord, metadata: dict[str, Any],
) -> None:
    """Mount the exact registered pack at the standalone workspace's workflow root."""
    from factory_app.workflows._shared.workflow_integration import read_verified_workflow_archive

    names = [validate_canonical_bundle_name(item["workflow_name"]) for item in metadata["workflows"]]
    distinct = {name.casefold() for name in names}
    if "extended_orchestration" in distinct or len(distinct) != len(names):
        raise ValueError("Pinned workflow catalog contains a reserved or duplicate root")
    roots = {name: f"workflows/{name}/" for name in names}
    mounted, diagnostics = _unpack_bundle(read_verified_workflow_archive(record), roots=roots, include_binary=True)
    if diagnostics or any(f"{prefix}orchestrator.yaml" not in mounted for prefix in roots.values()):
        raise ValueError("Pinned workflow archive does not contain its declared workspace files")
    # A self-contained app may already contain the exact same workflow. Never
    # overlay individual files from different versions of the same workflow.
    mounted_roots = {prefix.casefold() for prefix in roots.values()}
    for path, content in files.items():
        parts = path.casefold().split("/", 2)
        if path.casefold() == "workflows" or (len(parts) >= 2 and f"{parts[0]}/{parts[1]}/" in mounted_roots and mounted.get(path) != content):
            raise ValueError("App workspace conflicts with a pinned workflow file")
    composed = files | mounted
    if (len(composed) > APP_BUNDLE_MAX_FILES or
            sum(len(item.encode("utf-8")) if isinstance(item, str) else len(item) for item in composed.values()) > APP_BUNDLE_MAX_TOTAL_BYTES):
        raise ValueError("Composed preview exceeds bundle limits")
    files.update(mounted)


def _unpack_bundle(
    raw: bytes, *, roots: dict[str, str], include_binary: bool, retain_svg_text: bool = False,
) -> tuple[dict[str, str | bytes], list[dict[str, Any]]]:
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
            root, separator, member = path.partition("/")
            reason = None
            if stat.S_ISLNK(info.external_attr >> 16):
                reason = "symlink"
            elif root not in roots or (not separator and not info.is_dir()):
                reason = "outside_bundle_root"
            elif info.is_dir():
                continue
            else:
                path = roots[root] + member
                if path.casefold() in seen:
                    reason = "duplicate_path"
                elif len(seen) >= APP_BUNDLE_MAX_FILES:
                    reason = "file_limit"
                elif info.file_size > APP_BUNDLE_MAX_FILE_BYTES:
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
            if suffix in APP_BINARY_ASSET_SUFFIXES:
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
