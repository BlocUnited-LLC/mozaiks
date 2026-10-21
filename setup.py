"""Setuptools package discovery for the flat-layout Mozaiks repository."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from setuptools import find_namespace_packages, setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

_REVISION_PATH = Path("mozaiksai/_build_revision.json")
_COMMIT = re.compile(r"[0-9a-f]{40}")


def _source_commit(root: Path) -> str | None:
    if (root / ".git").exists():
        try:
            commit = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True, timeout=20,
            ).stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                check=True, capture_output=True, text=True, timeout=20,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Cannot verify the exact Mozaiks package source revision") from exc
        if dirty:
            raise RuntimeError("Commit tracked package changes before building a Mozaiks distribution")
    elif (root / _REVISION_PATH).exists():
        try:
            revision = json.loads((root / _REVISION_PATH).read_text(encoding="utf-8"))
            if not isinstance(revision, dict) or revision.get("schema_version") != "mozaiks.source_revision.v1":
                raise ValueError
            commit = revision["commit"]
        except (OSError, ValueError, KeyError) as exc:
            raise RuntimeError("Source distribution has no verified Mozaiks revision") from exc
    else:
        # A Docker context may intentionally omit Git metadata. Its generic
        # host wheel can run, but Android export will refuse missing provenance.
        return None
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise RuntimeError("Mozaiks package source revision must be an exact Git commit")
    return commit


def _revision_bytes(root: Path) -> bytes | None:
    commit = _source_commit(root)
    if commit is None:
        return None
    return (json.dumps({
        "schema_version": "mozaiks.source_revision.v1", "commit": commit,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _verify_packaged_git_sources(root: Path, staged_root: Path, *, wheel: bool) -> None:
    if not (root / ".git").exists():
        return
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--full-name", "-z"],
            check=True, capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Cannot verify the exact Mozaiks package source files") from exc
    tracked = set(result.stdout.decode("utf-8", "surrogateescape").split("\0"))
    for staged in staged_root.rglob("*"):
        if not staged.is_file():
            continue
        name = staged.relative_to(staged_root).as_posix()
        if name == _REVISION_PATH.as_posix():
            continue  # This file is generated from the verified commit below.
        if not wheel and name == "PKG-INFO" and not (root / name).exists():
            continue  # Setuptools creates this when no source PKG-INFO exists.
        if not wheel and name.startswith("mozaiks.egg-info/"):
            continue  # Setuptools writes distribution metadata into the sdist tree.
        if not wheel and name == "setup.cfg" and not (root / "setup.cfg").exists():
            continue  # Setuptools creates this in the release tree when no source config exists.
        source_name = name.replace("mozaiks_chat_ui/", "chat-ui/", 1) if wheel else name
        source = root / source_name
        if source_name not in tracked or source.is_symlink() or not source.is_file():
            raise RuntimeError(f"Package input is not part of the exact Git commit: {source_name}")
        if staged.read_bytes() != source.read_bytes():
            raise RuntimeError(f"Packaged source differs from the exact Git checkout: {source_name}")


class BuildPyWithRevision(build_py):
    def run(self) -> None:
        super().run()
        target = Path(self.build_lib) / _REVISION_PATH
        root = Path(__file__).resolve().parent
        revision = _revision_bytes(root)
        if revision is None:
            target.unlink(missing_ok=True)
            return
        _verify_packaged_git_sources(root, Path(self.build_lib), wheel=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(revision)


class SdistWithRevision(sdist):
    def make_release_tree(self, base_dir: str, files: list[str]) -> None:
        super().make_release_tree(base_dir, files)
        root = Path(__file__).resolve().parent
        revision = _revision_bytes(root)
        if revision is None:
            raise RuntimeError("Source distribution requires an exact Mozaiks source revision")
        _verify_packaged_git_sources(root, Path(base_dir), wheel=False)
        target = Path(base_dir) / _REVISION_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(revision)

PACKAGE_INCLUDES = [
    "factory_app",
    "factory_app.*",
    "logs",
    "logs.*",
    "mozaiks",
    "mozaiks.*",
    "mozaiks_cli",
    "mozaiks_cli.*",
    "mozaiksai",
    "mozaiksai.*",
    "web_shell",
    "web_shell.*",
]

PACKAGE_EXCLUDES = [
    "tests",
    "tests.*",
    "web_shell.node_modules",
    "web_shell.node_modules.*",
    "web_shell.dist",
    "web_shell.dist.*",
    "web_shell.playwright",
    "web_shell.playwright.*",
]


packages = find_namespace_packages(include=PACKAGE_INCLUDES, exclude=PACKAGE_EXCLUDES)
packages.append("mozaiks_chat_ui")
packages.extend(
    f"mozaiks_chat_ui.{package_name}"
    for package_name in find_namespace_packages(
        where="chat-ui",
        exclude=[
            "dist",
            "dist.*",
            "node_modules",
            "node_modules.*",
        ],
    )
)
packages = sorted(set(packages))

setup(
    packages=packages,
    package_dir={"mozaiks_chat_ui": "chat-ui"},
    package_data={"mozaiksai": ["_build_revision.json"]},
    cmdclass={"build_py": BuildPyWithRevision, "sdist": SdistWithRevision},
)
