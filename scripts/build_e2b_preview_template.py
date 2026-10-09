"""Build the canonical Mozaiks preview image as an E2B template.

The command is dry-run by default. E2B template builds are hosted-provider
operations and require an explicit --confirm-paid-build acknowledgement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCKERFILE = REPO_ROOT / "infra" / "docker" / "Dockerfile.preview"
DEFAULT_TEMPLATE_NAME = "mozaiks-preview"

# Keep hosted uploads stable and bounded. The live checkout contains mutable
# worktrees, caches, logs, and generated artifacts that must not be part of a
# preview image build context.
_PREVIEW_CONTEXT_FILES = ("pyproject.toml", "setup.py", "MANIFEST.in", "README.md", "LICENSE")
_PREVIEW_CONTEXT_DIRECTORIES = (
    "mozaiks",
    "mozaiksai",
    "mozaiks_cli",
    "logs",
    "factory_app",
    "web_shell",
    "chat-ui",
)


def _build_log(entry: Any) -> None:
    message = getattr(entry, "message", None) or getattr(entry, "text", None) or str(entry)
    # E2B build logs can contain Unicode symbols; keep Windows consoles from
    # turning a completed hosted build into a local helper failure.
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe_message = str(message).encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe_message, flush=True)


def _git_output(*args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            check=False, capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Git is required to build an E2B preview template") from exc
    if result.returncode:
        raise RuntimeError("Cannot read the committed E2B preview source from Git")
    return result.stdout


def _context_digest(context_root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(context_root.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(context_root).as_posix()
        digest.update(relative_path.encode("utf-8") + b"\0")
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as staged_file:
            for chunk in iter(lambda: staged_file.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _stage_preview_context(dockerfile: Path) -> tuple[tempfile.TemporaryDirectory[str], str, str]:
    root = Path(_git_output("rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != REPO_ROOT.resolve():
        raise RuntimeError("E2B preview source must be a Git checkout root")
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("E2B preview source must be a clean committed checkout")
    source_sha = _git_output("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    try:
        dockerfile_path = dockerfile.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("E2B preview Dockerfile must be tracked in this checkout") from exc
    selected_paths = (*_PREVIEW_CONTEXT_FILES, *_PREVIEW_CONTEXT_DIRECTORIES, dockerfile_path)
    context = tempfile.TemporaryDirectory(prefix="mozaiks-e2b-preview-")
    context_root = Path(context.name)
    try:
        with tempfile.TemporaryFile() as archive_file:
            result = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", source_sha, *selected_paths],
                check=False, stdout=archive_file, stderr=subprocess.PIPE,
            )
            if result.returncode:
                raise RuntimeError("Cannot archive the committed E2B preview source")
            archive_file.seek(0)
            with tarfile.open(fileobj=archive_file, mode="r:") as archive:
                for member in archive:
                    name = PurePosixPath(member.name)
                    if (
                        name.is_absolute() or ".." in name.parts or "\\" in member.name
                        or member.issym() or member.islnk()
                    ):
                        raise RuntimeError("E2B preview source contains an unsafe path or link")
                    relative_path = name.as_posix()
                    if relative_path == dockerfile_path:
                        destination = context_root / "Dockerfile.preview"
                    elif relative_path in _PREVIEW_CONTEXT_FILES or any(
                        relative_path == directory or relative_path.startswith(f"{directory}/")
                        for directory in _PREVIEW_CONTEXT_DIRECTORIES
                    ):
                        destination = context_root.joinpath(*name.parts)
                    elif member.isdir() and dockerfile_path.startswith(f"{relative_path}/"):
                        continue
                    else:
                        raise RuntimeError("E2B preview archive contains a path outside its source set")
                    if not destination.resolve().is_relative_to(context_root.resolve()):
                        raise RuntimeError("E2B preview source contains an unsafe path")
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        source_file = archive.extractfile(member)
                        if source_file is None:
                            raise RuntimeError("Cannot read an E2B preview source file")
                        with source_file, destination.open("wb") as staged_file:
                            shutil.copyfileobj(source_file, staged_file)
                    else:
                        raise RuntimeError("E2B preview source contains a non-file entry")
        for relative_path in _PREVIEW_CONTEXT_FILES:
            if not (context_root / relative_path).is_file():
                raise RuntimeError(f"E2B preview source is missing {relative_path}")
        for relative_path in _PREVIEW_CONTEXT_DIRECTORIES:
            if not (context_root / relative_path).is_dir():
                raise RuntimeError(f"E2B preview source is missing {relative_path}/")
        if not (context_root / "Dockerfile.preview").is_file():
            raise RuntimeError("E2B preview source is missing its Dockerfile")
        return context, source_sha, _context_digest(context_root)
    except Exception:
        context.cleanup()
        raise


def build_template(
    *,
    dockerfile: Path,
    template_name: str,
    cpu_count: int,
    memory_mb: int,
    confirm_paid_build: bool,
) -> dict[str, Any]:
    dockerfile = dockerfile.resolve()
    if not dockerfile.is_file():
        raise FileNotFoundError(f"E2B template Dockerfile not found: {dockerfile}")
    if not template_name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in template_name):
        raise ValueError("template_name must contain only lowercase letters, numbers, dashes, or underscores")
    if cpu_count < 1:
        raise ValueError("cpu_count must be positive")
    if memory_mb < 512 or memory_mb % 2:
        raise ValueError("memory_mb must be an even number of at least 512")
    if not confirm_paid_build:
        return {
            "status": "dry_run",
            "dockerfile": str(dockerfile),
            "template_name": template_name,
            "cpu_count": cpu_count,
            "memory_mb": memory_mb,
            "message": "Re-run with --confirm-paid-build to submit the E2B template build.",
        }
    if not os.getenv("E2B_API_KEY", "").strip():
        raise RuntimeError("E2B_API_KEY is required for an E2B template build")

    try:
        from e2b import Template
    except ImportError as exc:
        raise RuntimeError("Install the E2B extra before building: pip install 'mozaiks[e2b]'") from exc

    context, source_sha, context_sha256 = _stage_preview_context(dockerfile)
    try:
        context_root = Path(context.name)
        template = Template(file_context_path=context_root).from_dockerfile(
            str(context_root / "Dockerfile.preview")
        )
        result = Template.build(
            template,
            alias=template_name,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            on_build_logs=_build_log,
        )
    finally:
        context.cleanup()
    return {
        "status": "built",
        "template_name": template_name,
        "template_id": getattr(result, "template_id", None),
        "build_id": getattr(result, "build_id", None),
        "dockerfile": str(dockerfile),
        "source_sha": source_sha,
        "context_sha256": context_sha256,
    }


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, default=DEFAULT_DOCKERFILE)
    parser.add_argument("--name", default=DEFAULT_TEMPLATE_NAME)
    parser.add_argument("--cpu-count", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=2048)
    parser.add_argument("--confirm-paid-build", action="store_true")
    args = parser.parse_args()
    try:
        result = build_template(
            dockerfile=args.dockerfile,
            template_name=args.name,
            cpu_count=args.cpu_count,
            memory_mb=args.memory_mb,
            confirm_paid_build=args.confirm_paid_build,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
