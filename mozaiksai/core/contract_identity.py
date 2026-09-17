"""Deterministic identity for cross-repository contract inputs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "mozaiks.contract_identity.v1"


class ContractIdentityError(ValueError):
    """Raised when contract identity metadata cannot be verified."""


def _canonical_bytes(path: Path) -> bytes:
    """Ignore checkout line-ending differences while hashing text contracts."""
    raw = path.read_bytes()
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def build_contract_manifest(root: Path, paths: Sequence[str]) -> dict[str, Any]:
    """Build a stable manifest of contract files relative to ``root``."""
    entries: list[dict[str, str]] = []
    for raw_path in sorted(set(paths)):
        relative = PurePosixPath(raw_path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ContractIdentityError(f"contract path escapes root: {raw_path}")
        path = root.joinpath(*relative.parts)
        if not path.is_file():
            raise ContractIdentityError(f"contract file is missing: {raw_path}")
        entries.append(
            {"path": relative.as_posix(), "sha256": hashlib.sha256(_canonical_bytes(path)).hexdigest()}
        )
    return {"schema_version": "mozaiks.contract_manifest.v1", "files": entries}


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    """Return the content digest for a validated contract manifest."""
    return f"sha256:{hashlib.sha256(_canonical_json(dict(manifest))).hexdigest()}"


def build_contract_identity(
    root: Path,
    paths: Sequence[str],
    *,
    source_repo: str,
    source_commit: str,
) -> dict[str, Any]:
    """Build metadata suitable for generated output provenance."""
    if not source_repo or not source_commit:
        raise ContractIdentityError("source_repo and source_commit are required")
    manifest = build_contract_manifest(root, paths)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_repo": source_repo,
        "source_commit": source_commit,
        "contract_manifest": manifest,
        "contract_sha256": manifest_digest(manifest),
    }


def verify_contract_identity(
    metadata: Mapping[str, Any],
    root: Path,
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    """Recompute and verify identity metadata, returning the current identity."""
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise ContractIdentityError("unsupported contract identity schema")
    manifest = metadata.get("contract_manifest")
    if not isinstance(manifest, Mapping) or not isinstance(manifest.get("files"), list):
        raise ContractIdentityError("contract_manifest.files must be a list")
    paths: list[str] = []
    for entry in manifest["files"]:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            raise ContractIdentityError("contract manifest contains an invalid file entry")
        paths.append(entry["path"])
    if len(paths) != len(manifest["files"]):
        raise ContractIdentityError("contract manifest contains an invalid file entry")
    current = build_contract_manifest(root, paths)
    current_digest = manifest_digest(current)
    if current != dict(manifest) or current_digest != metadata.get("contract_sha256"):
        raise ContractIdentityError("contract files do not match recorded identity")
    recorded_commit = metadata.get("source_commit")
    if expected_source_commit is not None and recorded_commit != expected_source_commit:
        raise ContractIdentityError(
            f"source commit mismatch: recorded {recorded_commit!r}, expected {expected_source_commit!r}"
        )
    return dict(metadata)
