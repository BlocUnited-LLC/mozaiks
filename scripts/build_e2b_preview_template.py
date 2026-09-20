"""Build the canonical Mozaiks preview image as an E2B template.

The command is dry-run by default. E2B template builds are hosted-provider
operations and require an explicit --confirm-paid-build acknowledgement.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
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


def _stage_preview_context(dockerfile: Path) -> tempfile.TemporaryDirectory[str]:
    context = tempfile.TemporaryDirectory(prefix="mozaiks-e2b-preview-")
    context_root = Path(context.name)
    for relative_path in _PREVIEW_CONTEXT_FILES:
        source = REPO_ROOT / relative_path
        if not source.is_file():
            context.cleanup()
            raise FileNotFoundError(f"E2B preview context file not found: {source}")
        shutil.copy2(source, context_root / relative_path)
    for relative_path in _PREVIEW_CONTEXT_DIRECTORIES:
        source = REPO_ROOT / relative_path
        if not source.is_dir():
            context.cleanup()
            raise FileNotFoundError(f"E2B preview context directory not found: {source}")
        shutil.copytree(
            source,
            context_root / relative_path,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
    shutil.copy2(dockerfile, context_root / "Dockerfile.preview")
    return context


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

    context = _stage_preview_context(dockerfile)
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
