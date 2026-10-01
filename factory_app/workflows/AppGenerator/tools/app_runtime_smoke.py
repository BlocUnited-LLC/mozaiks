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
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)

SMOKE_CONTRACT_VERSION = "1.0"
SMOKE_TIMEOUT_SECONDS = 60.0
_CHILD_MODULE = "factory_app.workflows.AppGenerator.tools.app_runtime_smoke"
_EVENT_PREFIX = "@@mozaiks-runtime-smoke@@ "
_SMOKE_DATABASE_PREFIX = "mozaiks_runtime_smoke_"
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
            await asyncio.to_thread(child.kill)
            raise
        finally:
            try:
                await client.drop_database(database_name)
            except Exception as exc:
                logger.warning("APP_RUNTIME_SMOKE_DROP_FAILED: database=%s error=%s", database_name, type(exc).__name__)
        return _child_result(run, mongo_uri=mongo_uri, timeout_seconds=timeout_seconds, started=started)
    finally:
        client.close()


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


def _child_result(run: _ChildRun, *, mongo_uri: str, timeout_seconds: float, started: float) -> dict[str, Any]:
    events = _events(run.stdout)
    outcomes = [{key: value for key, value in event.items() if key != "event"} for event in events
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
    elif done is None:
        tail = " ".join(run.stderr.strip().splitlines()[-3:])[-600:]
        outcomes.append({
            "check": "smoke.process", "status": "failed", "path": path,
            "message": f"The runtime smoke process exited with code {run.returncode}{where} before finishing: "
                       f"{tail or 'no output'}",
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

    async def _request(
        self, method: str, module: str, action: str, principal: _Principal, params: dict[str, Any],
    ) -> tuple[int, Any, str]:
        since = len(self.capture.records)
        self.emit("calling", module=module, action=action, principal=principal.label)
        url = f"/api/modules/{module}/{action}"
        headers = {"Authorization": f"Bearer {principal.token}"}
        try:
            if method == "GET":
                request = self.http.get(url, params={key: str(value) for key, value in params.items()}, headers=headers)
            else:
                request = self.http.post(url, json=params, headers=headers)
            response = await asyncio.wait_for(request, timeout=_REQUEST_TIMEOUT_SECONDS)
        except TimeoutError:
            return 0, None, f"no response within {_REQUEST_TIMEOUT_SECONDS:.0f}s"
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

    hooks = PlatformHookRegistry()
    executor = ModuleExecutor(
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
        else:
            status, body, summary = await run.call("GET", entity.module, actions["get"], a, {"id": record_id})
            item = body.get("item") if isinstance(body, Mapping) else None
            if not 200 <= status < 300 or not isinstance(item, Mapping) or item.get(entity.id_field) != record_id:
                run.record(f"{tag}.a_get", False,
                           f"{describe(actions['get'], 'A')}(id={record_id!r}): expected 2xx with A's record, got {summary}.",
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
        else:
            status, body, summary = await run.call("GET", entity.module, actions["get"], b, {"id": record_id})
            item = body.get("item") if isinstance(body, Mapping) else None
            if 200 <= status < 300 and isinstance(item, Mapping) and item:
                run.record(f"{tag}.b_get_isolated", False,
                           f"{describe(actions['get'], 'B')}(id={record_id!r}) returned user A's record.", path=path_repo)
            elif status >= 500 or status == 0:
                run.record(f"{tag}.b_get_isolated", False,
                           f"{describe(actions['get'], 'B')}(id={record_id!r}): expected 404, got {summary}.",
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
            return "GET", {"id": record_id}
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
    "resolve_smoke_mongo_uri",
    "run_app_runtime_smoke",
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


def _child_main() -> int:
    """Entry point of ``python -m`` this module: one smoke run, results as JSON lines on stdout."""
    initial_environment = set(os.environ)
    request = json.loads(sys.stdin.read() or "{}")
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    sys.stdout = sys.stderr  # generated code's prints never reach the result channel

    def emit(kind: str, **data: Any) -> None:
        channel.write(_EVENT_PREFIX + json.dumps({"event": kind, **data}, default=str) + "\n")
        channel.flush()

    asyncio.run(_child_run(request, emit, initial_environment))
    channel.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_child_main())
