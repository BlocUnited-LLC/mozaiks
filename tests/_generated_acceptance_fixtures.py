"""Explicit contained-runtime evidence fixtures for non-live AppGenerator tests."""

import hashlib
import json
from unittest.mock import AsyncMock


def stub_contained_generated_runtime(monkeypatch, validation_module):
    """Keep static acceptance real while replacing only the live Docker probes."""
    image_id = "sha256:" + "a" * 64
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)

    def source_digest(files):
        digests = {path: hashlib.sha256(content.encode("utf-8")).hexdigest()
                   for path, content in files.items()}
        return hashlib.sha256(json.dumps(
            digests, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    async def load(files):
        return {
            "contract_version": "1.0", "status": "passed", "passed": True,
            "checks": [{"id": "app_runtime_load", "status": "passed", "passed": True}],
            "failed_tests": [], "worker_containment_verified": True,
            "validator_image_id": image_id, "source_content_sha256": source_digest(files),
        }

    async def smoke(files):
        return {
            "contract_version": "1.0", "status": "passed", "passed": True,
            "results": [{"check": "boot.http_ready", "status": "passed"}],
            "checks": [{"id": "app_runtime_smoke", "status": "passed", "passed": True,
                        "details": {"status": "passed", "check_count": 1,
                                    "failed_check_count": 0, "not_run_check_count": 0}}],
            "failed_tests": [], "observer_unverified_checks": ["event_rejection"],
            "observer_origin": "trusted_external_probe_v1", "observer_run_id": "c" * 32,
            "observed_boot": {"check": "boot.http_ready", "status": "passed"},
            "observer_completion_verified": True, "observer_cleanup_verified": True,
            "validator_image_id": image_id, "source_content_sha256": source_digest(files),
        }

    smoke_mock = AsyncMock(side_effect=smoke)
    if isinstance(validation_module, dict):
        monkeypatch.setitem(validation_module, "_app_runtime_load_result", AsyncMock(side_effect=load))
        monkeypatch.setitem(validation_module, "_app_runtime_smoke_result", smoke_mock)
    else:
        monkeypatch.setattr(validation_module, "_app_runtime_load_result", AsyncMock(side_effect=load))
        monkeypatch.setattr(validation_module, "_app_runtime_smoke_result", smoke_mock)
    return smoke_mock
