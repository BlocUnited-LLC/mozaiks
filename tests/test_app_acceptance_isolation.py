"""Generated AppGenerator acceptance never executes candidate Python in Studio."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke, app_validation


def _files() -> dict[str, str]:
    return {"app.json": '{"id":"fixture","name":"Fixture"}'}


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
    assert result["validator_image_id"] == image_id
    assert [kind for kind, _ in calls] == ["adapter", "create", "write", "run", "run", "remove"]
    assert calls[0][1]["image"] == image_id
    assert calls[1][1]["metadata"] == {"purpose": "app_validation"}
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
                    "failed_tests": [], "details": {},
                }))
            return SimpleNamespace(success=True, stdout="")

        async def terminate_session(self, **_kwargs):
            return False

    monkeypatch.setattr(docker_sandbox, "DockerSandboxAdapter", Worker)
    result = await app_validation._app_runtime_load_result(_files())

    assert result["status"] == "skipped"
    assert result["passed"] is None
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
async def test_generated_smoke_uses_contained_observer_and_blocks_unverified_events(monkeypatch):
    image_id = "sha256:" + "b" * 64
    monkeypatch.setattr(app_runtime_smoke, "_preflight_generated_image", lambda: image_id)
    observed: list[tuple[Path, dict]] = []

    async def smoke(app_root, **kwargs):
        observed.append((app_root, kwargs))
        assert (app_root / "app.json").read_text(encoding="utf-8") == _files()["app.json"]
        return {
            "contract_version": "1.0", "status": "passed", "passed": True,
            "checks": [{"id": "app_runtime_smoke", "status": "passed", "passed": True,
                        "details": {"status": "passed"}}],
            "failed_tests": [], "observer_unverified_checks": ["event_rejection"],
        }

    monkeypatch.setattr(app_runtime_smoke, "run_contained_imported_app_runtime_smoke", smoke)
    result = await app_validation._app_runtime_smoke_result(_files())

    assert observed[0][1]["image"] == image_id
    assert observed[0][1]["expected_image_id"] == image_id
    assert result["status"] == "pending"
    assert result["passed"] is None
    assert result["checks"][0]["details"]["blocking"] is True


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
    assert smoked["status"] == "pending"
    assert smoked["observer_unverified_checks"] == ["event_rejection"]
