"""Immutable pack sources for installing Community Components into existing apps.

A pack can be installed from a local directory or from an exact commit of a
local git repository. A git source is exported blob by blob, exactly as the
repository stores it, so its digest is the same on every OS and checkout.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_FULL_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class PackSourceError(ValueError):
    """Raised when a pack source cannot be acquired exactly as requested."""


@dataclass(frozen=True)
class PackSource:
    """A local pack directory, or a pack folder at an exact commit of a local git repository."""

    directory: Path | None = None
    git_repository: Path | None = None
    git_revision: str | None = None
    git_path: str | None = None

    def __post_init__(self) -> None:
        git_fields = (self.git_repository, self.git_revision, self.git_path)
        if self.directory is not None and any(field is not None for field in git_fields):
            raise PackSourceError("A pack source is either a directory or a git revision, not both")
        if self.directory is None and not all(field is not None for field in git_fields):
            raise PackSourceError("A git pack source needs a repository, a full commit and a pack path")
        if self.git_revision is not None and not _FULL_SHA_RE.fullmatch(self.git_revision):
            raise PackSourceError(
                f"Git revision must be a full commit id, not {self.git_revision!r}: "
                "a branch or tag can move, so it cannot name an immutable pack version"
            )
        if self.git_path is not None:
            parts = self.git_path.replace("\\", "/").strip("/").split("/")
            if not self.git_path.strip() or any(part in {"", ".", ".."} for part in parts):
                raise PackSourceError(f"Pack path must be a relative folder inside the repository: {self.git_path!r}")

    @property
    def is_git(self) -> bool:
        return self.git_repository is not None


def sanitized_remote(url: str) -> str:
    """A remote URL with any user name, password or token removed, so a locator never persists credentials."""
    text = url.strip()
    if "://" not in text:
        # scp-like "git@host:org/repo.git": the user is a transport login, not a secret.
        return text
    parts = urlsplit(text)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _git(repository: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise PackSourceError("Installing from a git revision requires git on PATH")
    completed = subprocess.run(
        [git, "-C", str(repository), *args],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PackSourceError(f"git {' '.join(args[:2])} failed in {repository}: {detail}")
    return completed.stdout


def git_locator(source: PackSource) -> str:
    """The portable, credential-free locator recorded for a git source."""
    assert source.git_repository is not None and source.git_revision and source.git_path
    try:
        remote = _git(source.git_repository, "remote", "get-url", "origin").decode("utf-8").strip()
    except PackSourceError:
        remote = ""
    origin = sanitized_remote(remote) if remote else "local"
    path = source.git_path.replace("\\", "/").strip("/")
    return f"git+{origin}@{source.git_revision}#{path}"


def export_git_pack(source: PackSource, destination: Path) -> None:
    """Write the pack folder's blobs at the exact commit into ``destination``.

    Links and submodules are refused; every file is written with the bytes the
    repository stores, without line-ending conversion.
    """
    assert source.git_repository is not None and source.git_revision and source.git_path
    repository = source.git_repository.resolve()
    revision = source.git_revision
    _git(repository, "cat-file", "-e", f"{revision}^{{commit}}")
    prefix = source.git_path.replace("\\", "/").strip("/")
    listing = _git(repository, "ls-tree", "-r", "-z", "--full-tree", revision, "--", prefix)
    entries: list[tuple[str, str]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _tab, raw_path = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if not path.startswith(prefix + "/"):
            raise PackSourceError(f"Pack path {prefix!r} at {revision} must be a folder, not a file")
        if kind != "blob" or mode == "120000":
            raise PackSourceError(f"Pack {prefix!r} at {revision} contains a link or submodule: {path}")
        entries.append((path, oid))
    if not entries:
        raise PackSourceError(f"Pack path {prefix!r} has no files at commit {revision}")
    batch = _git(repository, "cat-file", "--batch", input_bytes="".join(f"{oid}\n" for _path, oid in entries).encode())
    cursor = 0
    for path, oid in entries:
        header_end = batch.index(b"\n", cursor)
        header = batch[cursor:header_end].decode("ascii").split()
        if len(header) != 3 or header[0] != oid or header[1] != "blob":
            raise PackSourceError(f"git returned an unexpected object for {path}")
        size = int(header[2])
        start = header_end + 1
        content = batch[start : start + size]
        cursor = start + size + 1
        relative = path[len(prefix) :].lstrip("/")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


@contextmanager
def materialized_source(source: PackSource) -> Iterator[Path]:
    """Yield a directory holding the pack exactly as the source names it."""
    if not source.is_git:
        assert source.directory is not None
        directory = source.directory.resolve()
        if not directory.is_dir():
            raise PackSourceError(f"Pack directory not found: {directory}")
        yield directory
        return
    with tempfile.TemporaryDirectory(prefix="mozaiks-pack-") as temporary:
        destination = Path(temporary) / "pack"
        destination.mkdir()
        export_git_pack(source, destination)
        yield destination


__all__ = [
    "PackSource",
    "PackSourceError",
    "export_git_pack",
    "git_locator",
    "materialized_source",
    "sanitized_remote",
]
