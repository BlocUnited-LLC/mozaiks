"""Local packaging of an existing canonical workspace through Factory renderers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def _verified_build_result(mobile_dir: Path) -> dict:
    from mozaiksai.core.semantics.portable_path import validate_portable_path

    manifest = json.loads((mobile_dir / "delivery.manifest.json").read_text(encoding="utf-8"))
    receipt = json.loads((mobile_dir / "build-result.json").read_text(encoding="utf-8"))
    if (
        receipt.get("schema_version") != "mozaiks.android_build_result.v1"
        or receipt.get("status") != "succeeded"
        or receipt.get("device_acceptance") != "not_run"
    ):
        raise ValueError("Android compilation did not produce a successful build receipt")
    for name in ("source_digest", "spec_digest"):
        if not manifest.get(name) or receipt.get(name) != manifest[name]:
            raise ValueError(f"Android build receipt does not match {name}")
    for name, expected in (
        ("pack_digest", manifest.get("pack", {}).get("digest")),
        ("framework_commit", manifest.get("framework", {}).get("commit")),
        ("framework_resource_digest", manifest.get("framework", {}).get("resource_digest")),
    ):
        if not expected or receipt.get(name) != expected:
            raise ValueError(f"Android build receipt does not match {name}")

    apk = receipt.get("apk")
    if not isinstance(apk, dict) or not isinstance(apk.get("path"), str):
        raise ValueError("Android build receipt is missing its APK")
    relative = validate_portable_path(apk["path"]).text
    root = mobile_dir.resolve()
    artifact = root / relative
    if not artifact.resolve().is_relative_to(root):
        raise ValueError("Android APK path leaves the delivery directory")
    for entry in (artifact, *artifact.parents):
        if entry == root:
            break
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError("Android APK path must not contain links")
    if not artifact.is_file():
        raise ValueError("Android APK is not a regular file")
    with artifact.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if apk.get("sha256") != digest or apk.get("size_bytes") != size or size == 0:
        raise ValueError("Android APK does not match the build receipt")
    return receipt


def run(args) -> int:
    # Import Factory resources only when this command runs.
    from factory_app.workflows.AppGenerator.tools.deployment_contract import (
        materialize_android_workspace,
    )

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    node = None if args.prepare_only else shutil.which("node")
    if not args.prepare_only and node is None:
        raise ValueError("Android compilation requires Node.js on PATH")
    result = materialize_android_workspace(Path(args.workspace), config, Path(args.output))
    if args.prepare_only:
        print(json.dumps(result, default=str))
        return 0

    mobile_dir = Path(result["mobile_dir"])
    subprocess.run(
        [node, str(mobile_dir / "build.mjs")],
        cwd=mobile_dir,
        env={**os.environ, "MOZAIKS_PYTHON": sys.executable, "PYTHON_DOTENV_DISABLED": "1"},
        check=True,
    )
    receipt = _verified_build_result(mobile_dir)
    print(json.dumps({**result, **receipt}, default=str))
    return 0
