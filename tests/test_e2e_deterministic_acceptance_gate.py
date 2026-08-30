"""Deterministic offline generated-bundle functional acceptance gate.

This gate exercises every deterministic validation, loading, and runtime
stage that sits below the AG2/Factory reasoning boundary.  It starts from a
fixed, hand-authored canonical bundle fixture -- NOT from a production
materializer or AG2 reasoning run -- and drives that fixture through
production scanners, validators, the AppLoader, module dispatch, entitlement
enforcement, and a fully-offline platform host boot.

Proof boundary (what IS exercised):

    hand-authored canonical bundle fixture
    -> production bundle scanner (scan_generated_bundle)
    -> production functional scanner (scan_functional_generated_app)
    -> production validation facade (validate_generated_app_bundle)
    -> production acceptance gate (run_app_bundle_acceptance_gate)
    -> AppLoader.load (production bundle loader)
    -> ModuleExecutor + ExecutorRegistry (production dispatch)
    -> ConfiguredEntitlementAdapter (production entitlement gate)
    -> TestClient(platform.app) boot + representative HTTP requests

Explicitly outside scope (NOT exercised by this gate):

    - AG2 reasoning (fixture is static, no LLM is called)
    - AppPlan / AppBuildPlan processing
    - Production Jinja template materialization (fixture is hand-authored)
    - CapabilityPack selection or composition
    - Real databases (MongoDB faked via in-memory stub)
    - Paid Factory execution
    - Deployment, CI/CD, or provider integration

Negative tests prove the gate catches each class of reference-closure failure
through targeted mutations of the same fixture.

Requirements: pytest, no LLM, no MongoDB, no Redis, no Docker, no API keys.
"""

from __future__ import annotations

import json
import textwrap
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_validation import (
    run_app_bundle_acceptance_gate,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    scan_generated_bundle,
)
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
from mozaiksai.core.auth.adapters.registry import register_adapter, reset_auth_adapter
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.validation import (
    GeneratedAppValidationRequest,
    scan_functional_generated_app,
    validate_generated_app_bundle,
)

# ---------------------------------------------------------------------------
# Test-only fakes for offline boot (no Mongo, no Redis, no external auth)
# ---------------------------------------------------------------------------


class _TestAuthAdapter(BaseAuthAdapter):
    name = "test"

    async def validate_token(self, token: str) -> UserClaims:  # noqa: ARG002
        return UserClaims(
            user_id="gate-user-1",
            email="gate@acceptance.test",
            name="Gate User",
            roles=["user"],
            scopes=["access_as_user"],
            raw_claims={},
            provider=self.name,
            app_id="e2e-gate-app",
        )

    def is_enabled(self) -> bool:
        return True


class _FakeMongoAdmin:
    async def command(self, *_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {"ok": 1}


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeMongoAdmin()

    def close(self) -> None:
        pass


class _FakeCollection:
    """In-memory collection stand-in for entitlement assignment lookup."""

    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self._docs: list[dict[str, Any]] = list(docs or [])

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, int] | None = None,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return deepcopy(doc)
        return None

    def replace_docs(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)


# ---------------------------------------------------------------------------
# Canonical app fixture -- a small but meaningful generated app bundle
# ---------------------------------------------------------------------------

