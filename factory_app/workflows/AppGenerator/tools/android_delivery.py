"""Deterministic Android delivery of an existing canonical app workspace.

This renderer creates portable tooling, not an application revision or a hosted
job. Native compilation is performed by the emitted build.mjs entrypoint.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import io
import ipaddress
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator

from mozaiksai import resources
from mozaiksai.core.runtime.app.auth_contract import validate_app_auth_contract
from mozaiksai.core.runtime.app.layout_registry import (
    ExtensionSlot,
    LayoutExtension,
    LayoutOwner,
    PathScope,
    Requirement,
    build_app_layout_registry,
)
from mozaiksai.core.semantics.archive import (
    ArchiveEntry,
    archive_digest,
    build_deterministic_archive,
)
from mozaiksai.core.semantics.portable_path import detect_collisions, validate_portable_path

from .android_export_policy import BINARY_SUFFIXES, validate_android_export_file
from .generated_bundle_scanner import scan_app_contracts
from .resolve_managed_capability_templates import (
    resolve_managed_capability_templates,
    verify_pack_integrity,
)

_MAX_FILES = 4096
_MAX_FILE_BYTES = 16_000_000
_MAX_SOURCE_BYTES = 64_000_000
_PACKAGE_ID = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_EXCLUDED_PARTS = {".git", ".local", "__pycache__", "node_modules", ".pytest_cache"}
_WEB_ROOT_FILES = {"App.jsx", "main.jsx", "styles.css", "index.html", "package.json", "package-lock.json", "postcss.config.js", "tailwind.config.js", "vite.config.js", "workflowUi.js"}
_UI_ROOT_FILES = {"package.json", "package-lock.json", "postcss.config.js", "tailwind.config.js", "tsconfig.json"}


class AndroidDeliveryError(ValueError):
    """The source or requested delivery cannot be packaged safely."""


class AndroidDeliverySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal["mozaiks.android_delivery.v1"]
    package_id: str = Field(min_length=3, max_length=150)
    display_name: str = Field(min_length=1, max_length=60)
    version_name: str = Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", max_length=32)
    version_code: int = Field(ge=1, le=2_100_000_000)
    backend_origin: str
    build_type: Literal["debug"]

    @field_validator("package_id")
    @classmethod
    def package_identifier(cls, value: str) -> str:
        if not _PACKAGE_ID.fullmatch(value):
            raise ValueError("package_id must be a lowercase reverse-domain Android identifier")
        return value

    @field_validator("display_name")
    @classmethod
    def display_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("display_name must be trimmed text without control characters")
        return value

    @field_validator("backend_origin")
    @classmethod
    def https_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
            or "?" in value or "#" in value
            or any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value
            or parsed.netloc.endswith(":")
        ):
            raise ValueError("backend_origin must be an HTTPS origin without a path, query or credentials")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("backend_origin has an invalid port") from exc
        if str(HttpUrl(value)).removesuffix("/") != value:
            raise ValueError("backend_origin must use its canonical lowercase origin without the default port")
        hostname = parsed.hostname
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            # This deterministic contract rejects local names without resolving DNS.
            # Backend reachability and resolved-address checks belong to the host.
            local_suffixes = ("localhost", "local", "localdomain", "internal", "intranet", "lan", "home", "corp", "home.arpa")
            labels = hostname.split(".")
            if (
                len(hostname) > 253 or len(labels) < 2
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or any(hostname == suffix or hostname.endswith("." + suffix) for suffix in local_suffixes)
            ):
                raise ValueError("backend_origin must use a public hostname or IP address") from None
        else:
            # Use plain IPv4 literals rather than IPv4-mapped IPv6 aliases, whose
            # reserved-address classification differs between Python versions.
            mapped_ipv4 = isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
            if not address.is_global or address.is_reserved or address.is_multicast or mapped_ipv4:
                raise ValueError("backend_origin must use a public hostname or IP address")
        return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _archive(files: dict[str, bytes]) -> bytes:
    return build_deterministic_archive(ArchiveEntry(path=name, content=raw) for name, raw in files.items())


def _reject_link(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise AndroidDeliveryError(f"Links are not supported in Android delivery inputs: {path.name}")
    if path.exists() and not (path.is_file() or path.is_dir()):
        raise AndroidDeliveryError(f"Only regular files and directories are supported: {path.name}")


def _check_ancestors(path: Path) -> None:
    for candidate in (path, *path.parents):
        _reject_link(candidate)


def _capture_tree(root: Path, prefix: str, *, exclude_build_artifacts: bool = True) -> dict[str, bytes]:
    _reject_link(root)
    if not root.is_dir():
        raise AndroidDeliveryError(f"Source directory is missing: {prefix}")
    files: dict[str, bytes] = {}
    size = 0
    def entries(directory: Path):
        for entry in sorted(os.scandir(directory), key=lambda entry: entry.name):
            path = Path(entry.path)
            _reject_link(path)
            if exclude_build_artifacts and entry.name in _EXCLUDED_PARTS:
                continue
            if entry.is_dir(follow_symlinks=False):
                yield from entries(path)
            else:
                yield path

    for path in entries(root):
        relative = path.relative_to(root)
        name = validate_portable_path(f"{prefix}/{relative.as_posix()}").text
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise AndroidDeliveryError(f"Source file exceeds the delivery limit: {name}")
        raw = path.read_bytes()
        if prefix in {"app", "workflows"}:
            try:
                validate_android_export_file(name, raw)
            except ValueError as exc:
                raise AndroidDeliveryError(str(exc)) from None
        size += len(raw)
        if len(files) >= _MAX_FILES or size > _MAX_SOURCE_BYTES:
            raise AndroidDeliveryError("Source workspace exceeds the Android delivery limit")
        files[name] = raw
    detect_collisions(files)
    return files


def _source_contract(files: dict[str, bytes], factory_root: Path) -> tuple[str, str, str]:
    registry = files.get("workflows/extended_orchestration/extension_registry.json")
    if registry is not None:
        registry_value = json.loads(registry)
        if not isinstance(registry_value, dict):
            raise AndroidDeliveryError("Workflow registry must be an object")
        if registry_value.get("extends"):
            raise AndroidDeliveryError("Android delivery currently requires app-owned workflows; inherited Factory workflows are unsupported")
    text_files: dict[str, str] = {}
    for name, raw in files.items():
        if not name.startswith("app/"):
            continue
        if Path(name).suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text_files[name[4:]] = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AndroidDeliveryError(f"Unsupported binary source: {name}") from exc
    errors = scan_app_contracts(text_files)
    if errors:
        raise AndroidDeliveryError("App contract validation failed: " + "; ".join(errors))
    try:
        app = json.loads(text_files["app.json"])
    except (KeyError, ValueError) as exc:
        raise AndroidDeliveryError("A canonical workspace requires app/app.json") from exc
    app_id = app.get("appId") if isinstance(app, dict) else None
    if not isinstance(app_id, str) or not app_id.strip():
        raise AndroidDeliveryError("The source app must have a nonempty appId")
    callback = "/auth/callback"
    if "config/auth.yaml" in text_files:
        contract = validate_app_auth_contract(yaml.safe_load(text_files["config/auth.yaml"]))
        callback = contract.routes.callback
    if "?" in callback or "#" in callback:
        raise AndroidDeliveryError("Native auth callback routes must not contain a query or fragment")
    expected = (factory_root / "build_context/webapp_builder/templates/ui/auth/authAdapter.js").read_text(encoding="utf-8")
    adapter = text_files.get("ui/auth/authAdapter.js")
    if adapter is not None and adapter.replace("\r\n", "\n").strip() != expected.replace("\r\n", "\n").strip():
        raise AndroidDeliveryError("Custom auth adapters are not supported by Android delivery")
    barrel = text_files.get("ui/index.js", "")
    canonical_export = re.compile(r"export\s*\{\s*createAuthAdapter\s*\}\s*from\s*(['\"])\./auth/authAdapter(?:\.js)?\1\s*;?")
    remainder, count = canonical_export.subn("", barrel)
    if count > 1 or (count and adapter is None) or "createAuthAdapter" in remainder or re.search(r"\bexport\s*\*", remainder):
        raise AndroidDeliveryError("The app extension barrel has an unsupported auth override or wildcard export")
    if "ui/browser-index.js" in text_files:
        raise AndroidDeliveryError("ui/browser-index.js is reserved for disposable native composition")
    return app_id, callback, "canonical_facade" if count else "shared_default"


def _resource_selected(path: str) -> bool:
    root, _, relative = path.partition("/")
    if root == "web_shell":
        return relative in _WEB_ROOT_FILES
    if root == "chat-ui":
        return relative in _UI_ROOT_FILES or relative.startswith("src/")
    return False


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode:
        raise AndroidDeliveryError("Unable to verify the exact OSS resource revision")
    return result.stdout


def _framework_snapshot() -> tuple[dict[str, bytes], dict[str, Any]]:
    shell, ui = resources.resolve_web_shell_root(), resources.resolve_chat_ui_root()
    if shell is None or ui is None:
        raise AndroidDeliveryError("The installed OSS distribution is missing shared-shell resources")
    _check_ancestors(shell)
    _check_ancestors(ui)
    shell, ui = shell.resolve(), ui.resolve()
    repo = shell.parent
    files: dict[str, bytes] = {}
    if (repo / ".git").exists():
        if ui != repo / "chat-ui" or shell != repo / "web_shell":
            raise AndroidDeliveryError("Shared-shell resource roots must belong to the same OSS revision")
        commit = _git(repo, "rev-parse", "HEAD").decode().strip()
        if _git(repo, "status", "--porcelain", "--untracked-files=no", "--", "web_shell", "chat-ui").strip():
            raise AndroidDeliveryError("Shared-shell resources differ from their recorded OSS revision")
        raw = _git(repo, "archive", "--format=tar", commit, "web_shell", "chat-ui")
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for entry in archive:
                if entry.isdir():
                    continue
                if not _resource_selected(entry.name):
                    continue
                if not entry.isfile():
                    raise AndroidDeliveryError("OSS build resources must be regular files")
                stream = archive.extractfile(entry)
                assert stream is not None
                files[entry.name] = stream.read()
    else:
        try:
            distribution = importlib.metadata.distribution("mozaiks")
            direct = json.loads(distribution.read_text("direct_url.json") or "{}")
            commit = direct.get("vcs_info", {}).get("commit_id", "")
            for distribution_entry in distribution.files or []:
                name = str(distribution_entry).replace("\\", "/")
                mapped = name.replace("mozaiks_chat_ui/", "chat-ui/", 1)
                if not _resource_selected(mapped):
                    continue
                actual = Path(str(distribution.locate_file(distribution_entry))).resolve()
                expected = (shell if mapped.startswith("web_shell/") else ui) / mapped.split("/", 1)[1]
                if actual != expected or not distribution_entry.hash or distribution_entry.hash.mode != "sha256":
                    raise AndroidDeliveryError("Installed resource provenance is missing or mismatched")
                _check_ancestors(actual)
                raw = actual.read_bytes()
                digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
                if digest != distribution_entry.hash.value:
                    raise AndroidDeliveryError("Installed shared-shell resources differ from their distribution")
                files[mapped] = raw
        except (importlib.metadata.PackageNotFoundError, ValueError, OSError) as exc:
            raise AndroidDeliveryError("Install Mozaiks from an exact Git commit to establish resource provenance") from exc
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise AndroidDeliveryError("The OSS installation must identify an exact Git commit")
    required = {"web_shell/package.json", "web_shell/package-lock.json", "web_shell/vite.config.js", "chat-ui/package.json", "chat-ui/package-lock.json", "chat-ui/src/auth/authAdapter.js"}
    if not required.issubset(files):
        raise AndroidDeliveryError("The exact OSS source is missing required build resources")
    detect_collisions(files)
    entries = [{"path": name, "sha256": _sha(raw), "size_bytes": len(raw)} for name, raw in sorted(files.items())]
    return files, {"commit": commit, "resource_digest": archive_digest(_archive(files)), "files": entries}


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for name, raw in sorted(files.items()):
        target = root / validate_portable_path(name).text
        _check_ancestors(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def stage_android_framework(mobile_dir: Path) -> dict[str, Any]:
    """Restore excluded build resources from the exact installed OSS revision."""
    mobile_dir = Path(mobile_dir).absolute()
    _check_ancestors(mobile_dir)
    manifest = json.loads((mobile_dir / "delivery.manifest.json").read_text(encoding="utf-8"))
    files, framework = _framework_snapshot()
    if manifest.get("schema_version") != "mozaiks.android_delivery_manifest.v1" or manifest.get("framework") != framework:
        raise AndroidDeliveryError("Installed OSS resources do not match this Android delivery")
    destination = mobile_dir / ".local/framework"
    _check_ancestors(destination)
    if destination.exists() and any(destination.iterdir()):
        raise AndroidDeliveryError("Framework staging already exists; use a fresh delivery workspace")
    _write_files(destination, files)
    return framework


def _factory_root() -> Path:
    factory_root = resources.resolve_factory_app_root()
    if factory_root is None:
        raise AndroidDeliveryError("The OSS installation is missing Factory packaging resources")
    _check_ancestors(factory_root)
    return factory_root


def _inventory(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [{"path": name, "sha256": _sha(raw), "size_bytes": len(raw)} for name, raw in sorted(files.items())]


def _render_delivery(source: dict[str, bytes], spec: AndroidDeliverySpec) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, Any]]:
    """One renderer owns both initial output and verification of exported input."""
    factory_root = _factory_root()
    app_id, callback_path, auth_mode = _source_contract(source, factory_root)
    source_archive = _archive(source)
    pack_root = factory_root / "build_context/mobile"
    pack = verify_pack_integrity(pack_root, "mobile")
    template_files = resolve_managed_capability_templates(
        [{"id": "mobile", "pack_source_path": str(pack_root)}],
        context_variables={"build_timestamp": "1980-01-01T00:00:00Z"},
    )
    exported = dict(source)
    for entry in template_files:
        name = f"app/{validate_portable_path(entry['filename']).text}"
        raw = entry["content"].encode("utf-8")
        if name in exported and exported[name] != raw:
            raise AndroidDeliveryError(f"Mobile pack conflicts with existing source: {name}")
        exported[name] = raw
    framework_files, framework = _framework_snapshot()
    callback_uri = f"{spec.package_id}:{callback_path}"
    package = json.loads((pack_root / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((pack_root / "package-lock.json").read_text(encoding="utf-8"))
    if package.get("dependencies") != lock.get("packages", {}).get("", {}).get("dependencies"):
        raise AndroidDeliveryError("Mobile package and lockfile dependencies do not agree")
    exported.update({
        "mobile/package.json": _json_bytes(package),
        "mobile/package-lock.json": _json_bytes(lock),
        "mobile/capacitor.config.json": _json_bytes({"appId": spec.package_id, "appName": spec.display_name, "webDir": ".local/web", "includePlugins": ["@capacitor/app", "@capacitor/browser"]}),
        "mobile/build.mjs": Path(__file__).with_name("android_delivery_build.mjs").read_bytes(),
    })
    manifest = {
        "schema_version": "mozaiks.android_delivery_manifest.v1", "app_id": app_id,
        "spec": spec.model_dump(), "spec_digest": "sha256:" + _sha(_json_bytes(spec.model_dump())),
        "source_digest": archive_digest(source_archive), "callback_uri": callback_uri,
        "source_files": _inventory(source),
        "browser_auth": {"mode": auth_mode},
        "pack": {"id": pack["pack_id"], "version": pack["version"], "digest": pack["digest"]},
        "framework": framework,
        "files": _inventory(exported),
    }
    exported["mobile/delivery.manifest.json"] = _json_bytes(manifest)
    registry = build_app_layout_registry((LayoutExtension(
        slot=ExtensionSlot.ANDROID_DELIVERY, pack_id="mobile",
    ),))
    for name in exported:
        if name.startswith("mobile/"):
            family = registry.match_path(name, PathScope.WORKSPACE_ROOT).family
            if family.owner != LayoutOwner.DOWNLOAD_RENDERER or family.requirement != Requirement.GENERATED:
                raise AndroidDeliveryError(f"Android tooling must be owned by the workspace delivery renderer: {name}")
    detect_collisions(exported)
    return exported, framework_files, manifest


def verify_android_delivery(mobile_dir: Path) -> dict[str, Any]:
    """Bind all build inputs to the source, spec, pack and installed OSS revision."""
    mobile_dir = Path(mobile_dir).absolute()
    _check_ancestors(mobile_dir)
    manifest_path = mobile_dir / "delivery.manifest.json"
    _reject_link(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "mozaiks.android_delivery_manifest.v1":
        raise AndroidDeliveryError("Invalid Android delivery manifest")
    spec = AndroidDeliverySpec.model_validate(manifest.get("spec"))
    workspace = mobile_dir.parent
    captured = _capture_tree(workspace / "app", "app", exclude_build_artifacts=False)
    if (workspace / "workflows").exists():
        captured.update(_capture_tree(workspace / "workflows", "workflows", exclude_build_artifacts=False))
    entries = manifest.get("source_files")
    if not isinstance(entries, list) or not entries:
        raise AndroidDeliveryError("Original source inventory is missing")
    source: dict[str, bytes] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
            raise AndroidDeliveryError("Invalid original source inventory")
        name = validate_portable_path(entry["path"]).text
        if name in source or name not in captured:
            raise AndroidDeliveryError("Original source inventory differs from the exported workspace")
        source[name] = captured[name]
    exported, framework_files, expected = _render_delivery(source, spec)
    if manifest != expected:
        raise AndroidDeliveryError("Android delivery manifest differs from its canonical inputs")
    expected_app = {name: raw for name, raw in exported.items() if name.startswith(("app/", "workflows/"))}
    if captured != expected_app:
        raise AndroidDeliveryError("Android delivery contains changed or unexpected source files")
    for name, raw in exported.items():
        target = workspace / name
        _check_ancestors(target)
        if not target.is_file() or target.read_bytes() != raw:
            raise AndroidDeliveryError(f"Android delivery file differs from its canonical input: {name}")
    framework_root = mobile_dir / ".local/framework"
    _check_ancestors(framework_root)
    actual_framework = _capture_tree(framework_root, "framework", exclude_build_artifacts=False)
    if {name.removeprefix("framework/"): raw for name, raw in actual_framework.items()} != framework_files:
        raise AndroidDeliveryError("Staged OSS resources differ from their recorded revision")
    return expected


def materialize_android_workspace(
    workspace: Path, config: dict[str, Any] | AndroidDeliverySpec, output_dir: Path,
) -> dict[str, Any]:
    """Export reusable Android tooling; compilation and device acceptance are separate."""
    spec = AndroidDeliverySpec.model_validate(config.model_dump() if isinstance(config, AndroidDeliverySpec) else config)
    workspace, output_dir = Path(workspace).absolute(), Path(output_dir).absolute()
    _check_ancestors(workspace)
    _check_ancestors(output_dir)
    workspace, output_dir = workspace.resolve(), output_dir.resolve()
    if output_dir == workspace or output_dir.is_relative_to(workspace) or workspace.is_relative_to(output_dir):
        raise AndroidDeliveryError("Delivery output must be separate from the source workspace")
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise AndroidDeliveryError("Delivery output must be an empty directory")
    source = _capture_tree(workspace / "app", "app")
    if (workspace / "workflows").exists():
        source.update(_capture_tree(workspace / "workflows", "workflows"))
    detect_collisions(source)
    if len(source) > _MAX_FILES or sum(map(len, source.values())) > _MAX_SOURCE_BYTES:
        raise AndroidDeliveryError("Source workspace exceeds the Android delivery limit")
    exported, framework_files, manifest = _render_delivery(source, spec)
    source_archive = _archive(source)
    archive = _archive(exported)
    export_root = output_dir / "workspace"
    _write_files(export_root, exported)
    _write_files(export_root / "mobile/.local/framework", framework_files)
    (output_dir / "source.zip").write_bytes(source_archive)
    (output_dir / "android-workspace.zip").write_bytes(archive)
    return {
        "status": "prepared", "workspace_dir": str(export_root), "mobile_dir": str(export_root / "mobile"),
        "source_archive": str(output_dir / "source.zip"), "archive_path": str(output_dir / "android-workspace.zip"),
        "manifest_path": str(export_root / "mobile/delivery.manifest.json"),
        "source_digest": manifest["source_digest"], "archive_digest": archive_digest(archive),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Verify or restore exact OSS Android build resources")
    parser.add_argument("operation", choices=["stage-framework", "verify"])
    parser.add_argument("mobile_dir", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.operation == "stage-framework":
            stage_android_framework(arguments.mobile_dir)
        else:
            verify_android_delivery(arguments.mobile_dir)
    except ValidationError as exc:
        parser.exit(1, "Android delivery validation failed: " + "; ".join(
            ".".join(map(str, error["loc"])) + ": " + error["msg"]
            for error in exc.errors(include_input=False)
        ) + "\n")
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Android delivery verification failed ({type(exc).__name__})\n")
