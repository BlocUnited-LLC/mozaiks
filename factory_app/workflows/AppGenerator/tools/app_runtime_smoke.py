"""Runtime smoke gate: boot a generated app bundle and use it as two people.

The static acceptance checks read files. This gate runs the bundle: it loads
it with the platform host's ``AppLoader``, applies its declared indexes and
migrations to a disposable database on the host's configured Mongo, mounts its
API router extensions, and calls its module actions through the real module
router over an in-process ASGI client as synthetic signed-in principals.

Every check is derived from the bundle's own contracts: ``data/contract.json``
collections and their canonical action ids, ``module.yaml`` schemas, gates and
surfaces, ``config/subscriptions.yaml`` plans and assignment store, and the
token scopes ``config/auth.yaml`` grants. Nothing is configured per app.

Generated code never runs in the factory process. ``run_app_runtime_smoke``
(the parent, called by acceptance) creates the disposable database, starts
``python -m`` this module as a child process and drops the database however
the child ends. The child's environment holds only what a Python process needs
to start on this OS (no host secrets, provider keys or Mongo URI; the database
URI arrives on stdin), it is killed at a hard total time limit, and it streams
each check result back as one JSON line so a killed run still reports what it
finished. Inside the child the composed app is dispatched in enforce mode with
its own (empty) platform hook registry, an in-memory audit log, a local event
recorder and no usage metering; module persistence, entitlement reads and
migration history all use the disposable database. Startup services are not
started: they run outside module dispatch with their own clients.

An emitted event that breaks its declared contract does not fail the action
that emitted it: the runtime reports the action as succeeded and names the
rejected event on its dispatch result. The gate reads that result and records
each distinct rejected event (per action, event type and reason) as its own
failed check.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from anyio import CancelScope
from fastapi import FastAPI, Request
from httpx import TimeoutException

logger = logging.getLogger(__name__)

SMOKE_CONTRACT_VERSION = "1.0"
SMOKE_TIMEOUT_SECONDS = 60.0
_CHILD_MODULE = "factory_app.workflows.AppGenerator.tools.app_runtime_smoke"
_EVENT_PREFIX = "@@mozaiks-runtime-smoke@@ "
_SMOKE_DATABASE_PREFIX = "mozaiks_runtime_smoke_"
_CONTAINED_APP_ROOT = PurePosixPath("/workspace/app")
_CONTAINED_PLAN_ROOT = PurePosixPath("/workspace/plan")
_CONTAINED_IMAGE = "mozaiks-sandbox:local"
_MAX_IMPORTED_SOURCE_BYTES = 64_000_000
_MAX_IMPORTED_SOURCE_FILES = 4096
_CONTAINER_STARTUP_SECONDS = 20.0
_CONTAINER_STDOUT_LIMIT_BYTES = 4_000_000
_CONTAINER_STDERR_LIMIT_BYTES = 256_000
_PING_TIMEOUT_SECONDS = 5.0
_STEP_TIMEOUT_SECONDS = 20.0
_REQUEST_TIMEOUT_SECONDS = 10.0
_UNREACHABLE_SURFACES = frozenset({"internal", "admin_internal"})
_RUNTIME_LOGGER = "mozaiks.workflow"
# The child's environment: what a Python process needs to start on this OS. No
# other host variable (secrets, provider keys, MONGO_URI, MOZAIKS_*) is passed.
_CHILD_ENVIRONMENT = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
                      "LANG", "LC_ALL", "LC_CTYPE")

_FIX_SUGGESTION = (
    "Fix the named generated file so the app boots and its declared module actions behave as the "
    "data contract, module.yaml and config/subscriptions.yaml declare when two signed-in users use it."
)


# --------------------------------------------------------------------------- parent: database and child process


def resolve_smoke_mongo_uri() -> str | None:
    """The host's configured Mongo URI, or None when no database is configured."""
    from mozaiksai.core.secrets import inspect_secret_config, resolve_secret

    try:
        if not inspect_secret_config("MONGO_URI").configured:
            return None
        return str(resolve_secret("MONGO_URI") or "").strip() or None
    except Exception as exc:  # secret policy failure; never log the value
        logger.warning("APP_RUNTIME_SMOKE_DATABASE_UNAVAILABLE: %s", type(exc).__name__)
        return None


