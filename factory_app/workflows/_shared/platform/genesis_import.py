"""Exact, draft-only import of an already projected existing app bundle."""

from __future__ import annotations

import hashlib
import io
import json
import stat
import tempfile
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from factory_app.app.modules.app_registry.backend.schemas import (
    GenesisAcceptanceReceipt,
    GenesisImportClaim,
)
from factory_app.app.modules.app_registry.backend.service import AppRegistryService
from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from mozaiksai.core.artifacts.content_store import (
    ArtifactContentStore,
    get_artifact_content_store,
    read_verified_artifact_bundle,
)
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


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_entries(*, files_manifest: Sequence[BuildRecordFileEntry | dict[str, Any]],
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
                provenance: PinnedSourceProvenance, claim_sha256: str,
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
        and metadata.get("genesis_import_claim_sha256") == claim_sha256
        and metadata.get("brownfield_genesis") == provenance.model_dump(mode="json")
        and record.files_manifest == entries
    )


def _reserved_claim(import_state: dict[str, Any]) -> GenesisImportClaim:
    return GenesisImportClaim.model_validate({
        key: import_state.get(key) for key in GenesisImportClaim.model_fields
        if key != "status"
    })


def _record_matches_claim(
    record: BuildRecord, row: dict[str, Any], claim: GenesisImportClaim, *,
    owner_user_id: str, execution_app_id: str, build_registry_id: str,
) -> bool:
    metadata = record.commit_metadata.metadata
    return all((
        row.get("build_registry_id") == build_registry_id,
        row.get("owner_user_id") == owner_user_id,
        row.get("chat_app_id") == execution_app_id,
        record.id == claim.build_record_id,
        record.app_id == row.get("app_id"),
        record.parent_build_record_id is None,
        record.build_family == record.build_key == "app_bundle",
        record.commit_metadata.author_user_id == owner_user_id,
        metadata.get("bundle_mode") == "brownfield_genesis_import",
        metadata.get("build_registry_id") == build_registry_id,
        metadata.get("execution_app_id") == execution_app_id,
        metadata.get("target_app_id") == row.get("app_id"),
        metadata.get("bundle_name") == claim.bundle_name,
        metadata.get("bundle_sha256") == claim.bundle_sha256,
        metadata.get("content_digest") == claim.bundle_sha256,
        metadata.get("content_backend") == claim.content_backend,
        metadata.get("genesis_import_claim_sha256") == _canonical_digest(claim.model_dump(mode="json")),
        metadata.get("brownfield_genesis") == {
            "source_id": claim.source_id, "revision_id": claim.revision_id, "tree_id": claim.tree_id,
        },
        _canonical_digest([entry.model_dump(mode="json") for entry in record.files_manifest])
        == claim.manifest_sha256,
    ))


async def read_imported_genesis_review_bundle(
    record: BuildRecord, *, owner_user_id: str, execution_app_id: str,
    build_registry_id: str, registry_service: AppRegistryService | None = None,
) -> bytes:
    """Return exact source bytes for owner review, including a reserved draft."""
    registry = registry_service or AppRegistryService()
    row = (await registry.get_app_record(
        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
    )).get("app")
    if not isinstance(row, dict):
        raise GenesisImportError("Factory target is unavailable to this owner")
    state = row.get("genesis_import")
    if not isinstance(state, dict) or state.get("status") not in {"reserved", "accepted"}:
        raise GenesisImportError("Factory target has no reviewed Genesis source")
    try:
        claim = _reserved_claim(state)
    except ValueError as exc:
        raise GenesisImportError("Factory Genesis reservation is invalid") from exc
    if (not _record_matches_claim(
        record, row, claim, owner_user_id=owner_user_id,
        execution_app_id=execution_app_id, build_registry_id=build_registry_id,
    ) or (state["status"] == "reserved" and record.lifecycle_status != BuildRecordStatus.DRAFT)):
        raise GenesisImportError("imported Genesis record differs from its reserved source")
    raw = await read_verified_artifact_bundle(record, max_bytes=_MAX_ARCHIVE_BYTES)
    if hashlib.sha256(raw).hexdigest() != claim.bundle_sha256:
        raise GenesisImportError("imported Genesis archive digest differs from its reservation")
    return raw


