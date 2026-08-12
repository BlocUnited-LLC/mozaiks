from __future__ import annotations

import json
import textwrap
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.render_infra_scaffold import save_infra_scaffold
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.admin.registry import AdminRegistry, build_admin_shell_routes
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
from mozaiksai.core.auth.adapters.registry import register_adapter, reset_auth_adapter
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition import module_executor as module_executor_mod
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.startup import validation as startup_validation
from mozaiksai.core.validation import GeneratedAppValidationRequest, scan_functional_generated_app
from mozaiksai.core.validation.generated_app import validate_generated_app_bundle
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    _page_from_plan,
    _page_stem_from_path,
)
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
)
from mozaiksai.hosts import platform
from mozaiksai.hosts import runtime as runtime_host

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = ROOT / "factory_app" / "workflows"


class _Context:
    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


class _MatrixAuthAdapter(BaseAuthAdapter):
    name = "matrix"

    async def validate_token(self, token: str) -> UserClaims:  # noqa: ARG002
        return UserClaims(
            user_id="matrix-user",
            email="matrix-user@example.test",
            name="Matrix User",
            roles=["user", "admin"],
            scopes=["access_as_user"],
            raw_claims={},
            provider=self.name,
            app_id="matrix",
        )

    def is_enabled(self) -> bool:
        return True


class _FakeMongoAdmin:
    async def command(self, *_args: Any, **_kwargs: Any) -> dict[str, int]:
        return {"ok": 1}


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeMongoAdmin()

    def __getitem__(self, _name: str) -> _FakeMongoClient:
        return self

    def close(self) -> None:
        return None


class _PersistenceCollection:
    def __init__(self, *, app_id: str, module_id: str, entity_name: str, rows: list[dict[str, Any]]) -> None:
        self.app_id = app_id
        self.module_id = module_id
        self.entity_name = entity_name
        self.rows = rows

    async def insert_one(self, document: Mapping[str, Any]) -> dict[str, Any]:
        row = {"app_id": self.app_id, **dict(document)}
        self.rows.append(row)
        return {"inserted_id": row.get("_id") or row.get("id")}

    async def find_many(
        self,
        query: Mapping[str, Any] | None = None,
        *,
        limit: int = 50,
        sort: Any = None,
        projection: Any = None,
    ) -> list[dict[str, Any]]:
        criteria = {"app_id": self.app_id, **dict(query or {})}
        rows = [
            dict(row)
            for row in self.rows
            if all(row.get(key) == value for key, value in criteria.items())
        ]
        return rows[:limit]


class _PersistenceContext:
    stores: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def __init__(
        self,
        *,
        app_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        **_kwargs: Any,
    ) -> None:
        self.app_id = app_id
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.user_id = user_id

    def collection(self, module_id: str, entity_name: str) -> _PersistenceCollection:
        rows = self.stores.setdefault((module_id, entity_name), [])
        return _PersistenceCollection(
            app_id=self.app_id,
            module_id=module_id,
            entity_name=entity_name,
            rows=rows,
        )

    @classmethod
    def reset(cls) -> None:
        cls.stores = {}


class _RuntimeAcceptanceCollection:
    async def find_one(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def update_one(self, *args: Any, **kwargs: Any) -> None:
        return None


class _RuntimeAcceptancePersistence:
    def __init__(self) -> None:
        self.created_sessions: list[dict[str, Any]] = []

    async def create_chat_session(self, **kwargs: Any) -> None:
        self.created_sessions.append(dict(kwargs))

    async def get_or_assign_cache_seed(self, chat_id: str, app_id: str | None = None) -> int:
        return 12345

    async def load_run_history(self, chat_id: str, app_id: str | None = None) -> list[dict[str, Any]]:
        return []


@dataclass(frozen=True)
class _ArchetypeSpec:
    archetype_id: str
    app_id: str
    app_name: str
    plan: dict[str, Any]
    modules: tuple[str, ...]
    pages: tuple[str, ...]
    runtime_checks: Callable[[TestClient, dict[str, str], Any], None]
    extra_files: dict[str, str] = field(default_factory=dict)
    workspace_files: dict[str, str] = field(default_factory=dict)
    auth_enabled: bool = False


def _fixture_plan(name: str) -> dict[str, Any]:
    raw = json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))
    return raw.get("AppBuildPlan", raw)


def _file_map(assembled: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(item["filename"]): str(item["content"])
        for item in assembled.get("code_files") or []
        if isinstance(item, Mapping) and item.get("filename")
    }


