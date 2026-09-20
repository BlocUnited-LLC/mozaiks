"""Exact canonical bundle-entry identity for app-bundle manifests.

The bundle digest authority is the digest of the uniquely identified
canonical bundle archive entry — never "any manifest entry with this
digest". These tests prove the closed identity rule: exactly one
``application/zip`` entry, a valid digest, and the exact
``{bundle_name}/{archive}`` path persisted with the record.
"""
from __future__ import annotations

import hashlib

import pytest

from mozaiksai.core.artifacts.models import (
    ArtifactCommitMetadata,
    BuildRecord,
    BuildRecordFileEntry,
    CanonicalBundleEntryError,
    resolve_canonical_bundle_entry,
)

_ZIP_DIGEST = hashlib.sha256(b"bundle zip bytes").hexdigest()
_JSON_DIGEST = hashlib.sha256(b'{"app_id": "field_service"}').hexdigest()


def _record(
    *,
    manifest: list[BuildRecordFileEntry],
    bundle_name: str | None = "GeneratedApp",
    build_family: str = "app_bundle",
    build_key: str = "app_bundle",
) -> BuildRecord:
    metadata: dict = {"artifact_path": "/tmp/GeneratedApp.zip"}
    if bundle_name is not None:
        metadata["bundle_name"] = bundle_name
    return BuildRecord(
        _id="av_app_bundle_1",
        app_id="field_service",
        build_family=build_family,
        build_key=build_key,
        version_number=1,
        lineage_root_id="av_app_bundle_1",
        files_manifest=manifest,
        commit_metadata=ArtifactCommitMetadata(metadata=metadata),
    )


def _zip_entry(
    *,
    path: str = "GeneratedApp/GeneratedApp.zip",
    sha256: str | None = _ZIP_DIGEST,
    content_type: str = "application/zip",
) -> BuildRecordFileEntry:
    return BuildRecordFileEntry(path=path, sha256=sha256, content_type=content_type)


def _app_json_entry(sha256: str = _JSON_DIGEST) -> BuildRecordFileEntry:
    return BuildRecordFileEntry(
        path="GeneratedApp/app/app.json", sha256=sha256, content_type="application/json"
    )


def test_valid_manifest_resolves_exactly_the_bundle_archive() -> None:
    record = _record(manifest=[_zip_entry(), _app_json_entry()])
    entry = resolve_canonical_bundle_entry(record)
    assert entry.path == "GeneratedApp/GeneratedApp.zip"
    assert entry.sha256 == _ZIP_DIGEST
    # Identity is the entry, never digest equality: another file's digest is
    # not bundle authority even if a caller presents it.
    assert entry.sha256 != _JSON_DIGEST


def test_zero_bundle_entries_fail_closed() -> None:
    record = _record(manifest=[_app_json_entry()])
    with pytest.raises(CanonicalBundleEntryError, match="no canonical bundle entry"):
        resolve_canonical_bundle_entry(record)
    with pytest.raises(CanonicalBundleEntryError):
        resolve_canonical_bundle_entry(_record(manifest=[]))


def test_multiple_bundle_entries_fail_closed() -> None:
    record = _record(
        manifest=[
            _zip_entry(),
            _zip_entry(path="GeneratedApp/second-archive.zip", sha256="b" * 64),
        ]
    )
    with pytest.raises(CanonicalBundleEntryError, match="exactly one is required"):
        resolve_canonical_bundle_entry(record)


def test_missing_or_malformed_digest_fails_closed() -> None:
    for bad in (None, "", "not-a-sha", "B" * 64):
        record = _record(manifest=[_zip_entry(sha256=bad)])
        with pytest.raises(CanonicalBundleEntryError, match="sha256"):
            resolve_canonical_bundle_entry(record)


def test_record_identity_requires_canonical_family_and_key() -> None:
    """A record is never accepted as the app bundle merely because its
    manifest looks bundle-shaped: build_family/build_key must both be exactly
    "app_bundle"."""
    for family, key in (
        ("workflow_bundle", "app_bundle"),
        ("app_bundle", "workflow_bundle"),
        ("app_context_version", "app_context_version"),
    ):
        record = _record(
            manifest=[_zip_entry()], build_family=family, build_key=key
        )
        with pytest.raises(
            CanonicalBundleEntryError, match="not the canonical app_bundle record"
        ):
            resolve_canonical_bundle_entry(record)

    # Correct family/key resolves (positive control).
    entry = resolve_canonical_bundle_entry(_record(manifest=[_zip_entry()]))
    assert entry.path == "GeneratedApp/GeneratedApp.zip"


