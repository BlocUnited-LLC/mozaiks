from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from mozaiksai.core.app_context.scan_policy import (
    default_context_graph_scan_policy,
    is_excluded_source_directory_path,
    is_sensitive_source_path,
    safe_scan_relpath,
    skip_reason_for_path,
)
from mozaiksai.core.artifacts.content_store import (
    ArtifactContentStore,
    ContentIntegrityError,
    ContentNotFoundError,
)
from mozaiksai.core.artifacts.models import resolve_canonical_bundle_entry
from mozaiksai.core.artifacts.store import ArtifactStore

_LEGACY_MAX_FILE_BYTES = 80_000
_IMPORT_EXTENSIONS = (
    "",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".py",
    "/index.js",
    "/index.jsx",
    "/index.ts",
    "/index.tsx",
)


def safe_relpath(raw: Any) -> str | None:
    return safe_scan_relpath(raw)


async def load_artifact_workspace(
    *,
    artifact_store: ArtifactStore,
    app_id: str,
    build_record_id: str,
    content_store: ArtifactContentStore | None = None,
    build_family: str | None = None,
    build_key: str | None = None,
    require_verified: bool = False,
) -> dict[str, Any]:
    artifact = await artifact_store.get_build_record(
        app_id=app_id,
        build_record_id=build_record_id,
    )
    if artifact is None:
        return {"present": False, "reason": "artifact_not_found", "build_record_id": build_record_id}
    if (
        artifact.app_id != app_id
        or artifact.id != build_record_id
        or (build_family is not None and artifact.build_family != build_family)
        or (build_key is not None and artifact.build_key != build_key)
    ):
        return {"present": False, "reason": "artifact_identity_mismatch", "build_record_id": build_record_id}

    metadata = dict(artifact.commit_metadata.metadata or {}) if artifact.commit_metadata else {}
    workspace_dir = metadata.get("workspace_dir")
    artifact_path = metadata.get("artifact_path")
    content_digest = metadata.get("content_digest")
    content_ref = metadata.get("content_ref")
    content_backend = metadata.get("content_backend")
    bundle_digest = None
    coverage = {"artifact_source_incomplete": False}

    # Canonical bundles always use the one verified byte reader, including local
    # ZIPs whose digest lives only in the manifest. Unverified callers may still
    # read workspaces without a canonical manifest, but cannot claim authority.
    canonical_bundle = artifact.build_family == artifact.build_key == "app_bundle" and (
        metadata.get("bundle_name") or any(entry.content_type == "application/zip" for entry in artifact.files_manifest)
    )
    if require_verified or content_digest or canonical_bundle:
        from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle

        try:
            entry = resolve_canonical_bundle_entry(artifact)
            files, diagnostics = await read_artifact_bundle(artifact)
            if any(item["code"] in {"unsafe_path", "outside_bundle_root", "duplicate_path", "symlink"} for item in diagnostics):
                raise ValueError("artifact_bundle_source_layout_invalid")
            for item in diagnostics:
                path = str(item["path"]).removeprefix(f"{metadata['bundle_name']}/")
                _mark_omitted_source(path, coverage)
            file_map = {path: content for path, content in files.items() if not _skip_artifact_workspace_path(path)}
            bundle_digest = f"sha256:{entry.sha256}"
            source = f"content_digest:{content_backend or 'unknown'}" if content_digest else "verified_artifact_bundle"
        except (ContentIntegrityError, ContentNotFoundError, ValueError, OSError, zipfile.BadZipFile):
            return {
                "present": False,
                "reason": "content_digest_unavailable_or_invalid",
                "build_record_id": artifact.id,
            }
    elif workspace_dir and Path(str(workspace_dir)).exists():
        file_map = read_workspace_dir(Path(str(workspace_dir)), coverage=coverage)
        source = "workspace_dir"
    elif artifact_path and Path(str(artifact_path)).exists():
        file_map = read_artifact_zip(
            Path(str(artifact_path)),
            coverage=coverage,
            workspace_index=metadata.get("bundle_mode") == "app_intelligence_workspace_index",
        )
        source = "artifact_zip"
    elif content_ref:
        if content_store is None:
            return {
                "present": False,
                "reason": "content_store_unavailable",
                "build_record_id": artifact.id,
                "content_ref": content_ref,
                "content_backend": content_backend,
            }
        try:
            data = await content_store.get_bundle(content_ref)
            file_map = read_artifact_zip_bytes(data, coverage=coverage)
            source = f"content_store:{content_backend or 'unknown'}"
        except ContentNotFoundError:
            return {
                "present": False,
                "reason": "content_ref_not_found",
                "build_record_id": artifact.id,
                "content_ref": content_ref,
                "content_backend": content_backend,
            }
        except Exception as exc:
            return {
                "present": False,
                "reason": "content_store_error",
                "build_record_id": artifact.id,
                "content_ref": content_ref,
                "content_backend": content_backend,
                "error": str(exc),
            }
    else:
        return {
            "present": False,
            "reason": "workspace_unavailable",
            "build_record_id": artifact.id,
            "artifact_path": artifact_path,
            "workspace_dir": workspace_dir,
        }

    return {
        "present": True,
        "artifact": artifact,
        "source": source,
        "file_map": file_map,
        "workspace_dir": workspace_dir,
        "artifact_path": artifact_path,
        "content_ref": content_ref,
        "content_digest": content_digest,
        "content_backend": content_backend,
        "scan_health": coverage,
        "provenance": {
            "app_id": artifact.app_id,
            "build_record_id": artifact.id,
            "build_family": artifact.build_family,
            "build_key": artifact.build_key,
            "bundle_digest": bundle_digest,
            "verified": bundle_digest is not None,
        },
    }