def _write_files(root: Path, files: Mapping[str, str]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _validation_pages(plan: Mapping[str, Any], files: Mapping[str, str]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for page in plan.get("pages") or []:
        if not isinstance(page, Mapping):
            continue
        stem = None
        for rel_path in files:
            candidate = _page_stem_from_path(rel_path)
            if candidate and candidate == _page_stem_from_path(f"ui/pages/{page.get('name')}.yaml"):
                stem = candidate
                break
        if stem is None:
            route = str(page.get("route") or "").strip()
            stem = route.strip("/").split("/")[-1] if route and route != "/" else str(page.get("name") or "page")
        pages.append(_page_from_plan(dict(page), stem=stem))
    return pages


def _assert_not_missing_or_placeholder(response: Any, *, surface: str) -> None:
    body = response.text.lower()
    assert response.status_code != 404, f"ROUTE_GAP surface={surface} body={response.text}"
    assert response.status_code != 501, f"RUNTIME_GAP surface={surface} body={response.text}"
    assert "not implemented" not in body, f"MATERIALIZATION_GAP surface={surface} body={response.text}"
    assert "not_implemented" not in body, f"MATERIALIZATION_GAP surface={surface} body={response.text}"


def _module_contract(module_id: str, actions: list[tuple[str, str]], *, entitlement: str | None = None) -> str:
    action_entries = []
    for action_id, description in actions:
        entry: dict[str, Any] = {
            "id": action_id,
            "description": description,
            "handler_method": action_id,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
        if entitlement and action_id.startswith(("export_", "publish_")):
            entry["entitlement_gate"] = entitlement
        action_entries.append(entry)
    return yaml.safe_dump(
        {
            "schema_version": "mozaiks.module.v1",
            "module": {
                "id": module_id,
                "display_name": module_id.replace("_", " ").title(),
                "version": "1.0.0",
                "handler": "backend.handler:GeneratedHandler",
            },
            "actions": action_entries,
        },
        sort_keys=False,
    )


def _plan_actions(spec: _ArchetypeSpec, module_id: str) -> list[str]:
    for pack in spec.plan.get("capability_packs") or []:
        if not isinstance(pack, Mapping):
            continue
        if str(pack.get("capability_pack_id") or pack.get("surface_id") or "") != module_id:
            continue
        actions = [str(action) for action in pack.get("operations") or [] if str(action).strip()]
        if actions:
            return actions
    return []


def _simple_backend(module_id: str, actions: list[str]) -> dict[str, str]:
    methods = []
    for action in actions:
        if action.startswith("list_"):
            payload = '{"items": [], "count": 0}'
        elif action.startswith("create_"):
            payload = '{"created": True, "payload": params}'
        elif action.startswith("export_"):
            payload = '{"exported": True, "download_url": "/exports/matrix.csv"}'
        elif action.startswith("publish_"):
            payload = '{"published": True}'
        elif action == "summarize_source":
            payload = '{"summary": str(params.get("source_text") or "")[:32]}'
        else:
            payload = '{"ok": True, "action": "' + action + '"}'
        methods.append(
            textwrap.dedent(
                f"""
                async def {action}(self, ctx, **params):
                    return {payload}
                """
            ).strip()
        )
    return {
        f"modules/{module_id}/backend/handler.py": (
            "class GeneratedHandler:\n"
            + "\n\n".join(textwrap.indent(method, "    ") for method in methods)
            + "\n"
        ),
        f"modules/{module_id}/backend/repo.py": (
            "class GeneratedRepo:\n"
            "    async def list(self):\n"
            "        return []\n"
        ),
        f"modules/{module_id}/backend/policy.py": "def scoped_query(filters):\n    return dict(filters or {})\n",
        f"modules/{module_id}/backend/service.py": (
            "class GeneratedService:\n"
            "    async def health(self):\n"
            "        return {\"ok\": True}\n"
        ),
    }


def _persistent_backend(module_id: str, singular: str, title_field: str) -> dict[str, str]:
    return {
        f"modules/{module_id}/backend/handler.py": textwrap.dedent(
            f"""
            from .repo import Repo


            class GeneratedHandler:
                async def create_{singular}(self, ctx, **params):
                    record = {{
                        "id": str(params.get("{singular}_id") or params.get("id") or "{singular}-1"),
                        "{title_field}": str(params.get("{title_field}") or params.get("name") or params.get("title") or "Untitled"),
                        "status": str(params.get("status") or "open"),
                    }}
                    if params.get("project_id"):
                        record["project_id"] = params["project_id"]
                    return await Repo("{module_id}").create(ctx, record)

                async def list_{module_id}(self, ctx, **params):
                    return {{"items": await Repo("{module_id}").list(ctx, params), "count": 0}}
            """
        ).strip()
        + "\n",
        f"modules/{module_id}/backend/repo.py": textwrap.dedent(
            f"""
            class Repo:
                def __init__(self, entity_name):
                    self.entity_name = entity_name

                async def create(self, ctx, record):
                    collection = ctx.persistence.collection("{module_id}", self.entity_name)
                    await collection.insert_one(record)
                    return record

                async def list(self, ctx, query):
                    collection = ctx.persistence.collection("{module_id}", self.entity_name)
                    return await collection.find_many(query or {{}}, limit=50)
            """
        ).strip()
        + "\n",
        f"modules/{module_id}/backend/service.py": (
            "class GeneratedService:\n"
            "    async def health(self):\n"
            "        return {\"ok\": True}\n"
        ),
        f"modules/{module_id}/backend/policy.py": "def scoped_query(filters):\n    return dict(filters or {})\n",
    }


def _database_files(app_id: str, modules: tuple[str, ...], *, migration_path: str) -> dict[str, str]:
    surfaces = [
        {
            "surface_id": module_id,
            "surface_kind": "module",
            "collections": [{"module_id": module_id, "name": module_id, "entity_name": module_id}],
        }
        for module_id in modules
    ]
    return {
        "data/contract.json": json.dumps({"version": "1", "app_id": app_id, "surfaces": surfaces}, indent=2, sort_keys=True) + "\n",
        migration_path: json.dumps(
            {
                "migration_id": "001_indexes",
                "version": "1",
                "operations": [
                    {"type": "ensure_collection", "module_id": module_id, "entity_name": module_id}
                    for module_id in modules
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }


def _base_plan(
    *,
    app_kind: str,
    pages: list[dict[str, Any]],
    modules: dict[str, list[str]],
    auth_strategy: str = "none",
    extra_tasks: list[dict[str, Any]] | None = None,
    capability_packs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = [
        {
            "task_id": "task_persistence",
            "task_type": "persistence_contract",
            "capability_pack_id": None,
            "surface_id": "database",
            "surface_kind": None,
            "execution_target": "AppGenerator",
            "initial_agent": "DatabaseAgent",
            "description": "Stage deterministic data contract.",
            "initial_message": "Generate data/contract.json and data/migrations/001_indexes.json.",
            "owned_paths": ["data/contract.json", "data/migrations/001_indexes.json"],
            "depends_on": [],
        }
    ]
    packs = list(capability_packs or [])
    for module_id, actions in modules.items():
        if not any(pack.get("capability_pack_id") == module_id for pack in packs):
            packs.append(
                {
                    "capability_pack_id": module_id,
                    "surface_id": module_id,
                    "surface_kind": "module",
                    "pack_type": "crud_pack",
                    "label": module_id.replace("_", " ").title(),
                    "summary": f"{module_id} module.",
                    "implementation_mode": "deterministic",
                    "primary_entities": [module_id.title()],
                    "primary_pages": [page["name"] for page in pages],
                    "operations": actions,
                    "required_integrations": [],
                    "agentic_extensions": [],
                }
            )
        tasks.extend(
            [
                {
                    "task_id": f"task_{module_id}_module",
                    "task_type": "module_contract",
                    "capability_pack_id": module_id,
                    "surface_id": module_id,
                    "surface_kind": "module",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ConfigMiddlewareAgent",
                    "description": f"Generate the {module_id} module contract.",
                    "initial_message": f"Generate modules/{module_id}/module.yaml.",
                    "owned_paths": [f"modules/{module_id}/module.yaml"],
                    "depends_on": ["task_persistence"],
                },
                {
                    "task_id": f"task_{module_id}_models",
                    "task_type": "data_models",
                    "capability_pack_id": module_id,
                    "surface_id": module_id,
                    "surface_kind": "module",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ModelAgent",
                    "description": f"Generate the {module_id} data shape contract.",
                    "initial_message": f"Generate modules/{module_id}/backend/schemas.py.",
                    "owned_paths": [f"modules/{module_id}/backend/schemas.py"],
                    "depends_on": [f"task_{module_id}_module"],
                },
                {
                    "task_id": f"task_{module_id}_services",
                    "task_type": "business_services",
                    "capability_pack_id": module_id,
                    "surface_id": module_id,
                    "surface_kind": "module",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ServiceAgent",
                    "description": f"Generate the {module_id} backend service files.",
                    "initial_message": f"Generate backend files for {module_id}.",
                    "owned_paths": [
                        f"modules/{module_id}/backend/handler.py",
                        f"modules/{module_id}/backend/service.py",
                        f"modules/{module_id}/backend/repo.py",
                        f"modules/{module_id}/backend/policy.py",
                    ],
                    "depends_on": [f"task_{module_id}_models"],
                },
            ]
        )
    page_paths = [f"ui/pages/{str(page['name']).lower().replace(' ', '_')}.yaml" for page in pages]
    tasks.append(
        {
            "task_id": "task_pages",
            "task_type": "page_bundle",
            "capability_pack_id": None,
            "surface_id": "pages",
            "surface_kind": "ui_only",
            "execution_target": "AppGenerator",
            "initial_agent": "AppSchemaAgent",
            "description": "Generate declarative page schemas.",
            "initial_message": "Generate app.json and ui/pages/*.yaml files from AppBuildPlan pages.",
            "owned_paths": ["app.json", *page_paths],
            "depends_on": [f"task_{module_id}_services" for module_id in modules],
        }
    )
    tasks.extend(extra_tasks or [])
    return {
        "agent_message": f"Captured {app_kind} AppBuildPlan.",
        "app_kind": app_kind,
        "pages": pages,
        "entities": [{"name": module_id.title()} for module_id in modules],
        "roles": [{"id": "user", "label": "User"}],
        "auth_strategy": auth_strategy,
        "service_scope": list(modules),
        "frontend_scope": [str(page["name"]) for page in pages],
        "capability_packs": packs,
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": tasks,
        "generation_order": [task["task_id"] for task in tasks],
    }


def _app_task_output(spec: _ArchetypeSpec, *, task_type: str, task: Mapping[str, Any]) -> dict[str, Any]:
    module_id = str(task.get("capability_pack_id") or "")
    if task_type == "persistence_contract":
        owned_paths = [str(path) for path in task.get("owned_paths") or []]
        migration_path = next(
            (path for path in owned_paths if path.startswith("data/migrations/")),
            "data/migrations/001_indexes.json",
        )
        return {"code_files": [{"filename": path, "content": content} for path, content in _database_files(spec.app_id, spec.modules, migration_path=migration_path).items()]}
    if task_type == "module_contract":
        owned_paths = [str(path) for path in task.get("owned_paths") or []]
        if module_id == "reports":
            actions = [("view_report", "View reports."), ("export_report", "Export reports.")]
            files = [{"filename": "modules/reports/module.yaml", "content": _module_contract("reports", actions, entitlement="reports.export")}]
            files.extend(
                {"filename": path, "content": "schema_version: mozaiks.events.v1\nevents: []\n"}
                for path in owned_paths
                if path.endswith("/contracts/events.yaml")
            )
            return {"code_files": files}
        if module_id == "entitlement_dispatch":
            actions = [
                ("activate_subscription", "Activate subscription."),
                ("deactivate_subscription", "Deactivate subscription."),
            ]
            return {"code_files": [{"filename": "modules/entitlement_dispatch/module.yaml", "content": _module_contract("entitlement_dispatch", actions)}]}
        actions = {
            "projects": [("create_project", "Create project."), ("list_projects", "List projects.")],
            "tasks": [("create_task", "Create task."), ("list_tasks", "List tasks.")],
            "research": [("summarize_source", "Summarize source."), ("start_research", "Start research workflow.")],
            "incidents": [("create_incident", "Create incident."), ("list_incidents", "List incidents.")],
            "posts": [("create_post", "Create post."), ("list_posts", "List posts.")],
            "comments": [("create_comment", "Create comment."), ("list_comments", "List comments.")],
        }.get(module_id)
        if actions is None:
            actions = [(action, f"{action.replace('_', ' ').title()}.") for action in _plan_actions(spec, module_id)]
        files = [{"filename": f"modules/{module_id}/module.yaml", "content": _module_contract(module_id, actions)}]
        files.extend(
            {"filename": path, "content": "schema_version: mozaiks.events.v1\nevents: []\n"}
            for path in owned_paths
            if path.endswith("/contracts/events.yaml")
        )
        return {"code_files": files}
    if task_type == "business_services":
        owned_paths = {str(path) for path in task.get("owned_paths") or []}
        if module_id == "entitlement_dispatch":
            backend_files = _simple_backend(
                "entitlement_dispatch",
                ["activate_subscription", "deactivate_subscription"],
            )
            return {
                "code_files": [
                    {"filename": path, "content": content}
                    for path, content in backend_files.items()
                    if not owned_paths or path in owned_paths
                ]
            }
        if module_id in {"projects", "tasks"}:
            singular = module_id[:-1]
            title_field = "name" if module_id == "projects" else "title"
            return {"code_files": [{"filename": path, "content": content} for path, content in _persistent_backend(module_id, singular, title_field).items()]}
        actions = {
            "reports": ["view_report", "export_report"],
            "research": ["summarize_source", "start_research"],
            "incidents": ["create_incident", "list_incidents"],
            "posts": ["create_post", "list_posts"],
            "comments": ["create_comment", "list_comments"],
        }.get(module_id)
        if actions is None:
            actions = _plan_actions(spec, module_id)
        backend_files = _simple_backend(module_id, actions)
        return {
            "code_files": [
                {"filename": path, "content": content}
                for path, content in backend_files.items()
                if not owned_paths or path in owned_paths
            ]
        }
    if task_type == "data_models":
        owned_paths = [str(path) for path in task.get("owned_paths") or []]
        return {
            "code_files": [
                {"filename": path, "content": "from typing import TypedDict\n\n\nclass MatrixRecord(TypedDict, total=False):\n    id: str\n"}
                for path in owned_paths
                if path.endswith(".py")
            ]
        }
    if task_type == "subscription_config":
        return {
            "code_files": [
                {
                    "filename": "config/subscriptions.yaml",
                    "content": textwrap.dedent(
                        """
                        schema_version: mozaiks.subscriptions.v1
                        label: Matrix SaaS
                        default_plan_id: free
                        assignment_store:
                          data_alias: billing.subscriptions
                          app_id_field: app_id
                          user_id_field: user_id
                          status_field: status
                          plan_id_field: plan_id
                          capabilities_field: granted_capabilities
                          active_statuses: [active, trialing]
                        plans:
                          - plan_id: free
                            label: Free
                            capabilities: [reports.view]
                          - plan_id: pro
                            label: Pro
                            capabilities: [reports.view, reports.export]
                        """
                    ).lstrip(),
                }
            ]
        }
    if task_type == "page_bundle":
        return {
            "code_files": [
                {
                    "filename": "app.json",
                    "content": json.dumps(
                        {
                            "appId": spec.app_id,
                            "appName": spec.app_name,
                            "version": "1.0.0",
                            "authRequired": spec.auth_enabled,
                            "startup": {"landing_spot": spec.plan["pages"][0]["route"]},
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                }
            ]
        }
    raise AssertionError(f"Unhandled matrix task: {task_type} {task}")


async def _materialize_spec(spec: _ArchetypeSpec, tmp_path: Path) -> tuple[dict[str, str], Path, Any]:
    ctx = _Context(
        {
            "app_id": spec.app_id,
            "app_name": spec.app_name,
            "app_slug": spec.app_id,
            "chat_id": f"{spec.app_id}-chat",
            "build_task_model": "AppBuildTask",
            "app_validation_strategy_used": "skip",
            "app_validation_status": "skipped",
        }
    )
    app_build_plan(AppBuildPlan=spec.plan, context_variables=ctx)
    assert ctx.get("app_plan_ready") is True, f"PLAN_LOSS archetype={spec.archetype_id}"

    task_batches = load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS_ROOT)
    assert task_batches is not None

    async def _fake_run(self: Any, request: Any) -> AG2TaskBatchRunnerResult:  # noqa: ARG001
        task = dict(request.context_variables.get("current_build_task") or {})
        output = _app_task_output(
            spec,
            task_type=str(request.context_variables.get("current_build_task_type") or task.get("task_type")),
            task=task,
        )
        return AG2TaskBatchRunnerResult(
            status=RunStatus.COMPLETED,
            output=output,
            channel_id=f"{request.batch_id}:{request.task_id}",
            close_reason="deterministic_archetype_matrix",
        )

    import mozaiksai.core.adapters.ag2_task_batch_runner as ag2_task_batch_runner

    original_run = ag2_task_batch_runner.AG2TaskBatchRunner.run
    ag2_task_batch_runner.AG2TaskBatchRunner.run = _fake_run
    try:
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator",
            trigger_agent="AppPlanAgent",
            batches_config=task_batches,
            agents={
                "ConfigMiddlewareAgent": object(),
                "DatabaseAgent": object(),
                "ModelAgent": object(),
                "ServiceAgent": object(),
                "AppSchemaAgent": object(),
            },
            context_variables=ctx.data,
            chat_id=ctx.get("chat_id"),
            app_id=ctx.get("app_id"),
            user_id="matrix-user",
            fresh_agents_per_task=False,
        )
    finally:
        ag2_task_batch_runner.AG2TaskBatchRunner.run = original_run

    assembled = await assemble_app_tasks(context_variables=ctx)
    files = _file_map(assembled)
    if spec.auth_enabled:
        auth_scaffold = await save_infra_scaffold(
            emit_infra=False,
            emit_auth_adapter=True,
            context_variables=ctx.data,
        )
        files.update(_file_map(auth_scaffold))
    files.update(spec.extra_files)

    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=files,
            pages=_validation_pages(spec.plan, files),
            build_tasks=spec.plan.get("build_tasks", []),
            capability_packs=spec.plan.get("capability_packs", []),
        )
    )
    assert validation.passed is True, f"MATERIALIZATION_GAP {spec.archetype_id}: {validation.diagnostics}"
    assert scan_functional_generated_app(files, capability_packs=spec.plan.get("capability_packs", [])) == []
    gate = await run_app_bundle_acceptance_gate(
        files=files,
        context_variables=ctx,
        capability_packs=spec.plan.get("capability_packs", []),
    )
    assert gate["passed"] is True, f"MATERIALIZATION_GAP {spec.archetype_id}: {gate}"

    for page in spec.plan.get("pages", []):
        route = str(page.get("route") or "")
        if route:
            assert any(f"route: {route}" in content for path, content in files.items() if path.startswith("ui/pages/")), (
                f"PLAN_LOSS archetype={spec.archetype_id} route={route}"
            )
    for module_id in spec.modules:
        assert f"modules/{module_id}/module.yaml" in files, f"MODULE_GAP archetype={spec.archetype_id} module={module_id}"

    app_root = tmp_path / spec.archetype_id / "app"
    _write_files(app_root, files)
    if spec.workspace_files:
        _write_files(app_root.parent, spec.workspace_files)
    loaded = await AppLoader.load(str(app_root))
    assert {module.name for module in loaded.modules} == set(spec.modules)
    for page_name in spec.pages:
        assert page_name in {page.name for page in loaded.definition.pages}
    return files, app_root, loaded


def _configure_platform(
    *,
    app_root: Path,
    loaded: Any,
    spec: _ArchetypeSpec,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "true" if spec.auth_enabled else "false")
    monkeypatch.setenv("AUTH_PROVIDER", "matrix")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    if spec.workspace_files:
        monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(app_root.parent / "workflows"))
    fake_mongo = _FakeMongoClient()
    monkeypatch.setattr(runtime_host, "get_mongo_client", lambda: fake_mongo)
    monkeypatch.setattr(startup_validation, "get_mongo_client", lambda: fake_mongo)
    monkeypatch.setattr(module_executor_mod, "MongoPersistenceContext", _PersistenceContext)
    reset_auth_adapter()
    if spec.auth_enabled:
        register_adapter("matrix", _MatrixAuthAdapter)

    executor = ModuleExecutor()
    for module in loaded.modules:
        executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
            action_entitlements=module.action_entitlement_map,
        )
    registry = ExecutorRegistry()
    registry.register(executor)
    platform.app.state.executor_registry = registry
    platform.app.state.subscriptions_config = loaded.subscriptions_config
    platform.app.state.failed_module_names = []
    platform.app.state.startup_degraded = False
    platform.app.state.startup_degraded_reason = None
    platform.app.state.module_action_surfaces = {
        module.name: module.action_api_surface_map for module in loaded.modules
    }


def _admin_registry_file() -> dict[str, str]:
    return {
        "admin/admin_registry.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.admin.registry.v1
            pages:
              - id: operations
                label: Operations
                path: /admin/operations
                order: 10
                enabled: true
                scope: app
                surfaces: [platform]
            """
        ).lstrip()
    }


def _workflow_files() -> dict[str, str]:
    return {
        "workflows/ResearchWorkflow/orchestrator.yaml": textwrap.dedent(
            """
            workflow_name: ResearchWorkflow
            max_turns: 3
            human_in_the_loop: false
            workflow_startup_mode: AgentDriven
            orchestration_pattern: ag2_network
            initial_message: ResearchAgent, summarize the supplied local source text.
            initial_agent: ResearchAgent
            """
        ).lstrip(),
        "workflows/ResearchWorkflow/agents.yaml": textwrap.dedent(
            """
            agents:
              - name: ResearchAgent
                prompt_sections:
                  - id: role
                    heading: '[ROLE]'
                    content: Deterministically summarize local source text.
                  - id: output
                    heading: '[OUTPUT]'
                    content: Return valid JSON matching ResearchSummary.
                max_consecutive_auto_reply: 2
                structured_outputs_required: true
            """
        ).lstrip(),
        "workflows/ResearchWorkflow/context_variables.yaml": textwrap.dedent(
            """
            definitions:
              source_text:
                source:
                  type: state
                  default: Local deterministic source
            agents:
              ResearchAgent:
                variables: [source_text]
            """
        ).lstrip(),
        "workflows/ResearchWorkflow/structured_outputs.yaml": textwrap.dedent(
            """
            registry:
              ResearchAgent: ResearchSummary
            models:
              ResearchSummary:
                type: model
                fields:
                  summary:
                    type: str
                    description: Source summary.
            """
        ).lstrip(),
        "workflows/ResearchWorkflow/tools.yaml": textwrap.dedent(
            """
            tools:
              - agent: ResearchAgent
                file: summarize.py
                function: summarize_source
                tool_type: Agent_Tool
                auto_tool_call: false
            lifecycle_tools: []
            """
        ).lstrip(),
        "workflows/ResearchWorkflow/tools/__init__.py": "",
        "workflows/ResearchWorkflow/tools/summarize.py": "async def summarize_source(source_text: str):\n    return {'summary': source_text[:32]}\n",
        "workflows/ResearchWorkflow/transition_graph.yaml": textwrap.dedent(
            """
            transition_rules:
              - source_agent: ResearchAgent
                target_agent: terminate
                transition_type: after_turn
            """
        ).lstrip(),
        "workflows/ResearchWorkflow/ui_config.yaml": "visual_agents:\n  - ResearchAgent\n",
    }


def _crud_checks(client: TestClient, _files: dict[str, str], _loaded: Any) -> None:
    headers = {"Authorization": "Bearer matrix-token"}
    for name, route in (("projects", "/projects"), ("tasks", "/tasks")):
        page = client.get(f"/api/pages/{name}", headers=headers)
        _assert_not_missing_or_placeholder(page, surface=f"/api/pages/{name}")
        assert page.status_code == 200
        assert page.json()["route"] == route
    create_project = client.post(
        "/api/modules/projects/create_project",
        json={"params": {"name": "Matrix Project"}},
        headers=headers,
    )
    _assert_not_missing_or_placeholder(create_project, surface="/api/modules/projects/create_project")
    assert create_project.status_code == 200
    list_projects = client.post("/api/modules/projects/list_projects", json={"params": {}}, headers=headers)
    _assert_not_missing_or_placeholder(list_projects, surface="/api/modules/projects/list_projects")
    assert list_projects.status_code == 200
    assert list_projects.json()["items"][0]["name"] == "Matrix Project"


def _saas_checks(client: TestClient, _files: dict[str, str], _loaded: Any) -> None:
    headers = {"Authorization": "Bearer matrix-token"}
    page = client.get("/api/pages/reports", headers=headers)
    _assert_not_missing_or_placeholder(page, surface="/api/pages/reports")
    assert page.status_code == 200
    list_reports = client.post("/api/modules/reports/view_report", json={"params": {}}, headers=headers)
    _assert_not_missing_or_placeholder(list_reports, surface="/api/modules/reports/view_report")
    assert list_reports.status_code == 200
    export_report = client.post("/api/modules/reports/export_report", json={"params": {}}, headers=headers)
    _assert_not_missing_or_placeholder(export_report, surface="/api/modules/reports/export_report")
    assert export_report.status_code in {200, 402}


def _workflow_checks(client: TestClient, _files: dict[str, str], _loaded: Any) -> None:
    import os

    from mozaiksai.core.workflow.workflow_manager import initialize_workflows, workflow_manager

    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"])
    initialize_workflows(str(workflow_root))
    assert "ResearchWorkflow" in workflow_manager.get_all_workflow_names(), "WORKFLOW_GAP workflow=ResearchWorkflow"
    headers = {"Authorization": "Bearer matrix-token"}
    workflows = client.get("/api/workflows", headers=headers)
    _assert_not_missing_or_placeholder(workflows, surface="/api/workflows")
    assert workflows.status_code in {200, 401, 403}
    summarize = client.post(
        "/api/modules/research/summarize_source",
        json={"params": {"source_text": "Deterministic local research source"}},
        headers=headers,
    )
    _assert_not_missing_or_placeholder(summarize, surface="/api/modules/research/summarize_source")
    assert summarize.status_code == 200
    assert summarize.json()["summary"] == "Deterministic local research sou"


def _admin_checks(client: TestClient, _files: dict[str, str], _loaded: Any) -> None:
    shell = client.get("/api/shell-config?surface=platform")
    _assert_not_missing_or_placeholder(shell, surface="/api/shell-config")
    assert shell.status_code == 200
    paths = {page.get("path") for page in shell.json().get("pages", [])}
    assert "/operations" in paths
    admin_registry = AdminRegistry.model_validate(yaml.safe_load(_files["admin/admin_registry.yaml"]) or {})
    admin_routes = {route["path"] for route in build_admin_shell_routes(admin_registry)}
    assert "/admin/operations" in admin_routes
    denied = client.post("/api/modules/incidents/list_incidents", json={"params": {}})
    _assert_not_missing_or_placeholder(denied, surface="/api/modules/incidents/list_incidents")
    assert denied.status_code in {401, 403}
    incidents = client.post(
        "/api/modules/incidents/list_incidents",
        json={"params": {}},
        headers={"Authorization": "Bearer matrix-token"},
    )
    _assert_not_missing_or_placeholder(incidents, surface="/api/modules/incidents/list_incidents")
    assert incidents.status_code == 200


def _community_checks(client: TestClient, _files: dict[str, str], _loaded: Any) -> None:
    for name in ("posts", "comments"):
        page = client.get(f"/api/pages/{name}")
        _assert_not_missing_or_placeholder(page, surface=f"/api/pages/{name}")
        assert page.status_code == 200
    denied = client.post("/api/modules/posts/create_post", json={"params": {"title": "Hello"}})
    _assert_not_missing_or_placeholder(denied, surface="/api/modules/posts/create_post")
    assert denied.status_code in {401, 403}
    headers = {"Authorization": "Bearer matrix-token"}
    post = client.post("/api/modules/posts/create_post", json={"params": {"title": "Hello"}}, headers=headers)
    _assert_not_missing_or_placeholder(post, surface="/api/modules/posts/create_post")
    assert post.status_code == 200
    comments = client.post("/api/modules/comments/list_comments", json={"params": {}}, headers=headers)
    _assert_not_missing_or_placeholder(comments, surface="/api/modules/comments/list_comments")
    assert comments.status_code == 200


def _matrix_specs() -> list[_ArchetypeSpec]:
    saas_plan = _fixture_plan("appplan_saas_entitlement_dispatch_output.json")
    projects_plan = _base_plan(
        app_kind="authenticated_crud_projects",
        pages=[
            {
                "name": "projects",
                "route": "/projects",
                "title": "Projects",
                "page_type_hint": "record_list",
                "sections_hint": [
                    {
                        "primitive": "DataTable",
                        "section_id_hint": "projects",
                        "title_hint": "Projects",
                        "config_hint": json.dumps({"api_endpoint": "/api/modules/projects/list_projects"}),
                    }
                ],
            },
            {
                "name": "tasks",
                "route": "/tasks",
                "title": "Tasks",
                "page_type_hint": "record_list",
                "sections_hint": [
                    {
                        "primitive": "DataTable",
                        "section_id_hint": "tasks",
                        "title_hint": "Tasks",
                        "config_hint": json.dumps({"api_endpoint": "/api/modules/tasks/list_tasks"}),
                    }
                ],
            },
        ],
        modules={"projects": ["create_project", "list_projects"], "tasks": ["create_task", "list_tasks"]},
        auth_strategy="required",
    )
    workflow_plan = _base_plan(
        app_kind="workflow_agent",
        pages=[
            {
                "name": "research",
                "route": "/research",
                "title": "Research",
                "page_type_hint": "workflow_board",
                "sections_hint": [
                    {
                        "primitive": "ActionButton",
                        "section_id_hint": "start-research",
                        "title_hint": "Start Research",
                        "config_hint": json.dumps({"api_endpoint": "/api/modules/research/start_research"}),
                    }
                ],
            }
        ],
        modules={"research": ["summarize_source", "start_research"]},
    )
    workflow_plan["agent_backend_required"] = True
    workflow_plan["workflow_touchpoints"] = [
        {
            "page_name": "research",
            "workflow_id": "ResearchWorkflow",
            "label": "Start research",
            "context_variables": {"source_text": "Local deterministic source"},
        }
    ]
    admin_plan = _base_plan(
        app_kind="admin_operations_dashboard",
        pages=[
            {
                "name": "operations",
                "route": "/operations",
                "title": "Operations",
                "page_type_hint": "analytics_dashboard",
                "sections_hint": [
                    {
                        "primitive": "DataTable",
                        "section_id_hint": "incidents",
                        "title_hint": "Incidents",
                        "config_hint": json.dumps({"api_endpoint": "/api/modules/incidents/list_incidents"}),
                    }
                ],
            }
        ],
        modules={"incidents": ["create_incident", "list_incidents"]},
    )
    community_plan = _base_plan(
        app_kind="community_content",
        pages=[
            {
                "name": "posts",
                "route": "/posts",
                "title": "Posts",
                "page_type_hint": "activity_feed",
                "sections_hint": [
                    {
                        "primitive": "ResourceList",
                        "section_id_hint": "posts",
                        "title_hint": "Posts",
                        "config_hint": json.dumps({"api_endpoint": "/api/modules/posts/list_posts"}),
                    }
                ],
            },
            {
                "name": "comments",
                "route": "/comments",
                "title": "Comments",
                "page_type_hint": "record_list",
                "sections_hint": [
                    {
                        "primitive": "DataTable",
                        "section_id_hint": "comments",
                        "title_hint": "Comments",
                        "config_hint": json.dumps({"api_endpoint": "/api/modules/comments/list_comments"}),
                    }
                ],
            },
        ],
        modules={"posts": ["create_post", "list_posts"], "comments": ["create_comment", "list_comments"]},
    )
    return [
        _ArchetypeSpec(
            archetype_id="authenticated_crud_projects",
            app_id="project_management",
            app_name="Project Management",
            plan=projects_plan,
            modules=("projects", "tasks"),
            pages=("projects", "tasks"),
            runtime_checks=_crud_checks,
            auth_enabled=True,
        ),
        _ArchetypeSpec(
            archetype_id="monetized_saas_reports",
            app_id="generated-saas-plan",
            app_name="Generated SaaS Plan",
            plan=saas_plan,
            modules=("entitlement_dispatch", "reports"),
            pages=("reports",),
            runtime_checks=_saas_checks,
            auth_enabled=True,
        ),
        _ArchetypeSpec(
            archetype_id="workflow_agent_research",
            app_id="workflow-agent-matrix",
            app_name="Workflow Agent Matrix",
            plan=workflow_plan,
            modules=("research",),
            pages=("research",),
            runtime_checks=_workflow_checks,
            workspace_files=_workflow_files(),
            auth_enabled=True,
        ),
        _ArchetypeSpec(
            archetype_id="admin_operations_dashboard",
            app_id="ops-dashboard",
            app_name="Operations Dashboard",
            plan=admin_plan,
            modules=("incidents",),
            pages=("operations",),
            runtime_checks=_admin_checks,
            extra_files=_admin_registry_file(),
            auth_enabled=True,
        ),
        _ArchetypeSpec(
            archetype_id="community_content",
            app_id="community-content",
            app_name="Community Content",
            plan=community_plan,
            modules=("posts", "comments"),
            pages=("posts", "comments"),
            runtime_checks=_community_checks,
            auth_enabled=True,
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", _matrix_specs(), ids=lambda spec: spec.archetype_id)
async def test_appbuildplan_archetype_matrix_materializes_deterministically_and_boots(
    spec: _ArchetypeSpec,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_state = {
        "executor_registry": getattr(platform.app.state, "executor_registry", None),
        "subscriptions_config": getattr(platform.app.state, "subscriptions_config", None),
        "failed_module_names": list(getattr(platform.app.state, "failed_module_names", [])),
        "startup_degraded": getattr(platform.app.state, "startup_degraded", False),
        "startup_degraded_reason": getattr(platform.app.state, "startup_degraded_reason", None),
        "module_action_surfaces": deepcopy(getattr(platform.app.state, "module_action_surfaces", {})),
    }
    _PersistenceContext.reset()
    try:
        files_one, app_root, loaded = await _materialize_spec(spec, tmp_path / "run1")
        files_two, _, _ = await _materialize_spec(spec, tmp_path / "run2")
        assert files_one == files_two, f"MATERIALIZATION_GAP nondeterministic file map for {spec.archetype_id}"

        _configure_platform(app_root=app_root, loaded=loaded, spec=spec, monkeypatch=monkeypatch)
        fake_persistence = _RuntimeAcceptancePersistence()
        fake_chat_collection = _RuntimeAcceptanceCollection()

        async def _fake_chat_coll() -> _RuntimeAcceptanceCollection:
            return fake_chat_collection

        monkeypatch.setattr(platform, "persistence_manager", fake_persistence)
        monkeypatch.setattr(platform.runtime_app, "persistence_manager", fake_persistence)
        monkeypatch.setattr(platform.runtime_app, "_chat_coll", _fake_chat_coll)
        monkeypatch.setattr(platform.runtime_app, "simple_transport", None)
        monkeypatch.setattr(platform.runtime_app, "mongo_client", _FakeMongoClient())

        with TestClient(platform.app, raise_server_exceptions=False) as client:
            health = client.get("/health")
            assert health.status_code == 200, health.text
            shell = client.get("/api/shell-config?surface=platform")
            _assert_not_missing_or_placeholder(shell, surface="/api/shell-config")
            assert shell.status_code == 200, shell.text
            spec.runtime_checks(client, files_one, loaded)
    finally:
        from mozaiksai.core.workflow.workflow_manager import initialize_workflows

        initialize_workflows(str(WORKFLOWS_ROOT))
        platform.app.state.executor_registry = previous_state["executor_registry"]
        platform.app.state.subscriptions_config = previous_state["subscriptions_config"]
        platform.app.state.failed_module_names = previous_state["failed_module_names"]
        platform.app.state.startup_degraded = previous_state["startup_degraded"]
        platform.app.state.startup_degraded_reason = previous_state["startup_degraded_reason"]
        platform.app.state.module_action_surfaces = previous_state["module_action_surfaces"]
        auth_registry._adapter_registry.clear()
        reset_auth_adapter()


@pytest.mark.asyncio
async def test_archetype_matrix_acceptance_fails_when_declared_surface_is_dropped(tmp_path: Path) -> None:
    spec = next(item for item in _matrix_specs() if item.archetype_id == "community_content")
    files, _, _ = await _materialize_spec(spec, tmp_path / "run")
    mutated = dict(files)
    mutated["modules/comments/backend/handler.py"] = mutated["modules/comments/backend/handler.py"].replace(
        "\n    async def list_comments(self, ctx, **params):\n        return {\"items\": [], \"count\": 0}\n",
        "\n",
    )

    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=mutated,
            pages=_validation_pages(spec.plan, mutated),
            build_tasks=spec.plan.get("build_tasks", []),
            capability_packs=spec.plan.get("capability_packs", []),
        )
    )
    codes = {item.code for item in scan_functional_generated_app(mutated)}

    assert validation.passed is False
    assert "MISSING_MODULE_HANDLER" in codes or "MISSING_MODULE_ACTION" in codes
