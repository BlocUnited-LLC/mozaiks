"""Reviewable repository patches from an approved, harvested coding workspace.

The authenticated host owns plan approval, snapshot identity, repository reads,
and source-control publication. This module verifies bounded workspace transport
into disposable host staging and returns an external-patch candidate for review.
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import struct
import unicodedata
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mozaiksai.core.secrets.contract import is_secret_contract_path, validate_secret_contract_text
from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    ArchiveManifestEntry,
    build_deterministic_archive,
    read_archive_manifest,
)

from .contracts import (
    MAX_READ_ONLY_INSPECTION_BYTES,
    MAX_READ_ONLY_INSPECTION_FILES,
    StagedPatchProposal,
    is_repository_env_template_path,
    is_secret_sensitive_path,
    safe_artifact_relpath,
)
from .execution_context import ApprovedExecutionContext
from .workspace import StagedCodingWorkspace, harvest_coding_workspace, materialize_coding_workspace

_MAX_REPOSITORY_ARCHIVE_BYTES = 16_777_216
_MAX_REPOSITORY_ARCHIVE_FILES = 50
_EMPTY_REPOSITORY_ARCHIVE = b"mozaiks.repository.empty.v1\n"
_ZIP_END_RECORD = struct.Struct("<4s4H2IH")
_ZIP_CENTRAL_HEADER_SIZE = 46


class RepositorySnapshotEvidence(BaseModel):
    """Host-verified projection of the persisted approved repository snapshot.

    The host must verify that this is the complete immutable snapshot record
    bound to the approved plan. Its digest cannot be recomputed here because
    hosted snapshot identity includes host-owned tenant and exclusion evidence.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    repository_full_name: str = Field(min_length=1)
    baseline_commit_sha: str = Field(min_length=40, max_length=40)
    snapshot_digest: str = Field(min_length=1)
    file_manifest: dict[str, str]

    @field_validator("file_manifest")
    @classmethod
    def _validate_manifest(cls, manifest: dict[str, str]) -> dict[str, str]:
        for path, digest in manifest.items():
            _canonical_path(path)
            if not digest.startswith("sha256:") or len(digest) != 71 or any(
                char not in "0123456789abcdef" for char in digest[7:]
            ):
                raise ValueError(f"REPOSITORY_PATCH_MANIFEST_HASH: {path!r}")
        return manifest


class RepositoryPatchFile(BaseModel):
    """One exact approved operation, independently observed in the staged tree."""

    model_config = ConfigDict(extra="forbid")

    path: str
    op: Literal["create", "update", "delete"] = "update"
    previous_sha256: str | None = None
    new_sha256: str | None = None
    content: str | None = None
    diff: str

    @model_validator(mode="after")
    def _validate_operation(self) -> RepositoryPatchFile:
        expected = {
            "create": (False, True, True),
            "update": (True, True, True),
            "delete": (True, False, False),
        }[self.op]
        actual = (self.previous_sha256 is not None, self.new_sha256 is not None, self.content is not None)
        if actual != expected:
            raise ValueError("repository patch hashes and content do not match operation")
        return self


