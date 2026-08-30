"""ADR 0007 Slice 4A deterministic archive transport envelope.

ZIP here is a deterministic transport envelope over registered files — never a
semantic artifact family, a graph input, or an independently planned compiler
output. Envelope bytes and manifests may be digested as transport and
persistence evidence, but archive identity cannot author semantics.

Determinism contract: entry names use ``mozaiks.portable_path.v1``; entries
are ordered by the UTF-8 bytes of their portable text; timestamps are pinned
to the DOS epoch; platform metadata, permissions, and compression settings are
fixed; collision checks and link rejection fail closed. The same entry set
always produces byte-identical archive output on every host.

No filesystem access, no semantic authority, no AG2 imports.
"""

from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from mozaiksai.core.semantics.portable_path import (
    PortablePathError,
    detect_collisions,
    validate_portable_path,
)
from mozaiksai.core.semantics.refs import SemanticsModel

ARCHIVE_SCHEMA_VERSION: Literal["mozaiks.deterministic_archive.v1"] = (
    "mozaiks.deterministic_archive.v1"
)

# Fixed envelope constants. Changing any of these is a schema-version change.
_DOS_EPOCH = (1980, 1, 1, 0, 0, 0)
_CREATE_SYSTEM_UNIX = 3
_FILE_MODE = 0o644
_EXTERNAL_ATTR = (stat.S_IFREG | _FILE_MODE) << 16
# STORED, not DEFLATED: deflate output may vary across zlib builds, and the
# envelope's byte-identity guarantee must not depend on compressor internals.
_COMPRESSION = zipfile.ZIP_STORED
_UTF8_NAME_FLAG = 0x800


class ArchiveError(ValueError):
    """The archive violates the deterministic transport envelope contract."""


class ArchiveEntry(SemanticsModel):
    """One registered file carried by the envelope."""

    path: str = Field(min_length=1)
    content: bytes


class ArchiveManifestEntry(SemanticsModel):
    path: str
    size_bytes: int
    content_sha256: str


class ArchiveManifest(SemanticsModel):
    """Transport evidence for one deterministic archive — never semantics."""

    schema_version: Literal["mozaiks.deterministic_archive.v1"] = ARCHIVE_SCHEMA_VERSION
    entries: tuple[ArchiveManifestEntry, ...]
    archive_sha256: str


def build_deterministic_archive(entries: Iterable[ArchiveEntry]) -> bytes:
    """Serialize ``entries`` into byte-identical deterministic ZIP output.

    Entry paths are validated against the portable profile and collision
    checks before any byte is written; entry order is the UTF-8 byte order of
    the portable text, independent of input order.
    """
    materialized = list(entries)
    if not materialized:
        raise ArchiveError("deterministic archive requires at least one entry")

    validated = [(validate_portable_path(entry.path), entry.content) for entry in materialized]
    detect_collisions([portable.text for portable, _content in validated])
    ordered = sorted(validated, key=lambda item: item[0].text.encode("utf-8"))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=_COMPRESSION) as archive:
        for portable, content in ordered:
            info = zipfile.ZipInfo(filename=portable.text, date_time=_DOS_EPOCH)
            info.create_system = _CREATE_SYSTEM_UNIX
            info.external_attr = _EXTERNAL_ATTR
            info.compress_type = _COMPRESSION
            archive.writestr(info, content)
    return buffer.getvalue()


