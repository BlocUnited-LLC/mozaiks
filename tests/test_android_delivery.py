from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools import android_delivery as delivery
from mozaiksai.core.semantics.archive import read_archive_manifest
from mozaiksai.core.session.build_context import project_build_context

ROOT = Path(__file__).resolve().parents[1]


def test_mobile_pack_does_not_project_into_ordinary_factory_plans():
    config = yaml.safe_load((ROOT / "factory_app/build_context/mobile/context.yaml").read_text())
    assert project_build_context(
        workflow_id="AppGenerator", config=config,
        provider_values={"capability_packs": [{"id": "mobile"}]},
        context_variables={}, trigger_payload={},
    ) == {}


@pytest.fixture
def spec():
    return {
        "schema_version": "mozaiks.android_delivery.v1", "package_id": "org.example.alpine",
        "display_name": "Alpine Club", "version_name": "1.2.3", "version_code": 7,
        "backend_origin": "https://api.example.invalid", "build_type": "debug",
    }


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "independent-app"
    (root / "app/ui").mkdir(parents=True)
    (root / "app/app.json").write_text(json.dumps({"appId": "alpine-club", "appName": "Alpine Club", "authRequired": False}))
    (root / "app/ui/index.js").write_text("export function register() {}\n")
    (root / "app/brand").mkdir()
    (root / "app/brand/icon.png").write_bytes(b"\x89PNG\x00\xff\x01")
    (root / ".env").write_text("TEST_SECRET=must-not-be-captured\n")
    return root


