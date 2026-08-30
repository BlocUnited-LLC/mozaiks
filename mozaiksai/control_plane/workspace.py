"""Disposable coding workspaces with deterministic post-run diff harvest.

The coding lane needs one hardened write path: files are materialized into a
per-request workspace, a provider (the structured-output worker today, an
ACP-driven CLI coding agent later) operates on that workspace only, and the
result is harvested by comparing content hashes against the pre-run manifest.
Enforcement lives here, outside any model: paths are normalized and refused
before writing, symlinks are never followed, and every post-run change that
falls outside the editable manifest is reported as a scope violation instead
of being accepted.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.control_plane.contracts import is_secret_sensitive_path, safe_artifact_relpath

WorkspaceViolationKind = Literal[
    "unsafe_path",
    "secret_path",
    "symlink",
    "outside_allowlist",
    "delete_denied",
]


class WorkspaceScopeViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: WorkspaceViolationKind
    detail: str


class HarvestedFile(BaseModel):
    """One editable file observed in the workspace after provider execution."""

    model_config = ConfigDict(extra="forbid")

    path: str
    op: Literal["create", "update", "delete"]
    modified: bool
    previous_sha256: str | None = None
    new_sha256: str | None = None
    content: str | None = None


class WorkspaceHarvest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[HarvestedFile] = Field(default_factory=list)
    violations: list[WorkspaceScopeViolation] = Field(default_factory=list)
    total_content_bytes: int = 0

    @property
    def clean(self) -> bool:
        return not self.violations


@dataclass(slots=True)
class StagedCodingWorkspace:
    """A materialized per-request workspace plus its pre-run content manifest."""

    workspace_root: Path
    editable_manifest: dict[str, str] = field(default_factory=dict)

    def cleanup(self) -> None:
        """Remove the workspace tree. Idempotent and best-effort."""
        shutil.rmtree(self.workspace_root, ignore_errors=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize_coding_workspace(
    files: dict[str, str],
    *,
    workspace_root: Path,
) -> StagedCodingWorkspace:
    """Write the scoped files into a fresh workspace and record their hashes.

    Every path must normalize to a safe bundle-relative path and must not match
    the staging secret-path policy; any offender fails the whole materialization
    rather than being silently skipped — a scoped-file set that contains an
    unsafe or secret-sensitive path is a scoping bug upstream, not something to
    paper over at the write layer.
    """
    root = workspace_root.resolve()
    root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, str] = {}
    for raw_path, content in files.items():
        safe = safe_artifact_relpath(raw_path)
        if safe is None:
            raise ValueError(f"WORKSPACE_UNSAFE_PATH: {raw_path!r} is not a safe bundle-relative path")
        if is_secret_sensitive_path(safe):
            raise ValueError(f"WORKSPACE_SECRET_PATH: refusing to materialize secret-sensitive path {safe!r}")

        destination = (root / safe).resolve()
        if destination != root and not str(destination).startswith(str(root) + os.sep):
            raise ValueError(f"WORKSPACE_ESCAPE: {raw_path!r} resolves outside the workspace root")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # write_bytes, not write_text: text mode would translate newlines on
        # Windows and desynchronize the on-disk bytes from the manifest hash.
        data = str(content).encode("utf-8")
        destination.write_bytes(data)
        manifest[safe] = hashlib.sha256(data).hexdigest()

    return StagedCodingWorkspace(workspace_root=root, editable_manifest=manifest)


def harvest_coding_workspace(
    workspace: StagedCodingWorkspace,
    *,
    allow_new_files: bool = False,
    allow_deletes: bool = False,
) -> WorkspaceHarvest:
    """Deterministically diff the workspace against its pre-run manifest.

    Walks the real tree without following symlinks. Any symlink is a violation
    regardless of target. Files outside the editable manifest are violations
    unless ``allow_new_files``; manifest files missing from disk are violations
    unless ``allow_deletes``. Nothing here consults the provider's own claims —
    the walk is the only source of truth.
    """
    root = workspace.workspace_root.resolve()
    files: list[HarvestedFile] = []
    violations: list[WorkspaceScopeViolation] = []
    seen: set[str] = set()
    total_bytes = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        for name in list(dirnames):
            if (current / name).is_symlink():
                rel = (current / name).relative_to(root).as_posix()
                violations.append(
                    WorkspaceScopeViolation(
                        path=rel,
                        kind="symlink",
                        detail="Symlinked directory found in workspace; not traversed.",
                    )
                )
                dirnames.remove(name)
        for name in filenames:
            full = current / name
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                violations.append(
                    WorkspaceScopeViolation(
                        path=rel,
                        kind="symlink",
                        detail="Symlinked file found in workspace; not read.",
                    )
                )
                continue
            seen.add(rel)
            previous = workspace.editable_manifest.get(rel)
            new_sha = _sha256_file(full)
            if previous is None:
                if not allow_new_files:
                    violations.append(
                        WorkspaceScopeViolation(
                            path=rel,
                            kind="outside_allowlist",
                            detail="File was created outside the editable manifest.",
                        )
                    )
                    continue
                op: Literal["create", "update"] = "create"
                modified = True
            else:
                op = "update"
                modified = new_sha != previous
            content = full.read_bytes().decode("utf-8", errors="replace")
            total_bytes += len(content.encode("utf-8"))
            files.append(
                HarvestedFile(
                    path=rel,
                    op=op,
                    modified=modified,
                    previous_sha256=previous,
                    new_sha256=new_sha,
                    content=content,
                )
            )

    for rel, previous in sorted(workspace.editable_manifest.items()):
        if rel in seen:
            continue
        if allow_deletes:
            files.append(
                HarvestedFile(
                    path=rel,
                    op="delete",
                    modified=True,
                    previous_sha256=previous,
                    new_sha256=None,
                    content=None,
                )
            )
        else:
            violations.append(
                WorkspaceScopeViolation(
                    path=rel,
                    kind="delete_denied",
                    detail="Editable file was deleted from the workspace; deletes are not allowed.",
                )
            )

    return WorkspaceHarvest(files=files, violations=violations, total_content_bytes=total_bytes)


__all__ = [
    "HarvestedFile",
    "StagedCodingWorkspace",
    "WorkspaceHarvest",
    "WorkspaceScopeViolation",
    "harvest_coding_workspace",
    "materialize_coding_workspace",
]
