from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from mozaiksai.core.artifacts.content_store import ArtifactContentStore, ContentNotFoundError
from mozaiksai.core.artifacts.store import ArtifactStore

_MAX_FILE_BYTES = 80_000
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


def safe_relpath(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    normalized = raw.replace("\\", "/").strip().strip("/")
    if not normalized:
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return None
    return str(path)


async def load_artifact_workspace(
    *,
    artifact_store: ArtifactStore,
    app_id: str,
    artifact_version_id: str,
    content_store: Optional[ArtifactContentStore] = None,
) -> dict[str, Any]:
    artifact = await artifact_store.get_artifact_version(
        app_id=app_id,
        artifact_version_id=artifact_version_id,
    )
    if artifact is None:
        return {"present": False, "reason": "artifact_not_found", "artifact_version_id": artifact_version_id}

    metadata = dict((artifact.commit_metadata.metadata or {})) if artifact.commit_metadata else {}
    workspace_dir = metadata.get("workspace_dir")
    artifact_path = metadata.get("artifact_path")
    content_ref = metadata.get("content_ref")
    content_backend = metadata.get("content_backend")

    if workspace_dir and Path(str(workspace_dir)).exists():
        file_map = read_workspace_dir(Path(str(workspace_dir)))
        source = "workspace_dir"
    elif artifact_path and Path(str(artifact_path)).exists():
        file_map = read_artifact_zip(Path(str(artifact_path)))
        source = "artifact_zip"
    elif content_ref:
        if content_store is None:
            return {
                "present": False,
                "reason": "content_store_unavailable",
                "artifact_version_id": artifact.id,
                "content_ref": content_ref,
                "content_backend": content_backend,
            }
        try:
            data = await content_store.get_bundle(content_ref)
            file_map = read_artifact_zip_bytes(data)
            source = f"content_store:{content_backend or 'unknown'}"
        except ContentNotFoundError:
            return {
                "present": False,
                "reason": "content_ref_not_found",
                "artifact_version_id": artifact.id,
                "content_ref": content_ref,
                "content_backend": content_backend,
            }
        except Exception as exc:
            return {
                "present": False,
                "reason": "content_store_error",
                "artifact_version_id": artifact.id,
                "content_ref": content_ref,
                "content_backend": content_backend,
                "error": str(exc),
            }
    else:
        return {
            "present": False,
            "reason": "workspace_unavailable",
            "artifact_version_id": artifact.id,
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
        "content_backend": content_backend,
    }


def read_workspace_dir(root: Path) -> dict[str, str]:
    file_map: dict[str, str] = {}
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        rel = safe_relpath(file_path.relative_to(root).as_posix())
        if rel is None:
            continue
        if file_path.stat().st_size > _MAX_FILE_BYTES:
            continue
        try:
            file_map[rel] = file_path.read_text(encoding="utf-8")
        except Exception:
            continue
    return file_map


def _read_from_archive(archive: zipfile.ZipFile) -> dict[str, str]:
    raw_entries: list[tuple[str, str]] = []
    for info in archive.infolist():
        if info.is_dir() or info.file_size > _MAX_FILE_BYTES:
            continue
        safe_name = safe_relpath(info.filename)
        if safe_name is None:
            continue
        try:
            decoded = archive.read(info.filename).decode("utf-8")
        except Exception:
            continue
        raw_entries.append((safe_name, decoded))

    if not raw_entries:
        return {}

    split_paths = [path.split("/") for path, _ in raw_entries]
    if split_paths and all(len(parts) > 1 for parts in split_paths):
        first = split_paths[0][0]
        if all(parts[0] == first for parts in split_paths):
            return {"/".join(path.split("/")[1:]): content for path, content in raw_entries}
    return dict(raw_entries)


def read_artifact_zip(zip_path: Path) -> dict[str, str]:
    with zipfile.ZipFile(zip_path, "r") as archive:
        return _read_from_archive(archive)


def read_artifact_zip_bytes(data: bytes) -> dict[str, str]:
    """Read an artifact zip from raw bytes (e.g. retrieved from a content store)."""
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        return _read_from_archive(archive)


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
