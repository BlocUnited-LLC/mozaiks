"""Bounded, root-owned candidate staging inside the trusted preview image."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path
from typing import Any

from mozaiksai.core.sandbox.sealed_limits import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_MANIFEST_BYTES,
    MAX_TAR_BYTES,
    MAX_TOTAL_BYTES,
)
from mozaiksai.core.semantics.portable_path import detect_collisions, validate_portable_path

STAGE_ROOT = Path("/root/.mozaiks-sealed-stage")
ARCHIVE_PATH = STAGE_ROOT / "candidate.tar"
MANIFEST_PATH = STAGE_ROOT / "manifest.json"
WORKSPACE = Path("/workspace")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _candidate_path(path: str) -> str:
    portable = validate_portable_path(path)
    if portable.text != path or (path != "requirements.txt" and not path.startswith(("app/", "workflows/"))):
        raise ValueError("Invalid sealed candidate path")
    return path


def build_stage_upload(files: dict[str, bytes]) -> tuple[bytes, bytes]:
    """Produce only regular tar members and a digest manifest for the guest verifier."""
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES or "app/app.json" not in files:
        raise ValueError("Invalid sealed candidate file set")
    paths = sorted(_candidate_path(path) for path in files)
    detect_collisions(paths)
    if any(not isinstance(files[path], bytes) or len(files[path]) > MAX_FILE_BYTES for path in paths):
        raise ValueError("Invalid sealed candidate file content")
    if sum(len(files[path]) for path in paths) > MAX_TOTAL_BYTES:
        raise ValueError("Sealed candidate file set exceeds its byte limit")
    entries = [
        {"path": path, "size": len(files[path]), "sha256": hashlib.sha256(files[path]).hexdigest()}
        for path in paths
    ]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in paths:
            member = tarfile.TarInfo(path)
            member.size = len(files[path])
            member.mode = 0o444
            member.uid = 0
            member.gid = 0
            archive.addfile(member, io.BytesIO(files[path]))
    payload = buffer.getvalue()
    if len(payload) > MAX_TAR_BYTES:
        raise ValueError("Sealed candidate stage upload exceeds its byte limit")
    manifest = json.dumps({"version": 1, "files": entries}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise ValueError("Sealed candidate stage manifest exceeds its byte limit")
    return payload, manifest


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate sealed stage manifest key")
    return result


def _read_manifest(payload: bytes) -> dict[str, tuple[int, str]]:
    parsed = json.loads(payload, object_pairs_hook=_unique_object)
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"version", "files"}
        or type(parsed["version"]) is not int
        or parsed["version"] != 1
    ):
        raise ValueError("Invalid sealed stage manifest")
    entries = parsed["files"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
        raise ValueError("Invalid sealed stage manifest file count")
    result: dict[str, tuple[int, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise ValueError("Invalid sealed stage manifest entry")
        path = _candidate_path(entry["path"])
        size, digest = entry["size"], entry["sha256"]
        if path in result or type(size) is not int or not 0 <= size <= MAX_FILE_BYTES or (
            not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
        ):
            raise ValueError("Invalid sealed stage manifest entry")
        result[path] = size, digest
    detect_collisions(result)
    if "app/app.json" not in result or sum(size for size, _ in result.values()) > MAX_TOTAL_BYTES:
        raise ValueError("Invalid sealed stage manifest file set")
    return result


def verify_stage_upload(archive_path: Path, manifest_path: Path) -> dict[str, tuple[int, str]]:
    """Check the full upload before extracting any member into /workspace."""
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Sealed stage manifest exceeds its byte limit")
    manifest = _read_manifest(manifest_path.read_bytes())
    if archive_path.stat().st_size > MAX_TAR_BYTES:
        raise ValueError("Sealed stage archive exceeds its byte limit")
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive:
            if not member.isfile() or len(seen) >= MAX_FILES:
                raise ValueError("Sealed stage tar contains an unsupported member")
            path = _candidate_path(member.name)
            if path in seen or path not in manifest or member.size != manifest[path][0]:
                raise ValueError("Sealed stage tar does not match its manifest")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Sealed stage tar member cannot be read")
            digest = hashlib.sha256()
            with source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != manifest[path][1]:
                raise ValueError("Sealed stage tar digest does not match")
            seen.add(path)
    detect_collisions(seen)
    if seen != set(manifest):
        raise ValueError("Sealed stage tar is missing manifest members")
    return manifest


def _require_root_owned(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode)) or (
        info.st_uid != 0 or info.st_mode & 0o022
    ):
        raise RuntimeError("Preview template source or stage path is not root-owned and read-only")


def _check_template_and_workspace() -> None:
    for path, directory in (
        (Path("/opt/mozaiks"), True),
        (Path("/opt/mozaiks/web_shell"), True),
        (Path("/opt/mozaiks/web_shell/vite.config.js"), False),
        (Path("/opt/mozaiks/web_shell/node_modules"), True),
    ):
        _require_root_owned(path, directory=directory)
    tailwind_link = Path("/opt/mozaiks/web_shell/.mozaiks-tailwind-sources")
    if not tailwind_link.is_symlink() or os.readlink(tailwind_link) != "/tmp/mozaiks-preview/tailwind-sources":
        raise RuntimeError("Preview template Tailwind source path is not sealed")
    workspace_info = WORKSPACE.lstat()
    if not stat.S_ISDIR(workspace_info.st_mode):
        raise RuntimeError("Preview workspace is not a directory")
    if {entry.name for entry in WORKSPACE.iterdir()} != {"logs"}:
        raise RuntimeError("Preview workspace contains preexisting candidate paths")
    logs_info = (WORKSPACE / "logs").lstat()
    if (
        not stat.S_ISDIR(logs_info.st_mode)
        or logs_info.st_uid != 10001
        or stat.S_IMODE(logs_info.st_mode) != 0o700
        or any((WORKSPACE / "logs").iterdir())
    ):
        raise RuntimeError("Preview workspace logs path is not an empty directory")


def _extract_verified_archive(archive_path: Path) -> None:
    root_fd = os.open(WORKSPACE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created_dirs = {"app", "workflows"}
    try:
        os.fchown(root_fd, 0, 0)
        os.fchmod(root_fd, 0o755)
        for name in ("app", "workflows"):
            os.mkdir(name, 0o755, dir_fd=root_fd)
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive:
                parts = _candidate_path(member.name).split("/")
                parent_fd = os.dup(root_fd)
                try:
                    prefix = ""
                    for segment in parts[:-1]:
                        prefix = f"{prefix}/{segment}" if prefix else segment
                        if prefix not in created_dirs:
                            os.mkdir(segment, 0o755, dir_fd=parent_fd)
                            created_dirs.add(prefix)
                        next_fd = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                        os.close(parent_fd)
                        parent_fd = next_fd
                    file_fd = os.open(
                        parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o444, dir_fd=parent_fd,
                    )
                    try:
                        source = archive.extractfile(member)
                        if source is None:
                            raise RuntimeError("Sealed stage tar member cannot be read")
                        with source, os.fdopen(file_fd, "wb", closefd=False) as target:
                            while chunk := source.read(1024 * 1024):
                                target.write(chunk)
                        os.fchown(file_fd, 0, 0)
                        os.fchmod(file_fd, 0o444)
                    finally:
                        os.close(file_fd)
                finally:
                    os.close(parent_fd)
        for relative in sorted(created_dirs, key=lambda path: path.count("/"), reverse=True):
            directory_fd = os.open(WORKSPACE / relative, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fchown(directory_fd, 0, 0)
                os.fchmod(directory_fd, 0o555)
            finally:
                os.close(directory_fd)
        os.fchmod(root_fd, 0o555)
    finally:
        os.close(root_fd)


def _verify_workspace(manifest: dict[str, tuple[int, str]]) -> None:
    _require_root_owned(WORKSPACE, directory=True)
    if stat.S_IMODE(WORKSPACE.lstat().st_mode) != 0o555:
        raise RuntimeError("Sealed preview workspace remains writable")
    found: set[str] = set()
    expected_dirs = {"app", "workflows", "logs"}
    for path in manifest:
        parts = path.split("/")
        expected_dirs.update("/".join(parts[:index]) for index in range(1, len(parts)))
    found_dirs: set[str] = set()
    for root, directories, filenames in os.walk(WORKSPACE, followlinks=False):
        current = Path(root)
        for directory in directories:
            relative = (current / directory).relative_to(WORKSPACE).as_posix()
            found_dirs.add(relative)
            if relative == "logs":
                if not stat.S_ISDIR((current / directory).lstat().st_mode):
                    raise RuntimeError("Sealed preview logs path changed type")
                continue
            info = (current / directory).lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o555:
                raise RuntimeError("Sealed preview source directory is mutable")
        for filename in filenames:
            file_path = current / filename
            relative = file_path.relative_to(WORKSPACE).as_posix()
            if relative not in manifest:
                raise RuntimeError("Sealed preview workspace contains an unexpected file")
            info = file_path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o444:
                raise RuntimeError("Sealed preview source file is mutable")
            if info.st_size != manifest[relative][0] or hashlib.sha256(file_path.read_bytes()).hexdigest() != manifest[relative][1]:
                raise RuntimeError("Sealed preview source digest changed")
            found.add(relative)
    if found != set(manifest) or found_dirs != expected_dirs:
        raise RuntimeError("Sealed preview workspace does not match its source manifest")


def _clean_uploads() -> None:
    ARCHIVE_PATH.unlink(missing_ok=True)
    MANIFEST_PATH.unlink(missing_ok=True)
    STAGE_ROOT.rmdir()


def cleanup_uploaded_candidate() -> None:
    """Discard a partial upload after SDK failure, without following a link."""
    if not STAGE_ROOT.exists() and not STAGE_ROOT.is_symlink():
        return
    _require_root_owned(STAGE_ROOT, directory=True)
    if stat.S_IMODE(STAGE_ROOT.lstat().st_mode) != 0o700:
        raise RuntimeError("Sealed stage upload directory is not private")
    _clean_uploads()


def stage_uploaded_candidate() -> None:
    """Called only as root in a newly allocated, private E2B template."""
    try:
        _require_root_owned(STAGE_ROOT, directory=True)
        if stat.S_IMODE(STAGE_ROOT.lstat().st_mode) != 0o700:
            raise RuntimeError("Sealed stage upload directory is not private")
        for path in (ARCHIVE_PATH, MANIFEST_PATH):
            _require_root_owned(path, directory=False)
        if {entry.name for entry in STAGE_ROOT.iterdir()} != {ARCHIVE_PATH.name, MANIFEST_PATH.name}:
            raise RuntimeError("Sealed stage upload directory contains unexpected entries")
        manifest = verify_stage_upload(ARCHIVE_PATH, MANIFEST_PATH)
        _check_template_and_workspace()
        _extract_verified_archive(ARCHIVE_PATH)
        _verify_workspace(manifest)
    finally:
        cleanup_uploaded_candidate()


def main() -> int:
    try:
        if len(sys.argv) == 1:
            stage_uploaded_candidate()
        elif sys.argv[1:] == ["--cleanup"]:
            cleanup_uploaded_candidate()
        else:
            raise ValueError("Invalid sealed staging action")
    except Exception:
        print("Sealed candidate staging failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