def archive_digest(data: bytes) -> str:
    """sha256 of the envelope bytes — transport/persistence evidence only."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _verify_entry_metadata(info: zipfile.ZipInfo) -> None:
    """Reject any entry metadata the canonical writer does not produce.

    Each field is checked independently so a violation names the exact
    non-canonical field rather than a generic mismatch.
    """
    if info.is_dir():
        raise ArchiveError(f"directory entry not permitted: {info.filename!r}")
    if info.compress_type != _COMPRESSION:
        raise ArchiveError(f"non-canonical compression method on entry: {info.filename!r}")
    if info.create_system != _CREATE_SYSTEM_UNIX:
        raise ArchiveError(f"non-canonical create_system on entry: {info.filename!r}")
    if info.external_attr != _EXTERNAL_ATTR:
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ArchiveError(f"link entry not permitted: {info.filename!r}")
        if not stat.S_ISREG(mode):
            raise ArchiveError(
                f"missing or non-regular-file type bits on entry: {info.filename!r}"
            )
        if mode & 0o111:
            raise ArchiveError(f"executable permission bits on entry: {info.filename!r}")
        raise ArchiveError(
            f"non-canonical permissions or attributes on entry: {info.filename!r}"
        )
    if info.extra != b"":
        raise ArchiveError(f"extra field not permitted on entry: {info.filename!r}")
    if info.comment != b"":
        raise ArchiveError(f"comment not permitted on entry: {info.filename!r}")
    if info.internal_attr != 0:
        raise ArchiveError(f"non-canonical internal attributes on entry: {info.filename!r}")
    expected_flags = _UTF8_NAME_FLAG if any(ord(char) > 0x7F for char in info.filename) else 0
    if info.flag_bits != expected_flags:
        raise ArchiveError(f"non-canonical name-encoding flags on entry: {info.filename!r}")
    if info.date_time != _DOS_EPOCH:
        raise ArchiveError(f"non-canonical timestamp on entry: {info.filename!r}")


def read_archive_manifest(data: bytes) -> ArchiveManifest:
    """Verify envelope conformance and return its transport manifest.

    Verification is fail-closed against the canonical writer, not merely
    against what :mod:`zipfile` can read: every identity-affecting entry field
    (compression, create_system, permissions and type bits, extra fields,
    comments, name-encoding flags, internal attributes, timestamps), the
    archive comment, entry names, ordering, and collisions are checked
    independently, and the envelope bytes must equal the canonical
    re-serialization of their own entries, so an archive the Slice 4A writer
    could not have produced never verifies. Archive identity remains transport
    evidence only — verification never grants semantic authority.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), mode="r")
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a readable archive: {exc}") from exc

    with archive:
        if archive.comment != b"":
            raise ArchiveError("archive comment not permitted")
        infos = archive.infolist()
        if not infos:
            raise ArchiveError("empty archive envelope")

        texts: list[str] = []
        entries: list[ArchiveEntry] = []
        manifest_entries: list[ArchiveManifestEntry] = []
        previous_key: bytes | None = None
        for info in infos:
            _verify_entry_metadata(info)
            try:
                portable = validate_portable_path(info.filename)
            except PortablePathError as exc:
                raise ArchiveError(f"non-portable entry name: {exc.reason}: {info.filename!r}") from exc
            encoded = portable.text.encode("utf-8")
            if previous_key is not None and encoded <= previous_key:
                raise ArchiveError(f"entries out of canonical order at: {portable.text!r}")
            previous_key = encoded
            try:
                content = archive.read(info.filename)
            except zipfile.BadZipFile as exc:
                raise ArchiveError(f"unreadable entry: {portable.text!r}: {exc}") from exc
            texts.append(portable.text)
            entries.append(ArchiveEntry(path=portable.text, content=content))
            manifest_entries.append(
                ArchiveManifestEntry(
                    path=portable.text,
                    size_bytes=len(content),
                    content_sha256=f"sha256:{hashlib.sha256(content).hexdigest()}",
                )
            )

        detect_collisions(texts)

    # Closure check: the bytes must be exactly what the canonical writer
    # produces for this entry set, so no field outside the independent checks
    # (local headers, prepended data, zip64 records) can smuggle variance.
    if build_deterministic_archive(entries) != data:
        raise ArchiveError("archive bytes are not the canonical serialization of their entries")

    return ArchiveManifest(
        entries=tuple(manifest_entries),
        archive_sha256=archive_digest(data),
    )


__all__ = [
    "ARCHIVE_SCHEMA_VERSION",
    "ArchiveEntry",
    "ArchiveError",
    "ArchiveManifest",
    "ArchiveManifestEntry",
    "archive_digest",
    "build_deterministic_archive",
    "read_archive_manifest",
]
