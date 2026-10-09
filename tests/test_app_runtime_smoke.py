"""Runtime smoke gate over recorded generated bundles.

The dead bundle is the recorded 93a7 replay that passed every static gate of
its day while it could not start, crashed on writes and denied paying users.
The good bundle is the recorded fdfa818e run replayed through AppGenerator at
c8b9ea2e. The gate runs each bundle in its child process against a real Mongo
(MONGO_URI); nothing about the gate is mocked.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import site
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke
from factory_app.workflows.AppGenerator.tools.app_runtime_smoke import (
    child_environment,
    preflight_contained_imported_smoke,
    run_app_runtime_smoke,
    run_contained_imported_app_runtime_smoke,
)
from factory_app.workflows.AppGenerator.tools.app_validation import (
    _write_files_to_dir,
    run_app_bundle_acceptance_gate,
)
from mozaiksai.control_plane.validation_evidence import normalize_validation_evidence

FIXTURES = Path(__file__).parent / "fixtures"
SMOKE_PREFIX = "mozaiks_runtime_smoke_"
SERVICE = "modules/task_management/backend/service.py"
MODULE_YAML = "modules/task_management/module.yaml"
EVENTS_YAML = "modules/task_management/contracts/events.yaml"
HOST_SECRETS = {
    "OPENAI_API_KEY": "sk-host-secret",
    "MOZAIKS_CLOUD_API_KEY": "host-cloud-key",
    "AZURE_CLIENT_SECRET": "host-azure-secret",
    "RUNTIME_PLATFORM_EXTENSIONS": "app.services.platform_hooks:get_bundle",
}


def _bundle(name: str) -> dict[str, str]:
    return dict(json.loads((FIXTURES / name).read_text(encoding="utf-8"))["files"])


def _dead() -> dict[str, str]:
    return _bundle("runtime_smoke_dead_bundle_93a7.json")


def _good(extra_service: str = "") -> dict[str, str]:
    files = _bundle("runtime_smoke_good_bundle_fdfa818e.json")
    if extra_service:
        files[SERVICE] = files[SERVICE] + "\n\n" + extra_service
    return files


def _good_with_required_unique_assignment_fields() -> dict[str, str]:
    files = _good()
    contract = json.loads(files["data/contract.json"])
    collection = contract["surfaces"][1]["collections"][0]
    collection["fields"].extend([
        {"name": "subscription_id", "type": "string", "required": True},
        {"name": "plan_name", "type": "string", "required": True},
        {"name": "created_at", "type": "string", "required": True},
        {"name": "updated_at", "type": "string", "required": True},
    ])
    next(field for field in collection["fields"] if field["name"] == "granted_capabilities")["required"] = True
    collection["indexes"].append({
        "name": "subscription_id_unique", "unique": True,
        "keys": [{"field": "subscription_id", "order": 1}],
    })
    files["data/contract.json"] = json.dumps(contract)
    return files


async def _smoke(files: dict[str, str], mongo_uri: str | None, **kwargs) -> dict:
    with tempfile.TemporaryDirectory(prefix="runtime-smoke-test-") as tmp:
        app_root = Path(tmp) / "app"
        _write_files_to_dir(app_root, files)
        return await run_app_runtime_smoke(app_root, mongo_uri=mongo_uri, **kwargs)


async def _contained_smoke(files: dict[str, str], **kwargs) -> dict:
    with tempfile.TemporaryDirectory(prefix="contained-runtime-smoke-test-") as tmp:
        app_root = Path(tmp) / "app"
        for path, content in files.items():
            target = app_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        kwargs.setdefault("expected_source_sha256", {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in files.items()
        })
        if image := kwargs.get("image"):
            inspected = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", image],
                capture_output=True, text=True, timeout=5, check=True,
            )
            kwargs.setdefault("expected_image_id", inspected.stdout.strip())
        return await run_contained_imported_app_runtime_smoke(app_root, **kwargs)


def _by_check(result: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in result["results"]:
        rows.setdefault(row["check"], row)
    return rows


@pytest.fixture
async def mongo():
    """The real Mongo, and a guard that the smoke left no database behind and created none of its own."""
    from motor.motor_asyncio import AsyncIOMotorClient

    uri = os.environ.get("MONGO_URI")
    if not uri:
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MONGO_URI is not set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if os.getenv("MOZAIKS_REQUIRE_REAL_MONGO"):
            pytest.fail("Real MongoDB is required")
        pytest.skip("MongoDB is unavailable")
    before = set(await client.list_database_names())
    yield type("Mongo", (), {"uri": uri, "client": client})
    created = sorted(set(await client.list_database_names()) - before)
    for name in created:
        if name.startswith(SMOKE_PREFIX):
            await client.drop_database(name)
    client.close()
    assert not created, f"the runtime smoke created databases on the host's Mongo: {created}"


# --------------------------------------------------------------------------- no database (D1, D2)


async def test_no_database_is_reported_as_skipped_never_as_a_pass():
    result = await _smoke(_good(), None)

    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert result["skipped_reason"] == "no database configured"
    assert result["failed_tests"] == []
    assert result["checks"] == [{
        "id": "app_runtime_smoke",
        "passed": None,
        "status": "skipped",
        "message": "Runtime smoke skipped: no database configured. Boot, two-user CRUD and entitlement checks did not run.",
        "details": {"status": "skipped", "skipped_reason": "no database configured", "blocking": False},
    }]


async def test_assignment_fixture_uses_declared_required_fields_and_unique_index_for_two_users():
    from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

    files = _good_with_required_unique_assignment_fields()
    contract = json.loads(files["data/contract.json"])
    collection = contract["surfaces"][1]["collections"][0]
    collection["fields"].append({
        "name": "approval_granted", "type": "boolean", "required": True, "default": "false",
    })
    config = SubscriptionsConfig.model_validate(yaml.safe_load(files["config/subscriptions.yaml"]))
    item = app_runtime_smoke._plan_stores(config)[0]
    plan = next(plan for plan in item.plans if plan.plan_id == "pro")

    class AssignmentRows:
        def __init__(self):
            self.rows: list[dict] = []

        async def insert_one(self, record):
            required = {field["name"] for field in collection["fields"] if field.get("required")}
            assert all(record.get(name) is not None for name in required)
            assert record["subscription_id"] not in {row["subscription_id"] for row in self.rows}
            self.rows.append(dict(record))

    rows = AssignmentRows()
    run = SimpleNamespace(
        app_id="fixture-app", load=SimpleNamespace(data_contract=contract),
        database={"billing_subscriptions": rows},
    )
    for user_id in ("user-a", "user-b"):
        assert await app_runtime_smoke._seed_assignment(run, SimpleNamespace(user_id=user_id), item, plan)

    assert [row["user_id"] for row in rows.rows] == ["user-a", "user-b"]
    assert all(row["granted_capabilities"] == plan.capabilities for row in rows.rows)
    assert all(row["plan_name"] == plan.label for row in rows.rows)
    assert all(row["approval_granted"] is False for row in rows.rows)
    assert all(datetime.fromisoformat(row["created_at"]) for row in rows.rows)
    assert all(datetime.fromisoformat(row["updated_at"]) for row in rows.rows)


async def test_assignment_fixture_fails_closed_for_unknown_required_field_without_default():
    from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

    files = _good_with_required_unique_assignment_fields()
    contract = json.loads(files["data/contract.json"])
    contract["surfaces"][1]["collections"][0]["fields"].append({
        "name": "approval_granted", "type": "boolean", "required": True,
    })
    config = SubscriptionsConfig.model_validate(yaml.safe_load(files["config/subscriptions.yaml"]))
    item = app_runtime_smoke._plan_stores(config)[0]
    plan = next(plan for plan in item.plans if plan.plan_id == "pro")
    outcomes = []

    def record(check, passed, message, *, path):
        outcomes.append(SimpleNamespace(check=check, passed=passed, message=message, path=path))

    class NoWrites:
        async def insert_one(self, _record):
            pytest.fail("fixture inserted an assignment with an invented approval value")

    run = SimpleNamespace(
        app_id="fixture-app", load=SimpleNamespace(data_contract=contract),
        database={"billing_subscriptions": NoWrites()}, outcomes=outcomes, record=record,
    )
    assert not await app_runtime_smoke._seed_assignment(run, SimpleNamespace(user_id="user-a"), item, plan)
    assert [(outcome.check, outcome.passed, outcome.path) for outcome in outcomes] == [
        ("subscriptions.assignment_store", False, "data/contract.json"),
    ]
    assert "approval_granted" in outcomes[0].message


async def test_unique_assignment_store_passes_two_user_runtime_smoke(mongo):
    result = await _smoke(_good_with_required_unique_assignment_fields(), mongo.uri)

    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=1)
    checks = _by_check(result)
    assert checks["crud.task_management.tasks.b_list_isolated"]["status"] == "passed"
    assert checks["entitlement.task_management.update_task.entitled_allowed"]["status"] == "passed"


async def test_unreachable_database_is_skipped_with_the_driver_error():
    result = await _smoke(_good(), "mongodb://127.0.0.1:9/?serverSelectionTimeoutMS=300")

    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert result["skipped_reason"].startswith("database unreachable (")


async def test_imported_smoke_without_docker_is_pending_not_a_pass(monkeypatch):
    from mozaiksai.core.adapters import docker_sandbox

    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: False)
    result = await _contained_smoke(_good())

    assert result["status"] == "skipped"
    assert result["passed"] is None
    assert result["skipped_reason"] == "contained Docker validation is unavailable"
    assert result["checks"][0]["details"]["blocking"] is True
    assert result["observer_unverified_checks"] == ["event_rejection"]
    assert "acceptance_scope" not in result


def test_imported_smoke_preflight_requires_docker_and_pinned_image(monkeypatch):
    from mozaiksai.core.adapters import docker_sandbox

    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: False)
    with pytest.raises(RuntimeError, match="contained Docker validation is unavailable"):
        preflight_contained_imported_smoke(expected_image_id="sha256:" + "a" * 64)

    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: True)
    monkeypatch.delenv("MOZAIKS_IMPORTED_SMOKE_IMAGE_ID", raising=False)
    with pytest.raises(RuntimeError, match="contained validator image identity is unconfigured"):
        preflight_contained_imported_smoke()


def test_imported_smoke_preflight_returns_exact_immutable_image_id(monkeypatch):
    from mozaiksai.core.adapters import docker_sandbox

    image_id = "sha256:" + "a" * 64
    commands = []
    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: True)

    def inspect(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=image_id + "\n")

    monkeypatch.setattr(app_runtime_smoke.subprocess, "run", inspect)
    assert preflight_contained_imported_smoke(image="trusted:local", expected_image_id=image_id) == image_id
    assert len(commands) == 1
    assert commands[0][0] == "docker"
    assert commands[0][1] == "--config"
    assert commands[0][3] == "--host"
    assert commands[0][-5:] == ["image", "inspect", "--format", "{{.Id}}", "trusted:local"]


async def test_imported_smoke_requires_pinned_validator_image(monkeypatch, tmp_path):
    from mozaiksai.core.adapters import docker_sandbox

    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: True)
    monkeypatch.delenv("MOZAIKS_IMPORTED_SMOKE_IMAGE_ID", raising=False)
    monkeypatch.setattr(
        app_runtime_smoke, "_copy_imported_app",
        lambda *_args, **_kwargs: pytest.fail("source was staged without a pinned validator image"),
    )
    result = await run_contained_imported_app_runtime_smoke(
        tmp_path, expected_source_sha256={"app.json": hashlib.sha256(b"{}").hexdigest()},
    )
    assert result["status"] == "skipped"
    assert result["skipped_reason"] == "contained validator image identity is unconfigured"
    assert result["checks"][0]["details"]["blocking"] is True
    assert result["observer_unverified_checks"] == ["event_rejection"]


async def test_imported_smoke_rejects_changed_validator_tag_before_staging(monkeypatch, tmp_path):
    from mozaiksai.core.adapters import docker_sandbox

    monkeypatch.setattr(docker_sandbox, "docker_available", lambda: True)
    monkeypatch.setattr(app_runtime_smoke.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="sha256:" + "b" * 64,
    ))
    monkeypatch.setattr(
        app_runtime_smoke, "_copy_imported_app",
        lambda *_args, **_kwargs: pytest.fail("source was staged with a changed validator image"),
    )
    result = await run_contained_imported_app_runtime_smoke(
        tmp_path, expected_source_sha256={"app.json": hashlib.sha256(b"{}").hexdigest()},
        expected_image_id="sha256:" + "a" * 64,
    )
    assert result["status"] == "skipped"
    assert result["skipped_reason"] == "contained Docker image is unavailable"


@pytest.mark.parametrize("receipt_order", ["done_before_boot", "outcome_after_done"])
async def test_imported_smoke_requires_ordered_completion_receipt(monkeypatch, receipt_order):
    image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(app_runtime_smoke, "preflight_contained_imported_smoke", lambda **_kwargs: image_id)

    class ReorderedObserver:
        def run(self, _app_root, _plan_root, _image, _timeout, nonce):
            boot = {"event": "outcome", "observer_nonce": nonce,
                    "check": "boot.http_ready", "status": "passed"}
            done = {"event": "done", "observer_nonce": nonce}
            events = ([done, boot] if receipt_order == "done_before_boot" else
                      [boot, done, {"event": "outcome", "observer_nonce": nonce,
                                    "check": "crud.items.a_create", "status": "passed"}])
            stdout = "".join(
                app_runtime_smoke._EVENT_PREFIX + json.dumps(event) + "\n" for event in events
            )
            return app_runtime_smoke._ChildRun(stdout, "", 0, False, contained=True)

        def close(self):
            pass

    monkeypatch.setattr(app_runtime_smoke, "_ContainedDockerProcess", ReorderedObserver)
    result = await _contained_smoke(_good())

    assert result["status"] == "failed"
    assert _by_check(result)["smoke.observer"]["status"] == "failed"
    assert "observer_origin" not in result
    assert result["observer_unverified_checks"] == ["event_rejection"]


async def test_imported_smoke_rejects_hardlinked_host_file(tmp_path):
    source = tmp_path / "host-secret"
    source.write_text("host secret", encoding="utf-8")
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_text("{}", encoding="utf-8")
    os.link(source, app_root / "linked-secret")

    with pytest.raises(ValueError, match="link or special file"):
        app_runtime_smoke._copy_imported_app(app_root, tmp_path / "copy")


def test_imported_smoke_rejects_existing_staging_path(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_text("{}", encoding="utf-8")
    destination = tmp_path / "copy"
    destination.mkdir()
    sentinel = destination / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")

    with pytest.raises(FileExistsError):
        app_runtime_smoke._copy_imported_app(app_root, destination)
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_imported_probe_mount_excludes_app_python(tmp_path):
    app_root = tmp_path / "app"
    files = {
        "app.json": "{}", "data/contract.json": "{}",
        "modules/tasks/module.yaml": "schema_version: mozaiks.module.v1\n",
        "modules/tasks/backend/handler.py": "raise RuntimeError('untrusted')\n",
        "services/secrets.py": "raise RuntimeError('untrusted')\n",
    }
    _write_files_to_dir(app_root, files)

    plan_root = tmp_path / "plan"
    app_runtime_smoke._copy_probe_plan(app_root, plan_root)

    assert {path.relative_to(plan_root).as_posix() for path in plan_root.rglob("*") if path.is_file()} == {
        "app.json", "data/contract.json", "modules/tasks/module.yaml",
    }


async def test_contained_server_refuses_recorded_boot_failure(monkeypatch):
    import motor.motor_asyncio
    import uvicorn

    class Client:
        def close(self):
            pass

    async def failed_boot(run, _initial_environment):
        run.record("boot.migrations", False, "migration failed")
        return True

    monkeypatch.setattr(motor.motor_asyncio, "AsyncIOMotorClient", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(app_runtime_smoke, "_boot", failed_boot)
    monkeypatch.setattr(uvicorn, "Server", lambda *_args: pytest.fail("failed boot was served"))

    assert await app_runtime_smoke._serve_imported_app("smoke-db", "a" * 32, "mongodb://localhost") == 1


def test_imported_smoke_rejects_changed_bytes_during_copy(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_bytes(b'{"appId":"changed"}')
    with pytest.raises(ValueError, match="bytes differ"):
        app_runtime_smoke._copy_imported_app(
            app_root, tmp_path / "copy", expected_sha256={
                "app.json": hashlib.sha256(b'{"appId":"verified"}').hexdigest(),
            },
        )


def test_imported_smoke_rejects_missing_verified_file(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="missing a verified source file"):
        app_runtime_smoke._copy_imported_app(
            app_root, tmp_path / "copy", expected_sha256={
                "app.json": hashlib.sha256(b"{}").hexdigest(),
                "module.yaml": hashlib.sha256(b"missing").hexdigest(),
            },
        )


def test_imported_smoke_rejects_symlink(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, app_root / "linked", target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks is unavailable on this Windows host")
    with pytest.raises(ValueError, match="directory link"):
        app_runtime_smoke._copy_imported_app(app_root, tmp_path / "fresh-copy")


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions are Windows-specific")
def test_imported_smoke_rejects_ntfs_junction(tmp_path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    junction = app_root / "linked"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True, text=True, check=False,
    )
    if created.returncode != 0:
        pytest.skip("creating an NTFS junction is unavailable")
    try:
        with pytest.raises(ValueError, match="directory link"):
            app_runtime_smoke._copy_imported_app(app_root, tmp_path / "fresh-copy")
    finally:
        junction.rmdir()
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "outside"


async def test_imported_smoke_cancellation_waits_for_container_registration(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    removed = []

    def create(command, **_kwargs):
        assert command[0] == "docker" and "create" in command
        entered.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(app_runtime_smoke.subprocess, "run", create)
    monkeypatch.setattr(app_runtime_smoke.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("cancelled container started"))
    monkeypatch.setattr(app_runtime_smoke, "_container_removed", lambda name, **_kwargs: removed.append(name) or True)
    child = app_runtime_smoke._ContainedDockerProcess()
    running = asyncio.create_task(asyncio.to_thread(
        child.run, tmp_path, tmp_path, "sha256:" + "a" * 64, 1.0, "a" * 32,
    ))
    assert await asyncio.to_thread(entered.wait, 5)
    cancelling = asyncio.create_task(asyncio.to_thread(child.kill))
    await asyncio.sleep(0.05)
    assert not cancelling.done()
    release.set()
    await cancelling
    result = await running
    assert result.contained is True
    assert result.returncode is None
    assert removed and set(removed) == {child.name, child.probe_name}
    child.close()


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
@pytest.mark.parametrize("crash", [False, True])
async def test_imported_smoke_isolated_docker_boot_and_cleanup(monkeypatch, crash):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    name = "mozaiks-imported-smoke-" + ("1" if not crash else "2") * 20
    monkeypatch.setattr(app_runtime_smoke, "uuid4", lambda: UUID(hex=("1" if not crash else "2") * 32))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host-secret-must-not-enter-container")
    monkeypatch.setenv("MONGO_URI", "mongodb://host-user:host-password@host.example:27017/host")
    if crash:
        files = _good()
        handler = "modules/task_management/backend/handler.py"
        files[handler] = "import os\nos._exit(3)\n" + files[handler]
    else:
        files = _good(
            "import os, socket\n\nasync def before_create_task(ctx, values):\n"
            "    if os.getenv('OPENAI_API_KEY') or os.getenv('MONGO_URI'):\n"
            "        raise RuntimeError('host configuration entered the container')\n"
            "    try:\n"
            "        socket.create_connection(('1.1.1.1', 443), timeout=0.5)\n"
            "    except OSError:\n"
            "        pass\n"
            "    else:\n"
            "        raise RuntimeError('external egress is available')\n"
            "    try:\n"
            "        open('/workspace/app/app.json', 'ab')\n"
            "    except OSError:\n"
            "        pass\n"
            "    else:\n"
            "        raise RuntimeError('imported app source is writable')\n"
        )
    result = await _contained_smoke(files, image=image)

    assert result["status"] == ("failed" if crash else "passed"), result["failed_tests"]
    assert result["validator_image_id"].startswith("sha256:")
    assert len(result["source_content_sha256"]) == 64
    assert result["observer_unverified_checks"] == ["event_rejection"]
    if crash:
        assert "observer_origin" not in result
    else:
        assert result["observer_origin"] == "trusted_external_probe_v1"
        assert result["observer_run_id"] == ("1" * 32)
        assert result["observed_boot"] == {"check": "boot.http_ready", "status": "passed"}
    assert "host-password" not in json.dumps(result)
    assert "sk-host-secret-must-not-enter-container" not in json.dumps(result)
    assert "smoke.cleanup" not in _by_check(result)
    if crash:
        assert _by_check(result)["smoke.process"]["status"] == "failed"
    else:
        assert _by_check(result)["boot.http_ready"]["status"] == "passed"
    containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    assert not containers.stdout.strip()
    probe_containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^/{name}-observer$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    assert not probe_containers.stdout.strip()


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
async def test_imported_smoke_effective_containment_and_cancel_cleanup(monkeypatch):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    name = "mozaiks-imported-smoke-" + "3" * 20
    probe_name = name + "-observer"
    monkeypatch.setattr(app_runtime_smoke, "uuid4", lambda: UUID(hex="3" * 32))
    monkeypatch.setenv("MONGO_URI", "mongodb://host-user:host-password@host.example:27017/host")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host-secret-must-not-enter-container")
    files = _good("import time\n\nasync def before_create_task(ctx, values):\n    time.sleep(600)\n")
    task = asyncio.create_task(_contained_smoke(files, image=image))
    try:
        for _ in range(100):
            inspected = await asyncio.to_thread(
                subprocess.run, ["docker", "inspect", name], capture_output=True, text=True, timeout=5,
                check=False,
            )
            if inspected.returncode == 0:
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("contained runtime smoke container never started")
        container = json.loads(inspected.stdout)[0]
        host = container["HostConfig"]
        assert host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True
        assert host["Privileged"] is False
        assert host["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in host["SecurityOpt"]
        assert host["PidsLimit"] == 128
        assert host["Memory"] == 1024 ** 3
        assert host["NanoCpus"] == 1_000_000_000
        assert container["Config"]["User"] == "10001:10001"
        mounts = [mount for mount in container["Mounts"] if mount["Type"] == "bind"]
        assert len(mounts) == 1
        assert mounts[0]["Destination"] == "/workspace/app"
        assert mounts[0]["RW"] is False
        assert "mozaiks-imported-smoke-" in mounts[0]["Source"]
        assert "host-password" not in json.dumps(container["Config"])
        assert "sk-host-secret-must-not-enter-container" not in json.dumps(container["Config"])
        assert container["Config"]["OpenStdin"] is False
        for _ in range(100):
            inspected_probe = await asyncio.to_thread(
                subprocess.run, ["docker", "inspect", probe_name], capture_output=True, text=True,
                timeout=5, check=False,
            )
            if inspected_probe.returncode == 0:
                break
            await asyncio.sleep(0.2)
        else:
            pytest.fail("trusted observer container never started")
        probe = json.loads(inspected_probe.stdout)[0]
        probe_host = probe["HostConfig"]
        assert probe_host["NetworkMode"] == f"container:{container['Id']}"
        assert probe_host["PidMode"] == ""
        assert probe_host["ReadonlyRootfs"] is True
        assert probe_host["Privileged"] is False
        assert probe_host["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in probe_host["SecurityOpt"]
        assert probe["Config"]["User"] == "10001:10001"
        probe_mounts = [mount for mount in probe["Mounts"] if mount["Type"] == "bind"]
        assert len(probe_mounts) == 1
        assert probe_mounts[0]["Destination"] == "/workspace/plan"
        assert probe_mounts[0]["RW"] is False
        assert "host-password" not in json.dumps(probe["Config"])
        assert "sk-host-secret-must-not-enter-container" not in json.dumps(probe["Config"])
        assert probe["Config"]["OpenStdin"] is False
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    assert not containers.stdout.strip()
    probe_containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^/{probe_name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    assert not probe_containers.stdout.strip()


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
async def test_imported_app_cannot_forge_observer_outcomes_through_stdout():
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    files = _good(
        "import json, os, sys\n\nasync def before_create_task(ctx, values):\n"
        "    nonce = sys.argv[-1]\n"
        "    for event in ({'event': 'outcome', 'check': 'boot.http_ready', 'status': 'passed'},"
        " {'event': 'done'}):\n"
        "        os.write(1, ('@@mozaiks-runtime-smoke@@ ' + json.dumps({**event, 'observer_nonce': nonce})"
        " + '\\n').encode())\n"
        "    raise RuntimeError('real create failed')\n"
    )
    result = await _contained_smoke(files, image=image)

    assert result["status"] == "failed"
    assert "observer_origin" not in result
    assert any(row["status"] == "failed" and row["check"].startswith("crud.")
               for row in result["results"])


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
@pytest.mark.parametrize("fault", ["migration", "router"])
async def test_imported_boot_failure_cannot_receive_observer_receipt(fault):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    files = _good()
    if fault == "migration":
        files["data/migrations/001_broken.json"] = "{invalid json"
    else:
        files["modules/task_management/runtime_extensions.yaml"] = (
            "schema_version: mozaiks.runtime_extensions.v1\nextensions:\n"
            "  - kind: api_router\n    entrypoint: backend.missing:router\n"
        )

    result = await _contained_smoke(files, image=image)

    assert result["status"] == "failed", result["results"]
    assert "observer_origin" not in result
    assert "observer_run_id" not in result
    assert "observed_boot" not in result
    checks = _by_check(result)
    assert any(checks.get(name, {}).get("status") == "failed"
               for name in ("boot.http_ready", "smoke.process"))


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
async def test_imported_smoke_timeout_removes_container(monkeypatch):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    name = "mozaiks-imported-smoke-" + "4" * 20
    monkeypatch.setattr(app_runtime_smoke, "uuid4", lambda: UUID(hex="4" * 32))
    files = _good("import time\n\nasync def before_create_task(ctx, values):\n    time.sleep(600)\n")

    result = await _contained_smoke(files, image=image, timeout_seconds=0.5)

    assert result["status"] == "failed"
    assert "smoke.timeout" in _by_check(result), result["results"]
    assert "smoke.cleanup" not in _by_check(result)
    containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    assert not containers.stdout.strip()


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
async def test_imported_smoke_excess_output_fails_with_bounded_capture(monkeypatch):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    name = "mozaiks-imported-smoke-" + "5" * 20
    monkeypatch.setattr(app_runtime_smoke, "uuid4", lambda: UUID(hex="5" * 32))
    files = _good(
        "import os\n\nasync def before_create_task(ctx, values):\n"
        "    os.write(1, b'x' * 4_100_000)\n"
    )

    result = await _contained_smoke(files, image=image)

    assert result["status"] == "failed"
    assert _by_check(result)["smoke.output"]["status"] == "failed"
    containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=5, check=True,
    )
    assert not containers.stdout.strip()


async def test_acceptance_reports_a_skipped_smoke_explicitly_and_consistently():
    # The suite-wide conftest default resolves no smoke database.
    context: dict = {}
    result = await run_app_bundle_acceptance_gate(files=_good(), context_variables=context)

    assert result["status"] != "passed"
    assert result["passed"] is False
    assert "snapshot_digest" not in result
    assert result["bundle_repair"]["target_agent"] is None
    assert context["integration_tests_passed"] is False
    smoke_check = next(check for check in result["checks"] if check["id"] == "app_runtime_smoke")
    assert (smoke_check["passed"], smoke_check["status"]) == (None, "skipped")
    assert result["app_runtime_smoke"]["passed"] is None
    assert result["skipped_checks"] == [
        {"id": "app_runtime_load_worker", "reason": "contained AppLoader worker source, image, or cleanup was not verified"},
        {"id": "app_runtime_smoke", "reason": "no database configured"},
    ]
    assert result["validation_evidence"]["skipped"] == ["app_runtime_load_worker", "app_runtime_smoke"]
    assert "app_runtime_smoke" not in result["validation_evidence"]["completed"]
    assert "app_runtime_smoke" not in result["validation_evidence"]["failed"]
    # What the build UI reads carries the skip too.
    assert context["integration_test_result"]["skipped_checks"] == result["skipped_checks"]
    assert context["app_runtime_smoke_result"]["status"] == "skipped"


async def test_skipped_validation_evidence_is_canonical():
    result = await run_app_bundle_acceptance_gate(files=_good())

    evidence = normalize_validation_evidence(result["validation_evidence"])

    assert evidence.skipped == ["app_runtime_load_worker", "app_runtime_smoke"]
    assert evidence.skipped_names() == {"app_runtime_load_worker", "app_runtime_smoke"}
    assert "app_runtime_smoke" not in evidence.completed_names() | evidence.failed_names()


# --------------------------------------------------------------------------- the child process (S2)


def test_child_environment_holds_no_host_configuration(monkeypatch):
    for name, value in HOST_SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MONGO_URI", "mongodb://host-user:host-password@db.example/app")

    environment = child_environment()

    assert not set(environment) & {*HOST_SECRETS, "MONGO_URI"}
    assert not any(value in "".join(environment.values()) for value in [*HOST_SECRETS.values(), "host-password"])
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert set(environment) <= {
        *app_runtime_smoke._CHILD_ENVIRONMENT,
        "PYTHONPATH", "PYTHON_DOTENV_DISABLED", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows Python resolves user-site packages through APPDATA")
def test_child_environment_preserves_windows_python_user_site():
    child = subprocess.run(
        [sys.executable, "-c", "import site; print(site.getusersitepackages())"],
        env=child_environment(), capture_output=True, text=True, check=True,
    )
    assert child.stdout.strip() == site.getusersitepackages()


async def test_generated_code_runs_without_any_host_secret_in_its_environment(mongo, monkeypatch):
    for name, value in HOST_SECRETS.items():
        monkeypatch.setenv(name, value)
    files = _good(
        "import os\n\nasync def before_create_task(ctx, values):\n"
        "    raise RuntimeError('child environment: ' + ','.join(sorted(os.environ)))\n"
    )

    result = await _smoke(files, mongo.uri)

    message = _by_check(result)["crud.task_management.tasks.a_create"]["message"]
    visible = set(message.split("child environment: ", 1)[1].split(" (at ", 1)[0].split(","))
    assert visible
    assert not visible & {*HOST_SECRETS, "MONGO_URI"}
    assert visible <= {
        *app_runtime_smoke._CHILD_ENVIRONMENT,
        "PYTHONPATH", "PYTHON_DOTENV_DISABLED", "PYTHONUTF8", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE",
        "USERPROFILE" if os.name == "nt" else "HOME",
    }
    assert mongo.uri not in json.dumps(result)


async def test_a_handler_that_blocks_past_the_budget_is_killed_and_reported(mongo):
    files = _good("import time\n\nasync def before_create_task(ctx, values):\n    time.sleep(600)\n")

    started = time.monotonic()
    result = await _smoke(files, mongo.uri, timeout_seconds=30)
    elapsed = time.monotonic() - started

    assert elapsed < 45
    assert result["status"] == "failed"
    timeout = _by_check(result)["smoke.timeout"]
    assert timeout["message"].startswith(
        "The runtime smoke was stopped at its 30s limit while task_management.create_task was running as user A."
    )
    assert timeout["path"] == SERVICE
    # Checks finished before the hang are still reported.
    assert _by_check(result)["boot.app_load"]["status"] == "passed"


async def test_a_child_that_dies_is_reported_and_its_database_still_dropped(mongo):
    files = _good()
    files["modules/task_management/backend/handler.py"] = "import os\nos._exit(3)\n" + files[
        "modules/task_management/backend/handler.py"
    ]

    result = await _smoke(files, mongo.uri)

    assert result["status"] == "failed"
    process = _by_check(result)["smoke.process"]
    assert process["message"].startswith("The runtime smoke process exited with code 3 during boot.app_load")
    # The mongo fixture asserts that the disposable database was dropped.


# --------------------------------------------------------------------------- startup services (S1)


async def test_startup_services_are_not_started_and_the_host_database_is_untouched(mongo):
    files = _good()
    files["modules/task_management/runtime_extensions.yaml"] = (
        "schema_version: mozaiks.runtime_extensions.v1\nextensions:\n"
        "  - kind: startup_service\n    entrypoint: backend.warmup:WarmupService\n"
    )
    # The mozaiks_cloud pack's reporter pattern: writes app metrics through the process Mongo client.
    files["modules/task_management/backend/warmup.py"] = (
        "from types import SimpleNamespace\nfrom mozaiksai.core.metrics.app_metrics import AppMetrics\n\n"
        "class WarmupService:\n    async def start(self):\n"
        "        await AppMetrics(SimpleNamespace(app_id='release-greenfield-value-target')).ensure_indexes()\n\n"
        "    async def stop(self):\n        pass\n"
    )

    result = await _smoke(files, mongo.uri)

    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=1)
    service = _by_check(result)["boot.startup_services"]
    assert service["status"] == "not_run"
    assert "1 startup_service extension(s) declared by ['task_management'] are not started" in service["message"]
    # The mongo fixture asserts that no database was created on the host's Mongo.


# --------------------------------------------------------------------------- recorded bundles


async def test_recorded_good_bundle_boots_and_passes_every_runtime_check(mongo):
    result = await _smoke(_good(), mongo.uri)

    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=1)
    checks = _by_check(result)
    assert {row["status"] for row in checks.values()} == {"passed"}
    tag = "crud.task_management.tasks"
    assert set(checks) == {
        "boot.app_load", "boot.indexes",
        f"{tag}.a_create", f"{tag}.a_list", f"{tag}.get_missing", f"{tag}.a_get",
        f"{tag}.b_list_isolated", f"{tag}.b_get_isolated", f"{tag}.b_update_denied", f"{tag}.b_delete_denied",
        f"{tag}.a_update", f"{tag}.a_delete",
        "entitlement.task_management.update_task.default_plan_denied",
        "entitlement.task_management.update_task.entitled_allowed",
    }
    assert checks[f"{tag}.b_update_denied"]["message"] == "B's update of A's record is refused (404); the record is unchanged."
    assert checks["entitlement.task_management.update_task.default_plan_denied"]["message"].endswith("denied with 402.")
    assert checks["entitlement.task_management.update_task.entitled_allowed"]["message"].endswith("allowed (200).")
    assert result["events_emitted"] == ["domain.task.created", "domain.task.deleted", "domain.task.updated"]


@pytest.mark.parametrize("id_field", ["_id", "task_id", "id"])
@pytest.mark.parametrize("search_by", ["title", None])
async def test_canonical_reads_and_writes_use_their_own_declared_keys(mongo, id_field, search_by):
    from mozaiksai.core.workflow.generator_support.code_files import (
        extract_code_file_map_from_payload,
    )
    from mozaiksai.core.workflow.generator_support.module_account_data import (
        materialize_module_account_handlers,
    )
    from mozaiksai.core.workflow.generator_support.module_policy import materialize_module_policies
    from mozaiksai.core.workflow.generator_support.module_read_actions import (
        materialize_module_read_implementations,
    )
    from mozaiksai.core.workflow.generator_support.module_write_actions import (
        close_module_actions,
        materialize_module_schemas,
        materialize_module_write_implementations,
    )

    names = ["title", "user_id", *([id_field] if id_field != "_id" else [])]
    contract = {"version": "1", "surfaces": [{
        "surface_id": "task_management", "surface_kind": "module", "collections": [{
            "name": "tasks", "entity": "Task", "scope": "app", "tenancy": "per_user", "owner_field": "user_id",
            "ownership": {"surface_id": "task_management", "surface_kind": "module"},
            "fields": [{"name": name, "type": "string", "required": True} for name in names],
            "search_by": search_by,
            "lifecycle": {"write_mode": "module_action", "migration_policy": "additive_only"},
        }],
    }]}
    plan = {"capability_packs": [{"capability_pack_id": "task_management", "capability_source": "generated_module",
                                   "primary_entities": ["Task"]}], "pages": []}
    closed = close_module_actions({"module_contract": {
        "module_id": "task_management", "module_yaml": {
            "schema_version": "mozaiks.module.v1",
            "module": {"id": "task_management", "handler": "backend.handler:TaskManagementHandler"}, "actions": [],
        },
    }}, app_build_plan=plan, data_contract=contract)
    files = extract_code_file_map_from_payload(closed)
    for materialize in (materialize_module_schemas, materialize_module_read_implementations,
                        materialize_module_write_implementations, materialize_module_account_handlers):
        files.update(materialize(files, app_build_plan=plan, data_contract=contract))
    files.update(materialize_module_policies(files, contract))
    files["app.json"] = json.dumps({"appName": "Lookup Probe", "authRequired": True})
    files["config/auth.yaml"] = _good()["config/auth.yaml"]
    files["data/contract.json"] = json.dumps(contract)

    result = await _smoke(files, mongo.uri)

    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=1)
    checks = _by_check(result)
    assert {row["status"] for row in checks.values()} == {"passed"}
    for check in ("a_create", "a_list", "get_missing", "a_get", "b_list_isolated", "b_get_isolated",
                  "b_update_denied", "b_delete_denied", "a_update", "a_delete"):
        assert checks[f"crud.task_management.tasks.{check}"]["status"] == "passed"


async def test_an_entitlement_gate_without_subscriptions_yaml_is_allowed_like_production(mongo):
    files = _good()
    del files["config/subscriptions.yaml"]

    result = await _smoke(files, mongo.uri)

    assert result["status"] == "passed", json.dumps(result["failed_tests"], indent=1)
    checks = _by_check(result)
    assert checks["crud.task_management.tasks.a_update"]["status"] == "passed"
    assert not [check for check in checks if check.startswith(("entitlement.", "subscriptions.")) or check.endswith(".plan")]


def _created_event_without_owner(files: dict[str, str]) -> dict[str, str]:
    """The good bundle, emitting its created event without the user_id its events.yaml schema requires."""
    emit = "await ctx.emit('domain.task.created', item)"
    assert files[SERVICE].count(emit) == 1
    files[SERVICE] = files[SERVICE].replace(
        emit, "await ctx.emit('domain.task.created', {k: v for k, v in item.items() if k != 'user_id'})",
    )
    return files


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
async def test_contained_smoke_names_its_unverified_rejected_event_check():
    result = await _contained_smoke(
        _created_event_without_owner(_good()), image=os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"],
    )

    # The write and HTTP response succeed although A drops the invalid event.
    # B cannot see that rejection; its passing result must declare the gap.
    assert result["status"] == "passed", result["results"]
    assert result["observer_origin"] == "trusted_external_probe_v1"
    assert result["observer_unverified_checks"] == ["event_rejection"]
    assert "acceptance_scope" not in result
    assert not any(row["check"].startswith("event.") for row in result["results"])


@pytest.mark.skipif(
    not os.getenv("MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"),
    reason="set MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE to a freshly built local preview image",
)
async def test_generated_scope_preserves_invalid_emission_as_unverified(monkeypatch):
    image = os.environ["MOZAIKS_TEST_CONTAINED_SMOKE_IMAGE"]
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True, check=True, timeout=5,
    )
    monkeypatch.setenv("MOZAIKS_APP_RUNTIME_IMAGE_ID", inspected.stdout.strip())

    result = await app_runtime_smoke.run_contained_generated_app_runtime_smoke(
        _created_event_without_owner(_good()),
    )

    assert result["status"] == "passed", result
    assert result["observer_unverified_checks"] == ["event_rejection"]
    assert result["acceptance_scope"] == {
        "version": "2.0", "excluded_observer_checks": ["event_rejection"],
    }
    assert result["observer_completion_verified"] is True
    assert result["observer_cleanup_verified"] is True
    assert not any(row["check"].startswith("event.") for row in result["results"])


async def test_a_rejected_event_fails_its_own_check_while_the_write_behind_it_passes(mongo):
    result = await _smoke(_created_event_without_owner(_good()), mongo.uri)

    assert result["status"] == "failed"
    checks = _by_check(result)
    event_check = "event.task_management.create_task.domain.task.created"
    assert {check for check, row in checks.items() if row["status"] == "failed"} == {event_check}
    message = checks[event_check]["message"]
    assert message.startswith("task_management.create_task as user A emitted domain.task.created (event evt_")
    assert "(at $.required: Missing required properties: 'user_id'.)" in message
    assert checks[event_check]["path"] == SERVICE
    # The runtime reports the create as succeeded; only the event is refused.
    assert checks["crud.task_management.tasks.a_create"]["status"] == "passed"
    assert result["events_emitted"] == ["domain.task.deleted", "domain.task.updated"]
    assert [item["check"] for item in result["failed_tests"]] == [event_check]


def _events_rejected_four_ways(files: dict[str, str]) -> dict[str, str]:
    """The good bundle with one event rejected per category, two of them in one action.

    create_task emits its created event without user_id and an event it does
    not declare, list_tasks emits an event it does not declare, and the
    deleted event's declared schema cannot be evaluated.
    """
    service = files[SERVICE]
    created = "    await ctx.emit('domain.task.created', item)\n"
    listed = "    return {'items': items, 'total': result['total']}"
    assert service.count(created) == 1 and service.count(listed) == 1
    service = service.replace(created, (
        "    await ctx.emit('domain.task.created', {k: v for k, v in item.items() if k != 'user_id'})\n"
        "    await ctx.emit('domain.task.audited', {'task_id': item['task_id']})\n"
    ))
    files[SERVICE] = service.replace(listed, "    await ctx.emit('domain.task.listed', {'total': result['total']})\n" + listed)
    events = yaml.safe_load(files[EVENTS_YAML])
    [deleted] = [event for event in events["events"] if event["type"] == "domain.task.deleted"]
    deleted["payload_schema"] = {"$ref": "#/definitions/missing"}
    files[EVENTS_YAML] = yaml.safe_dump(events, sort_keys=False)
    return files


async def test_every_rejected_event_fails_its_own_check_pointing_at_the_file_to_fix(mongo):
    result = await _smoke(_events_rejected_four_ways(_good()), mongo.uri)

    assert result["status"] == "failed"
    checks = _by_check(result)
    failed = {check: row for check, row in checks.items() if row["status"] == "failed"}
    expected = {
        "event.task_management.create_task.domain.task.created": (
            SERVICE, "(at $.required: Missing required properties: 'user_id'.)",
        ),
        "event.task_management.create_task.domain.task.audited": (
            MODULE_YAML, "which task_management.create_task does not declare in its module.yaml emits",
        ),
        "event.task_management.list_tasks.domain.task.listed": (
            MODULE_YAML, "which task_management.list_tasks does not declare in its module.yaml emits",
        ),
        "event.task_management.delete_task.domain.task.deleted": (
            EVENTS_YAML, "whose payload_schema in contracts/events.yaml cannot be evaluated (Schema evaluation failed.)",
        ),
    }
    assert set(failed) == set(expected)
    for check, (path, fragment) in expected.items():
        assert failed[check]["path"] == path, (check, failed[check]["path"])
        assert fragment in failed[check]["message"], (check, failed[check]["message"])
    # Every action behind a rejected event succeeded, so the CRUD checks all ran and passed.
    assert all(row["status"] == "passed" for check, row in checks.items() if check.startswith("crud."))
    assert result["events_emitted"] == ["domain.task.updated"]
    assert sorted(item["check"] for item in result["failed_tests"]) == sorted(expected)


async def test_one_event_rejected_for_two_reasons_reports_both(mongo):
    files = _good()
    created = "    await ctx.emit('domain.task.created', item)\n"
    assert files[SERVICE].count(created) == 1
    files[SERVICE] = files[SERVICE].replace(created, (
        "    await ctx.emit('domain.task.created', {k: v for k, v in item.items() if k != 'user_id'})\n"
        "    await ctx.emit('domain.task.created', {**item, 'status': 'archived'})\n"
    ))

    result = await _smoke(files, mongo.uri)

    assert result["status"] == "failed"
    check = "event.task_management.create_task.domain.task.created"
    failed = [row for row in result["results"] if row["status"] == "failed"]
    assert [row["check"] for row in failed] == [check, check]
    assert all(row["path"] == SERVICE for row in failed)
    messages = [row["message"] for row in failed]
    assert any("(at $.required: Missing required properties: 'user_id'.)" in message for message in messages)
    assert any("(at $.properties.status.enum: Value does not satisfy the 'enum' constraint.)" in message
               for message in messages)
    assert [item["check"] for item in result["failed_tests"]] == [check, check]
    assert all(row["status"] == "passed" for check_id, row in _by_check(result).items() if check_id.startswith("crud."))
    assert result["events_emitted"] == ["domain.task.deleted", "domain.task.updated"]


async def test_acceptance_fails_a_bundle_whose_event_breaks_its_schema(mongo, monkeypatch):
    monkeypatch.setattr(app_runtime_smoke, "resolve_smoke_mongo_uri", lambda: mongo.uri)

    result = await run_app_bundle_acceptance_gate(files=_created_event_without_owner(_good()))

    assert result["app_runtime_smoke"]["status"] == "failed"
    assert "app_runtime_smoke" in result["validation_evidence"]["failed"]
    smoke_errors = [error for error in result["bundle_repair"]["errors"] if error.startswith("app_runtime_smoke: ")]
    assert len(smoke_errors) == 1
    assert "emitted domain.task.created" in smoke_errors[0]


async def test_recorded_dead_bundle_fails_for_its_known_runtime_reasons(mongo):
    result = await _smoke(_dead(), mongo.uri)

    assert result["status"] == "failed"
    checks = _by_check(result)
    failed = {check: row for check, row in checks.items() if row["status"] == "failed"}
    tag = "crud.task_management.tasks"
    expected = {
        "boot.indexes": ("data_contract collection task_management.tasks.indexes[0].name is required", "data/contract.json"),
        "boot.migrations": ("001_billing_portal_collections.json.version or", "data/migrations/*.json"),
        "subscriptions.assignment_store": (
            "assignment_store.data_alias 'billing.subscriptions' is not declared in data/contract.json aliases",
            "data/contract.json",
        ),
        "permission.task_management.create_task": (
            "requires permissions ['task.create'] that no signed-in user holds", "modules/task_management/module.yaml",
        ),
        "permission.task_management.update_task": (
            "requires permissions ['task.update'] that no signed-in user holds", "modules/task_management/module.yaml",
        ),
        f"{tag}.a_create": (
            "AttributeError: 'dict' object has no attribute 'title' (at modules/task_management/backend/repo.py:",
            "modules/task_management/backend/repo.py",
        ),
        f"{tag}.get_missing": (
            "LookupError: Record not found (at modules/task_management/backend/repo.py:",
            "modules/task_management/backend/repo.py",
        ),
        "entitlement.task_management.summarize_tasks.entitled_allowed": (
            "got 402 ENTITLEMENT_REQUIRED", "config/subscriptions.yaml",
        ),
        "entitlement.task_management.update_task.entitled_allowed": (
            "got 402 ENTITLEMENT_REQUIRED", "config/subscriptions.yaml",
        ),
    }
    assert set(failed) == set(expected)
    for check, (fragment, path) in expected.items():
        assert fragment in failed[check]["message"], (check, failed[check]["message"])
        assert failed[check]["path"] == path, (check, failed[check]["path"])
    for step in ("a_get", "b_list_isolated", "b_get_isolated", "b_update_denied", "b_delete_denied", "a_update", "a_delete"):
        assert checks[f"{tag}.{step}"]["status"] == "not_run"
    assert all(item["error"] and "Not run" not in item["error"] for item in result["failed_tests"])
    assert len(result["failed_tests"]) == len(expected)


async def test_dead_bundle_write_path_reports_its_wrong_id_and_owner_fields(mongo):
    files = _dead()
    # The replay3 repair of the TypedDict crash, so the write path behind it is reached.
    key = "modules/task_management/backend/schemas.py"
    source = files[key].replace(
        "from typing import TypedDict, Optional",
        "from typing import TypedDict, Optional\n\n\nclass _AttrRequest(dict):\n"
        "    def __getattr__(self, key):\n        return self.get(key)\n",
    )
    source = source.replace("class TaskCreateRequest(TypedDict):", "class TaskCreateRequest(_AttrRequest):")
    files[key] = source.replace("class TaskUpdateRequest(TypedDict):", "class TaskUpdateRequest(_AttrRequest):")

    result = await _smoke(files, mongo.uri)

    create = _by_check(result)["crud.task_management.tasks.a_create"]
    assert create["status"] == "failed"
    assert "but no stored task_management.tasks record has that task_id" in create["message"]
    assert "'owner_id'" in create["message"]
    assert "id field 'task_id' and owner field 'user_id'" in create["message"]
    assert create["path"] == "modules/task_management/backend/repo.py"


async def test_migration_history_stays_in_the_disposable_database(mongo):
    from mozaiksai.core.runtime.persistence.migrations import (
        APP_DATA_MIGRATIONS_COLLECTION,
        SYSTEM_DATABASE,
    )

    files = _good()
    app_id = json.loads(files["data/contract.json"])["app_id"]
    files["data/migrations/001_task_indexes.json"] = json.dumps({
        "migration_id": "smoke_history_probe",
        "version": "1",
        "description": "Add a lookup index",
        "operations": [{
            "type": "ensure_index", "module_id": "task_management", "entity_name": "tasks",
            "index": {"name": "tasks_title_idx", "keys": [["title", 1]]},
        }],
    })
    history = mongo.client[SYSTEM_DATABASE][APP_DATA_MIGRATIONS_COLLECTION]
    before = await history.count_documents({"app_id": app_id, "migration_id": "smoke_history_probe"})

    result = await _smoke(files, mongo.uri)

    assert _by_check(result)["boot.migrations"] == {
        "check": "boot.migrations", "status": "passed", "message": "1 data migration(s) applied.", "path": None,
    }
    assert await history.count_documents({"app_id": app_id, "migration_id": "smoke_history_probe"}) == before


async def test_acceptance_fails_the_dead_bundle_and_routes_smoke_diagnostics_to_repair(mongo, monkeypatch):
    monkeypatch.setattr(app_runtime_smoke, "resolve_smoke_mongo_uri", lambda: mongo.uri)

    result = await run_app_bundle_acceptance_gate(files=_dead())

    assert "app_runtime_smoke" in result["validation_evidence"]["failed"]
    assert result["app_runtime_smoke"]["status"] == "failed"
    assert result["skipped_checks"] == []
    smoke_errors = [error for error in result["bundle_repair"]["errors"] if error.startswith("app_runtime_smoke: ")]
    assert len(smoke_errors) == len(result["app_runtime_smoke"]["failed_tests"])
    assert any("indexes[0].name is required" in error for error in smoke_errors)


async def test_acceptance_passes_the_smoke_for_the_good_bundle(mongo, monkeypatch):
    monkeypatch.setattr(app_runtime_smoke, "resolve_smoke_mongo_uri", lambda: mongo.uri)

    result = await run_app_bundle_acceptance_gate(files=_good())

    assert result["app_runtime_smoke"]["status"] == "passed"
    assert "app_runtime_smoke" in result["validation_evidence"]["completed"]
    assert result["validation_evidence"]["skipped"] == []