def test_archive_path_requires_exact_canonical_equality() -> None:
    """Only {bundle_name}/{bundle_name}.zip is the archive identity — never
    two-segments, non-empty basename, .zip suffix, or application/zip alone.
    The exact digest under a wrong path is still rejected."""
    for bad_path in (
        "GeneratedApp/other.zip",
        "GeneratedApp/evil.zip",
        "GeneratedApp/not-a-zip",
        "GeneratedApp/.",
        "GeneratedApp/..",
        "GeneratedApp/subdir/file.zip",
        "other/GeneratedApp.zip",
        "GeneratedApp/app/assets/copy.zip",
        "OtherBundle/GeneratedApp.zip",
        "GeneratedApp.zip",
        "GeneratedApp//",
        "GeneratedApp/GeneratedApp.ZIP",
        "GeneratedApp/GeneratedApp.zip/",
    ):
        record = _record(manifest=[_zip_entry(path=bad_path)])
        with pytest.raises(
            CanonicalBundleEntryError, match="exact canonical archive path"
        ):
            resolve_canonical_bundle_entry(record)


def test_same_digest_under_noncanonical_path_is_not_bundle_authority() -> None:
    """A copy of the archive bytes parked under a noncanonical path (not
    typed as the bundle archive) never resolves — identity is the entry,
    never digest membership."""
    record = _record(
        manifest=[
            _app_json_entry(),
            BuildRecordFileEntry(
                path="GeneratedApp/app/assets/copy.zip",
                sha256=_ZIP_DIGEST,
                content_type="application/octet-stream",
            ),
        ]
    )
    with pytest.raises(CanonicalBundleEntryError, match="no canonical bundle entry"):
        resolve_canonical_bundle_entry(record)


def test_bundle_name_grammar_fails_closed() -> None:
    """The persisted bundle name must satisfy the closed shared grammar;
    invalid names are rejected, never normalized into valid ones."""
    from mozaiksai.core.artifacts.models import validate_canonical_bundle_name

    for bad_name in (
        ".",
        "..",
        "a/b",
        "a\\b",
        "../escape",
        "/absolute",
        "C:\\drive",
        "C:drive",
        "name\x00null",
        "name\nnewline",
        "with space",
        "dotted.name",
        "unicode\u00e9",
    ):
        with pytest.raises(CanonicalBundleEntryError, match="grammar"):
            validate_canonical_bundle_name(bad_name)
        record = _record(
            manifest=[_zip_entry(path=f"{bad_name}/{bad_name}.zip")],
            bundle_name=bad_name,
        )
        with pytest.raises(CanonicalBundleEntryError):
            resolve_canonical_bundle_entry(record)

    assert validate_canonical_bundle_name("Generated-App_2") == "Generated-App_2"


def test_record_without_bundle_name_identity_fails_closed() -> None:
    record = _record(manifest=[_zip_entry()], bundle_name=None)
    with pytest.raises(CanonicalBundleEntryError, match="bundle_name"):
        resolve_canonical_bundle_entry(record)
    record = _record(manifest=[_zip_entry()], bundle_name="")
    with pytest.raises(CanonicalBundleEntryError, match="bundle_name"):
        resolve_canonical_bundle_entry(record)


def test_shared_archive_path_formula_is_the_resolver_requirement() -> None:
    """One shared helper defines the archive identity for writer and
    resolver: the formula and the resolver requirement cannot drift."""
    from mozaiksai.core.artifacts.models import canonical_bundle_archive_path

    assert canonical_bundle_archive_path("GeneratedApp") == "GeneratedApp/GeneratedApp.zip"
    entry = resolve_canonical_bundle_entry(
        _record(manifest=[_zip_entry(path=canonical_bundle_archive_path("GeneratedApp"))])
    )
    assert entry.sha256 == _ZIP_DIGEST
    with pytest.raises(CanonicalBundleEntryError, match="grammar"):
        canonical_bundle_archive_path("../escape")
