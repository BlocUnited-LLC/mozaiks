"""CLI success requires a build receipt bound to the actual local APK."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from mozaiks_cli.commands import package as package_command
from mozaiks_cli.main import create_parser


@pytest.fixture
def completed_build(tmp_path):
    mobile = tmp_path / "output" / "workspace" / "mobile"
    mobile.mkdir(parents=True)
    manifest = {
        "source_digest": "sha256:" + "1" * 64,
        "spec_digest": "sha256:" + "2" * 64,
        "pack": {"digest": "sha256:" + "3" * 64},
        "framework": {"commit": "4" * 40, "resource_digest": "sha256:" + "5" * 64},
    }
    (mobile / "delivery.manifest.json").write_text(json.dumps(manifest))
    apk = mobile / "android/app/build/outputs/apk/debug/app-debug.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"fixture APK bytes; compilation is tested on the Android runner")
    receipt = {
        "schema_version": "mozaiks.android_build_result.v1",
        "status": "succeeded",
        "device_acceptance": "not_run",
        "source_digest": manifest["source_digest"],
        "spec_digest": manifest["spec_digest"],
        "pack_digest": manifest["pack"]["digest"],
        "framework_commit": manifest["framework"]["commit"],
        "framework_resource_digest": manifest["framework"]["resource_digest"],
        "apk": {
            "path": apk.relative_to(mobile).as_posix(),
            "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
            "size_bytes": apk.stat().st_size,
        },
    }
    (mobile / "build-result.json").write_text(json.dumps(receipt))
    return mobile, receipt, apk


def test_receipt_is_checked_against_actual_apk(completed_build):
    mobile, receipt, apk = completed_build
    assert package_command._verified_build_result(mobile) == receipt
    apk.write_bytes(b"replaced after compilation")
    with pytest.raises(ValueError, match="does not match"):
        package_command._verified_build_result(mobile)


@pytest.mark.parametrize("field", [
    "source_digest", "spec_digest", "pack_digest", "framework_commit", "framework_resource_digest",
])
def test_receipt_cannot_describe_another_build(completed_build, field):
    mobile, receipt, _ = completed_build
    receipt[field] = "another-build"
    (mobile / "build-result.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match=field):
        package_command._verified_build_result(mobile)


@pytest.mark.parametrize("patch", [
    {"status": "failed"}, {"status": "prepared"}, {"device_acceptance": "passed"},
    {"schema_version": "unknown"}, {"apk": None},
])
def test_incomplete_or_forged_success_receipt_is_rejected(completed_build, patch):
    mobile, receipt, _ = completed_build
    receipt.update(patch)
    (mobile / "build-result.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        package_command._verified_build_result(mobile)


@pytest.mark.parametrize("path", ["../outside.apk", "/outside.apk", "C:/outside.apk", "android\\outside.apk"])
def test_receipt_cannot_select_a_nonportable_or_external_file(completed_build, path):
    mobile, receipt, _ = completed_build
    receipt["apk"]["path"] = path
    (mobile / "build-result.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        package_command._verified_build_result(mobile)


def test_receipt_cannot_follow_a_link(completed_build):
    mobile, _, apk = completed_build
    other = mobile / "copied.apk"
    other.write_bytes(apk.read_bytes())
    apk.unlink()
    try:
        apk.symlink_to(other)
    except OSError:
        pytest.skip("Creating file symlinks requires Windows developer mode")
    with pytest.raises(ValueError, match="links"):
        package_command._verified_build_result(mobile)


def _args(tmp_path, *, prepare_only=False):
    config = tmp_path / "android.json"
    config.write_text('{"schema_version":"mozaiks.android_delivery.v1"}')
    argv = ["package", "android", str(tmp_path / "source"), "--config", str(config),
            "--output", str(tmp_path / "output")]
    if prepare_only:
        argv.append("--prepare-only")
    return create_parser().parse_args(argv)


def test_prepare_only_never_launches_a_toolchain(tmp_path, monkeypatch, capsys):
    from factory_app.workflows.AppGenerator.tools import deployment_contract

    prepared = {"status": "prepared", "mobile_dir": str(tmp_path / "mobile")}
    monkeypatch.setattr(deployment_contract, "materialize_android_workspace", lambda *_args: prepared)
    monkeypatch.setattr(package_command.subprocess, "run", lambda *_a, **_kw: pytest.fail("toolchain started"))
    monkeypatch.setattr(package_command.shutil, "which", lambda *_a: pytest.fail("Node required for preparation"))
    assert package_command.run(_args(tmp_path, prepare_only=True)) == 0
    assert json.loads(capsys.readouterr().out) == prepared


def test_failed_compile_cannot_report_an_existing_receipt(completed_build, tmp_path, monkeypatch, capsys):
    from factory_app.workflows.AppGenerator.tools import deployment_contract

    mobile, _, _ = completed_build
    monkeypatch.setattr(deployment_contract, "materialize_android_workspace", lambda *_a: {"mobile_dir": str(mobile)})
    monkeypatch.setattr(package_command.shutil, "which", lambda _name: "node")

    def failed_compile(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["node", "build.mjs"])

    monkeypatch.setattr(package_command.subprocess, "run", failed_compile)
    with pytest.raises(subprocess.CalledProcessError):
        package_command.run(_args(tmp_path))
    assert capsys.readouterr().out == ""


def test_missing_node_fails_before_output_is_created(tmp_path, monkeypatch):
    from factory_app.workflows.AppGenerator.tools import deployment_contract

    monkeypatch.setattr(package_command.shutil, "which", lambda _name: None)
    monkeypatch.setattr(deployment_contract, "materialize_android_workspace", lambda *_a: pytest.fail("output created"))
    with pytest.raises(ValueError, match="Node.js"):
        package_command.run(_args(tmp_path))
    assert not (tmp_path / "output").exists()


def test_success_runs_fixed_entrypoint_and_verifies_its_result(completed_build, tmp_path, monkeypatch, capsys):
    from factory_app.workflows.AppGenerator.tools import deployment_contract

    mobile, receipt, _ = completed_build
    monkeypatch.setattr(deployment_contract, "materialize_android_workspace", lambda *_a: {"mobile_dir": str(mobile)})
    monkeypatch.setattr(package_command.shutil, "which", lambda _name: "node")
    calls = []
    monkeypatch.setattr(package_command.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    assert package_command.run(_args(tmp_path)) == 0
    command, options = calls[0]
    assert command == ["node", str(mobile / "build.mjs")]
    assert options["cwd"] == mobile and options["check"] is True
    assert options["env"]["PYTHON_DOTENV_DISABLED"] == "1"
    assert Path(options["env"]["MOZAIKS_PYTHON"]).is_file()
    assert json.loads(capsys.readouterr().out)["apk"] == receipt["apk"]
