"""Offline proof of the E2B candidate upload before root extraction."""

from __future__ import annotations

import io
import tarfile

import pytest

from mozaiksai.core.sandbox.sealed_stage import build_stage_upload, verify_stage_upload


def _uploaded(tmp_path, archive: bytes, manifest: bytes):
    archive_path = tmp_path / "candidate.tar"
    manifest_path = tmp_path / "manifest.json"
    archive_path.write_bytes(archive)
    manifest_path.write_bytes(manifest)
    return archive_path, manifest_path


def test_stage_upload_verifies_every_file_before_extraction(tmp_path):
    files = {
        "app/app.json": b'{"appId":"preview"}',
        "workflows/example/orchestrator.yaml": b"name: example\n",
    }
    archive, manifest = build_stage_upload(files)
    paths = _uploaded(tmp_path, archive, manifest)
    verified = verify_stage_upload(*paths)
    assert set(verified) == set(files)

    tampered = archive.replace(files["app/app.json"], b'{"appId":"changed"}')
    paths[0].write_bytes(tampered)
    with pytest.raises(ValueError, match="digest"):
        verify_stage_upload(*paths)


@pytest.mark.parametrize("path", ["../escape", "app/../escape", "/app/escape", "workflows\\escape"])
def test_stage_upload_rejects_nonportable_paths(path):
    with pytest.raises(ValueError):
        build_stage_upload({"app/app.json": b"{}", path: b"unsafe"})


def test_stage_upload_rejects_case_fold_collision():
    with pytest.raises(ValueError, match="duplicate"):
        build_stage_upload({"app/app.json": b"{}", "app/APP.JSON": b"{}"})


def test_stage_verifier_rejects_link_member(tmp_path):
    _, manifest = build_stage_upload({"app/app.json": b"{}"})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("app/app.json")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    with pytest.raises(ValueError, match="unsupported member"):
        verify_stage_upload(*_uploaded(tmp_path, buffer.getvalue(), manifest))