def child_environment() -> dict[str, str]:
    """The child's whole environment: OS essentials plus the import roots of this very code."""
    environment = {name: os.environ[name] for name in _CHILD_ENVIRONMENT if os.environ.get(name)}
    roots: list[str] = []
    for package_name in ("mozaiksai", "factory_app", "logs"):
        package = sys.modules.get(package_name) or __import__(package_name)
        root = str(Path(str(package.__file__)).resolve().parents[1])
        if root not in roots:
            roots.append(root)
    environment.update({
        "PYTHONPATH": os.pathsep.join(roots),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return environment


@dataclass
class _ChildRun:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    cleanup_failed: bool = False
    output_truncated: bool = False
    contained: bool = False
    app_exited: bool = False


class _ChildProcess:
    """One child run; ``kill`` is safe to call from another thread."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.cancelled = False

    def run(self, app_root: Path, request: dict[str, Any], timeout_seconds: float) -> _ChildRun:
        self.process = subprocess.Popen(
            [sys.executable, "-m", _CHILD_MODULE],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            env=child_environment(), cwd=str(app_root.parent),
        )
        if self.cancelled:  # the gate was cancelled while the process was being created
            self.process.kill()
        try:
            stdout, stderr = self.process.communicate(input=json.dumps(request), timeout=timeout_seconds)
            return _ChildRun(stdout, stderr, self.process.returncode, False)
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, stderr = self.process.communicate()
            return _ChildRun(stdout, stderr, None, True)

    def kill(self) -> None:
        self.cancelled = True
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait()


async def run_app_runtime_smoke(
    app_root: Path, *, mongo_uri: str | None, timeout_seconds: float = SMOKE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Boot the bundle at ``app_root`` in a child process on a disposable database and use it as two users."""
    started = time.monotonic()
    if not mongo_uri:
        return _skipped("no database configured", started=started)
    if not (app_root / "app.json").is_file():
        return _skipped("the bundle has no app.json to boot", started=started)
    from motor.motor_asyncio import AsyncIOMotorClient

    client: Any = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=int(_PING_TIMEOUT_SECONDS * 1000))
    try:
        try:
            await asyncio.wait_for(client.admin.command("ping"), timeout=_PING_TIMEOUT_SECONDS)
        except Exception as exc:
            reason = _redact(f"database unreachable ({type(exc).__name__}: {exc})", mongo_uri)
            return _skipped(reason, started=started)
        database_name = f"{_SMOKE_DATABASE_PREFIX}{uuid4().hex[:20]}"
        request = {"app_root": str(app_root), "mongo_uri": mongo_uri, "database_name": database_name}
        child = _ChildProcess()
        try:
            run = await asyncio.to_thread(child.run, app_root, request, timeout_seconds)
        except BaseException:
            # ASGI cancellation must not leave generated code running.
            with CancelScope(shield=True):
                await asyncio.to_thread(child.kill)
            raise
        finally:
            with CancelScope(shield=True):
                try:
                    await client.drop_database(database_name)
                except Exception as exc:
                    logger.warning("APP_RUNTIME_SMOKE_DROP_FAILED: database=%s error=%s", database_name, type(exc).__name__)
        return _child_result(run, mongo_uri=mongo_uri, timeout_seconds=timeout_seconds, started=started)
    finally:
        client.close()


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _copy_imported_app(
    app_root: Path, destination: Path, *, expected_sha256: Mapping[str, str] | None = None,
) -> None:
    """Copy only regular staged app files into the sole host path mounted by Docker."""
    root_stat = app_root.lstat()
    if _is_link_or_reparse(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("imported app root must be a directory, not a symlink")
    destination.mkdir()
    total_bytes = 0
    file_count = 0
    copied_paths: set[str] = set()
    def fail_walk(error: OSError) -> None:
        raise error

    for root, directories, files in os.walk(app_root, followlinks=False, onerror=fail_walk):
        source_dir = Path(root)
        relative_dir = source_dir.relative_to(app_root)
        for name in directories:
            source = source_dir / name
            source_stat = source.lstat()
            if _is_link_or_reparse(source_stat) or not stat.S_ISDIR(source_stat.st_mode):
                raise ValueError("imported app contains a directory link")
            (destination / relative_dir / name).mkdir()
        for name in files:
            source = source_dir / name
            relative_path = (relative_dir / name).as_posix()
            if expected_sha256 is not None and relative_path not in expected_sha256:
                raise ValueError("imported app contains a file outside the verified source")
            source_stat = source.lstat()
            if (_is_link_or_reparse(source_stat) or not stat.S_ISREG(source_stat.st_mode)
                    or source_stat.st_nlink != 1):
                raise ValueError("imported app contains a link or special file")
            file_count += 1
            total_bytes += source_stat.st_size
            if file_count > _MAX_IMPORTED_SOURCE_FILES or total_bytes > _MAX_IMPORTED_SOURCE_BYTES:
                raise ValueError("imported app exceeds the source smoke limit")
            target = destination / relative_dir / name
            digest = hashlib.sha256()
            with source.open("rb") as reader, target.open("xb") as writer:
                copied = 0
                while chunk := reader.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > source_stat.st_size:
                        raise ValueError("imported app changed while staging")
                    digest.update(chunk)
                    writer.write(chunk)
                if copied != source_stat.st_size:
                    raise ValueError("imported app changed while staging")
            if expected_sha256 is not None and digest.hexdigest() != expected_sha256[relative_path]:
                raise ValueError("imported app bytes differ from the verified source")
            copied_paths.add(relative_path)
    if expected_sha256 is not None and copied_paths != set(expected_sha256):
        raise ValueError("imported app is missing a verified source file")
    if not (destination / "app.json").is_file():
        raise ValueError("imported app has no app.json")


def _copy_probe_plan(staged_root: Path, plan_root: Path) -> None:
    """Give the observer declarative contracts only; app Python stays in the other container."""
    paths = [Path("app.json"), Path("data/contract.json"), Path("config/auth.yaml"),
             Path("config/subscriptions.yaml")]
    modules_root = staged_root / "modules"
    if modules_root.is_dir():
        paths.extend(Path("modules") / module.name / "module.yaml"
                     for module in modules_root.iterdir() if module.is_dir())
        paths.extend(Path("modules") / module.name / "contracts/events.yaml"
                     for module in modules_root.iterdir() if module.is_dir())
    plan_root.mkdir()
    for relative in paths:
        source = staged_root / relative
        if source.is_file():
            target = plan_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def _container_removed(name: str) -> bool:
    """Force removal and verify that Docker no longer knows the container."""
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False)
        remaining = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return remaining.returncode == 0 and name not in remaining.stdout.splitlines()


class _ContainedDockerProcess:
    """Run untrusted app and image-owned observer in separate container processes."""

    def __init__(self) -> None:
        self.name = f"mozaiks-imported-smoke-{uuid4().hex[:20]}"
        self.probe_name = f"{self.name}-observer"
        self.app_process: subprocess.Popen[bytes] | None = None
        self.probe_process: subprocess.Popen[bytes] | None = None
        self.cancelled = False
        self.run_started = threading.Event()
        self.registration_done = threading.Event()

    def run(self, app_root: Path, plan_root: Path, image: str, timeout_seconds: float,
            observer_nonce: str) -> _ChildRun:
        database_name = f"{_SMOKE_DATABASE_PREFIX}{observer_nonce[:20]}"
        app_command = [
            "docker", "create", "--log-driver=none", "--name", self.name,
            "--network=none", "--read-only", "--init", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=128",
            "--memory=1g", "--memory-swap=1g", "--cpus=1",
            "--user=10001:10001",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=512m",
            "--tmpfs=/workspace/logs:rw,nosuid,nodev,size=16m",
            f"--mount=type=bind,source={app_root},target={_CONTAINED_APP_ROOT},readonly",
            "--env=PYTHON_DOTENV_DISABLED=1", "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTHONUNBUFFERED=1", "--env=MONGO_URI=",
            image, "python", "-m", _CHILD_MODULE, "--contained-serve", database_name, observer_nonce,
        ]
        probe_command = [
            "docker", "create", "--log-driver=none", "--name", self.probe_name,
            f"--network=container:{self.name}", "--read-only", "--init", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=128",
            "--memory=1g", "--memory-swap=1g", "--cpus=1",
            "--user=10001:10001",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=64m",
            "--tmpfs=/workspace/logs:rw,nosuid,nodev,size=16m",
            f"--mount=type=bind,source={plan_root},target={_CONTAINED_PLAN_ROOT},readonly",
            "--env=PYTHON_DOTENV_DISABLED=1", "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=PYTHONUNBUFFERED=1", "--env=MONGO_URI=",
            image, "python", "-m", _CHILD_MODULE, "--trusted-probe", database_name, observer_nonce,
        ]
        timed_out = False
        output = [bytearray() for _ in range(4)]
        truncated = [False] * 4
        returncode: int | None = None
        creation_error = ""
        readers: list[threading.Thread] = []

        def drain(process: subprocess.Popen[bytes], index: int, limit: int) -> None:
            stream = process.stdout if index % 2 == 0 else process.stderr
            assert stream is not None
            try:
                while chunk := os.read(stream.fileno(), 8192):
                    remaining = max(0, limit - len(output[index]))
                    output[index].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated[index] = True
            except OSError:
                truncated[index] = True

        def attach(process: subprocess.Popen[bytes], stdout_index: int) -> None:
            for index, limit in ((stdout_index, _CONTAINER_STDOUT_LIMIT_BYTES),
                                 (stdout_index + 1, _CONTAINER_STDERR_LIMIT_BYTES)):
                reader = threading.Thread(target=drain, args=(process, index, limit), daemon=True)
                reader.start()
                readers.append(reader)

        def register(command: list[str]) -> bool:
            nonlocal creation_error
            try:
                created = subprocess.run(
                    command, stdin=subprocess.DEVNULL, capture_output=True,
                    timeout=_CONTAINER_STARTUP_SECONDS, check=False,
                )
                if created.returncode == 0:
                    return True
                creation_error = "Docker could not register the contained smoke"
            except (OSError, subprocess.TimeoutExpired):
                creation_error = "Docker container registration did not complete"
            return False

        self.run_started.set()
        if self.cancelled:
            self.registration_done.set()
            return _ChildRun("", "contained smoke was cancelled before registration", None, False, contained=True)
        try:
            if register(app_command) and not self.cancelled:
                self.app_process = subprocess.Popen(
                    ["docker", "start", "--attach", self.name],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                attach(self.app_process, 0)
                # Docker needs the app container running before a second container
                # can join its isolated loopback namespace.
                deadline = time.monotonic() + _CONTAINER_STARTUP_SECONDS
                while time.monotonic() < deadline and not self.cancelled:
                    inspected = subprocess.run(
                        ["docker", "inspect", "--format", "{{.State.Running}}", self.name],
                        capture_output=True, text=True, timeout=5, check=False,
                    )
                    if inspected.returncode == 0 and inspected.stdout.strip() == "true":
                        break
                    if self.app_process.poll() is not None:
                        break
                    time.sleep(0.1)
                else:
                    creation_error = "Contained app did not start"
                if not creation_error and not self.cancelled and register(probe_command):
                    self.probe_process = subprocess.Popen(
                        ["docker", "start", "--attach", self.probe_name],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    attach(self.probe_process, 2)
            self.registration_done.set()
            if self.probe_process is not None:
                try:
                    self.probe_process.wait(timeout=timeout_seconds)
                    returncode = self.probe_process.returncode
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self.probe_process.kill()
                    self.probe_process.wait(timeout=5)
        finally:
            self.registration_done.set()
            app_exited = self.app_process is not None and self.app_process.poll() is not None
            cleanup_failed = not _container_removed(self.probe_name)
            cleanup_failed = not _container_removed(self.name) or cleanup_failed
            for reader in readers:
                reader.join(timeout=5)
            if any(reader.is_alive() for reader in readers):
                truncated[2] = True
        return _ChildRun(
            output[2].decode("utf-8", errors="replace"),
            creation_error or output[3].decode("utf-8", errors="replace")
            or output[1].decode("utf-8", errors="replace"),
            returncode, timed_out, cleanup_failed=cleanup_failed,
            output_truncated=any(truncated), contained=True, app_exited=app_exited,
        )

    def kill(self) -> None:
        self.cancelled = True
        if not self.run_started.is_set():
            return
        if not self.registration_done.wait(timeout=2 * _CONTAINER_STARTUP_SECONDS + 5):
            raise RuntimeError("Docker container registration could not be confirmed during cancellation")
        try:
            for process in (self.probe_process, self.app_process):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            _container_removed(self.probe_name)
            _container_removed(self.name)


def preflight_contained_imported_smoke(
    *, image: str | None = None, expected_image_id: str | None = None,
) -> str:
    """Require Docker and the exact trusted image ID before imported-source staging."""
    from mozaiksai.core.adapters.docker_sandbox import docker_available

    if not docker_available():
        raise RuntimeError("contained Docker validation is unavailable")
    pinned_image_id = expected_image_id or os.environ.get("MOZAIKS_IMPORTED_SMOKE_IMAGE_ID")
    if not pinned_image_id:
        raise RuntimeError("contained validator image identity is unconfigured")
    selected_image = image or os.environ.get("DOCKER_SANDBOX_IMAGE") or _CONTAINED_IMAGE
    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", selected_image],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("contained Docker image is unavailable") from None
    image_id = inspected.stdout.strip() if inspected.returncode == 0 else ""
    if image_id != pinned_image_id or not image_id.startswith("sha256:") or len(image_id) != 71:
        raise RuntimeError("contained Docker image is unavailable")
    return image_id


async def run_contained_imported_app_runtime_smoke(
    app_root: Path, *, timeout_seconds: float = SMOKE_TIMEOUT_SECONDS, image: str | None = None,
    expected_source_sha256: Mapping[str, str] | None = None,
    expected_image_id: str | None = None,
) -> dict[str, Any]:
    """Smoke verified imported bytes with a private MongoDB and no container egress."""
    started = time.monotonic()
    if not expected_source_sha256:
        return _imported_observer_scope(_contained_unavailable(
            "verified imported-source digests are unavailable", started=started,
        ))
    try:
        image_id = await asyncio.to_thread(
            preflight_contained_imported_smoke, image=image, expected_image_id=expected_image_id,
        )
    except RuntimeError as exc:
        return _imported_observer_scope(_contained_unavailable(str(exc), started=started))
    with tempfile.TemporaryDirectory(prefix="mozaiks-imported-smoke-") as temporary:
        staged_root = Path(temporary) / "app"
        plan_root = Path(temporary) / "plan"
        try:
            _copy_imported_app(app_root, staged_root, expected_sha256=expected_source_sha256)
            _copy_probe_plan(staged_root, plan_root)
        except (OSError, ValueError) as exc:
            return _imported_observer_scope(_summary([{
                "check": "smoke.source", "status": "failed", "path": None,
                "message": f"Imported app cannot be staged safely: {type(exc).__name__}: {exc}",
            }], {}, started=started))
        child = _ContainedDockerProcess()
        observer_nonce = uuid4().hex
        try:
            run = await asyncio.to_thread(child.run, staged_root, plan_root, image_id, timeout_seconds,
                                          observer_nonce)
        except Exception as exc:
            with CancelScope(shield=True):
                await asyncio.to_thread(child.kill)
            return _imported_observer_scope(_summary([{
                "check": "smoke.container", "status": "failed", "path": None,
                "message": f"Contained runtime smoke could not complete: {type(exc).__name__}.",
            }], {}, started=started))
        except BaseException:
            with CancelScope(shield=True):
                await asyncio.to_thread(child.kill)
            raise
        result = _child_result(run, mongo_uri="", timeout_seconds=timeout_seconds, started=started)
        events = _events(run.stdout)
        boot = [event for event in events if event.get("event") == "outcome"
                and event.get("check") == "boot.http_ready" and event.get("status") == "passed"]
        done = [event for event in events if event.get("event") == "done"]
        audit = done[0].get("event_audit") if len(done) == 1 else None
        valid_receipt = (bool(events) and len(boot) == 1 and len(done) == 1
                         and _valid_trusted_event_audit(events, audit, observer_nonce)
                         and all(event.get("observer_nonce") == observer_nonce for event in events))
        if result["status"] == "passed" and not valid_receipt:
            result = _summary([*result["results"], {
                "check": "smoke.observer", "status": "failed", "path": None,
                "message": "The external observer did not return one matching boot and completion receipt.",
            }], result, started=started)
        result["validator_image_id"] = image_id
        result["source_content_sha256"] = hashlib.sha256(json.dumps(
            expected_source_sha256, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if isinstance(audit, dict):
            result["trusted_event_audit"] = audit
        if result["status"] == "passed" and valid_receipt:
            result["observer_origin"] = "trusted_external_probe_v1"
            result["observer_run_id"] = observer_nonce
            result["observed_boot"] = {"check": "boot.http_ready", "status": "passed"}
        return _imported_observer_scope(result)


def _redact(text: str, mongo_uri: str) -> str:
    return text.replace(mongo_uri, "<database uri>") if mongo_uri else text


def _events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.startswith(_EVENT_PREFIX):
            continue
        try:
            event = json.loads(line[len(_EVENT_PREFIX):])
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _valid_trusted_event_audit(events: list[dict[str, Any]], audit: Any, run_id: str) -> bool:
    checks = [event for event in events if event.get("event") == "outcome"
              and event.get("check") == "event.audit"]
    fields = {
        "contract", "run_id", "request_count", "completed_count", "accepted_event_count",
        "rejected_event_count", "protocol_errors", "passed",
    }
    return (
        isinstance(audit, dict) and set(audit) == fields
        and audit.get("contract") == "trusted_event_audit_v1" and audit.get("run_id") == run_id
        and type(audit.get("request_count")) is int and audit["request_count"] > 0
        and type(audit.get("completed_count")) is int and audit["completed_count"] == audit["request_count"]
        and type(audit.get("accepted_event_count")) is int and audit["accepted_event_count"] >= 0
        and type(audit.get("rejected_event_count")) is int and audit["rejected_event_count"] == 0
        and audit.get("protocol_errors") == [] and audit.get("passed") is True
        and len(checks) == 1 and checks[0].get("status") == "passed"
        and checks[0].get("details") == {key: value for key, value in audit.items() if key != "passed"}
    )


def _child_result(run: _ChildRun, *, mongo_uri: str, timeout_seconds: float, started: float) -> dict[str, Any]:
    events = _events(run.stdout)
    outcomes = [{key: value for key, value in event.items() if key not in {"event", "observer_nonce"}} for event in events
                if event.get("event") == "outcome"]
    done = next((event for event in reversed(events) if event.get("event") == "done"), None)
    activity = next((event for event in reversed(events) if event.get("event") in {"calling", "step"}), None)
    where, path = "", None
    if activity is not None and activity.get("event") == "calling":
        where = f" while {activity['module']}.{activity['action']} was running as user {activity['principal']}"
        path = f"modules/{activity['module']}/backend/service.py"
    elif activity is not None:
        where = f" during {activity['name']}"
    if run.timed_out:
        outcomes.append({
            "check": "smoke.timeout", "status": "failed", "path": path,
            "message": f"The runtime smoke was stopped at its {timeout_seconds:.0f}s limit{where}. Generated code "
                       "must not block or wait on unreachable services when its actions are called.",
        })
    elif done is None or (run.contained and run.returncode != 0):
        tail = " ".join(run.stderr.strip().splitlines()[-3:])[-600:]
        phase = "before finishing" if done is None else "after reporting completion"
        outcomes.append({
            "check": "smoke.process", "status": "failed", "path": path,
            "message": f"The runtime smoke process exited with code {run.returncode}{where} {phase}: "
                       f"{tail or 'no output'}",
        })
    if run.app_exited:
        outcomes.append({
            "check": "smoke.process", "status": "failed", "path": None,
            "message": "The imported app container exited before the external observer finished.",
        })
    if run.cleanup_failed:
        outcomes.append({
            "check": "smoke.cleanup", "status": "failed", "path": None,
            "message": "The contained runtime smoke could not confirm Docker container removal.",
        })
    if run.output_truncated:
        outcomes.append({
            "check": "smoke.output", "status": "failed", "path": None,
            "message": "The contained runtime smoke exceeded its bounded output limit.",
        })
    for outcome in outcomes:
        outcome["message"] = _redact(str(outcome.get("message") or ""), mongo_uri)
    return _summary(outcomes, done or {}, started=started)


# --------------------------------------------------------------------------- result records


@dataclass
class _Outcome:
    check: str
    passed: bool | None  # None: the check had nothing to act on and did not run
    message: str
    path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": "not_run" if self.passed is None else "passed" if self.passed else "failed",
            "message": self.message,
            "path": self.path,
            **({"details": self.details} if self.details else {}),
        }


@dataclass(frozen=True)
class _Principal:
    label: str
    user_id: str
    token: str
    workspace_id: str | None
    extra_scopes: tuple[str, ...] = ()


@dataclass
class _Entity:
    module: str
    collection: dict[str, Any]
    entity: str
    id_field: str
    owner_field: str | None
    tenancy: str
    actions: dict[str, str]

    @property
    def label(self) -> str:
        return f"{self.module}.{self.collection.get('name')}"

    @property
    def lookup_field(self) -> str:
        from mozaiksai.core.workflow.generator_support.data_contract_fields import read_lookup_field

        return read_lookup_field(self.collection)


class _RunLogCapture(logging.Handler):
    """Keep the runtime's warning and error records (the child runs one smoke)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _skipped(reason: str, *, started: float) -> dict[str, Any]:
    message = f"Runtime smoke skipped: {reason}. Boot, two-user CRUD and entitlement checks did not run."
    return {
        "contract_version": SMOKE_CONTRACT_VERSION,
        "status": "skipped",
        "passed": None,
        "skipped_reason": reason,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "results": [],
        "checks": [{
            "id": "app_runtime_smoke",
            "passed": None,
            "status": "skipped",
            "message": message,
            "details": {"status": "skipped", "skipped_reason": reason, "blocking": False},
        }],
        "failed_tests": [],
        "warnings": [message],
    }


def _contained_unavailable(reason: str, *, started: float) -> dict[str, Any]:
    result = _skipped(reason, started=started)
    result["checks"][0]["details"]["blocking"] = True
    return result


def _imported_observer_scope(result: dict[str, Any]) -> dict[str, Any]:
    # A rejected ctx.emit leaves no HTTP or Mongo trace. A's in-process
    # rejection list cannot be evidence for the separate trusted observer.
    result["observer_unverified_checks"] = ["event_rejection"]
    return result


def _encode_event_payload(payload: dict[str, Any]) -> str:
    """BSON preserves the runtime value types that JSON would silently coerce."""
    from bson import BSON

    encoded = BSON.encode(payload)
    if len(encoded) > 700_000:
        raise ValueError("event payload exceeds the trusted observer limit")
    return base64.b64encode(encoded).decode("ascii")


def _decode_event_payload(encoded: Any) -> dict[str, Any]:
    from datetime import UTC

    from bson import BSON
    from bson.codec_options import CodecOptions

    if not isinstance(encoded, str) or len(encoded) > 950_000:
        raise ValueError("event payload is absent or oversized")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > 700_000:
        raise ValueError("event payload exceeds the trusted observer limit")
    return BSON(raw).decode(codec_options=CodecOptions(tz_aware=True, tzinfo=UTC))


class _TrustedEventAudit:
    """B-owned action ledger and canonical event validator; never imports app Python."""

    _MAX_ACTION_REQUESTS = 5000
    _MAX_EVENT_REQUESTS = 1000
    _MAX_PROTOCOL_ERRORS = 32

    def __init__(self, run_id: str, modules: list[Any], event_schemas: Mapping[str, Mapping[str, Any]]) -> None:
        from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor

        self.run_id = run_id
        self.validator = ModuleExecutor()
        for module in modules:
            self.validator.register(
                module.name, object(), action_method_map=module.action_method_map,
                action_emits=module.action_emits_map,
                event_payload_schemas=dict(event_schemas.get(module.name) or {}),
            )
        self.requests: dict[str, dict[str, Any]] = {}
        self.event_ids: set[str] = set()
        self.protocol_errors: list[str] = []
        self.rejections: list[tuple[str, str, str, Any]] = []
        self.accepted_events = 0

    def begin(self, module: str, action: str, principal_label: str) -> str:
        if len(self.requests) >= self._MAX_ACTION_REQUESTS:
            self._protocol_error("action request limit exceeded")
            raise RuntimeError("trusted observer action request limit exceeded")
        request_id = uuid4().hex
        self.requests[request_id] = {
            "module": module, "action": action, "principal_label": principal_label, "completed": False,
        }
        return request_id

    def _protocol_error(self, message: str) -> None:
        if len(self.protocol_errors) < self._MAX_PROTOCOL_ERRORS:
            self.protocol_errors.append(message)

    def observe(self, packet: Any) -> dict[str, Any]:
        """Count every message, including malformed/replayed ones, as evidence of failure."""
        from bson.errors import InvalidBSON

        from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
        from mozaiksai.core.runtime.composition.module_executor import ModuleRequest

        if not isinstance(packet, dict) or set(packet) != {
            "run_id", "request_id", "event_id", "module", "action", "event_type", "payload_bson",
        }:
            self._protocol_error("malformed event request")
            return {"accepted": False}
        request_id = packet["request_id"]
        row = self.requests.get(request_id) if isinstance(request_id, str) else None
        if (packet["run_id"] != self.run_id or row is None or row["completed"]
                or packet["module"] != row["module"] or packet["action"] != row["action"]):
            self._protocol_error("event request is outside its active action")
            return {"accepted": False}
        event_id = packet["event_id"]
        event_type = packet["event_type"]
        if (not isinstance(event_id, str) or not event_id.startswith("evt_")
                or len(event_id) != 36 or event_id in self.event_ids
                or not isinstance(event_type, str)):
            self._protocol_error("event request has invalid or replayed content")
            return {"accepted": False}
        try:
            payload = _decode_event_payload(packet["payload_bson"])
        except (TypeError, ValueError, InvalidBSON):
            self._protocol_error("event payload cannot be decoded")
            return {"accepted": False}
        if len(self.event_ids) >= self._MAX_EVENT_REQUESTS:
            self._protocol_error("event request limit exceeded")
            return {"accepted": False}
        self.event_ids.add(event_id)
        request = ModuleRequest(
            module=row["module"], action=row["action"], app_id="runtime-smoke-observer",
            authority=ModuleDispatchAuthority(
                kind="authenticated_user", permission_mode="enforce", reason="trusted event validation",
            ),
        )
        rejection = self.validator._event_rejection(request, event_id, event_type, payload)
        if rejection is not None:
            self.rejections.append((row["module"], row["action"], row["principal_label"], rejection))
            return {"accepted": False, "rejection": rejection.to_dict()}
        self.accepted_events += 1
        return {"accepted": True}

    def complete(self, request_id: str, *, received_response: bool) -> None:
        row = self.requests.get(request_id)
        if row is None or row["completed"]:
            self._protocol_error("action completion is absent or duplicated")
            return
        if not received_response:
            self._protocol_error("action response is absent")
            return
        row["completed"] = True

    def result(self) -> dict[str, Any]:
        completed = sum(row["completed"] for row in self.requests.values())
        return {
            "contract": "trusted_event_audit_v1", "run_id": self.run_id,
            "request_count": len(self.requests), "completed_count": completed,
            "accepted_event_count": self.accepted_events,
            "rejected_event_count": len(self.rejections),
            "protocol_errors": list(self.protocol_errors),
            "passed": bool(self.requests) and completed == len(self.requests)
            and not self.rejections and not self.protocol_errors,
        }


# --------------------------------------------------------------------------- the run


class _SmokeRun:
    def __init__(self, app_root: Path, client: Any, database_name: str, emit: Callable[..., None]) -> None:
        self.app_root = app_root
        self.client = client
        self.database_name = database_name
        self.emit = emit
        self.outcomes: list[_Outcome] = []
        self.capture = _RunLogCapture()
        self.app: Any = None
        self.load: Any = None
        self.app_id = "mozaiks-runtime-smoke"
        self.scopes: list[str] = []
        self.principals: dict[str, _Principal] = {}
        self.events: list[str] = []
        self.http: Any = None
        self.workspace_claims = False
        self.last_exception_path: str | None = None
        self.permission_denials: set[tuple[str, str]] = set()
        # (module, action, rejection) for every emitted event the runtime rejected.
        self.rejected_events: list[tuple[str, str, Any]] = []
        self.reported_rejections: set[tuple[str, str, str, str, str]] = set()
        self.external_server = False
        self.external_probe = False
        self.probe_nonce = ""
        self.event_audit: _TrustedEventAudit | None = None

    @property
    def database(self) -> Any:
        return self.client[self.database_name]

    def record(self, check: str, passed: bool, message: str, *, path: str | None = None, **details: Any) -> bool:
        outcome = _Outcome(check, passed, message, path, details)
        self.outcomes.append(outcome)
        self.emit("outcome", **outcome.to_dict())
        return passed

    def not_run(self, check: str, reason: str) -> None:
        outcome = _Outcome(check, None, f"Not run: {reason}")
        self.outcomes.append(outcome)
        self.emit("outcome", **outcome.to_dict())

    def step(self, name: str) -> None:
        self.emit("step", name=name)

    async def record_event(self, event_type: str, envelope: dict[str, Any]) -> None:
        self.events.append(str(event_type))

    def principal(self, label: str) -> _Principal:
        if label not in self.principals:
            slug = "".join(char if char.isalnum() else "-" for char in label.lower()).strip("-")
            self.principals[label] = _Principal(
                label=label,
                user_id=f"smoke-{slug}",
                token=f"smoke-{slug}-{uuid4().hex}",
                workspace_id=f"smoke-workspace-{slug}" if self.workspace_claims else None,
            )
        return self.principals[label]

    # ---------------------------------------------------------------- failure detail

    def _exception_detail(self, since: int) -> tuple[str | None, str | None]:
        """The last captured action exception after ``since``: text and bundle-relative file."""
        for record in reversed(self.capture.records[since:]):
            if not record.exc_info or not record.exc_info[1]:
                continue
            exc = record.exc_info[1]
            text = f"{type(exc).__name__}: {exc}"
            path = None
            root = str(self.app_root.resolve())
            for frame in reversed(traceback.extract_tb(record.exc_info[2])):
                filename = str(Path(frame.filename).resolve())
                if filename.startswith(root):
                    path = PurePosixPath(Path(filename).relative_to(root)).as_posix()
                    text += f" (at {path}:{frame.lineno})"
                    break
            return text, path
        return None, None

    def _with_scopes(self, principal: _Principal, scopes: list[str]) -> _Principal:
        label = f"{principal.label} +{','.join(scopes)}"
        if label not in self.principals:
            self.principals[label] = _Principal(
                label=label, user_id=principal.user_id, token=f"{principal.token}-{uuid4().hex[:8]}",
                workspace_id=principal.workspace_id, extra_scopes=tuple(scopes),
            )
        return self.principals[label]

    async def call(
        self, method: str, module: str, action: str, principal: _Principal, params: dict[str, Any],
    ) -> tuple[int, Any, str]:
        """Call one module action as ``principal``; return status, JSON body and a one-line summary.

        A permission module.yaml requires but config/auth.yaml never grants
        denies every signed-in user. That is recorded once per action as its
        own failure, and the call is repeated for the same user with exactly
        the missing permissions granted, so the defects behind the permission
        wall are reported in the same pass.
        """
        loaded = next((item for item in self.load.modules if item.name == module), None)
        required = list((loaded.action_permissions_map.get(action) or []) if loaded is not None else [])
        missing = [item for item in required if item not in self.scopes]
        if missing and (module, action) in self.permission_denials:
            principal = self._with_scopes(principal, missing)
        status, body, summary = await self._request(method, module, action, principal, params)
        if missing and status == 403 and (module, action) not in self.permission_denials:
            self.permission_denials.add((module, action))
            self.record(
                f"permission.{module}.{action}", False,
                f"{module}.{action} requires permissions {missing} that no signed-in user holds: config/auth.yaml "
                f"grants token scopes {self.scopes} and declares no roles, so every call is denied ({summary}). "
                "The remaining checks repeat such calls with those permissions granted to test the action itself.",
                path=f"modules/{module}/module.yaml",
            )
            status, body, summary = await self._request(method, module, action, self._with_scopes(principal, missing), params)
        return status, body, summary

    def _record_rejected_event(self, module: str, action: str, principal: _Principal, rejection: Any) -> None:
        """An emitted event the runtime rejected fails its own check, once per action, event and reason.

        The action's response cannot show it: the runtime reports an action
        whose handler completed as succeeded and only names the rejected event
        on the dispatch result. No reaction or notification runs for it.
        """
        key = (module, action, rejection.event_type, rejection.category, rejection.reason)
        if key in self.reported_rejections:
            return
        self.reported_rejections.add(key)
        if rejection.category == "undeclared":
            problem = f"which {module}.{action} does not declare in its module.yaml emits"
            path = f"modules/{module}/module.yaml"
        elif rejection.category == "schema_invalid":
            problem = f"whose payload_schema in contracts/events.yaml cannot be evaluated ({rejection.reason})"
            path = f"modules/{module}/contracts/events.yaml"
        else:
            problem = (f"whose payload does not satisfy the payload_schema contracts/events.yaml declares for it "
                       f"(at {rejection.schema_path}: {rejection.reason})")
            path = f"modules/{module}/backend/service.py"
        self.record(
            f"event.{module}.{action}.{rejection.event_type}", False,
            f"{module}.{action} as user {principal.label} emitted {rejection.event_type} (event {rejection.event_id}) "
            f"{problem}. The runtime did not dispatch it, so no reaction or notification ran for it, although the "
            "action itself succeeded.",
            path=path,
        )

    async def _request(
        self, method: str, module: str, action: str, principal: _Principal, params: dict[str, Any],
    ) -> tuple[int, Any, str]:
        since = len(self.capture.records)
        rejected_since = len(self.rejected_events)
        self.emit("calling", module=module, action=action, principal=principal.label)
        url = f"/api/modules/{module}/{action}"
        request_id = self.event_audit.begin(module, action, principal.label) if self.event_audit is not None else None
        headers = {"Authorization": f"Bearer {request_id or principal.token}"}
        if self.external_probe:
            assertion = {
                "nonce": self.probe_nonce, "user_id": principal.user_id,
                "workspace_id": principal.workspace_id, "label": principal.label,
                "extra_scopes": list(principal.extra_scopes),
            }
            headers["X-Mozaiks-Smoke-Principal"] = base64.urlsafe_b64encode(
                json.dumps(assertion, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
        received_response = False
        try:
            if method == "GET":
                request = self.http.get(url, params={key: str(value) for key, value in params.items()}, headers=headers)
            else:
                request = self.http.post(url, json=params, headers=headers)
            response = await asyncio.wait_for(request, timeout=_REQUEST_TIMEOUT_SECONDS)
            received_response = True
        except (TimeoutError, TimeoutException):
            return 0, None, f"no response within {_REQUEST_TIMEOUT_SECONDS:.0f}s"
        finally:
            if request_id is not None:
                assert self.event_audit is not None
                self.event_audit.complete(request_id, received_response=received_response)
            for rejected_module, rejected_action, rejection in self.rejected_events[rejected_since:]:
                self._record_rejected_event(rejected_module, rejected_action, principal, rejection)
        try:
            body = response.json()
        except ValueError:
            body = response.text[:300]
        summary = f"{response.status_code}"
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            summary += f" {detail.get('error_code') or ''}: {detail.get('error') or ''}".rstrip(": ")
        elif isinstance(detail, str):
            summary += f": {detail}"
        elif isinstance(detail, list) and detail and isinstance(detail[0], Mapping):
            summary += f": {detail[0].get('msg')} at {detail[0].get('loc')}"
        self.last_exception_path = None
        if response.status_code >= 500:
            exception, self.last_exception_path = self._exception_detail(since)
            if exception:
                summary += f"; {exception}"
        return response.status_code, body, summary


# --------------------------------------------------------------------------- composition


def _app_id(load: Any) -> str:
    contract = load.data_contract or {}
    config = getattr(load.definition, "config", None) or {}
    return str(contract.get("app_id") or config.get("appId") or config.get("app_id") or "mozaiks-runtime-smoke")


def _declared_extensions(modules: list[Any], kind: str) -> list[tuple[str, str]]:
    declared: list[tuple[str, str]] = []
    for module in modules:
        manifests = getattr(module, "manifests", None)
        extensions = getattr(manifests, "runtime_extensions", None) if manifests is not None else None
        for extension in getattr(extensions, "extensions", None) or []:
            if extension.kind == kind:
                declared.append((module.name, extension.entrypoint))
    return declared


def _alias_collection_name(alias: str, contract: Mapping[str, Any] | None) -> str:
    from mozaiksai.core.runtime.persistence.app_data import collection_name_for_alias

    return collection_name_for_alias(alias, contract=contract or {})


async def _boot(run: _SmokeRun, initial_environment: set[str]) -> bool:
    """Compose the generated app the way the platform host does. False when nothing can be called."""
    from httpx import ASGITransport, AsyncClient

    from mozaiksai.core.audit.audit_logger import AuditLogger, AuditRecord
    from mozaiksai.core.auth import optional_user
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
    from mozaiksai.core.runtime.app.loader import AppLoader
    from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
    from mozaiksai.core.runtime.composition.extensions import mount_module_routers
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
    from mozaiksai.core.runtime.persistence import (
        MongoPersistenceContext,
        PersistencePrincipal,
        apply_data_migrations,
        apply_database_indexes,
        load_data_migrations,
    )
    from mozaiksai.core.workflow.generator_support.module_write_actions import auth_contract_scopes
    from mozaiksai.hosts.routers import modules as module_router

    gained = sorted(set(os.environ) - initial_environment)
    if gained:
        run.record(
            "smoke.environment", False,
            f"Loading the Mozaiks runtime added environment variables {gained} to the smoke process (a .env "
            "file was read), so generated code was not run: it must never see host configuration.",
        )
        return False
    run.step("boot.app_load")
    try:
        load = await asyncio.wait_for(AppLoader.load(str(run.app_root)), timeout=_STEP_TIMEOUT_SECONDS)
    except Exception as exc:
        message = str(exc).replace(str(run.app_root), "app")
        run.record("boot.app_load", False, f"AppLoader.load() failed, so the app cannot start: {message}")
        return False
    run.load = load
    run.app_id = _app_id(load)
    if load.failed_module_names:
        for name in sorted(load.failed_module_names):
            error = (load.module_load_errors.get(name) or "module failed to load").replace(str(run.app_root), "app")
            run.record(
                "boot.app_load", False,
                f"Module {name!r} failed to load at startup; every call to it returns 503: {error}",
                path=f"modules/{name}/module.yaml",
            )
    else:
        run.record("boot.app_load", True, f"AppLoader loaded {len(load.modules)} module(s).")

    contract = load.data_contract
    from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections

    run.workspace_claims = any(
        collection.get("tenancy") == "per_workspace"
        for _owner, _kind, collection in iter_data_contract_collections(contract, require_complete_ownership=False)
    )

    def persistence() -> Any:
        return MongoPersistenceContext(app_id=run.app_id, database_name=run.database_name, client=run.client)

    if contract:
        run.step("boot.indexes")
        try:
            result = await asyncio.wait_for(
                apply_database_indexes(contract, app_id=run.app_id, persistence=persistence()),
                timeout=_STEP_TIMEOUT_SECONDS,
            )
            run.record("boot.indexes", True, f"{result.verified} declared index(es) created and verified.")
        except Exception as exc:
            run.record(
                "boot.indexes", False,
                f"Declared database indexes could not be applied, so the platform host refuses to start "
                f"with persistence enabled: {exc}",
                path="data/contract.json",
            )
    run.step("boot.migrations")
    try:
        migrations = load_data_migrations(run.app_root)
        if migrations:
            count = await asyncio.wait_for(
                apply_data_migrations(
                    app_id=run.app_id, migrations=migrations, persistence=persistence(),
                    history_client=run.client, history_database=run.database_name,
                ),
                timeout=_STEP_TIMEOUT_SECONDS,
            )
            run.record("boot.migrations", True, f"{count} data migration(s) applied.")
    except Exception as exc:
        run.record(
            "boot.migrations", False,
            f"Data migrations could not be applied at startup: {exc}",
            path="data/migrations/*.json",
        )

    class _RecordingAuditLogger(AuditLogger):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[AuditRecord] = []

        async def log(self, record: AuditRecord) -> None:
            self.records.append(record)

    def alias_collection(alias: str) -> Any:
        return run.database[_alias_collection_name(alias, contract)]

    class _ObservedModuleExecutor(ModuleExecutor):
        """Keeps the events each dispatch rejected; the HTTP response carries only the action's data."""

        def _build_context_emitter(self, request: Any, rejected_events: list[Any]) -> Any:
            if not run.external_server:
                return super()._build_context_emitter(request, rejected_events)

            async def emit_to_trusted_probe(event_type: str, payload: dict[str, Any]) -> Any:
                from httpx import AsyncClient

                from mozaiksai.core.runtime.composition.module_event_provenance import (
                    ModuleEventRejection,
                )

                packet = {
                    "run_id": run.probe_nonce, "request_id": request.auth_token,
                    "event_id": f"evt_{uuid4().hex}", "module": request.module,
                    "action": request.action, "event_type": str(event_type or "").strip(),
                    "payload_bson": _encode_event_payload(payload),
                }
                async with AsyncClient(base_url="http://127.0.0.1:8001", timeout=_REQUEST_TIMEOUT_SECONDS,
                                       trust_env=False) as client:
                    response = await client.post("/__mozaiks_smoke_event", json=packet)
                response.raise_for_status()
                decision = response.json()
                if decision == {"accepted": True}:
                    return None
                rejection = decision.get("rejection") if isinstance(decision, dict) else None
                if not isinstance(rejection, dict):
                    raise RuntimeError("trusted smoke event observer refused the request")
                item = ModuleEventRejection(**rejection)
                rejected_events.append(item)
                return item

            return emit_to_trusted_probe

        async def execute(self, request: Any, context: Any = None) -> Any:
            result = await super().execute(request, context)
            run.rejected_events.extend((request.module, request.action, item) for item in result.rejected_events)
            return result

    hooks = PlatformHookRegistry()
    executor = _ObservedModuleExecutor(
        event_emitter=run.record_event,
        entitlement_checker=(
            ConfiguredEntitlementAdapter(config=load.subscriptions_config, collection_resolver=alias_collection)
            if load.subscriptions_config is not None else None
        ),
        data_contract=contract,
        platform_hooks=hooks,
        audit_logger=_RecordingAuditLogger(),
        persistence_database=run.database_name,
        persistence_client=run.client,
    )
    for loaded_module in load.modules:
        executor.register_loaded_module(loaded_module)
    registry = ExecutorRegistry()
    registry.register(executor)

    app = FastAPI()
    app.state.executor_registry = registry
    app.state.module_action_surfaces = {module.name: module.action_api_surface_map for module in load.modules}
    app.state.failed_module_names = sorted(load.failed_module_names)
    app.state.data_contract = contract
    app.include_router(module_router.router)
    auth_yaml = run.app_root / "config" / "auth.yaml"
    run.scopes = sorted(auth_contract_scopes(
        {"config/auth.yaml": auth_yaml.read_text(encoding="utf-8")} if auth_yaml.is_file() else {}
    ))

    async def resolve_principal(request: Request) -> UserPrincipal | None:
        principal: _Principal | None
        if run.external_server:
            encoded = request.headers.get("x-mozaiks-smoke-principal") or ""
            if not encoded or len(encoded) > 4096:
                return None
            try:
                data = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
                if (not isinstance(data, dict) or data.get("nonce") != run.probe_nonce
                        or not isinstance(data.get("user_id"), str)
                        or not isinstance(data.get("label"), str)
                        or not isinstance(data.get("extra_scopes"), list)
                        or not all(isinstance(scope, str) for scope in data["extra_scopes"])
                        or data.get("workspace_id") is not None
                        and not isinstance(data["workspace_id"], str)):
                    return None
                principal = _Principal(data["label"], data["user_id"], "",
                                       data.get("workspace_id"), tuple(data["extra_scopes"]))
            except (ValueError, UnicodeError):
                return None
        else:
            header = request.headers.get("authorization") or ""
            token = header[7:].strip() if header.lower().startswith("bearer ") else ""
            principal = next((item for item in run.principals.values() if item.token == token), None)
        if principal is None:
            return None
        return UserPrincipal(
            user_id=principal.user_id, email=None, name=principal.label, roles=[],
            scopes=[*run.scopes, *principal.extra_scopes],
            raw_claims={"sub": principal.user_id, "app_id": run.app_id}, provider="runtime_smoke",
            app_id=run.app_id, workspace_id=principal.workspace_id, auth_provenance="token_validated",
        )

    def authenticated_persistence_principal(principal: UserPrincipal | None) -> PersistencePrincipal | None:
        if principal is None:
            return None
        return PersistencePrincipal(user_id=principal.user_id, workspace_id=principal.workspace_id)

    environment = module_router.ModuleDispatchEnvironment(
        authentication_enabled=True,
        platform_hooks=hooks,
        record_invocation=lambda **_: None,
        persistence_principal=authenticated_persistence_principal,
    )
    app.dependency_overrides[optional_user] = resolve_principal
    app.dependency_overrides[module_router.module_dispatch_environment] = lambda: environment
    if run.external_server:
        @app.get("/__mozaiks_smoke_ready")
        async def smoke_ready() -> dict[str, Any]:
            return {
                "app_id": run.app_id,
                "modules": sorted(module.name for module in load.modules),
                "failed_modules": sorted(load.failed_module_names),
            }

    declared_routers = _declared_extensions(load.modules, "api_router")
    if declared_routers:
        since = len(run.capture.records)
        mounted = mount_module_routers(app, load.modules)
        if mounted < len(declared_routers):
            failures = [record.getMessage() for record in run.capture.records[since:]
                        if "MODULE_EXTENSIONS" in record.getMessage()]
            run.record(
                "boot.runtime_extensions", False,
                f"{len(declared_routers) - mounted} of {len(declared_routers)} declared api_router "
                f"extension(s) did not mount: {'; '.join(failures) or 'no router returned'}",
                path=f"modules/{declared_routers[0][0]}/runtime_extensions.yaml",
            )
        else:
            run.record("boot.runtime_extensions", True, f"{mounted} api_router extension(s) mounted.")
    declared_services = _declared_extensions(load.modules, "startup_service")
    if declared_services:
        modules = sorted({name for name, _ in declared_services})
        run.not_run(
            "boot.startup_services",
            f"{len(declared_services)} startup_service extension(s) declared by {modules} are not started. They "
            "run outside module dispatch with their own clients, so the smoke cannot keep them inside its "
            "disposable database; only the deployed app starts them.",
        )

    run.app = app
    run.http = AsyncClient(transport=ASGITransport(app=app), base_url="http://runtime-smoke")
    return bool(load.modules)


# --------------------------------------------------------------------------- synthetic inputs


def _schema_type(schema: Mapping[str, Any]) -> str | None:
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), None)
    return kind if isinstance(kind, str) else None


def _input_schema(module: Any, action: str) -> dict[str, Any]:
    schemas = module.action_schemas_map.get(action) or {}
    schema = schemas.get("input") if isinstance(schemas, Mapping) else None
    return dict(schema) if isinstance(schema, Mapping) else {}


def _properties(schema: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return {}
    return {str(name): prop if isinstance(prop, Mapping) else {} for name, prop in properties.items()}


def _fields(entity: _Entity) -> dict[str, Mapping[str, Any]]:
    return {str(item.get("name")): item for item in entity.collection.get("fields") or [] if isinstance(item, Mapping)}


def _enum(prop: Mapping[str, Any], contract_field: Mapping[str, Any] | None) -> list[Any]:
    values = (contract_field or {}).get("enum") or prop.get("enum") or []
    return [item for item in values if item is not None]


def _kind(prop: Mapping[str, Any], contract_field: Mapping[str, Any] | None) -> str | None:
    kind = (contract_field or {}).get("type")
    return kind if isinstance(kind, str) else _schema_type(prop)


def _synthetic_value(name: str, prop: Mapping[str, Any], contract_field: Mapping[str, Any] | None) -> Any:
    """A valid value for one declared input: the first enum value, else one fixed value per type."""
    enum = _enum(prop, contract_field)
    if enum:
        return enum[0]
    kind = _kind(prop, contract_field)
    if kind == "datetime" or prop.get("format") == "date-time":
        return "2026-01-15T12:00:00+00:00"
    fixed: dict[str | None, Any] = {"date": "2026-01-15", "integer": 1, "number": 1, "boolean": True, "array": [], "object": {}}
    return fixed[kind] if kind in fixed else f"smoke {name}"


def _required_params(schema: Mapping[str, Any], fields: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    properties = _properties(schema)
    return {
        str(name): _synthetic_value(str(name), properties.get(str(name), {}), fields.get(str(name)))
        for name in schema.get("required") or []
    }


def _changed_value(current: Any, prop: Mapping[str, Any], contract_field: Mapping[str, Any] | None, label: str) -> Any:
    enum = _enum(prop, contract_field)
    if enum:
        return next((item for item in enum if item != current), None)
    kind = _kind(prop, contract_field)
    if kind == "string":
        return f"updated by {label}"
    if kind == "boolean":
        return not bool(current)
    if kind in {"integer", "number"}:
        return (current if isinstance(current, (int, float)) else 0) + 1
    return None


def _update_field(update_schema: Mapping[str, Any], entity: _Entity) -> str | None:
    """The first writable field whose change is observable: free text, then enum, boolean, number."""
    fields = _fields(entity)
    ranked: list[tuple[int, int, str]] = []
    for index, (name, prop) in enumerate(_properties(update_schema).items()):
        if name in {entity.id_field, entity.owner_field}:
            continue
        enum, kind = _enum(prop, fields.get(name)), _kind(prop, fields.get(name))
        rank = (
            0 if kind == "string" and not enum else
            1 if len(enum) > 1 else
            2 if kind == "boolean" else
            3 if kind in {"integer", "number"} else None
        )
        if rank is not None:
            ranked.append((rank, index, name))
    return min(ranked)[2] if ranked else None


# --------------------------------------------------------------------------- contract derivations


def _contract_entities(run: _SmokeRun) -> list[_Entity]:
    from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
    from mozaiksai.core.workflow.generator_support.data_contract_fields import record_id_field
    from mozaiksai.core.workflow.generator_support.module_action_inventory import (
        canonical_read_action_id,
        canonical_write_action_id,
    )

    modules = {module.name: module for module in run.load.modules}
    entities: list[_Entity] = []
    for owner, kind, collection in iter_data_contract_collections(run.load.data_contract, require_complete_ownership=False):
        module = modules.get(owner)
        if kind != "module" or module is None:
            continue
        entity_name = str(collection.get("entity") or "")
        try:
            candidates = {op: canonical_write_action_id(entity_name, op) for op in ("create", "update", "delete")}
            candidates.update({op: canonical_read_action_id(str(collection.get("name") or ""), op) for op in ("list", "get")})
        except ValueError:
            continue
        actions = {op: action for op, action in candidates.items() if action in module.action_method_map}
        if "create" not in actions:
            continue
        names = [str(item.get("name")) for item in collection.get("fields") or [] if isinstance(item, Mapping)]
        entities.append(_Entity(
            module=owner, collection=dict(collection), entity=entity_name,
            id_field=record_id_field(collection, names), owner_field=collection.get("owner_field"),
            tenancy=str(collection.get("tenancy") or ""), actions=actions,
        ))
    return entities


@dataclass(frozen=True)
class _PlanStore:
    store: Any
    plans: list[Any]
    default_plan_id: str | None


def _plan_stores(config: Any) -> list[_PlanStore]:
    if config is None:
        return []
    if config.products:
        return [_PlanStore(product.assignment_store, list(product.plans), product.default_plan_id) for product in config.products]
    return [_PlanStore(config.assignment_store, list(config.plans), config.default_plan_id)]


def _default_capabilities(config: Any) -> frozenset[str]:
    granted: set[str] = set()
    for item in _plan_stores(config):
        for plan in item.plans:
            if plan.plan_id == item.default_plan_id:
                granted.update(plan.capabilities or [])
    return frozenset(granted)


def _granting_plan(config: Any, capabilities: set[str]) -> tuple[_PlanStore, Any] | None:
    """The first declared plan granting every capability, else the one granting the most."""
    best: tuple[int, _PlanStore, Any] | None = None
    for item in _plan_stores(config):
        for plan in item.plans:
            covered = len(capabilities & set(plan.capabilities or []))
            if covered and (best is None or covered > best[0]):
                best = (covered, item, plan)
    return (best[1], best[2]) if best else None


def _assignment_store_defect(run: _SmokeRun, item: _PlanStore, plan: Any) -> tuple[str, str] | None:
    """Why no user can be granted ``plan`` through the declared assignment store, if anything."""
    store = item.store
    if store is None:
        return (
            f"Plan {plan.plan_id!r} grants capabilities but config/subscriptions.yaml declares no assignment_store, "
            "so no user can ever hold it.",
            "config/subscriptions.yaml",
        )
    if not store.user_id_field:
        return (
            f"assignment_store keys subscriptions by {store.app_id_field!r}/{store.tenant_id_field!r} without a "
            "user_id_field, so one assignment entitles every user of the app; per-user plans cannot be granted.",
            "config/subscriptions.yaml",
        )
    if not [status for status in store.active_statuses or [] if status]:
        return (
            "assignment_store.active_statuses is empty, so no assignment can ever grant a capability.",
            "config/subscriptions.yaml",
        )
    try:
        _alias_collection_name(store.data_alias, run.load.data_contract)
    except KeyError as exc:
        return (
            f"config/subscriptions.yaml assignment_store.data_alias {store.data_alias!r} is not declared in "
            f"data/contract.json aliases, so the entitlement adapter cannot read any assignment and every paid "
            f"capability is denied ({exc.args[0] if exc.args else exc}).",
            "data/contract.json",
        )
    return None


async def _seed_assignment(run: _SmokeRun, principal: _Principal, item: _PlanStore, plan: Any) -> bool:
    """Write one active assignment through the declared assignment_store data alias.

    A store that cannot hold the assignment is recorded once per defect as
    ``subscriptions.assignment_store``; the calls that follow still run and
    show what a subscriber of that plan actually gets.
    """
    defect = _assignment_store_defect(run, item, plan)
    if defect is not None:
        message, path = defect
        if not any(outcome.message == message for outcome in run.outcomes):
            run.record("subscriptions.assignment_store", False, message, path=path)
        return False
    store = item.store
    record = {
        store.app_id_field: run.app_id,
        store.user_id_field: principal.user_id,
        store.plan_id_field: plan.plan_id,
        store.status_field: next(status for status in store.active_statuses if status),
    }
    if store.tenant_id_field:
        record[store.tenant_id_field] = None
    if store.workspace_id_field:
        record[store.workspace_id_field] = None
    await run.database[_alias_collection_name(store.data_alias, run.load.data_contract)].insert_one(record)
    return True


# --------------------------------------------------------------------------- two-user CRUD


def _owner_value(entity: _Entity, principal: _Principal) -> str | None:
    return principal.workspace_id if entity.tenancy == "per_workspace" else principal.user_id


def _items(body: Any) -> list[Any] | None:
    if isinstance(body, Mapping) and isinstance(body.get("items"), list):
        return list(body["items"])
    return None


def _record_id(entity: _Entity, body: Any) -> Any:
    if not isinstance(body, Mapping):
        return None
    item = body.get("item")
    if isinstance(item, Mapping) and item.get(entity.id_field) is not None:
        return item.get(entity.id_field)
    return body.get(entity.id_field)


def _lookup_value(entity: _Entity, stored: Mapping[str, Any] | None) -> str | None:
    """Use the stored canonical get key; it need not be the generated write id."""
    value = stored.get(entity.lookup_field) if stored is not None else None
    return str(value) if value is not None else None


async def _stored(run: _SmokeRun, entity: _Entity, record_id: Any) -> dict[str, Any] | None:
    from mozaiksai.core.runtime.persistence import MongoPersistenceContext

    names = MongoPersistenceContext(
        app_id=run.app_id, database_name=run.database_name, client=run.client, data_contract=run.load.data_contract,
    )
    try:
        collection_name = names.collection_name(entity.module, str(entity.collection.get("name")))
    except Exception:
        return None
    values: list[Any] = [record_id]
    if entity.id_field == "_id":
        from bson import ObjectId

        if ObjectId.is_valid(str(record_id)):
            values.append(ObjectId(str(record_id)))
    document = await run.database[collection_name].find_one({entity.id_field: {"$in": values}})
    return dict(document) if document else None


async def _stored_fields(run: _SmokeRun, entity: _Entity) -> list[str]:
    from mozaiksai.core.runtime.persistence import MongoPersistenceContext

    names = MongoPersistenceContext(
        app_id=run.app_id, database_name=run.database_name, client=run.client, data_contract=run.load.data_contract,
    )
    try:
        collection_name = names.collection_name(entity.module, str(entity.collection.get("name")))
    except Exception:
        return []
    document = await run.database[collection_name].find_one({})
    return sorted(str(key) for key in (document or {}) if key != "_id")


async def _crud(run: _SmokeRun, entity: _Entity) -> None:
    module = next(item for item in run.load.modules if item.name == entity.module)
    actions = entity.actions
    tag = f"crud.{entity.label}"
    path_repo = f"modules/{entity.module}/backend/repo.py"
    surface = module.action_api_surface_map.get(actions["create"])
    if surface in _UNREACHABLE_SURFACES:
        run.not_run(tag, f"{actions['create']} is on the {surface} surface; signed-in users cannot call it over HTTP.")
        return
    owned = entity.tenancy in {"per_user", "per_workspace"}
    a, b = run.principal("A"), run.principal("B")
    # A and B hold the same plan, so any refusal of B is ownership, never entitlement.
    # Without config/subscriptions.yaml the host wires no entitlement adapter and
    # every gate allows, exactly as in production.
    gates = {module.action_entitlement_map.get(action) for action in actions.values()} - {None, ""}
    if gates and run.load.subscriptions_config is not None:
        choice = _granting_plan(run.load.subscriptions_config, set(gates))
        if choice is None:
            run.record(f"{tag}.plan", False, f"{entity.label} actions gate on {sorted(gates)} but no plan grants them.",
                       path="config/subscriptions.yaml")
        elif await _seed_assignment(run, a, choice[0], choice[1]) and owned:
            await _seed_assignment(run, b, choice[0], choice[1])

    def failure_path() -> str:
        return run.last_exception_path or f"modules/{entity.module}/backend/service.py"

    def describe(action: str, who: str, sent: Any = None) -> str:
        return f"{entity.module}.{action} as user {who}" + (f" with {sent}" if sent is not None else "")

    fields = _fields(entity)
    create = actions["create"]
    payload = _required_params(_input_schema(module, create), fields)
    status, body, summary = await run.call("POST", entity.module, create, a, payload)
    record_id = _record_id(entity, body) if 200 <= status < 300 else None
    stored = await _stored(run, entity, record_id) if record_id is not None else None
    no_record = f"user A's {create} produced no stored record to act on"
    if not 200 <= status < 300:
        run.record(f"{tag}.a_create", False,
                   f"{describe(create, 'A', payload)}: expected 2xx with the created record, got {summary}.",
                   path=failure_path())
    elif record_id is None:
        keys = sorted(body) if isinstance(body, Mapping) else type(body).__name__
        run.record(f"{tag}.a_create", False,
                   f"{describe(create, 'A')} returned {status} without the record id {entity.id_field!r} the data "
                   f"contract declares (response keys: {keys}).",
                   path=path_repo)
    elif stored is None:
        no_record = f"user A's {create} returned {entity.id_field}={record_id!r}, which no stored record has"
        run.record(f"{tag}.a_create", False,
                   f"{describe(create, 'A')} returned {entity.id_field}={record_id!r} but no stored {entity.label} "
                   f"record has that {entity.id_field}; the stored record fields are {await _stored_fields(run, entity)} "
                   f"where the data contract declares id field {entity.id_field!r} and owner field "
                   f"{entity.owner_field!r}. Reads, updates and deletes by the returned id can never find it.",
                   path=path_repo)
    elif owned and entity.owner_field and stored.get(entity.owner_field) != _owner_value(entity, a):
        run.record(f"{tag}.a_create", False,
                   f"The {entity.label} record user A created stores owner field {entity.owner_field!r} = "
                   f"{stored.get(entity.owner_field)!r}, expected {_owner_value(entity, a)!r} "
                   f"(stored fields: {sorted(key for key in stored if key != '_id')}).",
                   path=path_repo)
    else:
        run.record(f"{tag}.a_create", True, f"A created {entity.entity} {entity.id_field}={record_id!r}.")
    has_record = stored is not None
    lookup_value = _lookup_value(entity, stored)
    no_lookup = f"the stored record has no value for canonical get lookup {entity.lookup_field!r}"

    def listed_ids(body: Any) -> list[Any] | None:
        items = _items(body)
        return None if items is None else [item.get(entity.id_field) for item in items if isinstance(item, Mapping)]

    if "list" in actions:
        status, body, summary = await run.call("GET", entity.module, actions["list"], a, {})
        listed = listed_ids(body)
        if not 200 <= status < 300 or listed is None:
            run.record(f"{tag}.a_list", False, f"{describe(actions['list'], 'A')}: expected 2xx with items, got {summary}.",
                       path=failure_path())
        elif has_record and record_id not in listed:
            run.record(f"{tag}.a_list", False,
                       f"{describe(actions['list'], 'A')} did not list A's own record {record_id!r} "
                       f"(listed {entity.id_field} values: {listed[:5]}).", path=path_repo)
        else:
            run.record(f"{tag}.a_list", True,
                       f"A lists {len(listed)} record(s)" + (", including its own." if has_record else "."))
    if "get" in actions:
        missing_id = f"smoke-missing-{uuid4().hex[:8]}"
        status, body, summary = await run.call("GET", entity.module, actions["get"], a, {"id": missing_id})
        item = body.get("item") if isinstance(body, Mapping) else None
        found_nothing = status == 404 or (200 <= status < 300 and not item)
        run.record(f"{tag}.get_missing", found_nothing,
                   f"{describe(actions['get'], 'A')} for an id that does not exist: "
                   + ("not found." if found_nothing else f"expected 404, got {summary}."),
                   path=None if found_nothing else failure_path())
        if not has_record:
            run.not_run(f"{tag}.a_get", no_record)
        elif lookup_value is None:
            run.record(f"{tag}.a_get", False, no_lookup, path=path_repo)
        else:
            status, body, summary = await run.call("GET", entity.module, actions["get"], a, {"id": lookup_value})
            item = body.get("item") if isinstance(body, Mapping) else None
            if not 200 <= status < 300 or not isinstance(item, Mapping) or item.get(entity.id_field) != record_id:
                run.record(f"{tag}.a_get", False,
                           f"{describe(actions['get'], 'A')}(id={lookup_value!r}): expected 2xx with A's record, got {summary}.",
                           path=failure_path())
            else:
                run.record(f"{tag}.a_get", True, "A reads its own record by id.")
    if owned and "list" in actions:
        if not has_record:
            run.not_run(f"{tag}.b_list_isolated", no_record)
        else:
            status, body, summary = await run.call("GET", entity.module, actions["list"], b, {})
            listed = listed_ids(body)
            if not 200 <= status < 300 or listed is None:
                run.record(f"{tag}.b_list_isolated", False,
                           f"{describe(actions['list'], 'B')}: expected 2xx with B's own items, got {summary}.",
                           path=failure_path())
            elif record_id in listed:
                run.record(f"{tag}.b_list_isolated", False,
                           f"{describe(actions['list'], 'B')} returned user A's record {record_id!r}; "
                           f"{entity.tenancy} records must be visible only to their owner.", path=path_repo)
            else:
                run.record(f"{tag}.b_list_isolated", True, "B's list does not include A's record.")
    if owned and "get" in actions:
        if not has_record:
            run.not_run(f"{tag}.b_get_isolated", no_record)
        elif lookup_value is None:
            run.not_run(f"{tag}.b_get_isolated", no_lookup)
        else:
            status, body, summary = await run.call("GET", entity.module, actions["get"], b, {"id": lookup_value})
            item = body.get("item") if isinstance(body, Mapping) else None
            if 200 <= status < 300 and isinstance(item, Mapping) and item:
                run.record(f"{tag}.b_get_isolated", False,
                           f"{describe(actions['get'], 'B')}(id={lookup_value!r}) returned user A's record.", path=path_repo)
            elif status >= 500 or status == 0:
                run.record(f"{tag}.b_get_isolated", False,
                           f"{describe(actions['get'], 'B')}(id={lookup_value!r}): expected 404, got {summary}.",
                           path=failure_path())
            else:
                run.record(f"{tag}.b_get_isolated", True, f"B cannot read A's record ({status}).")

    update_schema = _input_schema(module, actions["update"]) if "update" in actions else {}
    update_field = _update_field(update_schema, entity) if "update" in actions else None
    update_props = _properties(update_schema)

    def update_params(current: Mapping[str, Any], who: str) -> dict[str, Any]:
        params: dict[str, Any] = {entity.id_field: record_id}
        if update_field:
            params[update_field] = _changed_value(
                current.get(update_field), update_props.get(update_field, {}), fields.get(update_field), f"user {who}",
            )
        return params

    for operation in ("update", "delete"):
        if not owned or operation not in actions:
            continue
        check = f"{tag}.b_{operation}_denied"
        if not has_record:
            run.not_run(check, no_record)
            continue
        action = actions[operation]
        before = await _stored(run, entity, record_id) or {}
        params = update_params(before, "B") if operation == "update" else {entity.id_field: record_id}
        status, _body, summary = await run.call("POST", entity.module, action, b, params)
        after = await _stored(run, entity, record_id)
        if status == 402:
            run.record(check, False,
                       f"{describe(action, 'B')} on user A's record was refused by the entitlement gate ({summary}), "
                       "although B holds the same plan as A; ownership was never exercised.", path=failure_path())
        elif status >= 500 or status == 0:
            run.record(check, False, f"{describe(action, 'B')} on user A's record: expected 403/404, got {summary}.",
                       path=failure_path())
        elif after is None if operation == "delete" else after != before:
            verb = "deleted" if operation == "delete" else "changed"
            run.record(check, False,
                       f"{describe(action, 'B')} {verb} user A's record {record_id!r} ({status}); "
                       f"{entity.tenancy} records must be writable only by their owner.",
                       path=path_repo)
        else:
            run.record(check, True, f"B's {operation} of A's record is refused ({status}); the record is unchanged.")

    if "update" in actions:
        if not has_record:
            run.not_run(f"{tag}.a_update", no_record)
        else:
            before = await _stored(run, entity, record_id) or {}
            params = update_params(before, "A")
            status, _body, summary = await run.call("POST", entity.module, actions["update"], a, params)
            after = await _stored(run, entity, record_id) or {}
            if not 200 <= status < 300:
                run.record(f"{tag}.a_update", False,
                           f"{describe(actions['update'], 'A', params)} on its own record: expected 2xx, got {summary}.",
                           path=failure_path())
            elif update_field and after.get(update_field) != params[update_field]:
                run.record(f"{tag}.a_update", False,
                           f"{describe(actions['update'], 'A', params)} returned {status} but the stored "
                           f"{update_field!r} is {after.get(update_field)!r}.", path=path_repo)
            else:
                changed = f" ({update_field})" if update_field else ""
                run.record(f"{tag}.a_update", True, f"A updated its own record{changed}.")
    if "delete" in actions:
        if not has_record:
            run.not_run(f"{tag}.a_delete", no_record)
        else:
            status, _body, summary = await run.call(
                "POST", entity.module, actions["delete"], a, {entity.id_field: record_id},
            )
            if not 200 <= status < 300:
                run.record(f"{tag}.a_delete", False,
                           f"{describe(actions['delete'], 'A')} on its own record: expected 2xx, got {summary}.",
                           path=failure_path())
            elif await _stored(run, entity, record_id) is not None:
                run.record(f"{tag}.a_delete", False,
                           f"{describe(actions['delete'], 'A')} returned {status} but record {record_id!r} is still stored.",
                           path=path_repo)
            else:
                run.record(f"{tag}.a_delete", True, "A deleted its own record.")


# --------------------------------------------------------------------------- entitlements


async def _gated_params(
    run: _SmokeRun, module: Any, action: str, principal: _Principal, entities: list[_Entity],
) -> tuple[str, dict[str, Any]]:
    """HTTP method and params for one gated call, creating the caller's own record when the action needs one."""
    for entity in entities:
        if entity.module != module.name or action not in entity.actions.values():
            continue
        operation = next(op for op, name in entity.actions.items() if name == action)
        fields = _fields(entity)
        if operation == "list":
            return "GET", {}
        if operation == "create":
            return "POST", _required_params(_input_schema(module, action), fields)
        create = entity.actions["create"]
        status, body, _summary = await run.call(
            "POST", module.name, create, principal, _required_params(_input_schema(module, create), fields),
        )
        record_id = _record_id(entity, body) if 200 <= status < 300 else None
        record_id = record_id if record_id is not None else "smoke-missing-record"
        if operation == "get":
            stored = await _stored(run, entity, record_id)
            return "GET", {"id": _lookup_value(entity, stored) or "smoke-missing-record"}
        return "POST", {entity.id_field: record_id}
    return "POST", _required_params(_input_schema(module, action), {})


async def _entitlements(run: _SmokeRun, entities: list[_Entity]) -> None:
    config = run.load.subscriptions_config
    if config is None:
        return
    default_caps = _default_capabilities(config)
    seeded: dict[str, _Principal] = {}
    for module in run.load.modules:
        for action, capability in sorted(module.action_entitlement_map.items()):
            if not capability:
                continue
            tag = f"entitlement.{module.name}.{action}"
            surface = module.action_api_surface_map.get(action)
            if surface in _UNREACHABLE_SURFACES:
                run.not_run(tag, f"{action} is on the {surface} surface; signed-in users cannot call it over HTTP.")
                continue
            choice = _granting_plan(config, {capability})
            if choice is None:
                run.record(f"{tag}.granted_by_plan", False,
                           f"{module.name}.{action} gates on {capability!r} but no plan in config/subscriptions.yaml "
                           "grants it, so no subscriber can ever use this action.",
                           path="config/subscriptions.yaml")
                continue
            item, plan = choice
            if capability in default_caps:
                run.not_run(f"{tag}.default_plan_denied",
                            f"{capability!r} is granted by the default plan, so there is no unentitled user to deny.")
            else:
                principal = run.principal("default plan")
                method, params = await _gated_params(run, module, action, principal, entities)
                status, _body, summary = await run.call(method, module.name, action, principal, params)
                run.record(
                    f"{tag}.default_plan_denied", status == 402,
                    f"{module.name}.{action} as a default-plan user without {capability!r}: "
                    + ("denied with 402." if status == 402 else f"expected 402 ENTITLEMENT_REQUIRED, got {summary}."),
                    path=None if status == 402 else (
                        f"modules/{module.name}/module.yaml" if 200 <= status < 300 or status == 403
                        else run.last_exception_path or f"modules/{module.name}/backend/service.py"
                    ),
                )
            label = f"plan {plan.plan_id}"
            if label not in seeded:
                # A failed seed is recorded; the call below still runs and shows
                # what a paying user of this plan actually gets.
                seeded[label] = run.principal(label)
                await _seed_assignment(run, seeded[label], item, plan)
            principal = seeded[label]
            method, params = await _gated_params(run, module, action, principal, entities)
            status, _body, summary = await run.call(method, module.name, action, principal, params)
            allowed = status not in {0, 401, 402, 403} and status < 500
            run.record(
                f"{tag}.entitled_allowed", allowed,
                f"{module.name}.{action} as a user with an active {plan.plan_id!r} assignment granting {capability!r}: "
                + (f"allowed ({status})." if allowed else f"expected the gate to allow it, got {summary}."),
                path=None if allowed else (
                    f"modules/{module.name}/module.yaml" if status == 403
                    else "config/subscriptions.yaml" if status == 402
                    else run.last_exception_path or f"modules/{module.name}/backend/service.py"
                ),
            )


# --------------------------------------------------------------------------- entry point


def _summary(outcomes: list[dict[str, Any]], meta: Mapping[str, Any], *, started: float) -> dict[str, Any]:
    failures = [outcome for outcome in outcomes if outcome.get("status") == "failed"]
    not_run = sum(outcome.get("status") == "not_run" for outcome in outcomes)
    passed = not failures
    ran = len(outcomes) - not_run
    message = (
        f"Generated app booted and passed {ran} runtime check(s) as two signed-in users."
        if passed else
        f"{len(failures)} of {ran} runtime check(s) failed when the generated app was booted and used"
        + (f"; {not_run} more could not run." if not_run else ".")
    )
    return {
        "contract_version": SMOKE_CONTRACT_VERSION,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "skipped_reason": None,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "app_id": meta.get("app_id"),
        "principals": meta.get("principals") or {},
        "results": outcomes,
        "events_emitted": meta.get("events_emitted") or [],
        "checks": [{
            "id": "app_runtime_smoke",
            "passed": passed,
            "status": "passed" if passed else "failed",
            "message": message,
            "details": {
                "status": "passed" if passed else "failed",
                "check_count": ran,
                "failed_check_count": len(failures),
                "not_run_check_count": not_run,
            },
        }],
        "failed_tests": [
            {
                "test": "app_runtime_smoke",
                "check": outcome.get("check"),
                **({"path": outcome["path"]} if outcome.get("path") else {}),
                "error": outcome.get("message"),
                "fix_suggestion": _FIX_SUGGESTION,
            }
            for outcome in failures
        ],
        "warnings": [],
    }


__all__ = [
    "SMOKE_TIMEOUT_SECONDS",
    "child_environment",
    "preflight_contained_imported_smoke",
    "resolve_smoke_mongo_uri",
    "run_app_runtime_smoke",
    "run_contained_imported_app_runtime_smoke",
]


# --------------------------------------------------------------------------- child process


async def _child_run(request: Mapping[str, Any], emit: Callable[..., None], initial_environment: set[str]) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client: Any = AsyncIOMotorClient(str(request["mongo_uri"]), serverSelectionTimeoutMS=int(_PING_TIMEOUT_SECONDS * 1000))
    run = _SmokeRun(Path(str(request["app_root"])), client, str(request["database_name"]), emit)
    runtime_logger = logging.getLogger(_RUNTIME_LOGGER)
    runtime_logger.addHandler(run.capture)
    try:
        if await _boot(run, initial_environment):
            entities = _contract_entities(run)
            for entity in entities:
                await _crud(run, entity)
            await _entitlements(run, entities)
    except Exception as exc:  # report the gate's own failure as a check, never a silent pass
        run.record("smoke.error", False, f"The runtime smoke could not complete: {type(exc).__name__}: {exc}")
    finally:
        if run.http is not None:
            await run.http.aclose()
        runtime_logger.removeHandler(run.capture)
        client.close()
    emit(
        "done",
        app_id=run.app_id,
        principals={
            label: {"user_id": principal.user_id, "workspace_id": principal.workspace_id,
                    "scopes": [*run.scopes, *principal.extra_scopes]}
            for label, principal in run.principals.items()
        },
        events_emitted=sorted(set(run.events)),
    )


def _child_main(request: Mapping[str, Any] | None = None) -> int:
    """Entry point of ``python -m`` this module: one smoke run, results as JSON lines on stdout."""
    initial_environment = set(os.environ)
    if request is None:
        request = json.loads(sys.stdin.read() or "{}")
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr  # generated code's prints never reach the result channel

    def emit(kind: str, **data: Any) -> None:
        channel.write(_EVENT_PREFIX + json.dumps({"event": kind, **data}, default=str) + "\n")
        channel.flush()

    asyncio.run(_child_run(request, emit, initial_environment))
    channel.close()
    return 0


async def _serve_imported_app(database_name: str, observer_nonce: str, mongo_uri: str) -> int:
    """A serves untrusted app behavior; its stdout is never a result channel."""
    import uvicorn
    from motor.motor_asyncio import AsyncIOMotorClient

    client: Any = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=2000)
    run = _SmokeRun(Path(_CONTAINED_APP_ROOT), client, database_name, lambda *_args, **_kwargs: None)
    run.external_server = True
    run.probe_nonce = observer_nonce
    try:
        booted = await _boot(run, set(os.environ))
        if not booted or any(outcome.passed is False for outcome in run.outcomes):
            return 1
        await run.http.aclose()
        run.http = None
        server = uvicorn.Server(uvicorn.Config(run.app, host="127.0.0.1", port=8000,
                                               access_log=False, log_level="warning"))
        await server.serve()
        return 0
    finally:
        if run.http is not None:
            await run.http.aclose()
        client.close()


def _probe_contracts(run: _SmokeRun) -> None:
    """Load only copied declarative files; B has no imported Python mount."""
    import yaml

    from mozaiksai.core.runtime.app.module_loader import ModuleDefinition, ModuleEventsManifest
    from mozaiksai.core.runtime.app.subscriptions_loader import load_subscriptions_config
    from mozaiksai.core.runtime.persistence.intent_loader import (
        iter_data_contract_collections,
        load_data_contract,
    )
    from mozaiksai.core.workflow.generator_support.module_write_actions import auth_contract_scopes

    root = run.app_root
    modules_root = root / "modules"
    modules = []
    event_schemas: dict[str, dict[str, Any]] = {}
    if modules_root.is_dir():
        for directory in sorted(modules_root.iterdir()):
            manifest = directory / "module.yaml"
            if directory.is_dir() and manifest.is_file():
                module = ModuleDefinition.model_validate(yaml.safe_load(
                    manifest.read_text(encoding="utf-8")))
                modules.append(module)
                events_path = directory / "contracts" / "events.yaml"
                if events_path.is_file():
                    events = ModuleEventsManifest.model_validate(yaml.safe_load(
                        events_path.read_text(encoding="utf-8")))
                    event_schemas[module.name] = {
                        event.type: dict(event.payload_schema)
                        for event in events.events if event.payload_schema
                    }
    app_config = json.loads((root / "app.json").read_text(encoding="utf-8"))
    contract = load_data_contract(root)
    run.load = SimpleNamespace(
        modules=modules, data_contract=contract,
        subscriptions_config=load_subscriptions_config(root),
        definition=SimpleNamespace(config=app_config),
    )
    run.app_id = _app_id(run.load)
    auth_yaml = root / "config" / "auth.yaml"
    run.scopes = sorted(auth_contract_scopes(
        {"config/auth.yaml": auth_yaml.read_text(encoding="utf-8")} if auth_yaml.is_file() else {}
    ))
    run.workspace_claims = any(
        collection.get("tenancy") == "per_workspace"
        for _owner, _kind, collection in iter_data_contract_collections(contract, require_complete_ownership=False)
    )
    run.event_audit = _TrustedEventAudit(run.probe_nonce, modules, event_schemas)


def _trusted_event_gateway(audit: _TrustedEventAudit) -> FastAPI:
    """The observer's only event ingress; malformed worker traffic fails the run."""
    app = FastAPI()

    @app.post("/__mozaiks_smoke_event")
    async def observe_event(request: Request) -> dict[str, Any]:
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > 1_000_000:
                audit._protocol_error("event request exceeds size limit")
                return {"accepted": False}
            content.extend(chunk)
        try:
            packet = json.loads(content)
        except (ValueError, UnicodeError):
            audit._protocol_error("event request is not JSON")
            return {"accepted": False}
        return audit.observe(packet)

    return app


async def _probe_imported_app(database_name: str, observer_nonce: str,
                              emit: Callable[..., None]) -> None:
    """B checks loopback HTTP and Mongo from a process without imported source."""
    import uvicorn
    from httpx import AsyncClient, HTTPError
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_uri = "mongodb://127.0.0.1:27017/?directConnection=true"
    client: Any = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=2000)
    run = _SmokeRun(Path(_CONTAINED_PLAN_ROOT), client, database_name, emit)
    run.external_probe = True
    run.probe_nonce = observer_nonce
    run.http = AsyncClient(base_url="http://127.0.0.1:8000", timeout=_REQUEST_TIMEOUT_SECONDS,
                           trust_env=False)
    gateway: Any = None
    gateway_task: asyncio.Task[Any] | None = None
    try:
        _probe_contracts(run)
        assert run.event_audit is not None
        gateway = uvicorn.Server(uvicorn.Config(
            _trusted_event_gateway(run.event_audit), host="127.0.0.1", port=8001,
            access_log=False, log_level="warning", lifespan="off",
        ))
        gateway_task = asyncio.create_task(gateway.serve())
        deadline = time.monotonic() + _CONTAINER_STARTUP_SECONDS
        while not gateway.started and time.monotonic() < deadline and not gateway_task.done():
            await asyncio.sleep(0.05)
        if not gateway.started:
            raise RuntimeError("trusted event gateway did not start")
        expected_modules = sorted(module.name for module in run.load.modules)
        deadline = time.monotonic() + _CONTAINER_STARTUP_SECONDS
        response: Any = None
        while time.monotonic() < deadline:
            try:
                response = await run.http.get("/__mozaiks_smoke_ready", timeout=1.0)
                if response.status_code == 200:
                    break
            except (HTTPError, OSError, TimeoutError):
                pass
            await asyncio.sleep(0.2)
        try:
            observed = response.json() if response is not None and response.status_code == 200 else {}
        except ValueError:
            observed = {}
        ready = (isinstance(observed, dict) and bool(expected_modules)
                 and observed.get("app_id") == run.app_id
                 and observed.get("modules") == expected_modules
                 and observed.get("failed_modules") == [])
        run.record("boot.http_ready", ready,
                   f"Loopback app endpoint {'reported the expected app and modules' if ready else 'did not report the expected app and modules'}.")
        if ready:
            await client.admin.command("ping")
            entities = _contract_entities(run)
            for entity in entities:
                await _crud(run, entity)
            await _entitlements(run, entities)
    except Exception as exc:
        run.record("smoke.error", False,
                   f"The external observer could not complete: {type(exc).__name__}: {exc}")
    finally:
        if gateway is not None:
            gateway.should_exit = True
        if gateway_task is not None:
            try:
                await asyncio.wait_for(gateway_task, timeout=5)
            except Exception as exc:
                run.record("event.gateway", False, f"Trusted event gateway did not stop: {type(exc).__name__}")
                gateway_task.cancel()
        if run.event_audit is not None:
            for module, action, label, rejection in run.event_audit.rejections:
                run._record_rejected_event(module, action, run.principal(label), rejection)
            audit_result = run.event_audit.result()
            run.record(
                "event.audit", audit_result["passed"],
                "Trusted observer completed its event request audit."
                if audit_result["passed"] else "Trusted observer found rejected or incomplete event requests.",
                **{key: value for key, value in audit_result.items() if key != "passed"},
            )
        await run.http.aclose()
        client.close()
    emit(
        "done", app_id=run.app_id,
        principals={
            label: {"user_id": principal.user_id, "workspace_id": principal.workspace_id,
                    "scopes": [*run.scopes, *principal.extra_scopes]}
            for label, principal in run.principals.items()
        },
        events_emitted=[],
        event_audit=run.event_audit.result() if run.event_audit is not None else None,
    )


def _trusted_probe_main(database_name: str, observer_nonce: str) -> int:
    def emit(kind: str, **data: Any) -> None:
        print(_EVENT_PREFIX + json.dumps(
            {"event": kind, "observer_nonce": observer_nonce, **data}, default=str,
        ), flush=True)

    asyncio.run(_probe_imported_app(database_name, observer_nonce, emit))
    return 0


def _contained_main(database_name: str, observer_nonce: str) -> int:
    """Start disposable MongoDB and the imported app server on isolated loopback."""
    from pymongo import MongoClient

    database_dir = Path(tempfile.mkdtemp(prefix="mozaiks-smoke-mongo-", dir="/tmp"))
    mongod = subprocess.Popen(
        ["mongod", "--dbpath", str(database_dir), "--bind_ip", "127.0.0.1", "--port", "27017",
         "--nounixsocket", "--quiet"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    mongo_uri = "mongodb://127.0.0.1:27017/?directConnection=true"
    client: MongoClient[dict[str, Any]] = MongoClient(
        mongo_uri, serverSelectionTimeoutMS=500, connectTimeoutMS=500,
    )
    try:
        deadline = time.monotonic() + _CONTAINER_STARTUP_SECONDS
        while time.monotonic() < deadline and mongod.poll() is None:
            try:
                client.admin.command("ping")
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("Contained smoke MongoDB did not start", file=sys.stderr)
            return 1
        return asyncio.run(_serve_imported_app(database_name, observer_nonce, mongo_uri))
    finally:
        client.close()
        if mongod.poll() is None:
            mongod.terminate()
            try:
                mongod.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mongod.kill()
                mongod.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--contained-serve":
        raise SystemExit(_contained_main(sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 4 and sys.argv[1] == "--trusted-probe":
        raise SystemExit(_trusted_probe_main(sys.argv[2], sys.argv[3]))
    raise SystemExit(_child_main())
