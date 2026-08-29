"""ADR 0007 Slice 4A portable path profile (``mozaiks.portable_path.v1``).

One host-independent path profile for every compiler-owned output. POSIX
separators, NFC storage normalization, Windows-compatible restrictions applied
on every host, and a conservative Unicode case-folded collision key. Absolute,
drive-qualified, drive-relative, UNC/device, traversal, glob, reserved-name,
empty-segment, control-character, alternate-data-stream, trailing-dot/space,
normalization, case, and file/directory-prefix collisions fail closed.

This module is deterministic-substrate only: no filesystem access, no
semantic authority, no AG2 imports. Target-specific external handoffs may add
restrictions but cannot weaken this profile.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Literal

from mozaiksai.core.semantics.refs import SemanticsModel

PORTABLE_PATH_SCHEMA_VERSION: Literal["mozaiks.portable_path.v1"] = "mozaiks.portable_path.v1"

_MAX_PATH_CHARS = 4096
_MAX_SEGMENT_CHARS = 255
_GLOB_CHARS = frozenset("*?[")
# Windows-invalid filename characters, enforced on every host. ':' also
# rejects NTFS alternate-data-stream syntax (name:stream).
_WINDOWS_INVALID = frozenset('<>:"|?*')
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_RESERVED_DEVICE_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)",
    re.IGNORECASE,
)


class PortablePathError(ValueError):
    """A path violates the ``mozaiks.portable_path.v1`` profile."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"portable path rejected: {reason}: {path!r}")


class PortablePath(SemanticsModel):
    """A validated, NFC-normalized, POSIX-separated relative path."""

    schema_version: Literal["mozaiks.portable_path.v1"] = PORTABLE_PATH_SCHEMA_VERSION
    segments: tuple[str, ...]

    @property
    def text(self) -> str:
        return "/".join(self.segments)

    @property
    def collision_key(self) -> str:
        """Conservative Unicode case-folded duplicate-detection key."""
        return "/".join(segment.casefold() for segment in self.segments)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.text


def _reject(path: str, reason: str) -> PortablePathError:
    return PortablePathError(path, reason)


def _validate_segment(raw_path: str, segment: str) -> str:
    if not segment:
        raise _reject(raw_path, "empty path segment")
    if segment in (".", ".."):
        raise _reject(raw_path, "relative traversal segment")
    if len(segment) > _MAX_SEGMENT_CHARS:
        raise _reject(raw_path, "segment exceeds 255 characters")
    for char in segment:
        code_point = ord(char)
        if code_point < 0x20 or code_point == 0x7F:
            raise _reject(raw_path, "control character in segment")
        if char in _WINDOWS_INVALID:
            raise _reject(raw_path, f"character {char!r} is not portable")
        if char in _GLOB_CHARS:
            raise _reject(raw_path, "glob metacharacter in segment")
    if segment.endswith(".") or segment.endswith(" "):
        raise _reject(raw_path, "trailing dot or space in segment")
    if segment.startswith(" "):
        raise _reject(raw_path, "leading space in segment")
    if _RESERVED_DEVICE_RE.match(segment):
        raise _reject(raw_path, "reserved Windows device name")
    return segment


def validate_portable_path(raw: str) -> PortablePath:
    """Validate ``raw`` against the profile and return its normalized form.

    Backslashes are rejected rather than translated: a compiler-owned producer
    must emit POSIX separators, and silently rewriting Windows separators
    would let two spellings of one path coexist upstream of normalization.
    """
    if not isinstance(raw, str):
        raise _reject(str(raw), "path must be a string")
    if not raw:
        raise _reject(raw, "empty path")
    if len(raw) > _MAX_PATH_CHARS:
        raise _reject(raw, "path exceeds 4096 characters")
    if "\x00" in raw:
        raise _reject(raw, "null byte")
    if "\\" in raw:
        raise _reject(raw, "backslash separator")
    if raw.startswith("/"):
        raise _reject(raw, "absolute path")
    if raw.startswith("~"):
        raise _reject(raw, "home-relative path")
    if _DRIVE_RE.match(raw):
        raise _reject(raw, "drive-qualified or drive-relative path")
    if raw.startswith("//"):
        raise _reject(raw, "UNC path")

    normalized = unicodedata.normalize("NFC", raw)
    segments = tuple(_validate_segment(raw, segment) for segment in normalized.split("/"))
    return PortablePath(segments=segments)


def collision_key(raw: str) -> str:
    """Case-folded key over the validated NFC form of ``raw``."""
    return validate_portable_path(raw).collision_key


def detect_collisions(paths: Iterable[str]) -> None:
    """Fail closed on duplicate and file/directory-prefix collisions.

    Two distinct spellings that case-fold to one key are a duplicate; a path
    whose folded key equals a folded directory prefix of another path is a
    file/directory collision. Both make an output set unrepresentable on at
    least one supported host, so both are rejected before any write.
    """
    seen: dict[str, str] = {}
    directory_keys: dict[str, str] = {}
    validated: list[PortablePath] = [validate_portable_path(path) for path in paths]

    for portable in validated:
        key = portable.collision_key
        text = portable.text
        if key in seen and seen[key] != text:
            raise _reject(text, f"case-fold duplicate of {seen[key]!r}")
        if key in seen and seen[key] == text:
            raise _reject(text, "duplicate path")
        seen[key] = text
        folded_segments = key.split("/")
        for index in range(1, len(folded_segments)):
            prefix = "/".join(folded_segments[:index])
            directory_keys.setdefault(prefix, text)

    for key, text in seen.items():
        if key in directory_keys:
            raise _reject(
                text,
                f"file/directory prefix collision with {directory_keys[key]!r}",
            )


__all__ = [
    "PORTABLE_PATH_SCHEMA_VERSION",
    "PortablePath",
    "PortablePathError",
    "collision_key",
    "detect_collisions",
    "validate_portable_path",
]
