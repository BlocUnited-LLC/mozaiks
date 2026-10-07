"""Exact, draft-only import of an already projected existing app bundle."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from mozaiksai.core.artifacts.content_store import ArtifactContentStore, get_artifact_content_store
from mozaiksai.core.artifacts.models import (
    ArtifactCommitMetadata,
    BuildRecord,
    BuildRecordFileEntry,
    BuildRecordStatus,
    BuildRecordValidationStatus,
    canonical_bundle_archive_path,
    resolve_canonical_bundle_entry,
    validate_canonical_bundle_name,
)
from mozaiksai.core.artifacts.store import BuildRecordStore, get_artifact_store
from mozaiksai.core.semantics.portable_path import detect_collisions, validate_portable_path

_MAX_ARCHIVE_BYTES = 64_000_000
_MAX_SOURCE_BYTES = 64_000_000
_MAX_SOURCE_FILES = 4096


class GenesisImportError(ValueError):
    """The source cannot become a complete, owner-bound draft build record."""


class PinnedSourceProvenance(BaseModel):
    """Secret-free source identity asserted by the trusted fetch/projection caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9._/-]+$")
    revision_id: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    tree_id: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _portable_exact(path: str) -> bool:
    try:
        return validate_portable_path(path).text == path
    except ValueError:
        return False