class RepositoryPatchCandidate(BaseModel):
    """Unvalidated external patch for host-owned review and PR publication."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mozaiks.refinement.repository_patch.v2"] = (
        "mozaiks.refinement.repository_patch.v2"
    )
    write_back_mode: Literal["external_patch"] = "external_patch"
    validation_state: Literal["pending"] = "pending"
    mutation_allowed: Literal[False] = False
    handoff_id: str
    request_id: str
    plan_id: str
    app_id: str
    build_registry_id: str
    repository_full_name: str
    baseline_commit_sha: str
    approved_plan_digest: str
    snapshot_digest: str
    proposal_id: str
    provider_id: str
    summary: str
    rationale: str
    changed_files: list[RepositoryPatchFile]
    required_validation_gates: list[str]
    required_security_gates: list[str]
    required_ci_checks: list[str]


def repository_patch_digest(candidate: RepositoryPatchCandidate) -> str:
    """Bind one complete v2 patch candidate for validation and publication.

    Operation order and gate-list order are not semantic. Every other field,
    including review text and diffs, is part of the exact attested candidate.
    """
    if not isinstance(candidate, RepositoryPatchCandidate):
        raise ValueError("REPOSITORY_PATCH_DIGEST_INPUT: invalid candidate")
    candidate = RepositoryPatchCandidate.model_validate(candidate.model_dump(mode="python"))
    if not 1 <= len(candidate.changed_files) <= _MAX_REPOSITORY_ARCHIVE_FILES:
        raise ValueError("REPOSITORY_PATCH_DIGEST_INPUT: invalid candidate or file count")
    identities: list[str] = []
    for change in candidate.changed_files:
        path = _canonical_path(change.path)
        for digest in (change.previous_sha256, change.new_sha256):
            if digest is not None and (
                len(digest) != 71 or not digest.startswith("sha256:")
                or any(char not in "0123456789abcdef" for char in digest[7:])
            ):
                raise ValueError("REPOSITORY_PATCH_DIGEST_HASH: invalid file hash")
        if change.content is not None and change.new_sha256 != f"sha256:{_sha256(change.content)}":
            raise ValueError("REPOSITORY_PATCH_DIGEST_HASH: content differs from file hash")
        folded = path.casefold()
        if any(
            folded == other or folded.startswith(f"{other}/") or other.startswith(f"{folded}/")
            for other in identities
        ):
            raise ValueError("REPOSITORY_PATCH_DIGEST_PATH: changed paths collide")
        identities.append(folded)
    canonical = candidate.model_dump(mode="json")
    canonical["changed_files"] = sorted(canonical["changed_files"], key=lambda item: item["path"])
    for field in ("required_validation_gates", "required_security_gates", "required_ci_checks"):
        canonical[field] = sorted(set(canonical[field]))
    raw = json.dumps(
        {"digest_schema": "mozaiks.refinement.repository_patch_digest.v1", "candidate": canonical},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError("REPOSITORY_PATCH_DIGEST_BUDGET: candidate exceeds digest limit")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_path(path: str) -> str:
    normalized = safe_artifact_relpath(path)
    if (
        normalized is None or normalized == "." or normalized != path
        or unicodedata.normalize("NFC", path) != path
    ):
        raise ValueError(f"REPOSITORY_PATCH_UNSAFE_PATH: {path!r}")
    return normalized


def _in_scope(path: str, paths: list[str], *, casefold: bool = False) -> bool:
    candidate = path.casefold() if casefold else path
    for scope in paths:
        root = scope.rstrip("/")
        if casefold:
            root = root.casefold()
        if candidate == root or candidate.startswith(f"{root}/"):
            return True
    return False


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _reject_env_template_path(path: str) -> None:
    if is_repository_env_template_path(path):
        raise ValueError(f"REPOSITORY_PATCH_ENV_TEMPLATE: {path!r} needs a names-only content gate")


def _validate_output_text(path: str, content: str) -> None:
    _reject_env_template_path(path)
    if is_secret_contract_path(path):
        validate_secret_contract_text(content)


def _unified_diff(path: str, before: str | None, after: str | None) -> str:
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        (after or "").splitlines(keepends=True),
        fromfile=f"a/{path}" if before is not None else "/dev/null",
        tofile=f"b/{path}" if after is not None else "/dev/null",
        lineterm="\n",
    )
    # difflib does not add Git's missing-final-newline marker to content lines.
    return "".join(
        line if line.endswith("\n") else f"{line}\n\\ No newline at end of file\n"
        for line in lines
    )


def _validate_archive_budget(*, max_files: int, max_archive_bytes: int) -> None:
    if not 1 <= max_files <= _MAX_REPOSITORY_ARCHIVE_FILES:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: invalid file limit")
    if not 1 <= max_archive_bytes <= _MAX_REPOSITORY_ARCHIVE_BYTES:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: invalid byte limit")


def _preflight_archive_directory(
    data: bytes, *, expected_files: int | None, max_bytes: int, max_files: int | None = None
) -> None:
    """Bound central-directory parsing before ZipFile creates entry objects.

    The canonical archive writer emits a single-disk ZIP without a comment or
    ZIP64 records. Walk the raw directory too: its entry count can disagree
    with the end record, which ZipFile does not use as a parsing limit.
    """
    if len(data) < _ZIP_END_RECORD.size:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_FORMAT: missing ZIP end record")
    end_offset = len(data) - _ZIP_END_RECORD.size
    signature, disk, directory_disk, disk_files, files, directory_size, directory_offset, comment_size = (
        _ZIP_END_RECORD.unpack_from(data, end_offset)
    )
    if (
        signature != b"PK\x05\x06"
        or disk != 0
        or directory_disk != 0
        or disk_files != files
        or comment_size != 0
        or directory_offset + directory_size != end_offset
    ):
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_FORMAT: invalid ZIP directory")
    limit = max_files if max_files is not None else expected_files
    if limit is None:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: missing file limit")
    if files > limit or (expected_files is not None and files != expected_files):
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: entry count exceeds selection")

    offset = directory_offset
    seen = 0
    total_bytes = 0
    while offset < end_offset:
        if (
            end_offset - offset < _ZIP_CENTRAL_HEADER_SIZE
            or data[offset:offset + 4] != b"PK\x01\x02"
        ):
            raise ValueError("REPOSITORY_PATCH_ARCHIVE_FORMAT: invalid ZIP directory entry")
        filename_size, extra_size, entry_comment_size = struct.unpack_from("<HHH", data, offset + 28)
        total_bytes += struct.unpack_from("<I", data, offset + 24)[0]
        seen += 1
        if seen > limit or total_bytes > max_bytes:
            raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: entry count or size exceeds limit")
        offset += _ZIP_CENTRAL_HEADER_SIZE + filename_size + extra_size + entry_comment_size
        if offset > end_offset:
            raise ValueError("REPOSITORY_PATCH_ARCHIVE_FORMAT: truncated ZIP directory entry")
    if seen != files:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_FORMAT: ZIP directory count mismatch")


def export_repository_workspace_archive(
    workspace: StagedCodingWorkspace,
    *,
    max_files: int,
    max_archive_bytes: int,
    create_paths: list[str] | None = None,
    delete_paths: list[str] | None = None,
) -> bytes:
    """Export every observed regular UTF-8 file after the coding turn stops.

    The archive is derived from the whole staged tree, never from the agent's
    proposed file list. It carries unchanged selected files too, so the host
    can verify the exact observed workspace set after transport. Run this only
    inside an isolated worker with filesystem and memory caps: the canonical
    harvester reads files before this archive byte budget is checked.
    """
    _validate_archive_budget(max_files=max_files, max_archive_bytes=max_archive_bytes)
    creates = set(create_paths or [])
    deletes = set(delete_paths or [])
    grants = [*(create_paths or []), *(delete_paths or [])]
    for path in grants:
        _canonical_path(path)
    if len({path.casefold() for path in grants}) != len(grants):
        raise ValueError("REPOSITORY_PATCH_GRANTS: duplicate operation path")
    occupied = {path.casefold() for path in [*workspace.editable_manifest, *workspace.read_only_manifest]}
    if (
        any(path.casefold() in occupied for path in creates)
        or deletes - set(workspace.editable_manifest)
    ):
        raise ValueError("REPOSITORY_PATCH_GRANTS: operation paths conflict with staged baseline")
    if len(workspace.editable_manifest) + len(creates) + len(workspace.read_only_manifest) > max_files:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: staged file count exceeds limit")
    harvest = harvest_coding_workspace(workspace, allow_new_files=bool(creates), allow_deletes=bool(deletes))
    if harvest.violations:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_SCOPE: staged tree has violations")
    for file in harvest.files:
        if (file.op == "create" and file.path not in creates) or (
            file.op == "delete" and file.path not in deletes
        ):
            raise ValueError(f"REPOSITORY_PATCH_ARCHIVE_OP: {file.path!r}")
    entries: list[ArchiveEntry] = []
    total_bytes = 0
    for file in harvest.files:
        path = _canonical_path(file.path)
        if file.op == "delete":
            continue
        if file.op not in {"update", "create"} or file.content is None:
            raise ValueError(f"REPOSITORY_PATCH_ARCHIVE_OP: {path!r}")
        raw = file.content.encode("utf-8")
        if file.new_sha256 != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"REPOSITORY_PATCH_ARCHIVE_UTF8: {path!r} is not exact UTF-8")
        _validate_output_text(path, file.content)
        total_bytes += len(raw)
        if total_bytes > max_archive_bytes:
            raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: staged contents exceed limit")
        entries.append(ArchiveEntry(path=path, content=raw))
    archive_bytes = build_deterministic_archive(entries) if entries else _EMPTY_REPOSITORY_ARCHIVE
    if len(archive_bytes) > max_archive_bytes:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: archive exceeds limit")
    return archive_bytes


def _verify_snapshot_identity(
    context: ApprovedExecutionContext,
    snapshot: RepositorySnapshotEvidence,
) -> None:
    if context.schema_version != "managed_refinement.execution_context.v1":
        raise ValueError("REPOSITORY_PATCH_CONTEXT_VERSION: unsupported execution context")
    if (
        snapshot.plan_id != context.plan_id
        or snapshot.request_id != context.request_id
        or snapshot.app_id != context.app_id
        or snapshot.repository_full_name != context.repository_full_name
        or snapshot.baseline_commit_sha != context.baseline_commit_sha
        or snapshot.snapshot_digest != context.snapshot_digest
    ):
        raise ValueError("REPOSITORY_PATCH_SNAPSHOT_IDENTITY: snapshot does not match approval")


def select_repository_read_only_files(
    context: ApprovedExecutionContext,
    *,
    snapshot: RepositorySnapshotEvidence,
    selected_paths: list[str],
    baseline_files: Mapping[str, str],
    validate_path: Callable[[str], object],
    max_files: int,
    max_bytes: int,
) -> dict[str, str]:
    """Bound host-fetched inspection bytes to approved, immutable snapshot evidence.

    A read-only path must be under both an allowed and read-only scope; the
    latter only narrows approval. The host supplies exact baseline bytes and
    a per-path policy callback. Nothing is fetched or persisted here.
    """
    _verify_snapshot_identity(context, snapshot)
    if not 1 <= max_files <= MAX_READ_ONLY_INSPECTION_FILES or not 1 <= max_bytes <= MAX_READ_ONLY_INSPECTION_BYTES:
        raise ValueError("REPOSITORY_PATCH_INSPECTION_BUDGET: invalid file or byte limit")
    if len(selected_paths) > max_files or len(set(selected_paths)) != len(selected_paths):
        raise ValueError("REPOSITORY_PATCH_INSPECTION_SELECTION: paths exceed limit or repeat")
    if set(baseline_files) != set(selected_paths):
        raise ValueError("REPOSITORY_PATCH_INSPECTION_SET: files differ from selected paths")
    total_bytes = 0
    for path in selected_paths:
        _canonical_path(path)
        if is_secret_sensitive_path(path) or is_secret_contract_path(path):
            raise ValueError(f"REPOSITORY_PATCH_SECRET_PATH: {path!r}")
        _reject_env_template_path(path)
        if (
            not _in_scope(path, context.allowed_paths)
            or not _in_scope(path, context.read_only_paths)
            or _in_scope(path, context.prohibited_paths, casefold=True)
        ):
            raise ValueError(f"REPOSITORY_PATCH_INSPECTION_SCOPE: {path!r} is not approved read-only context")
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
        content = baseline_files[path]
        if snapshot.file_manifest.get(path) != f"sha256:{_sha256(content)}":
            raise ValueError(f"REPOSITORY_PATCH_BASELINE_HASH: {path!r} differs from approved snapshot")
        total_bytes += len(content.encode("utf-8"))
        if total_bytes > max_bytes:
            raise ValueError("REPOSITORY_PATCH_INSPECTION_BUDGET: contents exceed limit")
    return dict(baseline_files)


def _verify_selected_baseline(
    context: ApprovedExecutionContext,
    *,
    snapshot: RepositorySnapshotEvidence,
    selected_paths: list[str],
    baseline_files: Mapping[str, str],
    validate_path: Callable[[str], object],
    validate_create_absence: Callable[[str], object] | None = None,
) -> dict[str, str]:
    _verify_snapshot_identity(context, snapshot)
    if (
        (not selected_paths and not context.create_paths)
        or len({path.casefold() for path in selected_paths}) != len(selected_paths)
    ):
        raise ValueError("REPOSITORY_PATCH_SELECTION: selected paths must be unique and nonempty")
    if len(selected_paths) + len(context.create_paths) > _MAX_REPOSITORY_ARCHIVE_FILES:
        raise ValueError("REPOSITORY_PATCH_SELECTION: selected paths exceed repository budget")
    if not set(context.delete_paths) <= set(selected_paths):
        raise ValueError("REPOSITORY_PATCH_GRANTS: delete paths must be selected baseline files")
    for path in baseline_files:
        _canonical_path(path)
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
    if set(baseline_files) != set(selected_paths):
        raise ValueError("REPOSITORY_PATCH_BASELINE_SET: baseline files must match selected paths")

    occupied = {path.casefold() for path in snapshot.file_manifest}
    if len(occupied) != len(snapshot.file_manifest):
        raise ValueError("REPOSITORY_PATCH_MANIFEST_COLLISION: baseline paths collide")
    for path in context.create_paths:
        _canonical_path(path)
        if is_secret_sensitive_path(path) or is_secret_contract_path(path):
            raise ValueError(f"REPOSITORY_PATCH_SECRET_PATH: {path!r}")
        _reject_env_template_path(path)
        if path.casefold() in occupied:
            raise ValueError(f"REPOSITORY_PATCH_CREATE_EXISTS: {path!r}")
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
        if validate_create_absence is None:
            raise ValueError("REPOSITORY_PATCH_CREATE_PROOF: complete baseline tree proof is required")
        if validate_create_absence(path) is not None:
            raise ValueError("REPOSITORY_PATCH_CREATE_PROOF: proof must raise or return None")

    expected_hashes: dict[str, str] = {}
    for path in selected_paths:
        _canonical_path(path)
        if is_secret_sensitive_path(path):
            raise ValueError(f"REPOSITORY_PATCH_SECRET_PATH: {path!r}")
        _reject_env_template_path(path)
        if (
            not _in_scope(path, context.allowed_paths)
            or _in_scope(path, context.prohibited_paths, casefold=True)
            or _in_scope(path, context.read_only_paths, casefold=True)
        ):
            raise ValueError(f"REPOSITORY_PATCH_SCOPE: {path!r} is not editable")
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
        manifest_hash = snapshot.file_manifest.get(path)
        content_hash = _sha256(baseline_files[path])
        if manifest_hash != f"sha256:{content_hash}":
            raise ValueError(f"REPOSITORY_PATCH_BASELINE_HASH: {path!r} differs from approved snapshot")
        expected_hashes[path] = content_hash
    return expected_hashes


def stage_repository_workspace_archive(
    context: ApprovedExecutionContext,
    *,
    snapshot: RepositorySnapshotEvidence,
    selected_paths: list[str],
    baseline_files: Mapping[str, str],
    archive_bytes: bytes,
    workspace_root: Path,
    validate_path: Callable[[str], object],
    validate_create_absence: Callable[[str], object] | None = None,
    max_files: int,
    max_archive_bytes: int,
) -> StagedCodingWorkspace:
    """Verify container output and reconstruct a host-owned disposable tree.

    No archive entry is extracted as a filesystem path. The approved baseline
    is materialized first, preserving its pre-run manifest; verified output
    bytes then replace only those exact selected files. The caller must keep
    this tree private and stable until ``finalize_repository_patch`` returns.
    """
    _validate_archive_budget(max_files=max_files, max_archive_bytes=max_archive_bytes)
    _verify_selected_baseline(
        context,
        snapshot=snapshot,
        selected_paths=selected_paths,
        baseline_files=baseline_files,
        validate_path=validate_path,
        validate_create_absence=validate_create_absence,
    )
    if len(selected_paths) + len(context.create_paths) > max_files or len(archive_bytes) > max_archive_bytes:
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_BUDGET: input exceeds limit")
    root = Path(workspace_root)
    if root.exists() or root.is_symlink():
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_WORKSPACE: target must not exist")

    manifest_entries: tuple[ArchiveManifestEntry, ...]
    if archive_bytes == _EMPTY_REPOSITORY_ARCHIVE:
        manifest_entries = ()
    else:
        _preflight_archive_directory(
            archive_bytes,
            expected_files=len(selected_paths) if not context.create_paths and not context.delete_paths else None,
            max_files=len(selected_paths) + len(context.create_paths),
            max_bytes=max_archive_bytes,
        )
        manifest_entries = read_archive_manifest(archive_bytes).entries
    entry_paths = {entry.path for entry in manifest_entries}
    if (
        (set(selected_paths) - entry_paths) - set(context.delete_paths)
        or entry_paths - set(selected_paths) - set(context.create_paths)
    ):
        raise ValueError("REPOSITORY_PATCH_ARCHIVE_SET: entries differ from approved operation grants")

    observed: dict[str, bytes] = {}
    if manifest_entries:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    else:
        archive = None
    try:
        for entry in manifest_entries:
            path = _canonical_path(entry.path)
            if validate_path(path) is not None:
                raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
            assert archive is not None
            raw = archive.read(path)
            if len(raw) != entry.size_bytes or f"sha256:{hashlib.sha256(raw).hexdigest()}" != entry.content_sha256:
                raise ValueError(f"REPOSITORY_PATCH_ARCHIVE_HASH: {path!r}")
            try:
                content = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError(f"REPOSITORY_PATCH_ARCHIVE_UTF8: {path!r}") from exc
            _validate_output_text(path, content)
            observed[path] = raw
    finally:
        if archive is not None:
            archive.close()

    workspace = StagedCodingWorkspace(workspace_root=root, strict_cleanup=True)
    try:
        workspace = materialize_coding_workspace(dict(baseline_files), workspace_root=root)
        workspace.strict_cleanup = True
        for path in set(selected_paths) - set(observed):
            (workspace.workspace_root / path).unlink()
        for path, raw in observed.items():
            target = workspace.workspace_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    except Exception:
        workspace.cleanup()
        raise
    return workspace


def finalize_repository_patch(
    context: ApprovedExecutionContext,
    *,
    snapshot: RepositorySnapshotEvidence,
    selected_paths: list[str],
    baseline_files: Mapping[str, str],
    workspace: StagedCodingWorkspace,
    proposal: StagedPatchProposal,
    validate_path: Callable[[str], object],
    validate_create_absence: Callable[[str], object] | None = None,
) -> RepositoryPatchCandidate:
    """Verify an approved repository edit against an independently harvested tree.

    This function reads the staged workspace and performs no writes, validation
    commands, Git actions, artifact promotion, or model calls. The host must
    supply a verified snapshot, fetch baseline files at its immutable commit,
    keep the provider confined to a disposable workspace, and call this only
    after the provider has stopped modifying that workspace.

    ``validate_path`` is the host's per-file policy. It must raise for any
    denied path; the finalizer calls it for every selected and proposed path.
    """

    expected_hashes = _verify_selected_baseline(
        context,
        snapshot=snapshot,
        selected_paths=selected_paths,
        baseline_files=baseline_files,
        validate_path=validate_path,
        validate_create_absence=validate_create_absence,
    )

    if workspace.editable_manifest != expected_hashes:
        raise ValueError("REPOSITORY_PATCH_WORKSPACE_BASELINE: staged files differ from selected baseline")
    harvest = harvest_coding_workspace(
        workspace,
        allow_new_files=bool(context.create_paths),
        allow_deletes=bool(context.delete_paths),
    )
    observed_paths = [file.path for file in harvest.files]
    observed_paths.extend(violation.path for violation in harvest.violations)
    for path in observed_paths:
        _canonical_path(path)
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
    if harvest.violations:
        violations = ", ".join(f"{item.kind}:{item.path}" for item in harvest.violations)
        raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_SCOPE: {violations}")
    if {file.path for file in harvest.files} - set(selected_paths) - set(context.create_paths):
        raise ValueError("REPOSITORY_PATCH_WORKSPACE_SET: harvested files differ from selection")

    harvested_changes: dict[str, tuple[Literal["create", "update", "delete"], str | None]] = {}
    for file in harvest.files:
        if (file.op == "create" and file.path not in context.create_paths) or (
            file.op == "delete" and file.path not in context.delete_paths
        ):
            raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_OP: {file.path!r}")
        if file.op == "delete":
            if file.previous_sha256 != expected_hashes[file.path] or file.new_sha256 is not None or file.content is not None or not file.modified:
                raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_HASH: {file.path!r}")
            harvested_changes[file.path] = (file.op, None)
            continue
        if file.content is None:
            raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_OP: {file.path!r}")
        _validate_output_text(file.path, file.content)
        observed_hash = _sha256(file.content)
        if file.op == "create":
            if file.previous_sha256 is not None or file.new_sha256 != observed_hash or not file.modified:
                raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_HASH: {file.path!r}")
        elif (
            file.previous_sha256 != expected_hashes[file.path]
            or file.new_sha256 != observed_hash
            or file.modified != (observed_hash != expected_hashes[file.path])
        ):
            raise ValueError(f"REPOSITORY_PATCH_WORKSPACE_HASH: {file.path!r}")
        if file.modified:
            harvested_changes[file.path] = (file.op, file.content)

    if proposal.status != "completed" or not harvested_changes:
        raise ValueError("REPOSITORY_PATCH_PROPOSAL_STATUS: completed nonempty edit required")
    proposed: dict[str, tuple[str, str | None]] = {}
    for change in proposal.changed_files:
        path = _canonical_path(change.path)
        if validate_path(path) is not None:
            raise ValueError("REPOSITORY_PATCH_HOST_POLICY: validate_path must raise or return None")
        if path in proposed or (
            change.op == "update" and path not in expected_hashes
        ) or (
            change.op == "create" and path not in context.create_paths
        ) or (
            change.op == "delete" and path not in context.delete_paths
        ):
            raise ValueError(f"REPOSITORY_PATCH_PROPOSAL_SCOPE: {path!r}")
        proposed[path] = (change.op, change.content)
    if proposed != harvested_changes:
        raise ValueError("REPOSITORY_PATCH_PROPOSAL_MISMATCH: provider output differs from staged bytes")
    owned_paths = [_canonical_path(path) for path in proposal.owned_paths]
    if len(set(owned_paths)) != len(owned_paths) or not set(owned_paths) <= set(selected_paths) | set(context.create_paths):
        raise ValueError("REPOSITORY_PATCH_OWNERSHIP: provider claimed an unselected path")
    if not set(proposed) <= set(owned_paths):
        raise ValueError("REPOSITORY_PATCH_OWNERSHIP: changed paths were not claimed")

    changes = []
    for path, (op, content) in sorted(harvested_changes.items()):
        changes.append(RepositoryPatchFile(
            path=path,
            op=op,
            previous_sha256=f"sha256:{expected_hashes[path]}" if op != "create" else None,
            new_sha256=f"sha256:{_sha256(content)}" if content is not None else None,
            content=content,
            diff=_unified_diff(path, baseline_files.get(path), content),
        ))
    return RepositoryPatchCandidate(
        handoff_id=context.handoff_id,
        request_id=context.request_id,
        plan_id=context.plan_id,
        app_id=context.app_id,
        build_registry_id=context.build_registry_id,
        repository_full_name=context.repository_full_name,
        baseline_commit_sha=context.baseline_commit_sha,
        approved_plan_digest=context.approved_plan_digest,
        snapshot_digest=context.snapshot_digest,
        proposal_id=proposal.proposal_id,
        provider_id=proposal.provider_id,
        summary=f"Approved repository file operations: {len(changes)}.",
        rationale="Derived from approved snapshot and host-verified workspace bytes; validation pending.",
        changed_files=changes,
        required_validation_gates=context.required_validation_gates,
        required_security_gates=context.required_security_gates,
        required_ci_checks=context.required_ci_checks,
    )


__all__ = [
    "RepositoryPatchCandidate",
    "RepositoryPatchFile",
    "RepositorySnapshotEvidence",
    "export_repository_workspace_archive",
    "finalize_repository_patch",
    "select_repository_read_only_files",
    "stage_repository_workspace_archive",
]
