"""Media bytes participate in the existing contained candidate acceptance gates."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke, app_validation
from factory_app.workflows.AppGenerator.tools.task_integrity import artifact_snapshot_digest
from mozaiksai.core.validation import validate_generated_app_candidate
from tests.test_app_acceptance_isolation import _observed_pass, _source_digest
from tests.test_generated_app_functional_acceptance import _basic_crud_files

# One opaque blue RGBA pixel, including valid PNG chunk checksums.
IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNQaHjwHwAExAKAc00zmAAAAABJRU5ErkJggg=="
)
IMAGE_PATH = "ui/assets/hero.png"
IMAGE_ID = "sha256:" + "a" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_image_evidence", [False, True])
async def test_candidate_media_is_validated_as_one_exact_snapshot(monkeypatch, stale_image_evidence):
    files = {**_basic_crud_files(), IMAGE_PATH: IMAGE}
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", IMAGE_ID)
    seen = []

    async def loader(candidate):
        seen.append(dict(candidate))
        assert candidate == files
        return {
            "passed": True, "worker_containment_verified": True,
            "validator_image_id": IMAGE_ID, "source_content_sha256": _source_digest(files),
            "checks": [], "failed_tests": [],
        }

    async def smoke(candidate):
        seen.append(dict(candidate))
        assert candidate == files
        observed = {**files, IMAGE_PATH: b"old image"} if stale_image_evidence else files
        return _observed_pass(observed, IMAGE_ID)

    build = AsyncMock(return_value={"validation_status": "passed", "validation_strategy": "docker"})
    monkeypatch.setattr(app_runtime_smoke, "run_contained_generated_app_runtime_load", loader)
    monkeypatch.setattr(app_runtime_smoke, "run_contained_generated_app_runtime_smoke", smoke)
    monkeypatch.setattr(app_validation, "_run_sandbox_validation", build)
    result = await validate_generated_app_candidate(files=files, app_id="owned-app", validation_strategy="docker")

    assert len(seen) == 2
    if stale_image_evidence:
        assert result["validation_status"] != "passed"
        assert "snapshot_digest" not in result["app_bundle_acceptance_result"]
        build.assert_not_awaited()
    else:
        assert result["validation_status"] == "passed", result
        assert build.await_args.kwargs["resolved_files"] == files
        assert result["app_bundle_acceptance_result"]["snapshot_digest"] == artifact_snapshot_digest(
            {}, _basic_crud_files(), binary_assets={IMAGE_PATH: IMAGE},
        )


@pytest.mark.asyncio
async def test_contained_smoke_stages_binary_bytes_and_binds_observer_digest(monkeypatch):
    files = {"app.json": "{}", IMAGE_PATH: IMAGE}
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: IMAGE_ID)

    async def observer(app_root, **kwargs):
        assert (app_root / IMAGE_PATH).read_bytes() == IMAGE
        assert kwargs["expected_source_sha256"][IMAGE_PATH] == hashlib.sha256(IMAGE).hexdigest()
        return _observed_pass(files, IMAGE_ID)

    monkeypatch.setattr(app_runtime_smoke, "run_contained_imported_app_runtime_smoke", observer)
    result = await app_runtime_smoke.run_contained_generated_app_runtime_smoke(files)
    assert result["status"] == "passed", result
    assert result["source_content_sha256"] == _source_digest(files)


@pytest.mark.asyncio
async def test_build_worker_receives_binary_files_at_canonical_workspace_paths(monkeypatch):
    from mozaiksai.core import adapters

    worker = SimpleNamespace(
        create_session=AsyncMock(return_value=SimpleNamespace(session_id="media-build", provider="docker")),
        write_files=AsyncMock(),
        run_command=AsyncMock(return_value=SimpleNamespace(success=True, stdout="", stderr="")),
        terminate_session=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(adapters, "DockerSandboxAdapter", lambda **_: worker)
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: IMAGE_ID)
    result = await app_validation.validate_app_build(
        files={"app.json": "{}"}, runtime_binary_assets={IMAGE_PATH: IMAGE},
        validation_strategy="docker", start_dev_server=False,
    )
    assert result["validation_status"] == "passed", result
    assert worker.write_files.await_args.kwargs["files"][f"app/{IMAGE_PATH}"] == IMAGE
    worker.terminate_session.assert_awaited_once_with(session_id="media-build")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["app.json", "modules/demo/backend/handler.py", "../escape.png"])
async def test_binary_source_and_escaping_assets_fail_before_worker_allocation(monkeypatch, path):
    gate = AsyncMock(side_effect=AssertionError("invalid bytes reached acceptance"))
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", gate)
    result = await validate_generated_app_candidate(
        files={"app.json": "{}", path: IMAGE}, app_id="app", validation_strategy="docker",
    )
    assert result["validation_status"] == "failed"
    gate.assert_not_awaited()


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
@pytest.mark.asyncio
async def test_media_candidate_passes_real_pinned_docker_acceptance_and_build(monkeypatch):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True, check=True, timeout=5,
    )
    image_id = inspected.stdout.strip()
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)
    monkeypatch.setenv("MOZAIKS_APP_VALIDATION_STRATEGY", "docker")
    text_files = _basic_crud_files()
    text_files["config/asset_manifest.json"] = json.dumps({
        "version": "1.0", "assets": [{
            "asset_id": "hero", "kind": "hero_image", "source": "generated",
            "path": IMAGE_PATH, "alt": "Blue test image",
        }],
    })
    text_files["ui/pages/orders.yaml"] += """
  - id: orders-hero
    primitive: Image
    config:
      asset_id: hero
      alt: Blue test image
      variant: banner
"""
    files = {**text_files, IMAGE_PATH: IMAGE}

    result = await validate_generated_app_candidate(
        files=files, app_id="support-operations", validation_strategy="docker", timeout_seconds=180,
    )

    assert result["validation_status"] == "passed", json.dumps(result, indent=2)
    acceptance = result["app_bundle_acceptance_result"]
    assert acceptance["status"] == "passed" and acceptance["passed"] is True
    assert acceptance["snapshot_digest"] == artifact_snapshot_digest(
        {}, text_files, binary_assets={IMAGE_PATH: IMAGE},
    )
    assert acceptance["snapshot_digest"] != artifact_snapshot_digest({}, text_files)
    assert acceptance["app_runtime_load_worker"]["passed"] is True
    for evidence in (acceptance["app_runtime_load"], acceptance["app_runtime_smoke"]):
        assert evidence["source_content_sha256"] == _source_digest(files)
        assert evidence["validator_image_id"] == image_id
    smoke = acceptance["app_runtime_smoke"]
    assert smoke["observer_origin"] == "trusted_external_probe_v1"
    assert smoke["observer_completion_verified"] is True
    assert smoke["observer_cleanup_verified"] is True
    build = result["app_validation_result"]
    assert build["validation_status"] == "passed"
    assert build["sandbox_provider"] == "docker"
    assert build["sandbox_image_id"] == image_id
