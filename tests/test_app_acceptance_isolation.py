"""Generated AppGenerator acceptance never executes candidate Python in Studio."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke, app_validation
from tests.test_generated_app_functional_acceptance import _basic_crud_files


def _files() -> dict[str, str]:
    return {"app.json": '{"id":"fixture","name":"Fixture"}'}


def _source_digest(files: dict[str, str]) -> str:
    digests = {path: hashlib.sha256(content.encode("utf-8")).hexdigest()
               for path, content in files.items()}
    return hashlib.sha256(json.dumps(
        digests, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _observed_pass(files: dict[str, str], image_id: str) -> dict:
    return {
        "contract_version": "1.0", "status": "passed", "passed": True,
        "results": [{"check": "boot.http_ready", "status": "passed"}],
        "checks": [{
            "id": "app_runtime_smoke", "status": "passed", "passed": True,
            "details": {"status": "passed", "check_count": 1,
                        "failed_check_count": 0, "not_run_check_count": 0},
        }],
        "failed_tests": [], "observer_unverified_checks": ["event_rejection"],
        "observer_origin": "trusted_external_probe_v1", "observer_run_id": "c" * 32,
        "observed_boot": {"check": "boot.http_ready", "status": "passed"},
        "observer_completion_verified": True, "observer_cleanup_verified": True,
        "validator_image_id": image_id, "source_content_sha256": _source_digest(files),
    }


@pytest.mark.asyncio
async def test_generated_acceptance_fails_closed_before_staging_without_image(monkeypatch):
    monkeypatch.delenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", raising=False)
    monkeypatch.setattr(
        app_runtime_smoke, "_stage_generated_source",
        lambda *_args, **_kwargs: pytest.fail("generated source staged without image preflight"),
    )
    monkeypatch.setattr(
        app_runtime_smoke, "_copy_imported_app",
        lambda *_args, **_kwargs: pytest.fail("generated source copied without image preflight"),
    )

    loaded = await app_validation._app_runtime_load_result(_files())
    smoked = await app_validation._app_runtime_smoke_result(_files())

    assert (loaded["status"], loaded["passed"]) == ("skipped", None)
    assert (smoked["status"], smoked["passed"]) == ("skipped", None)
    assert loaded["checks"][0]["details"]["blocking"] is True
    assert smoked["checks"][0]["details"]["blocking"] is True


@pytest.mark.asyncio
async def test_generated_acceptance_rejects_nontext_source_before_staging(monkeypatch):
    monkeypatch.setattr(
        app_runtime_smoke, "_stage_generated_source",
        lambda *_args, **_kwargs: pytest.fail("invalid source staged"),
    )
    result = await app_validation._app_runtime_smoke_result({"app.json": b"unsafe"})
    assert result["status"] == "skipped"
    assert result["checks"][0]["details"]["blocking"] is True


@pytest.mark.asyncio
async def test_generated_load_uses_only_contained_diagnostic_worker(monkeypatch):
    from mozaiksai.core.adapters import docker_sandbox
    from mozaiksai.core.runtime.app.loader import AppLoader

    image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(AppLoader, "load", lambda *_args: pytest.fail("Studio imported generated Python"))
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: image_id)
    calls: list[tuple[str, object]] = []
    result_json = json.dumps({
        "contract_version": "1.0", "passed": True,
        "worker_containment_verified": False,
        "checks": [{"id": "app_runtime_load", "passed": True}],
        "failed_tests": [], "warnings": [], "details": {"app_name": "Fixture"},
    })

    class Worker:
        def __init__(self, **kwargs):
            calls.append(("adapter", kwargs))

        async def create_session(self, **kwargs):
            calls.append(("create", kwargs))
            return SimpleNamespace(session_id="container-1")

        async def write_files(self, **kwargs):
            calls.append(("write", kwargs))

        async def run_command(self, **kwargs):
            calls.append(("run", kwargs))
            return SimpleNamespace(success=True, stdout=result_json if kwargs["command"].startswith("head ") else "")

        async def terminate_session(self, **kwargs):
            calls.append(("remove", kwargs))
            return True

    monkeypatch.setattr(docker_sandbox, "DockerSandboxAdapter", Worker)
    result = await app_validation._app_runtime_load_result(_files())

    assert result["passed"] is True
    assert result["worker_containment_verified"] is True
    assert result["validator_image_id"] == image_id
    assert [kind for kind, _ in calls] == ["adapter", "create", "write", "run", "run", "remove"]
    assert calls[0][1]["image"] == image_id
    assert calls[1][1]["metadata"] == {"purpose": "app_runtime_diagnostic"}
    assert calls[2][1]["files"] == _files()
    assert "app_runtime_load_probe" in calls[3][1]["command"]


@pytest.mark.asyncio
async def test_generated_load_requires_confirmed_container_removal(monkeypatch):
    from mozaiksai.core.adapters import docker_sandbox

    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: "sha256:" + "a" * 64)

    class Worker:
        def __init__(self, **_kwargs):
            pass

        async def create_session(self, **_kwargs):
            return SimpleNamespace(session_id="container-1")

        async def write_files(self, **_kwargs):
            pass

        async def run_command(self, **kwargs):
            if kwargs["command"].startswith("head "):
                return SimpleNamespace(success=True, stdout=json.dumps({
                    "contract_version": "1.0", "passed": True, "checks": [],
                    "worker_containment_verified": True,
                    "failed_tests": [], "details": {},
                }))
            return SimpleNamespace(success=True, stdout="")

        async def terminate_session(self, **_kwargs):
            return False

    monkeypatch.setattr(docker_sandbox, "DockerSandboxAdapter", Worker)
    result = await app_validation._app_runtime_load_result(_files())

    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert result.get("worker_containment_verified") is not True
    assert "removal" in result["skipped_reason"]


@pytest.mark.asyncio
async def test_generated_load_cancellation_removes_container_created_in_flight(monkeypatch):
    from mozaiksai.core.adapters import docker_sandbox

    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: "sha256:" + "a" * 64)
    entered = asyncio.Event()
    release = asyncio.Event()
    removed: list[str] = []

    class Worker:
        def __init__(self, **_kwargs):
            pass

        async def create_session(self, **_kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(session_id="container-1")

        async def terminate_session(self, *, session_id):
            removed.append(session_id)
            return True

    monkeypatch.setattr(docker_sandbox, "DockerSandboxAdapter", Worker)
    task = asyncio.create_task(app_validation._app_runtime_load_result(_files()))
    await entered.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert removed == ["container-1"]


@pytest.mark.asyncio
async def test_generated_smoke_applies_only_its_scoped_event_exclusion(monkeypatch):
    image_id = "sha256:" + "b" * 64
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: image_id)
    observed: list[tuple[Path, dict]] = []

    async def smoke(app_root, **kwargs):
        observed.append((app_root, kwargs))
        assert (app_root / "app.json").read_text(encoding="utf-8") == _files()["app.json"]
        return _observed_pass(_files(), image_id)

    monkeypatch.setattr(app_runtime_smoke, "run_contained_imported_app_runtime_smoke", smoke)
    result = await app_validation._app_runtime_smoke_result(_files())

    assert observed[0][1]["image"] == image_id
    assert observed[0][1]["expected_image_id"] == image_id
    assert (result["status"], result["passed"]) == ("passed", True)
    assert result["observer_unverified_checks"] == ["event_rejection"]
    assert result["acceptance_scope"] == {
        "version": "2.0", "excluded_observer_checks": ["event_rejection"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", [
    "missing_marker", "extra_marker", "image", "source", "origin", "run_id",
    "boot", "completion", "cleanup", "outcome", "summary", "failed_test",
])
async def test_generated_smoke_refuses_tampered_observer_evidence(monkeypatch, tamper):
    image_id = "sha256:" + "b" * 64
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: image_id)
    result = _observed_pass(_files(), image_id)
    if tamper == "missing_marker":
        del result["observer_unverified_checks"]
    elif tamper == "extra_marker":
        result["observer_unverified_checks"].append("auth")
    elif tamper == "image":
        result["validator_image_id"] = "sha256:" + "a" * 64
    elif tamper == "source":
        result["source_content_sha256"] = "0" * 64
    elif tamper == "origin":
        del result["observer_origin"]
    elif tamper == "run_id":
        result["observer_run_id"] = "wrong-run"
    elif tamper == "boot":
        result["observed_boot"] = None
    elif tamper == "completion":
        result["observer_completion_verified"] = False
    elif tamper == "cleanup":
        result["observer_cleanup_verified"] = False
    elif tamper == "outcome":
        result["results"].append({"check": "crud.items.a_create", "status": "failed"})
    elif tamper == "summary":
        result["checks"][0]["details"]["check_count"] = 2
    else:
        result["failed_tests"] = [{"test": "app_runtime_smoke", "error": "failed"}]

    async def smoke(_app_root, **_kwargs):
        return result

    monkeypatch.setattr(app_runtime_smoke, "run_contained_imported_app_runtime_smoke", smoke)
    scoped = await app_validation._app_runtime_smoke_result(_files())

    assert (scoped["status"], scoped["passed"]) == ("pending", None)
    assert scoped["checks"][0]["details"]["blocking"] is True
    assert "acceptance_scope" not in scoped


@pytest.mark.asyncio
async def test_generated_smoke_preflight_failure_never_writes_source(monkeypatch):
    def unavailable():
        raise RuntimeError("local Docker unavailable")

    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", unavailable)
    monkeypatch.setattr(
        app_runtime_smoke, "_stage_generated_source",
        lambda *_args, **_kwargs: pytest.fail("generated source staged before preflight"),
    )
    result = await app_validation._app_runtime_smoke_result(_files())
    assert result["status"] == "skipped"
    assert result["checks"][0]["details"]["blocking"] is True


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
@pytest.mark.asyncio
async def test_generated_acceptance_uses_local_docker_with_poisoned_client_settings(monkeypatch):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True, check=True, timeout=5,
    )
    image_id = inspected.stdout.strip()
    files = json.loads((Path(__file__).parent / "fixtures" /
                        "runtime_smoke_good_bundle_fdfa818e.json").read_text(encoding="utf-8"))["files"]
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
    monkeypatch.setenv("DOCKER_CONTEXT", "untrusted-remote")
    monkeypatch.setenv("DOCKER_CONFIG", "untrusted-config")

    loaded = await app_validation._app_runtime_load_result(files)
    smoked = await app_validation._app_runtime_smoke_result(files)

    assert loaded["passed"] is True, loaded
    assert loaded["validator_image_id"] == image_id
    assert smoked["validator_image_id"] == image_id
    assert smoked["observer_origin"] == "trusted_external_probe_v1", smoked
    assert smoked["observed_boot"] == {"check": "boot.http_ready", "status": "passed"}
    assert smoked["status"] == "passed"
    assert smoked["observer_unverified_checks"] == ["event_rejection"]
    assert smoked["acceptance_scope"] == {
        "version": "2.0", "excluded_observer_checks": ["event_rejection"],
    }


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
@pytest.mark.asyncio
async def test_generated_gate_admits_only_pinned_contained_evidence(monkeypatch):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True, check=True, timeout=5,
    )
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", inspected.stdout.strip())
    context: dict = {}
    files = json.loads((Path(__file__).parent / "fixtures" /
                        "runtime_smoke_good_bundle_fdfa818e.json").read_text(encoding="utf-8"))["files"]
    # This recorded bundle predates the self-hosted entitlement_dispatch module.
    del files["config/subscriptions.yaml"]

    result = await app_validation.run_app_bundle_acceptance_gate(
        files=files, context_variables=context,
    )

    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=2)
    assert "snapshot_digest" in result
    assert result["app_runtime_smoke"]["observer_unverified_checks"] == ["event_rejection"]
    assert result["app_runtime_smoke"]["acceptance_scope"] == {
        "version": "2.0", "excluded_observer_checks": ["event_rejection"],
    }
    assert result["app_runtime_load_worker"]["passed"] is True
    assert context["app_bundle_acceptance_result"]["snapshot_digest"] == result["snapshot_digest"]


@pytest.mark.asyncio
async def test_loader_diagnostic_cannot_authorize_or_block_promotion(monkeypatch):
    image_id = "sha256:" + "b" * 64
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)
    files = _basic_crud_files()

    async def loader(_files):
        return {
            "contract_version": "1.0", "passed": False,
            "worker_containment_verified": True,
            "validator_image_id": image_id,
            "source_content_sha256": _source_digest(files),
            "checks": [{"id": "app_runtime_load", "passed": False, "message": "Candidate diagnostic failed."}],
            "failed_tests": [{"test": "app_runtime_load", "error": "Candidate diagnostic failed."}],
            "warnings": [], "details": {},
        }

    async def smoke(_files):
        return _observed_pass(files, image_id)

    monkeypatch.setattr(app_validation, "_app_runtime_load_result", loader)
    monkeypatch.setattr(app_validation, "_app_runtime_smoke_result", smoke)
    context: dict = {}
    result = await app_validation.run_app_bundle_acceptance_gate(files=files, context_variables=context)

    assert result["status"] == "passed", result["validation_evidence"]
    assert "snapshot_digest" in result
    assert result["app_runtime_smoke"]["observer_unverified_checks"] == ["event_rejection"]
    assert result["app_runtime_smoke"]["acceptance_scope"] == {
        "version": "2.0", "excluded_observer_checks": ["event_rejection"],
    }
    assert context["app_bundle_acceptance_result"]["app_runtime_smoke"] == result["app_runtime_smoke"]
    assert "app_runtime_load" not in result["validation_evidence"]["completed"]
    assert "app_runtime_load" not in result["validation_evidence"]["failed"]
    assert "app_runtime_load_worker" in result["validation_evidence"]["completed"]
    assert result["app_runtime_load"]["passed"] is False
    assert result["bundle_repair"]["target_agent"] is None
    check = next(item for item in result["checks"] if item["id"] == "app_runtime_load")
    assert check["details"]["blocking"] is False


@pytest.mark.asyncio
async def test_loader_success_cannot_override_failed_external_smoke(monkeypatch):
    async def loader(_files):
        return {
            "contract_version": "1.0", "passed": True,
            "worker_containment_verified": True,
            "checks": [{"id": "app_runtime_load", "passed": True}],
            "failed_tests": [], "warnings": [], "details": {},
        }

    async def smoke(_files):
        return {
            "contract_version": "1.0", "status": "failed", "passed": False,
            "checks": [{"id": "app_runtime_smoke", "status": "failed", "passed": False}],
            "failed_tests": [{"test": "app_runtime_smoke", "check": "boot.http_ready", "error": "No app boot."}],
            "warnings": [],
        }

    monkeypatch.setattr(app_validation, "_app_runtime_load_result", loader)
    monkeypatch.setattr(app_validation, "_app_runtime_smoke_result", smoke)
    result = await app_validation.run_app_bundle_acceptance_gate(files=_basic_crud_files())

    assert result["status"] == "failed", result
    assert "snapshot_digest" not in result
    assert "app_runtime_smoke" in result["validation_evidence"]["failed"]
    assert "app_runtime_load" not in result["validation_evidence"]["completed"]


@pytest.mark.asyncio
async def test_loader_worker_cleanup_must_be_host_verified_even_if_smoke_passes(monkeypatch):
    image_id = "sha256:" + "b" * 64
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)
    files = _basic_crud_files()

    async def loader(_files):
        return {
            "contract_version": "1.0", "passed": True,
            "worker_containment_verified": False,
            "skipped_reason": "contained AppLoader worker removal could not be confirmed",
            "checks": [{"id": "app_runtime_load", "passed": True}],
            "failed_tests": [], "warnings": [], "details": {},
        }

    async def smoke(_files):
        return _observed_pass(files, image_id)

    monkeypatch.setattr(app_validation, "_app_runtime_load_result", loader)
    monkeypatch.setattr(app_validation, "_app_runtime_smoke_result", smoke)
    result = await app_validation.run_app_bundle_acceptance_gate(files=files)

    assert result["status"] == "pending", result["validation_evidence"]
    assert "snapshot_digest" not in result
    assert result["validation_evidence"]["skipped"] == ["app_runtime_load_worker"]
    assert result["skipped_checks"] == [{
        "id": "app_runtime_load_worker",
        "reason": "contained AppLoader worker removal could not be confirmed",
    }]
    check = next(item for item in result["checks"] if item["id"] == "app_runtime_load_worker")
    assert check["details"]["blocking"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["source", "image", "receipt", "cleanup", "scope", "worker_source"])
async def test_acceptance_gate_refuses_tampered_containment_evidence(monkeypatch, tamper):
    image_id = "sha256:" + "b" * 64
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", image_id)
    files = _basic_crud_files()
    observed = _observed_pass(files, image_id)
    worker = {
        "contract_version": "1.0", "passed": True,
        "worker_containment_verified": True,
        "validator_image_id": image_id,
        "source_content_sha256": _source_digest(files),
        "checks": [{"id": "app_runtime_load", "passed": True}],
        "failed_tests": [], "warnings": [], "details": {},
    }
    if tamper == "source":
        observed["source_content_sha256"] = "0" * 64
    elif tamper == "image":
        observed["validator_image_id"] = "sha256:" + "a" * 64
    elif tamper == "receipt":
        observed["observer_completion_verified"] = False
    elif tamper == "cleanup":
        observed["observer_cleanup_verified"] = False
    elif tamper == "scope":
        observed["acceptance_scope"] = {"version": "2.0", "excluded_observer_checks": []}
    else:
        worker["source_content_sha256"] = "0" * 64

    async def loader(_files):
        return worker

    async def smoke(_files):
        return observed

    monkeypatch.setattr(app_validation, "_app_runtime_load_result", loader)
    monkeypatch.setattr(app_validation, "_app_runtime_smoke_result", smoke)
    result = await app_validation.run_app_bundle_acceptance_gate(files=files)

    assert result["status"] == "pending", result["validation_evidence"]
    assert "snapshot_digest" not in result
    assert "app_runtime_smoke" in result["validation_evidence"]["skipped"] or tamper == "worker_source"