def _receipt_matches(
    record: BuildRecord, registry_row: dict[str, Any], *,
    owner_user_id: str, execution_app_id: str, build_registry_id: str,
) -> bool:
    state = registry_row.get("genesis_import")
    if not isinstance(state, dict) or state.get("status") != "accepted":
        return False
    try:
        claim = _reserved_claim(state)
        receipt = GenesisAcceptanceReceipt.model_validate(state.get("acceptance"))
    except ValueError:
        return False
    metadata = record.commit_metadata.metadata
    validation = metadata.get("genesis_validation")
    return bool(
        _record_matches_claim(
            record, registry_row, claim, owner_user_id=owner_user_id,
            execution_app_id=execution_app_id, build_registry_id=build_registry_id,
        )
        and receipt.accepted_by == owner_user_id
        and isinstance(validation, dict)
        and validation.get("contract") == receipt.validation_contract
        and validation.get("sha256") == receipt.validation_sha256
        and validation.get("bundle_sha256") == claim.bundle_sha256
        and validation.get("manifest_sha256") == claim.manifest_sha256
        and record.validation_status == BuildRecordValidationStatus.PASSED
        and record.app_validation_status == "passed"
        and record.lifecycle_status not in {
            BuildRecordStatus.DRAFT, BuildRecordStatus.ARCHIVED, BuildRecordStatus.DELETED,
        }
    )


async def require_accepted_genesis_baseline(
    record: BuildRecord, *, owner_user_id: str, execution_app_id: str,
    build_registry_id: str, registry_service: AppRegistryService | None = None,
) -> None:
    """Refuse an imported baseline without its durable reviewed-source receipt."""
    if record.commit_metadata.metadata.get("bundle_mode") != "brownfield_genesis_import":
        return
    registry = registry_service or AppRegistryService()
    row = (await registry.get_app_record(
        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
    )).get("app")
    if not isinstance(row, dict) or not _receipt_matches(
        record, row, owner_user_id=owner_user_id,
        execution_app_id=execution_app_id, build_registry_id=build_registry_id,
    ):
        raise GenesisImportError("imported Genesis source has no matching accepted review receipt")
    if hashlib.sha256(await read_verified_artifact_bundle(record, max_bytes=_MAX_ARCHIVE_BYTES)).hexdigest() != row["genesis_import"]["bundle_sha256"]:
        raise GenesisImportError("accepted Genesis source bytes changed")