def _canonical_fixture() -> dict[str, str]:
    """Return a hand-authored canonical app bundle fixture.

    This fixture is NOT produced by Jinja materialization, AppBuildPlan
    processing, or any CapabilityPack engine.  It is a hand-written
    dictionary representing the file tree that the full Factory pipeline
    would produce for a small but meaningful app.

    Surfaces exercised:
      - app.json (identity + auth declaration + landing spot)
      - Two routes in route_manifest.json (multi-route coverage)
      - Two schema-native pages: tasks (CRUD) and analytics (dashboard)
      - One module (tasks) with two actions (list_tasks, create_task)
        - create_task emits an event
        - list_tasks is entitlement-gated
      - Handler, service, repo, policy, schemas backend layers
      - Persistence contract (data/contract.json, no live DB)
      - Event and reaction declarations (events.yaml, reactions.yaml)
      - Subscription/entitlement contract (subscriptions.yaml)
      - Auth declaration (config/auth.yaml + ui/auth/authAdapter.js)
      - Security secrets-by-name contract
      - Health endpoint (provided by platform.app at /health)
      - Config files (ai.json, shell.json)
      - Entitlement dispatch module (required by assignment_store)
      - Notification contract (required when reaction targets notification)
      - Bounded custom code escape hatch (authAdapter.js)
    """
    return {
        "app.json": json.dumps(
            {
                "appId": "e2e-gate-app",
                "appName": "E2E Gate App",
                "version": "1.0.0",
                "authRequired": True,
                "startup": {"landing_spot": "/tasks"},
            }
        ),
        "config/ai.json": json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        "config/shell.json": json.dumps({"navigation": {"autoFromPages": True}}),
        "config/subscriptions.yaml": textwrap.dedent("""\
            schema_version: mozaiks.subscriptions.v1
            label: E2E Gate Plans
            default_plan_id: free
            assignment_store:
              data_alias: billing.subscriptions
              app_id_field: app_id
              user_id_field: user_id
              status_field: status
              plan_id_field: plan_id
              capabilities_field: granted_capabilities
              plan_snapshot_field: plan_snapshot
              active_statuses: [active, trialing]
            plans:
              - plan_id: free
                label: Free
                capabilities: []
              - plan_id: pro
                label: Pro
                capabilities: [tasks.view, tasks.create]
        """),
        "config/auth.yaml": textwrap.dedent("""\
            schema_version: mozaiks.auth.v1
            auth_required: true
            strategy: oidc
            mode: brokered_oidc
            signup_enabled: false
            routes:
              login: /login
              callback: /auth/callback
              logout: /login
              post_login_default: /tasks
            frontend:
              adapter: oidc_pkce
              client_id_env: VITE_OIDC_CLIENT_ID
              authority_env: VITE_OIDC_AUTHORITY
              discovery_url_env: VITE_OIDC_DISCOVERY_URL
              redirect_uri_env: VITE_OIDC_REDIRECT_URI
              scope_env: VITE_OIDC_SCOPE
              default_scopes: [openid, profile, email]
            runtime:
              provider_env: AUTH_PROVIDER
              enabled_env: AUTH_ENABLED
              authority_env: MOZAIKS_OIDC_AUTHORITY
              discovery_url_env: MOZAIKS_OIDC_DISCOVERY_URL
              issuer_env: AUTH_ISSUER
              jwks_url_env: AUTH_JWKS_URL
        """),
        "security/secrets.yaml": "version: 1\nsecrets: []\n",
        "data/contract.json": json.dumps(
            {
                "version": "1",
                "app_id": "e2e-gate-app",
                "surfaces": [
                    {
                        "surface_id": "tasks",
                        "surface_kind": "module",
                        "collections": [
                            {
                                "module_id": "tasks",
                                "name": "tasks",
                                "entity_name": "tasks",
                                "indexes": [
                                    {
                                        "name": "task_created_at",
                                        "keys": [{"field": "created_at", "order": -1}],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/tasks",
                        "component": "SchemaPage",
                        "label": "Tasks",
                        "schema": "tasks",
                    },
                    {
                        "path": "/analytics",
                        "component": "SchemaPage",
                        "label": "Analytics",
                        "schema": "analytics",
                    },
                ]
            }
        ),
        "ui/pages/tasks.yaml": textwrap.dedent("""\
            schema_version: mozaiks.app_page.v1
            name: tasks
            route: /tasks
            title: Tasks
            page_type: record_list
            layout: full-width
            sections:
              - id: tasks-table
                primitive: DataTable
                title: All Tasks
                config:
                  columns:
                    - key: id
                      label: ID
                    - key: title
                      label: Title
                    - key: status
                      label: Status
                    - key: created_at
                      label: Created
                  search: true
                  selection: single
                  api_endpoint: /api/modules/tasks/list_tasks
              - id: create-task
                primitive: Form
                title: Create Task
                config:
                  fields:
                    - name: title
                      label: Title
                      type: text
                  submit_action:
                    label: Create Task
                    action_type: submit
                    href: /api/modules/tasks/create_task
        """),
        "ui/pages/analytics.yaml": textwrap.dedent("""\
            schema_version: mozaiks.app_page.v1
            name: analytics
            route: /analytics
            title: Analytics Dashboard
            page_type: analytics_dashboard
            layout: full-width
            sections:
              - id: summary-strip
                primitive: SummaryStrip
                title: Key Metrics
                config:
                  items:
                    - label: Total Tasks
                      value: "0"
              - id: chart
                primitive: DataTable
                title: Task Breakdown
                config:
                  columns:
                    - key: status
                      label: Status
                    - key: count
                      label: Count
        """),
        # ---- Module: tasks ----
        "modules/tasks/module.yaml": textwrap.dedent("""\
            schema_version: mozaiks.module.v1
            module:
              id: tasks
              display_name: Tasks
              version: 1.0.0
              handler: backend.handler:TasksModule
            actions:
              - id: list_tasks
                description: List all tasks.
                handler_method: list_tasks
                entitlement_gate: tasks.view
                input_schema:
                  type: object
                  properties: {}
                output_schema:
                  type: object
              - id: create_task
                description: Create a new task.
                handler_method: create_task
                entitlement_gate: tasks.create
                emits:
                  - domain.tasks.task_created
                input_schema:
                  type: object
                  required: [title]
                  properties:
                    title: {type: string}
                    description: {type: string}
                output_schema:
                  type: object
            permissions:
              - id: tasks.read
                description: Read tasks.
              - id: tasks.write
                description: Create tasks.
            capabilities:
              - capability_id: tasks.view
                kind: action
                target: list_tasks
                title: View tasks
              - capability_id: tasks.create
                kind: action
                target: create_task
                title: Create tasks
        """),
        "modules/tasks/backend/__init__.py": "",
        "modules/tasks/backend/handler.py": textwrap.dedent("""\
            from .service import TasksService


            class TasksModule:
                def __init__(self):
                    self.service = TasksService()

                async def list_tasks(self, ctx, **params):
                    return await self.service.list_tasks(ctx, **params)

                async def create_task(self, ctx, **params):
                    return await self.service.create_task(ctx, **params)
        """),
        "modules/tasks/backend/service.py": textwrap.dedent("""\
            from .repo import TasksRepo


            class TasksService:
                async def list_tasks(self, ctx, **params):
                    return {"tasks": await TasksRepo(ctx).list_tasks()}

                async def create_task(self, ctx, **params):
                    title = str(params.get("title") or "Untitled")
                    description = str(params.get("description") or "")
                    return await TasksRepo(ctx).create_task(title=title, description=description)
        """),
        "modules/tasks/backend/repo.py": textwrap.dedent("""\
            class TasksRepo:
                def __init__(self, ctx):
                    self.ctx = ctx

                async def list_tasks(self):
                    return [
                        {"id": "task_1", "title": "Setup CI", "status": "done", "created_at": "2026-08-01T00:00:00+00:00"},
                        {"id": "task_2", "title": "Write tests", "status": "in_progress", "created_at": "2026-08-10T00:00:00+00:00"},
                    ]

                async def create_task(self, *, title, description=""):
                    return {"id": "task_new", "title": title, "description": description, "status": "pending"}
        """),
        "modules/tasks/backend/policy.py": textwrap.dedent("""\
            def tasks_scope(*, app_id=None, user_id=None):
                return {"app_id": app_id, "user_id": user_id}
        """),
        "modules/tasks/backend/schemas.py": textwrap.dedent("""\
            from __future__ import annotations

            from pydantic import BaseModel


            class TaskCreateRequest(BaseModel):
                title: str
                description: str = ""


            class TaskResponse(BaseModel):
                id: str
                title: str
                status: str
        """),
        # ---- Event and reaction contracts ----
        "modules/tasks/contracts/events.yaml": textwrap.dedent("""\
            schema_version: mozaiks.events.v1
            events:
              - type: domain.tasks.task_created
                version: 1
                description: Emitted after a task is created.
                producer: tasks
                payload_schema:
                  type: object
                  properties:
                    task_id: {type: string}
                    title: {type: string}
        """),
        "modules/tasks/contracts/reactions.yaml": textwrap.dedent("""\
            schema_version: mozaiks.reactions.v1
            reactions:
              - id: on_task_created_notify
                event_type: domain.tasks.task_created
                target:
                  kind: notification
                  notification_id: task_created_receipt
        """),
        "modules/tasks/contracts/notifications.yaml": textwrap.dedent("""\
            schema_version: mozaiks.notifications.v1
            notifications:
              - id: task_created_receipt
                event_type: domain.tasks.task_created
                channels: [in_app]
                title: "Task created"
                body: "A new task was created."
        """),
        # ---- Entitlement dispatch module (required by assignment_store) ----
        "modules/entitlement_dispatch/module.yaml": textwrap.dedent("""\
            schema_version: mozaiks.module.v1
            module:
              id: entitlement_dispatch
              display_name: Entitlement Dispatch
              version: 1.0.0
              handler: backend.handler:EntitlementDispatchModule
            actions:
              - id: activate_subscription
                description: Activate a subscription assignment.
                handler_method: activate_subscription
                input_schema:
                  type: object
                  properties:
                    user_id: {type: string}
                    plan_id: {type: string}
                output_schema:
                  type: object
              - id: deactivate_subscription
                description: Deactivate a subscription assignment.
                handler_method: deactivate_subscription
                input_schema:
                  type: object
                  properties: {}
                output_schema:
                  type: object
        """),
        "modules/entitlement_dispatch/backend/__init__.py": "",
        "modules/entitlement_dispatch/backend/handler.py": textwrap.dedent("""\
            class EntitlementDispatchModule:
                async def activate_subscription(self, ctx, **params):
                    return {"activated": True, "plan_id": params.get("plan_id")}

                async def deactivate_subscription(self, ctx, **params):
                    return {"deactivated": True}
        """),
        # ---- Auth frontend escape hatch (required for authRequired apps) ----
        "ui/auth/authAdapter.js": textwrap.dedent("""\
            const TRANSACTION_KEY = 'e2e_oidc_transactions';
            function writeAuthTransaction(transaction) {
              return transaction.state;
            }
            function readAuthTransaction(state) {
              return state;
            }
            function clearStoredUserSession() {
              sessionStorage.removeItem('e2e_access_token');
            }
            async function handleCallback() {
              const state = new URLSearchParams(window.location.search).get('state');
              const transaction = readAuthTransaction(state);
              return { returnPath: transaction?.returnPath || '/tasks' };
            }
        """),
    }


# ---------------------------------------------------------------------------
# File-system materialization helper
# ---------------------------------------------------------------------------


def _write_bundle(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Platform runtime configuration helper (offline, no Mongo)
# ---------------------------------------------------------------------------


def _configure_platform(
    *,
    app_root: Path,
    loaded: Any,
    assignments: _FakeCollection,
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleExecutor:
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "test")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"):
        monkeypatch.delenv(name, raising=False)
    fake_mongo = _FakeMongoClient()
    monkeypatch.setattr("mozaiksai.hosts.runtime.get_mongo_client", lambda: fake_mongo)
    monkeypatch.setattr("mozaiksai.core.startup.validation.get_mongo_client", lambda: fake_mongo)
    reset_auth_adapter()
    register_adapter("test", _TestAuthAdapter)

    entitlement_adapter = ConfiguredEntitlementAdapter(
        config=loaded.subscriptions_config,
        collection_resolver=lambda alias: assignments,
    )
    module_executor = ModuleExecutor(entitlement_checker=entitlement_adapter)
    for module in loaded.modules:
        module_executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
            action_entitlements=module.action_entitlement_map,
        )

    from mozaiksai.hosts import platform

    registry = ExecutorRegistry()
    registry.register(module_executor)
    platform.app.state.executor_registry = registry
    platform.app.state.subscriptions_config = loaded.subscriptions_config
    platform.app.state.failed_module_names = []
    platform.app.state.startup_degraded = False
    platform.app.state.startup_degraded_reason = None
    platform.app.state.module_action_surfaces = {
        module.name: module.action_api_surface_map for module in loaded.modules
    }
    return module_executor


# ---------------------------------------------------------------------------
# State save/restore for platform singleton
# ---------------------------------------------------------------------------


def _save_platform_state() -> dict[str, Any]:
    from mozaiksai.hosts import platform

    return {
        "executor_registry": getattr(platform.app.state, "executor_registry", None),
        "subscriptions_config": getattr(platform.app.state, "subscriptions_config", None),
        "failed_module_names": list(getattr(platform.app.state, "failed_module_names", [])),
        "startup_degraded": getattr(platform.app.state, "startup_degraded", False),
        "startup_degraded_reason": getattr(platform.app.state, "startup_degraded_reason", None),
        "module_action_surfaces": deepcopy(getattr(platform.app.state, "module_action_surfaces", {})),
        # Snapshot the auth adapter registry so we can restore it exactly,
        # rather than clearing it (which would lose builtins registered by
        # prior tests or by _register_builtin_adapters).
        "auth_adapter_registry": dict(auth_registry._adapter_registry),
        "auth_adapter_instance": auth_registry._adapter_instance,
    }


def _restore_platform_state(state: dict[str, Any]) -> None:
    from mozaiksai.hosts import platform

    platform.app.state.executor_registry = state["executor_registry"]
    platform.app.state.subscriptions_config = state["subscriptions_config"]
    platform.app.state.failed_module_names = state["failed_module_names"]
    platform.app.state.startup_degraded = state["startup_degraded"]
    platform.app.state.startup_degraded_reason = state["startup_degraded_reason"]
    platform.app.state.module_action_surfaces = state["module_action_surfaces"]
    # Restore auth adapter registry to its pre-test snapshot instead of
    # clearing it unconditionally.
    auth_registry._adapter_registry.clear()
    auth_registry._adapter_registry.update(state["auth_adapter_registry"])
    auth_registry._adapter_instance = state["auth_adapter_instance"]


# ===========================================================================
# POSITIVE GATE: hand-authored fixture -> production scanners/validators
#                -> AppLoader -> dispatch -> platform boot -> HTTP surfaces
# ===========================================================================


class TestPositiveE2EAcceptanceGate:
    """The hand-authored canonical fixture passes every production validation
    and runtime stage from bundle scanning through platform boot.

    All validation is delegated to production systems -- no test-only
    validators are used.  The fixture is static and hand-authored; AG2
    reasoning, Jinja materialization, and CapabilityPack composition are
    not exercised.
    """

    def test_fixture_passes_bundle_scanner(self) -> None:
        """Production scan_generated_bundle returns zero errors."""
        errors = scan_generated_bundle(_canonical_fixture())
        assert errors == [], f"Bundle scanner errors: {errors}"

    def test_fixture_passes_functional_scanner(self) -> None:
        """Production scan_functional_generated_app returns zero diagnostics."""
        diagnostics = scan_functional_generated_app(_canonical_fixture())
        assert diagnostics == [], f"Functional scanner diagnostics: {diagnostics}"

    def test_fixture_passes_validation_facade(self) -> None:
        """Production validate_generated_app_bundle passes."""
        result = validate_generated_app_bundle(
            GeneratedAppValidationRequest(files=_canonical_fixture())
        )
        assert result.passed is True, [d.message for d in result.diagnostics if d.severity == "error"]

    @pytest.mark.asyncio
    async def test_fixture_passes_acceptance_gate(self) -> None:
        """Production run_app_bundle_acceptance_gate passes."""
        gate = await run_app_bundle_acceptance_gate(files=_canonical_fixture())
        assert gate["passed"] is True, gate
        assert gate["functional_completeness"]["passed"] is True

    @pytest.mark.asyncio
    async def test_fixture_loads_through_app_loader(self, tmp_path: Path) -> None:
        """AppLoader.load successfully parses the materialized fixture."""
        app_root = tmp_path / "app"
        _write_bundle(app_root, _canonical_fixture())
        loaded = await AppLoader.load(str(app_root))
        assert loaded.definition.name == "E2E Gate App"
        assert loaded.definition.config["authRequired"] is True
        module_names = sorted(m.name for m in loaded.modules)
        assert module_names == ["entitlement_dispatch", "tasks"]
        page_names = sorted(p.name for p in loaded.definition.pages)
        assert "tasks" in page_names
        assert "analytics" in page_names

    @pytest.mark.asyncio
    async def test_fixture_boots_and_serves_http_surfaces(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full boot: platform host starts and serves health, pages, and module actions."""
        from mozaiksai.hosts import platform

        saved = _save_platform_state()
        try:
            app_root = tmp_path / "app"
            _write_bundle(app_root, _canonical_fixture())
            loaded = await AppLoader.load(str(app_root))
            assignments = _FakeCollection()

            _configure_platform(
                app_root=app_root,
                loaded=loaded,
                assignments=assignments,
                monkeypatch=monkeypatch,
            )

            with TestClient(platform.app, raise_server_exceptions=False) as client:
                auth = {"Authorization": "Bearer test-token"}

                # Health endpoint
                health = client.get("/health")
                assert health.status_code == 200, health.text
                assert health.json()["status"] == "ok"

                # Shell config exposes both routes
                shell = client.get("/api/shell-config")
                assert shell.status_code == 200, shell.text
                paths = [p.get("path") for p in shell.json().get("pages", [])]
                assert "/tasks" in paths
                assert "/analytics" in paths

                # Page schema for tasks
                tasks_page = client.get("/api/pages/tasks")
                assert tasks_page.status_code == 200, tasks_page.text
                assert tasks_page.json()["route"] == "/tasks"

                # Page schema for analytics
                analytics_page = client.get("/api/pages/analytics")
                assert analytics_page.status_code == 200, analytics_page.text
                assert analytics_page.json()["route"] == "/analytics"

                # Entitlement-gated actions denied when no assignment
                denied = client.post(
                    "/api/modules/tasks/list_tasks",
                    json={"params": {}},
                    headers=auth,
                )
                assert denied.status_code == 402, denied.text
                assert denied.json()["detail"]["error_code"] == "ENTITLEMENT_REQUIRED"

                # Grant entitlements
                assignments.replace_docs(
                    [
                        {
                            "_id": "e2e-gate-app:gate-user-1",
                            "app_id": "e2e-gate-app",
                            "user_id": "gate-user-1",
                            "plan_id": "pro",
                            "status": "active",
                            "granted_capabilities": [
                                {"capability_id": "tasks.view"},
                                {"capability_id": "tasks.create"},
                            ],
                            "plan_snapshot": {
                                "granted_capabilities": [
                                    {"capability_id": "tasks.view"},
                                    {"capability_id": "tasks.create"},
                                ]
                            },
                        }
                    ]
                )

                # list_tasks succeeds after entitlement
                list_resp = client.post(
                    "/api/modules/tasks/list_tasks",
                    json={"params": {}},
                    headers=auth,
                )
                assert list_resp.status_code == 200, list_resp.text
                tasks = list_resp.json()["tasks"]
                assert len(tasks) == 2
                assert tasks[0]["id"] == "task_1"

                # create_task succeeds
                create_resp = client.post(
                    "/api/modules/tasks/create_task",
                    json={"params": {"title": "E2E Task", "description": "Acceptance proof"}},
                    headers=auth,
                )
                assert create_resp.status_code == 200, create_resp.text
                assert create_resp.json()["title"] == "E2E Task"
        finally:
            _restore_platform_state(saved)

    @pytest.mark.asyncio
    async def test_fixture_loads_identically_across_independent_writes(self, tmp_path: Path) -> None:
        """Two independent writes of the same fixture to disk produce
        identical AppLoader results, proving the fixture and loader are
        deterministic.  This does NOT prove Jinja materialization
        determinism -- the fixture is hand-authored, not materialized.
        """
        for run in ("run1", "run2"):
            root = tmp_path / run / "app"
            _write_bundle(root, _canonical_fixture())

        loaded_one = await AppLoader.load(str(tmp_path / "run1" / "app"))
        loaded_two = await AppLoader.load(str(tmp_path / "run2" / "app"))

        assert loaded_one.definition.name == loaded_two.definition.name
        assert loaded_one.definition.config == loaded_two.definition.config
        assert sorted(m.name for m in loaded_one.modules) == sorted(m.name for m in loaded_two.modules)
        assert sorted(p.name for p in loaded_one.definition.pages) == sorted(
            p.name for p in loaded_two.definition.pages
        )


# ===========================================================================
# NEGATIVE GATE: targeted fixture mutations that production scanners,
#                validators, and the AppLoader must catch
# ===========================================================================


class TestNegativeRouteClosure:
    """Route references that point to missing components are caught by
    production scan_functional_generated_app."""

    def test_route_to_missing_schema_page(self) -> None:
        files = _canonical_fixture()
        files["ui/route_manifest.json"] = json.dumps(
            {"pages": [{"path": "/ghost", "component": "SchemaPage", "schema": "ghost_page"}]}
        )
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "MISSING_SCHEMA_PAGE" in codes

    def test_route_to_missing_custom_component(self) -> None:
        files = _canonical_fixture()
        files["ui/route_manifest.json"] = json.dumps(
            {"pages": [{"path": "/custom", "component": "GhostComponent"}]}
        )
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "MISSING_ROUTE_COMPONENT" in codes


class TestNegativePageToModuleClosure:
    """Page api_endpoint references that point to missing module actions are
    caught by production scanners."""

    def test_page_endpoint_references_undeclared_action(self) -> None:
        files = _canonical_fixture()
        files["ui/pages/tasks.yaml"] = files["ui/pages/tasks.yaml"].replace(
            "/api/modules/tasks/list_tasks",
            "/api/modules/tasks/archive_task",
        )
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "MISSING_MODULE_ACTION" in codes

    def test_page_endpoint_references_undeclared_module(self) -> None:
        files = _canonical_fixture()
        files["ui/pages/tasks.yaml"] = files["ui/pages/tasks.yaml"].replace(
            "/api/modules/tasks/list_tasks",
            "/api/modules/ghost_module/list_tasks",
        )
        errors = scan_generated_bundle(files)
        assert any("ghost_module" in e and "not declared" in e for e in errors)


class TestNegativeActionHandlerClosure:
    """Declared actions without matching handler implementations are caught
    by production scan_functional_generated_app."""

    def test_missing_handler_method_on_handler_class(self) -> None:
        files = _canonical_fixture()
        # Remove create_task from handler
        files["modules/tasks/backend/handler.py"] = textwrap.dedent("""\
            from .service import TasksService


            class TasksModule:
                def __init__(self):
                    self.service = TasksService()

                async def list_tasks(self, ctx, **params):
                    return await self.service.list_tasks(ctx, **params)
        """)
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "MISSING_MODULE_ACTION" in codes

    def test_handler_file_missing_entirely(self) -> None:
        files = _canonical_fixture()
        del files["modules/tasks/backend/handler.py"]
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "MISSING_MODULE_HANDLER" in codes

    def test_handler_method_name_mismatch(self) -> None:
        files = _canonical_fixture()
        files["modules/tasks/module.yaml"] = files["modules/tasks/module.yaml"].replace(
            "handler_method: create_task",
            "handler_method: create_task_v2",
        )
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "MISSING_MODULE_ACTION" in codes


class TestNegativeEventReactionClosure:
    """Event/reaction schema closure failures are caught by production
    scan_generated_bundle and AppLoader."""

    def test_reaction_references_undeclared_event(self) -> None:
        files = _canonical_fixture()
        files["modules/tasks/contracts/reactions.yaml"] = textwrap.dedent("""\
            schema_version: mozaiks.reactions.v1
            reactions:
              - id: on_ghost_event
                event_type: domain.tasks.ghost_event
                target:
                  kind: notification
                  notification_id: ghost_receipt
        """)
        errors = scan_generated_bundle(files)
        assert any("ghost_event" in e and "not declared" in e for e in errors)

    def test_reaction_uses_unknown_target_kind(self) -> None:
        files = _canonical_fixture()
        files["modules/tasks/contracts/reactions.yaml"] = textwrap.dedent("""\
            schema_version: mozaiks.reactions.v1
            reactions:
              - id: on_task_created_webhook
                event_type: domain.tasks.task_created
                target:
                  kind: webhook
        """)
        errors = scan_generated_bundle(files)
        assert any("target.kind 'webhook' is not canonical" in e for e in errors)

    @pytest.mark.asyncio
    async def test_emitted_event_without_declared_schema_fails_at_load(
        self,
        tmp_path: Path,
    ) -> None:
        """Module loader rejects actions that emit undeclared event types."""
        files = _canonical_fixture()
        # Module action emits an event that is not in events.yaml
        files["modules/tasks/module.yaml"] = files["modules/tasks/module.yaml"].replace(
            "emits:\n      - domain.tasks.task_created",
            "emits:\n      - domain.tasks.task_archived",
        )
        app_root = tmp_path / "app"
        _write_bundle(app_root, files)
        loaded = await AppLoader.load(str(app_root))
        # The tasks module should fail to load due to undeclared emitted event
        assert "tasks" in loaded.failed_module_names

    def test_handler_target_reaction_without_handler_method(self) -> None:
        files = _canonical_fixture()
        files["modules/tasks/contracts/reactions.yaml"] = textwrap.dedent("""\
            schema_version: mozaiks.reactions.v1
            reactions:
              - id: on_task_created_handle
                event_type: domain.tasks.task_created
                target:
                  kind: handler
        """)
        errors = scan_generated_bundle(files)
        assert any("handler_method" in e for e in errors)


class TestNegativePlaceholderDetection:
    """Placeholder/NotImplementedError on required surfaces is caught by
    production scan_functional_generated_app."""

    def test_not_implemented_error_in_service(self) -> None:
        files = _canonical_fixture()
        files["modules/tasks/backend/service.py"] += (
            "\n\nasync def legacy_stub():\n    raise NotImplementedError('TODO')\n"
        )
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "PLACEHOLDER_IMPLEMENTATION" in codes

    def test_501_status_in_handler(self) -> None:
        files = _canonical_fixture()
        files["modules/tasks/backend/handler.py"] += (
            "\n\n# status_code = 501\n"
        )
        diag = scan_functional_generated_app(files)
        codes = {d.code for d in diag}
        assert "PLACEHOLDER_IMPLEMENTATION" in codes


class TestNegativeBundleStructure:
    """Bundle-level structural violations are caught by production
    scan_generated_bundle."""

    def test_unknown_page_type_rejected(self) -> None:
        files = _canonical_fixture()
        files["ui/pages/tasks.yaml"] = files["ui/pages/tasks.yaml"].replace(
            "page_type: record_list",
            "page_type: magic_layout",
        )
        errors = scan_generated_bundle(files)
        assert any("$.page_type: page_schema.literal_error" in e for e in errors)

    def test_unknown_section_primitive_rejected(self) -> None:
        files = _canonical_fixture()
        files["ui/pages/analytics.yaml"] = files["ui/pages/analytics.yaml"].replace(
            "primitive: SummaryStrip",
            "primitive: LegacyWidget",
        )
        errors = scan_generated_bundle(files)
        assert any("page_schema.value_error" in e for e in errors)

    def test_page_missing_name_field(self) -> None:
        files = _canonical_fixture()
        files["ui/pages/analytics.yaml"] = textwrap.dedent("""\
            route: /analytics
            title: Analytics
            sections: []
        """)
        errors = scan_generated_bundle(files)
        assert any("$.name: page_schema.missing" in e for e in errors)


class TestNegativeValidationFacade:
    """The production validation facade (validate_generated_app_bundle) and
    acceptance gate (run_app_bundle_acceptance_gate) catch mutations."""

    def test_validation_facade_fails_on_missing_handler(self) -> None:
        files = _canonical_fixture()
        del files["modules/tasks/backend/handler.py"]
        result = validate_generated_app_bundle(
            GeneratedAppValidationRequest(files=files)
        )
        assert result.passed is False
        error_codes = {d.code for d in result.diagnostics if d.severity == "error"}
        assert "MISSING_MODULE_HANDLER" in error_codes

    @pytest.mark.asyncio
    async def test_acceptance_gate_fails_on_missing_handler(self) -> None:
        files = _canonical_fixture()
        del files["modules/tasks/backend/handler.py"]
        gate = await run_app_bundle_acceptance_gate(files=files)
        assert gate["passed"] is False


class TestNegativeBootFailure:
    """Production AppLoader.load gracefully degrades when a module's handler
    cannot be imported."""

    @pytest.mark.asyncio
    async def test_boot_rejects_module_with_broken_handler_import(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AppLoader.load gracefully handles a module whose handler cannot import."""

        files = _canonical_fixture()
        # Break the handler so it cannot import
        files["modules/tasks/backend/handler.py"] = textwrap.dedent("""\
            from .nonexistent_dependency import BrokenService


            class TasksModule:
                def __init__(self):
                    self.service = BrokenService()

                async def list_tasks(self, ctx, **params):
                    return {}

                async def create_task(self, ctx, **params):
                    return {}
        """)
        app_root = tmp_path / "app"
        _write_bundle(app_root, files)
        loaded = await AppLoader.load(str(app_root))
        # The module should fail to load -- AppLoader reports degraded modules
        assert "tasks" in loaded.failed_module_names


class TestNegativeMissingHealthEndpoint:
    """Health endpoint availability through the production platform host.

    This independently confirms /health returns 200 as a standalone boot
    proof, complementing the positive gate's inline health check.
    """

    @pytest.mark.asyncio
    async def test_health_endpoint_is_exercised(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Confirm /health returns 200 as part of the boot proof."""
        from mozaiksai.hosts import platform

        saved = _save_platform_state()
        try:
            app_root = tmp_path / "app"
            _write_bundle(app_root, _canonical_fixture())
            loaded = await AppLoader.load(str(app_root))
            _configure_platform(
                app_root=app_root,
                loaded=loaded,
                assignments=_FakeCollection(),
                monkeypatch=monkeypatch,
            )
            with TestClient(platform.app, raise_server_exceptions=False) as client:
                health = client.get("/health")
                assert health.status_code == 200
                assert health.json()["status"] == "ok"
        finally:
            _restore_platform_state(saved)