def read_workspace_dir(root: Path, *, coverage: dict[str, bool] | None = None) -> dict[str, str]:
    file_map: dict[str, str] = {}
    policy = default_context_graph_scan_policy()
    for current, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(
            name for name in dir_names if name.lower() not in policy.excluded_dir_names
        )
        for file_name in sorted(file_names):
            file_path = Path(current) / file_name
            rel = safe_relpath(file_path.relative_to(root).as_posix())
            if rel is None:
                continue
            if _skip_artifact_workspace_path(rel):
                continue
            if not file_path.is_file():
                continue
            if file_path.stat().st_size > _LEGACY_MAX_FILE_BYTES:
                _mark_omitted_source(rel, coverage)
                continue
            try:
                file_map[rel] = file_path.read_text(encoding="utf-8")
            except Exception:
                _mark_omitted_source(rel, coverage)
                continue
    return file_map


def _read_from_archive(
    archive: zipfile.ZipFile,
    *,
    coverage: dict[str, bool] | None = None,
    workspace_index: bool = False,
) -> dict[str, str]:
    max_file_bytes = (
        default_context_graph_scan_policy().max_file_bytes if workspace_index else _LEGACY_MAX_FILE_BYTES
    )
    raw_entries: list[tuple[str, str]] = []
    seen_paths: set[str] = set()
    for info in sorted(archive.infolist(), key=lambda item: item.filename):
        if info.is_dir():
            continue
        safe_name = safe_relpath(info.filename)
        if safe_name is None:
            continue
        if _skip_artifact_workspace_path(safe_name):
            continue
        if info.file_size > max_file_bytes:
            _mark_omitted_source(safe_name, coverage)
            continue
        if safe_name in seen_paths:
            raise ValueError("artifact_source_path_ambiguous")
        seen_paths.add(safe_name)
        try:
            decoded = archive.read(info).decode("utf-8")
        except Exception:
            _mark_omitted_source(safe_name, coverage)
            continue
        raw_entries.append((safe_name, decoded))

    # The workspace index writes paths relative to the workspace without a wrapper.
    if workspace_index:
        return dict(raw_entries)

    split_paths = [path.split("/") for path, _ in raw_entries]
    if split_paths and all(len(parts) > 1 for parts in split_paths):
        first = split_paths[0][0]
        if all(parts[0] == first for parts in split_paths):
            return {"/".join(path.split("/")[1:]): content for path, content in raw_entries}
    return dict(raw_entries)


def _skip_artifact_workspace_path(path: str) -> bool:
    safe = safe_relpath(path)
    if safe is None:
        return True
    policy = default_context_graph_scan_policy()
    name = PurePosixPath(safe).name.lower()
    return (
        is_excluded_source_directory_path(safe, policy=policy)
        or is_sensitive_source_path(safe)
        or name in policy.excluded_file_names
        or ".min." in name
    )


def _mark_omitted_source(path: str, coverage: dict[str, bool] | None) -> None:
    if coverage is not None and skip_reason_for_path(path) is None:
        coverage["artifact_source_incomplete"] = True


def read_artifact_zip(
    zip_path: Path,
    *,
    coverage: dict[str, bool] | None = None,
    workspace_index: bool = False,
) -> dict[str, str]:
    with zipfile.ZipFile(zip_path, "r") as archive:
        return _read_from_archive(archive, coverage=coverage, workspace_index=workspace_index)


def read_artifact_zip_bytes(data: bytes, *, coverage: dict[str, bool] | None = None) -> dict[str, str]:
    """Read an artifact zip from raw bytes (e.g. retrieved from a content store)."""
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        return _read_from_archive(archive, coverage=coverage)


def siblings_for(*, path: str, file_map: dict[str, str], limit: int = 8) -> list[str]:
    parent = str(PurePosixPath(path).parent)
    if parent == ".":
        parent = ""
    siblings = []
    for candidate in sorted(file_map.keys()):
        if candidate == path:
            continue
        candidate_parent = str(PurePosixPath(candidate).parent)
        if candidate_parent == ".":
            candidate_parent = ""
        if candidate_parent == parent:
            siblings.append(candidate)
    return siblings[:limit]


def resolve_related_imports(*, path: str, content: str, file_map: dict[str, str], limit: int = 8) -> list[str]:
    if not content.strip():
        return []
    base_dir = PurePosixPath(path).parent
    related: list[str] = []
    for raw_import in extract_relative_imports(content):
        for candidate in candidate_paths(base_dir=base_dir, import_path=raw_import):
            if candidate in file_map and candidate not in related:
                related.append(candidate)
                break
    return related[:limit]


def extract_relative_imports(content: str) -> list[str]:
    patterns = [
        re.compile(r"""from\s+['"](\.[^'"]+)['"]"""),
        re.compile(r"""import\s+.*?\s+from\s+['"](\.[^'"]+)['"]"""),
        re.compile(r"""require\(\s*['"](\.[^'"]+)['"]\s*\)"""),
    ]
    imports: list[str] = []
    for pattern in patterns:
        for match in pattern.findall(content):
            candidate = str(match or "").strip()
            if candidate and candidate not in imports:
                imports.append(candidate)
    return imports


def candidate_paths(*, base_dir: PurePosixPath, import_path: str) -> list[str]:
    normalized_import = import_path.strip()
    if not normalized_import.startswith("."):
        return []
    candidates: list[str] = []
    for suffix in _IMPORT_EXTENSIONS:
        combined = (base_dir / f"{normalized_import}{suffix}").as_posix()
        safe = safe_relpath(combined)
        if safe and safe not in candidates:
            candidates.append(safe)
    return candidates