async def accept_existing_app_genesis(
    *, owner_user_id: str, execution_app_id: str, build_registry_id: str,
    build_record_id: str, reviewed_bundle_sha256: str, reviewed_manifest_sha256: str,
    registry_service: AppRegistryService | None = None,
    record_store: BuildRecordStore | None = None,
) -> BuildRecord:
    """Validate persisted source and commit explicit owner review, without deployment."""
    registry = registry_service or AppRegistryService()
    store = record_store or get_artifact_store()
    row = (await registry.get_app_record(
        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
    )).get("app")
    if not isinstance(row, dict) or row.get("chat_app_id") != execution_app_id:
        raise GenesisImportError("Factory target is unavailable to this owner and host")
    state = row.get("genesis_import")
    if not isinstance(state, dict) or state.get("status") not in {"reserved", "accepted"}:
        raise GenesisImportError("Factory target has no reserved Genesis source")
    try:
        claim = _reserved_claim(state)
    except ValueError as exc:
        raise GenesisImportError("Factory Genesis reservation is invalid") from exc
    if (claim.build_record_id != build_record_id
            or claim.bundle_sha256 != reviewed_bundle_sha256
            or claim.manifest_sha256 != reviewed_manifest_sha256):
        raise GenesisImportError("reviewed source digests do not match the reserved Genesis import")
    record = await store.get_build_record(app_id=row["app_id"], build_record_id=build_record_id)
    if record is None or record.lifecycle_status not in {
        BuildRecordStatus.DRAFT, BuildRecordStatus.CURRENT,
        BuildRecordStatus.SUPERSEDED, BuildRecordStatus.STALE,
    }:
        raise GenesisImportError("imported Genesis draft is unavailable for acceptance")
    if not _record_matches_claim(
        record, row, claim, owner_user_id=owner_user_id,
        execution_app_id=execution_app_id, build_registry_id=build_registry_id,
    ):
        raise GenesisImportError("imported Genesis record differs from its reserved source")
    if state["status"] == "reserved":
        from factory_app.workflows.AppGenerator.tools.app_validation import (
            require_contained_imported_smoke_runner,
        )

        require_contained_imported_smoke_runner()
    try:
        bundle_bytes = await read_verified_artifact_bundle(record, max_bytes=_MAX_ARCHIVE_BYTES)
        entries, declared = _manifest_entries(
            files_manifest=record.files_manifest, bundle_name=claim.bundle_name,
            bundle_bytes=bundle_bytes,
        )
        members = _archive_paths(bundle_bytes=bundle_bytes, bundle_name=claim.bundle_name)
        if set(members) != set(declared) or any(
            members[path].file_size != entry.size_bytes for path, entry in declared.items()
        ):
            raise GenesisImportError("persisted source archive and manifest differ")
        files = await _verified_source_files(
            bundle_bytes=bundle_bytes, bundle_name=claim.bundle_name,
            entries=entries, declared=declared,
        )
        app_json = files.get("app.json")
        app_manifest = json.loads(app_json) if isinstance(app_json, str) else None
        if not isinstance(app_manifest, dict) or app_manifest.get("appId") != row["app_id"]:
            raise GenesisImportError("persisted source app identity differs from Factory target")
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise GenesisImportError("persisted Genesis source failed complete-content verification") from exc
    if state["status"] == "accepted":
        if record.lifecycle_status == BuildRecordStatus.DRAFT:
            receipt = GenesisAcceptanceReceipt.model_validate(state.get("acceptance"))
            record = await store.accept_genesis_build_record(
                app_id=record.app_id, build_record_id=record.id,
                validation_sha256=receipt.validation_sha256,
            )
        if record is None:
            raise GenesisImportError("accepted Genesis artifact status could not be recovered")
        await require_accepted_genesis_baseline(
            record, owner_user_id=owner_user_id, execution_app_id=execution_app_id,
            build_registry_id=build_registry_id, registry_service=registry,
        )
        return record
    if record.lifecycle_status != BuildRecordStatus.DRAFT:
        raise GenesisImportError("unreviewed Genesis artifact is no longer a draft")

    from factory_app.workflows.AppGenerator.tools.app_validation import (
        run_app_bundle_acceptance_gate,
    )

    result = await run_app_bundle_acceptance_gate(
        files={path: content for path, content in files.items() if isinstance(content, str)},
        contained_imported_source=True,
        runtime_binary_assets={path: content for path, content in files.items() if isinstance(content, bytes)},
    )
    if result.get("status") != "passed" or result.get("passed") is not True:
        raise GenesisImportError("imported Genesis source failed canonical app-bundle runtime validation")
    evidence = {
        "contract": "app_bundle_acceptance_gate_v1",
        "bundle_sha256": claim.bundle_sha256,
        "manifest_sha256": claim.manifest_sha256,
        "snapshot_digest": result.get("snapshot_digest"),
        "passed_checks": [check.get("id") for check in result.get("checks", []) if check.get("passed") is True],
    }
    validation_sha256 = _canonical_digest(evidence)
    evidence["sha256"] = validation_sha256
    record = await store.mark_genesis_build_record_validated(
        app_id=record.app_id, build_record_id=record.id, validation=evidence,
    )
    if (record is None or record.lifecycle_status != BuildRecordStatus.DRAFT
            or record.validation_status != BuildRecordValidationStatus.PASSED
            or record.commit_metadata.metadata.get("genesis_validation") != evidence):
        raise GenesisImportError("Genesis draft changed before validated review")
    receipt = GenesisAcceptanceReceipt(
        accepted_by=owner_user_id, accepted_at=datetime.now(UTC),
        validation_sha256=validation_sha256,
    )
    accepted = await registry.accept_genesis_import(
        build_registry_id=build_registry_id, owner_user_id=owner_user_id,
        app_id=row["app_id"], chat_app_id=execution_app_id,
        claim=claim, receipt=receipt,
    )
    if accepted is None:
        raise GenesisImportError("Factory target changed before Genesis review could be accepted")
    record = await store.accept_genesis_build_record(
        app_id=record.app_id, build_record_id=record.id,
        validation_sha256=validation_sha256,
    )
    if record is None:
        raise GenesisImportError("accepted Genesis artifact status could not be committed")
    await require_accepted_genesis_baseline(
        record, owner_user_id=owner_user_id, execution_app_id=execution_app_id,
        build_registry_id=build_registry_id, registry_service=registry,
    )
    return record


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
            or owned.get("artifact_version_id") or owned.get("current_build_run")
            or owned.get("bundle_path")):
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
    claim = GenesisImportClaim(
        build_record_id=record_id,
        bundle_name=bundle_name,
        bundle_sha256=bundle_sha256,
        manifest_sha256=_canonical_digest([entry.model_dump(mode="json") for entry in entries]),
        content_backend=content_store.backend_name,
        source_id=provenance.source_id,
        revision_id=provenance.revision_id,
        tree_id=provenance.tree_id,
    )
    claim_sha256 = _canonical_digest(claim.model_dump(mode="json"))
    prior = await record_store.get_build_record(app_id=target_app_id, build_record_id=record_id)
    if prior is not None:
        if not _same_draft(
            prior, expected_id=record_id, target_app_id=target_app_id,
            owner_user_id=owner_user_id,
            build_registry_id=build_registry_id, execution_app_id=execution_app_id,
            bundle_name=bundle_name, bundle_sha256=bundle_sha256,
            content_backend=content_store.backend_name, provenance=provenance,
            claim_sha256=claim_sha256,
            entries=entries,
        ):
            raise GenesisImportError("source revision was already imported with different facts")
    elif await record_store.list_build_records(
        app_id=target_app_id, build_family="app_bundle", build_key="app_bundle", limit=1,
    ):
        raise GenesisImportError("Factory target already has an app-bundle lineage")

    reserved = await registry.reserve_genesis_import(
        build_registry_id=build_registry_id, owner_user_id=owner_user_id,
        app_id=target_app_id, chat_app_id=execution_app_id, claim=claim,
    )
    if reserved is None or reserved.get("genesis_import") != claim.model_dump(mode="json"):
        raise GenesisImportError("Factory target changed or reserved another Genesis source")
    if prior is not None:
        if await content_store.get_verified_blob(bundle_sha256) != bundle_bytes:
            raise GenesisImportError("stored source archive differs from verified bytes")
        return prior

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
        "genesis_import_claim_sha256": claim_sha256,
        "build_registry_id": build_registry_id,
        "target_app_id": target_app_id,
        "execution_app_id": execution_app_id,
        "brownfield_genesis": provenance.model_dump(mode="json"),
    }
    # Recheck the exact reservation after content upload. Generic lifecycle
    # writers cannot advance a target with a reserved Genesis import.
    reloaded = (await registry.get_app_record(
        owner_user_id=owner_user_id, build_registry_id=build_registry_id,
    )).get("app")
    if reloaded != reserved:
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
            claim_sha256=claim_sha256,
            entries=entries,
        ):
            raise GenesisImportError("concurrent source import did not match verified facts") from exc
        return raced


__all__ = [
    "GenesisImportError", "PinnedSourceProvenance", "import_existing_app_genesis_draft",
    "accept_existing_app_genesis", "require_accepted_genesis_baseline",
]