@pytest.fixture
def framework(monkeypatch):
    files = {"web_shell/package.json": b'{"name":"shell"}', "chat-ui/package.json": b'{"name":"ui"}'}
    value = {"commit": "a" * 40, "resource_digest": "sha256:" + "b" * 64, "files": [
        {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        for path, raw in sorted(files.items())
    ]}
    monkeypatch.setattr(delivery, "_framework_snapshot", lambda: (files, value))
    monkeypatch.setattr(delivery.resources, "resolve_factory_app_root", lambda: ROOT / "factory_app")
    return files, value


def test_export_is_deterministic_preserves_source_and_binary_assets(workspace, framework, spec, tmp_path):
    original = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    first = delivery.materialize_android_workspace(workspace, spec, tmp_path / "first")
    second = delivery.materialize_android_workspace(workspace, spec, tmp_path / "second")
    assert first["status"] == "prepared"
    assert Path(first["archive_path"]).read_bytes() == Path(second["archive_path"]).read_bytes()
    manifest = json.loads(Path(first["manifest_path"]).read_text())
    assert manifest["callback_uri"] == "org.example.alpine:/auth/callback"
    assert manifest["app_id"] == "alpine-club"
    assert manifest["pack"]["id"] == "mobile"
    archive = read_archive_manifest(Path(first["archive_path"]).read_bytes())
    assert archive.archive_sha256 == first["archive_digest"]
    paths = {item.path for item in archive.entries}
    assert "app/brand/icon.png" in paths and "app/ui/auth/capacitor/index.js" in paths
    assert "mobile/delivery.manifest.json" in paths
    assert not any(".local" in path or "node_modules" in path for path in paths)
    assert not any(path.endswith(".env") for path in paths)
    assert (Path(first["workspace_dir"]) / "app/brand/icon.png").read_bytes() == b"\x89PNG\x00\xff\x01"
    assert original == {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}


def test_renderer_requires_registered_workspace_tooling(workspace, framework, spec, tmp_path, monkeypatch):
    class Unregistered:
        def match_path(self, name, scope):
            raise ValueError("unregistered workspace tooling")
    monkeypatch.setattr(delivery, "build_app_layout_registry", lambda *_args: Unregistered())
    with pytest.raises(ValueError, match="unregistered workspace tooling"):
        delivery.materialize_android_workspace(workspace, spec, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("change", [
    {"unknown": "ignored?"}, {"build_type": "release"}, {"package_id": "example"},
    {"package_id": "org.Bad.app"}, {"version_code": True}, {"version_code": 0},
    {"display_name": "bad\nname"}, {"backend_origin": "http://api.example.com"},
    {"backend_origin": "https://user:password@example.com"}, {"backend_origin": "https://example.com/"},
    {"backend_origin": "https://example.com?"}, {"backend_origin": "https://example.com#"},
    {"backend_origin": "https://example.com:bad"}, {"version_name": "next"},
    {"backend_origin": "https://EXAMPLE.com"}, {"backend_origin": "https://example.com:443"},
    {"package_id": "org.example.my_app"},
])
def test_invalid_config_fails_before_output(workspace, spec, tmp_path, change):
    target = tmp_path / "output"
    with pytest.raises(ValidationError):
        delivery.materialize_android_workspace(workspace, {**spec, **change}, target)
    assert not target.exists()


def test_config_errors_do_not_disclose_input_values(spec):
    with pytest.raises(ValidationError) as error:
        delivery.AndroidDeliverySpec.model_validate({**spec, "private_key": "do-not-disclose"})
    assert "do-not-disclose" not in str(error.value)


def test_nonempty_or_overlapping_output_never_changes_source(workspace, spec, tmp_path):
    output = tmp_path / "occupied"
    output.mkdir()
    marker = output / "owned.txt"
    marker.write_text("keep")
    for target in (output, workspace, workspace / "mobile", workspace.parent):
        with pytest.raises(delivery.AndroidDeliveryError):
            delivery.materialize_android_workspace(workspace, spec, target)
    assert marker.read_text() == "keep"


@pytest.mark.parametrize("source_path,content,error", [
    ("app/ui/index.js", "export const createAuthAdapter = () => {};", "auth override"),
    ("app/ui/index.js", "export * from './unknown.js';", "wildcard export"),
    ("app/ui/browser-index.js", "export const data = 1;", "reserved"),
    ("app/ui/auth/authAdapter.js", "export const createAuthAdapter = () => {};", "Custom auth"),
    ("app/.env", "SECRET=private", "Private environment"),
    ("app/.env.example", "INTERNAL_API_KEY=real-secret-value", "secret variable"),
    ("workflows/extended_orchestration/extension_registry.json", '{"extends":"mozaiks.default_workflow_registry"}', "inherited Factory"),
])
def test_unsupported_source_fails_before_output(workspace, framework, spec, tmp_path, source_path, content, error):
    path = workspace / source_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    with pytest.raises(delivery.AndroidDeliveryError, match=error):
        delivery.materialize_android_workspace(workspace, spec, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_canonical_generated_auth_and_env_examples_are_supported(workspace, framework, spec, tmp_path, newline):
    auth = workspace / "app/ui/auth/authAdapter.js"
    auth.parent.mkdir()
    template = (ROOT / "factory_app/build_context/webapp_builder/templates/ui/auth/authAdapter.js").read_text()
    auth.write_bytes(template.replace("\n", newline).encode())
    (workspace / "app/ui/index.js").write_text("export { createAuthAdapter } from './auth/authAdapter.js';\nexport function register() {}\n")
    (workspace / "app/.env.example").write_text("MONGO_URI=<required>\nINTERNAL_API_KEY=\nAUTH_ANON_ACCESS=public\n")
    result = delivery.materialize_android_workspace(workspace, spec, tmp_path / "output")
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    assert manifest["browser_auth"] == {"mode": "canonical_facade"}
    assert (Path(result["workspace_dir"]) / "app/.env.example").is_file()
    delivery.verify_android_delivery(Path(result["mobile_dir"]))


@pytest.mark.parametrize("mutation", ["callback", "spec", "source_digest", "pack", "source_files", "addition", "modified_source", "framework", "helper"])
def test_delivery_verifier_rejects_changed_provenance(workspace, framework, spec, tmp_path, mutation):
    result = delivery.materialize_android_workspace(workspace, spec, tmp_path / "output")
    mobile = Path(result["mobile_dir"])
    path = Path(result["manifest_path"])
    manifest = json.loads(path.read_text())
    assert delivery.verify_android_delivery(mobile) == manifest
    if mutation == "callback":
        manifest["callback_uri"] = "org.example.alpine:/wrong"
    elif mutation == "spec":
        manifest["spec"]["backend_origin"] = "https://other.example.invalid"
    elif mutation == "source_digest":
        manifest["source_digest"] = "sha256:" + "0" * 64
    elif mutation == "pack":
        manifest["pack"]["digest"] = "sha256:" + "0" * 64
    elif mutation == "source_files":
        manifest["source_files"] = manifest["source_files"][1:]
    elif mutation == "addition":
        added = mobile.parent / "workflows/Extra/ui/index.js"
        added.parent.mkdir(parents=True)
        added.write_text("export const added = true;")
    elif mutation == "modified_source":
        (mobile.parent / "app/ui/index.js").write_text("export function register() { return 'changed'; }")
    elif mutation == "framework":
        (mobile / ".local/framework/web_shell/package.json").write_text("{}")
    elif mutation == "helper":
        (mobile / "build.mjs").write_text("console.log('changed');")
    path.write_bytes(delivery._json_bytes(manifest))
    with pytest.raises(delivery.AndroidDeliveryError):
        delivery.verify_android_delivery(mobile)


def test_framework_restore_requires_exact_provenance(workspace, framework, spec, tmp_path):
    result = delivery.materialize_android_workspace(workspace, spec, tmp_path / "output")
    mobile = Path(result["mobile_dir"])
    staged = mobile / ".local/framework"
    shutil.rmtree(staged)
    delivery.stage_android_framework(mobile)
    assert (staged / "web_shell/package.json").read_bytes() == framework[0]["web_shell/package.json"]
    with pytest.raises(delivery.AndroidDeliveryError, match="already exists"):
        delivery.stage_android_framework(mobile)
    shutil.rmtree(staged)
    path = mobile / "delivery.manifest.json"
    manifest = json.loads(path.read_text())
    manifest["framework"]["commit"] = "c" * 40
    path.write_text(json.dumps(manifest))
    with pytest.raises(delivery.AndroidDeliveryError, match="do not match"):
        delivery.stage_android_framework(mobile)
    assert not staged.exists()


def test_reparse_points_are_rejected_on_python_311():
    path = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=0o40755, st_file_attributes=0x400), name="junction")
    with pytest.raises(delivery.AndroidDeliveryError, match="Links"):
        delivery._reject_link(path)


def test_real_link_is_rejected_before_source_read(workspace, spec, tmp_path):
    link = workspace / "app/linked"
    target = tmp_path / "private"
    target.mkdir()
    (target / "private.txt").write_text("private")
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(delivery.AndroidDeliveryError, match="Links"):
        delivery.materialize_android_workspace(workspace, spec, tmp_path / "output")


def test_git_resource_snapshot_is_pinned_and_rejects_drift(tmp_path, monkeypatch):
    repo = tmp_path / "oss"
    repo.mkdir()
    files = {
        "web_shell/package.json": b"{}", "web_shell/package-lock.json": b"{}",
        "web_shell/vite.config.js": b"export default {};\n",
        "chat-ui/package.json": b"{}", "chat-ui/package-lock.json": b"{}",
        "chat-ui/src/auth/authAdapter.js": b"export function createAuthAdapter() {}\n",
    }
    delivery._write_files(repo, files)
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    git("config", "core.eol", "lf")
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "fixture")
    monkeypatch.setattr(delivery.resources, "resolve_web_shell_root", lambda: repo / "web_shell")
    monkeypatch.setattr(delivery.resources, "resolve_chat_ui_root", lambda: repo / "chat-ui")
    first, provenance = delivery._framework_snapshot()
    assert first == files
    assert len(provenance["commit"]) == 40
    (repo / "web_shell/vite.config.js").write_text("changed")
    with pytest.raises(delivery.AndroidDeliveryError, match="differ"):
        delivery._framework_snapshot()


def test_capture_rejects_portable_path_collisions_before_writing(tmp_path):
    if os.name == "nt":
        pytest.skip("Windows filesystem already disallows this case-only collision")
    root = tmp_path / "app"
    root.mkdir()
    (root / "Name.txt").write_text("one")
    (root / "name.txt").write_text("two")
    with pytest.raises(ValueError, match="case-fold duplicate"):
        delivery._capture_tree(root, "app")