def _import_record_id(*, target_app_id: str, build_registry_id: str, owner_user_id: str,
                      execution_app_id: str) -> str:
    identity = json.dumps(
        [target_app_id, build_registry_id, owner_user_id, execution_app_id],
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"av_{hashlib.sha256(identity).hexdigest()[:24]}"


def _manifest_entries(*, files_manifest: list[BuildRecordFileEntry | dict[str, Any]],
                      bundle_name: str, bundle_bytes: bytes) -> tuple[list[BuildRecordFileEntry], dict[str, BuildRecordFileEntry]]:
    try:
        entries = [BuildRecordFileEntry.model_validate(entry) for entry in files_manifest]
    except Exception as exc:
        raise GenesisImportError("source file manifest is invalid") from exc
    archive_path = canonical_bundle_archive_path(bundle_name)
    archive_digest = hashlib.sha256(bundle_bytes).hexdigest()
    provisional = BuildRecord(
        _id="av_" + "0" * 24, app_id="import-check", build_family="app_bundle",
        build_key="app_bundle", version_number=1, lineage_root_id="av_" + "0" * 24,
        files_manifest=entries,
        commit_metadata=ArtifactCommitMetadata(metadata={"bundle_name": bundle_name}),
    )
    try:
        archive_entry = resolve_canonical_bundle_entry(provisional)
    except ValueError as exc:
        raise GenesisImportError("source manifest has no exact canonical archive entry") from exc
    if archive_entry.path != archive_path or archive_entry.sha256 != archive_digest or archive_entry.size_bytes != len(bundle_bytes):
        raise GenesisImportError("source archive bytes do not match the declared manifest")
    if sum(entry.path == archive_path for entry in entries) != 1:
        raise GenesisImportError("source manifest repeats the canonical archive path")
    source_paths = [entry.path for entry in entries if entry.path != archive_path]
    try:
        detect_collisions([archive_path, *source_paths])
    except ValueError as exc:
        raise GenesisImportError("source file manifest has colliding paths") from exc
    source_entries: dict[str, BuildRecordFileEntry] = {}
    for entry in entries:
        if entry.path == archive_path:
            continue
        path = entry.path
        if (not _portable_exact(path) or not path.startswith(f"{bundle_name}/")
                or path == f"{bundle_name}/"
                or entry.sha256 is None or len(entry.sha256) != 64
                or any(char not in "0123456789abcdef" for char in entry.sha256)
                or entry.size_bytes is None or not entry.content_type
                or entry.content_type == "application/zip"):
            raise GenesisImportError("source file manifest has an invalid or duplicate member")
        source_entries[path] = entry
    if not source_entries or f"{bundle_name}/app.json" not in source_entries:
        raise GenesisImportError("source manifest is missing root app.json")
    return [archive_entry, *sorted(source_entries.values(), key=lambda entry: entry.path)], source_entries


def _archive_paths(*, bundle_bytes: bytes, bundle_name: str) -> dict[str, zipfile.ZipInfo]:
    paths: dict[str, zipfile.ZipInfo] = {}
    source_bytes = 0
    try:
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as archive:
            for info in archive.infolist():
                path = info.filename
                mode = stat.S_IFMT(info.external_attr >> 16)
                if (info.is_dir() or mode not in {0, stat.S_IFREG}
                        or info.flag_bits & 1 or not _portable_exact(path)
                        or not path.startswith(f"{bundle_name}/")
                        or path == f"{bundle_name}/" or path in paths):
                    raise GenesisImportError("source archive contains an unsafe or duplicate member")
                paths[path] = info
                source_bytes += info.file_size
                if len(paths) > _MAX_SOURCE_FILES or source_bytes > _MAX_SOURCE_BYTES:
                    raise GenesisImportError("source archive exceeds the import size or file limit")
    except (zipfile.BadZipFile, OSError) as exc:
        raise GenesisImportError("source archive is unreadable") from exc
    try:
        detect_collisions(paths)
    except ValueError as exc:
        raise GenesisImportError("source archive contains colliding members") from exc
    if not paths:
        raise GenesisImportError("source archive is empty")
    return paths


async def _verified_source_files(*, bundle_bytes: bytes, bundle_name: str,
                                 entries: list[BuildRecordFileEntry],
                                 declared: dict[str, BuildRecordFileEntry]) -> dict[str, str | bytes]:
    with tempfile.TemporaryDirectory(prefix="mozaiks-genesis-import-") as temp_dir:
        path = Path(temp_dir) / "artifact.zip"
        path.write_bytes(bundle_bytes)
        provisional = BuildRecord(
            _id="av_" + "0" * 24, app_id="import-check", build_family="app_bundle",
            build_key="app_bundle", version_number=1, lineage_root_id="av_" + "0" * 24,
            files_manifest=entries,
            commit_metadata=ArtifactCommitMetadata(
                metadata={"bundle_name": bundle_name, "artifact_path": str(path)},
            ),
        )
        try:
            files, diagnostics = await read_artifact_bundle(provisional, include_binary=True)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            raise GenesisImportError("source archive could not be read completely") from exc
    if diagnostics or set(files) != {path.removeprefix(f"{bundle_name}/") for path in declared}:
        raise GenesisImportError("source archive contains unsupported or incomplete content")
    for member_path, entry in declared.items():
        data = files[member_path.removeprefix(f"{bundle_name}/")]
        raw = data.encode("utf-8") if isinstance(data, str) else data
        if entry.size_bytes != len(raw) or entry.sha256 != hashlib.sha256(raw).hexdigest():
            raise GenesisImportError("source file bytes do not match the declared manifest")
    return files


def _same_draft(record: BuildRecord, *, expected_id: str, target_app_id: str,
                owner_user_id: str,
                build_registry_id: str, execution_app_id: str, bundle_name: str,
                bundle_sha256: str, content_backend: str,
                provenance: PinnedSourceProvenance,
                entries: list[BuildRecordFileEntry]) -> bool:
    metadata = record.commit_metadata.metadata
    return (
        record.id == expected_id and record.app_id == target_app_id
        and record.build_family == record.build_key == "app_bundle"
        and record.parent_build_record_id is None and record.lifecycle_status == BuildRecordStatus.DRAFT
        and record.commit_metadata.author_user_id == owner_user_id
        and metadata.get("build_registry_id") == build_registry_id
        and metadata.get("execution_app_id") == execution_app_id
        and metadata.get("target_app_id") == record.app_id
        and metadata.get("bundle_name") == bundle_name
        and metadata.get("bundle_mode") == "brownfield_genesis_import"
        and metadata.get("bundle_sha256") == bundle_sha256
        and metadata.get("content_digest") == bundle_sha256
        and metadata.get("content_backend") == content_backend
        and metadata.get("brownfield_genesis") == provenance.model_dump(mode="json")
        and record.files_manifest == entries
    )


async def import_existing_app_genesis_draft(
    *,
    owner_user_id: str,
    execution_app_id: str,
    build_registry_id: str,
    bundle_name: str,
    bundle_bytes: bytes,
    files_manifest: list[BuildRecordFileEntry | dict[str, Any]],
    source_provenance: PinnedSourceProvenance,
    registry_service: AppRegistryService | None = None,
    record_store: BuildRecordStore | None = None,
    content_store: ArtifactContentStore | None = None,
) -> BuildRecord:
    """Import a complete immutable source snapshot without accepting its Genesis lineage.

    The trusted caller supplies authenticated owner/host identity and exact bytes
    after source authorization and app-workspace projection. This function has no
    HTTP route, Git credential access, run allocation, or acceptance side effect.
    """
    if not all(isinstance(value, str) and value and value == value.strip() for value in
               (owner_user_id, execution_app_id, build_registry_id)):
        raise GenesisImportError("owner, execution host, and Factory target are required")
    try:
        bundle_name = validate_canonical_bundle_name(bundle_name)
        provenance = PinnedSourceProvenance.model_validate(source_provenance)
    except ValueError as exc:
        raise GenesisImportError("source identity is invalid") from exc
    if not isinstance(bundle_bytes, bytes) or not bundle_bytes or len(bundle_bytes) > _MAX_ARCHIVE_BYTES:
        raise GenesisImportError("source archive bytes are missing or exceed the import limit")

    registry = registry_service or AppRegistryService()
    owned = (await registry.get_app_record(
        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
    )).get("app")
    if (not isinstance(owned, dict) or owned.get("build_registry_id") != build_registry_id
            or owned.get("owner_user_id") != owner_user_id
            or owned.get("chat_app_id") != execution_app_id
            or not isinstance(owned.get("app_id"), str)
            or owned.get("lifecycle_state") != "draft"
            or owned.get("artifact_version_id") or owned.get("current_build_run")):
        raise GenesisImportError("Factory target is not an unstarted draft owned by this host")
    target_app_id = owned["app_id"]

    entries, declared = _manifest_entries(
        files_manifest=files_manifest, bundle_name=bundle_name, bundle_bytes=bundle_bytes,
    )
    members = _archive_paths(bundle_bytes=bundle_bytes, bundle_name=bundle_name)
    if set(members) != set(declared) or any(
        members[path].file_size != entry.size_bytes for path, entry in declared.items()
    ):
        raise GenesisImportError("source archive and declared file manifest differ")
    files = await _verified_source_files(
        bundle_bytes=bundle_bytes, bundle_name=bundle_name,
        entries=entries, declared=declared,
    )
    try:
        manifest = json.loads(files["app.json"])
    except (ValueError, TypeError) as exc:
        raise GenesisImportError("root app.json is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("appId") != target_app_id:
        raise GenesisImportError("root app.json does not match the Factory target")

    record_store = record_store or get_artifact_store()
    content_store = content_store or get_artifact_content_store()
    bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
    record_id = _import_record_id(
        target_app_id=target_app_id, build_registry_id=build_registry_id,
        owner_user_id=owner_user_id, execution_app_id=execution_app_id,
    )
    prior = await record_store.get_build_record(app_id=target_app_id, build_record_id=record_id)
    if prior is not None:
        if not _same_draft(
            prior, expected_id=record_id, target_app_id=target_app_id,
            owner_user_id=owner_user_id,
            build_registry_id=build_registry_id, execution_app_id=execution_app_id,
            bundle_name=bundle_name, bundle_sha256=bundle_sha256,
            content_backend=content_store.backend_name, provenance=provenance,
            entries=entries,
        ):
            raise GenesisImportError("source revision was already imported with different facts")
        if await content_store.get_verified_blob(bundle_sha256) != bundle_bytes:
            raise GenesisImportError("stored source archive differs from verified bytes")
        return prior
    if await record_store.list_build_records(
        app_id=target_app_id, build_family="app_bundle", build_key="app_bundle", limit=1,
    ):
        raise GenesisImportError("Factory target already has an app-bundle lineage")

    persisted_digest = await content_store.put_blob(
        bundle_bytes, expected_digest=bundle_sha256,
    )
    if persisted_digest != bundle_sha256 or await content_store.get_verified_blob(persisted_digest) != bundle_bytes:
        raise GenesisImportError("persisted source archive differs from verified bytes")
    metadata = {
        "bundle_name": bundle_name,
        "bundle_mode": "brownfield_genesis_import",
        "bundle_sha256": bundle_sha256,
        "bundle_size_bytes": len(bundle_bytes),
        "content_digest": persisted_digest,
        "content_backend": content_store.backend_name,
        "build_registry_id": build_registry_id,
        "target_app_id": target_app_id,
        "execution_app_id": execution_app_id,
        "brownfield_genesis": provenance.model_dump(mode="json"),
    }
    # Recheck owner/host/target after content upload; no draft is written for a
    # target that changed while source bytes were being validated or persisted.
    reloaded = (await registry.get_app_record(
        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
    )).get("app")
    if reloaded != owned:
        raise GenesisImportError("Factory target changed during source import")
    try:
        return await record_store.create_build_record(
            app_id=target_app_id, build_family="app_bundle", build_key="app_bundle",
            build_record_id=record_id, parent_build_record_id=None,
            files_manifest=[entry.model_dump(mode="python") for entry in entries],
            source_workflow=None, source_chat_id=None,
            lifecycle_status=BuildRecordStatus.DRAFT,
            validation_status=BuildRecordValidationStatus.PENDING,
            commit_metadata={
                "message": "Existing app source imported for Genesis review",
                "author_user_id": owner_user_id,
                "metadata": metadata,
            },
        )
    except DuplicateKeyError as exc:
        raced = await record_store.get_build_record(app_id=target_app_id, build_record_id=record_id)
        if raced is None or not _same_draft(
            raced, expected_id=record_id, target_app_id=target_app_id,
            owner_user_id=owner_user_id,
            build_registry_id=build_registry_id, execution_app_id=execution_app_id,
            bundle_name=bundle_name, bundle_sha256=bundle_sha256,
            content_backend=content_store.backend_name, provenance=provenance,
            entries=entries,
        ):
            raise GenesisImportError("concurrent source import did not match verified facts") from exc
        return raced


__all__ = ["GenesisImportError", "PinnedSourceProvenance", "import_existing_app_genesis_draft"]
