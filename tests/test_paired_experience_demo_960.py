"""Paired Projects/Tasks acceptance using scripted worker outputs.

The same domain contract and seed records back two presentations. AG2 worker
answers are scripted; this test does not claim unattended model generation.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.generated_ui_contract import audit_app_ui_bundle_integrity
from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai import __version__ as mozaiks_version
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.validation import GeneratedAppValidationRequest, validate_generated_app_bundle
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
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
from scripts.appgenerator_fixture_replay import execute_file_replay

ROOT = Path(__file__).resolve().parents[1]
BOARD_SOURCE = ROOT / "tests/fixtures/experience_demo_960/PlanningBoard.jsx"


def _field(name, kind="string", *, required=True, default=None, nullable=False, enum=None):
    return {"name": name, "type": kind, "required": required, "default": default,
            "nullable": nullable, "enum": enum}


def _domain_contract():
    collections = {
        "projects": ("Project", [
            _field("project_id"), _field("user_id"), _field("name"),
            _field("status", required=False, default='"planned"', enum=["planned", "active", "complete"]),
            _field("created_at", "date"), _field("updated_at", "datetime"),
        ]),
        "tasks": ("Task", [
            _field("task_id"), _field("user_id"), _field("project_id"), _field("title"),
            _field("description", required=False, nullable=True),
            _field("status", required=False, default='"todo"', enum=["todo", "doing", "done"]),
            _field("created_at", "date"), _field("updated_at", "datetime"),
        ]),
    }
    surfaces = []
    for module_id, (entity, fields) in collections.items():
        collection = {
            "name": module_id, "scope": "app", "tenancy": "per_user", "owner_field": "user_id",
            "entity": entity, "ownership": {"surface_id": module_id, "surface_kind": "module"},
            "fields": fields,
            "indexes": [{"keys": [{"field": f"{entity.lower()}_id", "order": 1}], "unique": True, "name": None}],
            "search_by": f"{entity.lower()}_id",
            "lifecycle": {"write_mode": "module_action", "migration_policy": "additive_only"},
        }
        surfaces.append({"surface_id": module_id, "surface_kind": "module", "collections": [collection]})
    return {"version": "1", "app_id": "paired-projects", "surfaces": surfaces, "shared_collections": []}


def _module_files(contract):
    plan = {"capability_packs": [
        {"capability_pack_id": module_id, "capability_source": "generated_module", "user_data_scope": True,
         "primary_entities": [entity]}
        for module_id, entity in (("projects", "Project"), ("tasks", "Task"))
    ], "pages": []}
    files = {}
    for module_id, handler in (("projects", "ProjectsHandler"), ("tasks", "TasksHandler")):
        output = {"module_contract": {"module_id": module_id, "module_yaml": {
            "schema_version": "mozaiks.module.v1",
            "module": {"id": module_id, "handler": f"backend.handler:{handler}"}, "actions": [],
        }}}
        closed = close_module_actions(output, app_build_plan=plan, data_contract=contract)
        files.update(extract_code_file_map_from_payload(closed))
    files.update(materialize_module_schemas(files, app_build_plan=plan, data_contract=contract))
    files.update(materialize_module_policies(files, contract))
    files.update(materialize_module_read_implementations(files, app_build_plan=plan, data_contract=contract))
    files.update(materialize_module_write_implementations(files, app_build_plan=plan, data_contract=contract))
    files.update(materialize_module_account_handlers(files, app_build_plan=plan, data_contract=contract))
    return files


def _section(name, primitive, config):
    return {"id": name, "primitive": primitive, "config": config}


def _page(name, route, sections):
    return yaml.safe_dump({
        "schema_version": "mozaiks.app_page.v1", "name": name, "route": route,
        "title": name.replace("_", " ").title(), "page_type": "record_list",
        "layout": "full-width", "sections": sections,
    }, sort_keys=False)


def _projects_page():
    return _page("projects", "/projects", [
        _section("projects-header", "PageHeader", {"title": "Projects", "subtitle": "A shared starting point for both experiences."}),
        _section("projects-list", "ResourceTable", {
            "data_source": {"module_id": "projects", "action_id": "list_projects"},
            "data_key": "items", "search": True, "selection": "single",
            "columns": [{"key": "name", "label": "Project"}, {"key": "status", "label": "Status", "type": "status"}],
            "actions": [
                {"label": "Create project", "action_type": "event", "event_type": "ui.modal.open",
                 "payload": {"modal_id": "project-create"}},
                {"label": "Project details", "action_type": "event", "event_type": "ui.modal.open",
                 "requires_selection": True, "payload": {"modal_id": "project-detail"}},
                {"label": "Edit project", "action_type": "event", "event_type": "ui.modal.open",
                 "requires_selection": True, "payload": {"modal_id": "project-edit"}},
                {"label": "Delete project", "action_type": "event", "event_type": "ui.modal.open",
                 "requires_selection": True, "payload": {"modal_id": "project-delete"}},
            ],
        }),
        _section("project-create", "Modal", {"title": "Create project", "children": [
            _section("project-create-form", "Form", {
                "fields": [{"name": "name", "label": "Project name", "type": "text", "required": True}],
                "submit_label": "Create project", "submit_action": {
                    "label": "Create project", "action_type": "submit", "closes_modal": True,
                    "data_source": {"module_id": "projects", "action_id": "create_project"},
                    "payload": [{"key": "name", "value": "{form.name}"}],
                },
            }),
        ]}),
        _section("project-detail", "Modal", {"title": "Project details", "children": [
            _section("project-detail-fields", "Form", {"initial_values_key": "selected_row", "disabled": True,
                "fields": [{"name": "name", "label": "Project", "type": "text"},
                           {"name": "status", "label": "Status", "type": "text"}],
            }),
        ]}),
        _section("project-edit", "Modal", {"title": "Edit project", "children": [
            _section("project-edit-form", "Form", {
                "initial_values_key": "selected_row", "fields": [
                    {"name": "name", "label": "Project name", "type": "text", "required": True},
                    {"name": "status", "label": "Status", "type": "select", "options": [
                        {"value": value, "label": label} for value, label in
                        (("planned", "Planned"), ("active", "Active"), ("complete", "Complete"))
                    ]},
                ],
                "submit_label": "Save project", "submit_action": {
                    "label": "Save project", "action_type": "submit", "closes_modal": True,
                    "data_source": {"module_id": "projects", "action_id": "update_project"},
                    "payload": [{"key": "project_id", "value": "{selected_row.project_id}"},
                                {"key": "name", "value": "{form.name}"},
                                {"key": "status", "value": "{form.status}"}],
                },
            }),
        ]}),
        _section("project-delete", "Modal", {
            "title": "Delete project", "description": "Permanently delete the selected project.", "children": [
                _section("project-delete-details", "Form", {
                    "initial_values_key": "selected_row", "disabled": True,
                    "fields": [{"name": "name", "label": "Project", "type": "text"}],
                }),
                _section("project-delete-form", "Form", {
                    "fields": [], "submit_label": "Delete project", "submit_action": {
                        "label": "Delete project", "action_type": "delete", "closes_modal": True,
                        "data_source": {"module_id": "projects", "action_id": "delete_project"},
                        "payload": [{"key": "project_id", "value": "{selected_row.project_id}"}],
                    },
                }),
            ],
        }),
    ])


def _operational_page():
    def open_modal(label, target):
        return {
            "label": label, "action_type": "event", "event_type": "ui.modal.open",
            "requires_selection": True, "payload": {"modal_id": target},
        }
    task_fields = [
        {"name": "title", "label": "Task", "type": "text", "required": True},
        {"name": "status", "label": "Status", "type": "select", "options": [
            {"value": value, "label": label} for value, label in
            (("todo", "Ready"), ("doing", "In motion"), ("done", "Done"))
        ]},
        {"name": "description", "label": "Details", "type": "textarea"},
    ]
    return _page("tasks", "/tasks", [
        _section("tasks-header", "PageHeader", {"title": "Task records", "subtitle": "Search, inspect and update work without leaving the page."}),
        _section("tasks-table", "ResourceTable", {
            "data_source": {"module_id": "tasks", "action_id": "list_tasks"},
            "data_key": "items", "selection": "single", "pagination": True,
            "page_size": 8, "search": True, "search_keys": ["title", "description"],
            "columns": [
                {"key": "title", "label": "Task"},
                {"key": "status", "label": "Status", "type": "status"},
                {"key": "project_id", "label": "Project"},
            ],
            "actions": [
                {"label": "Create task", "action_type": "event", "event_type": "ui.modal.open",
                 "payload": {"modal_id": "task-create"}},
                open_modal("View details", "task-detail"), open_modal("Edit task", "task-edit"),
                open_modal("Delete task", "task-delete"),
            ],
        }),
        _section("task-create", "Modal", {"title": "Create task", "children": [
            _section("task-create-form", "Form", {
                "fields": [{"name": "project_id", "label": "Project ID", "type": "text", "required": True},
                           *task_fields],
                "submit_label": "Create task", "submit_action": {
                    "label": "Create task", "action_type": "submit", "closes_modal": True,
                    "data_source": {"module_id": "tasks", "action_id": "create_task"},
                    "payload": [{"key": field, "value": f"{{form.{field}}}"}
                                for field in ("project_id", "title", "status", "description")],
                },
            }),
        ]}),
        _section("task-detail", "Modal", {
            "title": "Task details", "children": [_section("task-details-fields", "Form", {
                "initial_values_key": "selected_row", "fields": task_fields, "disabled": True,
            })],
        }),
        _section("task-edit", "Modal", {
            "title": "Edit task", "children": [_section("task-edit-form", "Form", {
                "initial_values_key": "selected_row", "fields": task_fields,
                "submit_label": "Save task", "submit_action": {
                    "label": "Save task", "action_type": "submit",
                    "data_source": {"module_id": "tasks", "action_id": "update_task"},
                    "closes_modal": True, "payload": [
                        {"key": "task_id", "value": "{selected_row.task_id}"},
                        {"key": "title", "value": "{form.title}"},
                        {"key": "status", "value": "{form.status}"},
                        {"key": "description", "value": "{form.description}"},
                    ],
                },
            })],
        }),
        _section("task-delete", "Modal", {
            "title": "Delete task", "description": "Permanently delete the selected task.", "children": [
                _section("task-delete-details", "Form", {
                    "initial_values_key": "selected_row", "disabled": True,
                    "fields": [{"name": "title", "label": "Task", "type": "text"}],
                }),
                _section("task-delete-form", "Form", {
                    "fields": [], "submit_label": "Delete task", "submit_action": {
                        "label": "Delete task", "action_type": "delete", "closes_modal": True,
                        "data_source": {"module_id": "tasks", "action_id": "delete_task"},
                        "payload": [{"key": "task_id", "value": "{selected_row.task_id}"}],
                    },
                }),
            ],
        }),
    ])


def _candidate_files(presentation):
    contract = _domain_contract()
    files = _module_files(contract)
    files["data/contract.json"] = json.dumps(contract)
    files["app.json"] = json.dumps({
        "appId": "paired-projects", "appName": "Project Ledger" if presentation == "A" else "Project Canvas",
        "version": "1.0.0", "authRequired": True,
        "startup": {"landing_spot": "/tasks" if presentation == "A" else "/board"},
    })
    files["config/ai.json"] = json.dumps({"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": None}})
    files["config/shell.json"] = json.dumps({"navigation": {"autoFromPages": True}, "header": {"show": True}})
    auth = yaml.safe_load((ROOT / "examples/canonical-apps/project-hub/app/config/auth.yaml").read_text(encoding="utf-8"))
    auth["routes"]["post_login_default"] = "/tasks" if presentation == "A" else "/board"
    files["config/auth.yaml"] = yaml.safe_dump(auth, sort_keys=False)
    files["ui/pages/projects.yaml"] = _projects_page()
    # Declarative pages are discovered from ui/pages/*.yaml. The manifest owns
    # custom routes only; SchemaPage is inherited and must not be registered.
    routes = []
    if presentation == "A":
        files["ui/pages/tasks.yaml"] = _operational_page()
    else:
        files["ui/pages/custom/board.jsx"] = BOARD_SOURCE.read_text(encoding="utf-8")
        files["ui/index.js"] = (
            "import PlanningBoard from './pages/custom/board';\n\n"
            "export function register(registerComponent) {\n"
            "  registerComponent('PlanningBoard', PlanningBoard, {description: 'Task planning board'});\n"
            "}\n"
        )
        routes.insert(0, {"id": "board", "path": "/board", "component": "PlanningBoard",
                          "label": "Planning board", "order": 10, "purpose": "Visual task planning and contextual inspection.",
                          "navigation": {"group": "main"}, "meta": {"title": "Planning board"}})
    files["ui/route_manifest.json"] = json.dumps({"pages": routes})
    return files


async def _scripted_replay(presentation):
    candidates = _candidate_files(presentation)
    tasks = []

    def task(task_id, task_type, agent, owned, depends_on, *, module_id=None, surface_kind="module"):
        tasks.append({
            "task_id": task_id, "task_type": task_type, "initial_agent": agent,
            "capability_pack_id": module_id, "surface_id": module_id or task_id,
            "surface_kind": surface_kind, "execution_target": "AppGenerator",
            "description": f"Emit scripted {task_type} candidate files.",
            "initial_message": "Emit the approved contract-bound files.",
            "owned_paths": sorted(owned), "depends_on": depends_on,
            "acceptance_criteria": ["Every owned file is admitted and all references close."],
        })

    task("persistence", "persistence_contract", "DatabaseAgent", ["data/contract.json"], [])
    for module_id in ("projects", "tasks"):
        root = f"modules/{module_id}/"
        task(f"{module_id}.contract", "module_contract", "ConfigMiddlewareAgent",
             [f"{root}module.yaml"], ["persistence"], module_id=module_id)
        task(f"{module_id}.models", "data_models", "ModelAgent",
             [f"{root}backend/schemas.py"], [f"{module_id}.contract"], module_id=module_id)
        task(f"{module_id}.services", "business_services", "ServiceAgent",
             [path for path in candidates if path.startswith(f"{root}backend/") and not path.endswith("/schemas.py")],
             [f"{module_id}.models", "persistence"], module_id=module_id)
    page_paths = [path for path in candidates if path not in {
        "data/contract.json", "config/auth.yaml",
        *[path for path in candidates if path.startswith("modules/")],
    }]
    task("pages", "page_bundle", "AppSchemaAgent", page_paths,
         ["projects.contract", "tasks.contract", "projects.services", "tasks.services"], surface_kind="ui_only")
    pages = [
        {"name": "Projects", "route": "/projects", "purpose": "Project collection and context.",
         "ui_surface": "declarative_page"},
        {"name": "Tasks" if presentation == "A" else "Planning Board",
         "route": "/tasks" if presentation == "A" else "/board",
         "purpose": "Task records" if presentation == "A" else "Interactive task planning board.",
         "ui_surface": "declarative_page" if presentation == "A" else "custom_react_page"},
    ]
    plan = {
        "app_kind": "internal_app", "auth_strategy": "basic-login", "roles": [], "entities": [],
        "pages": pages, "capability_packs": [
            {"capability_pack_id": module_id, "surface_id": module_id, "surface_kind": "module", "user_data_scope": True,
             "operations": [f"create_{entity}", f"update_{entity}", f"get_{module_id}", f"list_{module_id}"]}
            for module_id, entity in (("projects", "project"), ("tasks", "task"))
        ],
        "build_tasks": tasks, "generation_order": [item["task_id"] for item in tasks],
        "agent_backend_required": False,
    }
    context = ContextVariablesBridge({
        "workflow_name": "AppGenerator", "app_id": "paired-projects",
        "chat_id": f"scripted-paired-{presentation.lower()}",
        "run_build_binding": {"build_registry_id": "paired-projects-registry",
                              "target_app_id": "paired-projects",
                              "build_id": f"paired-{presentation.lower()}-build", "phase": "genesis"},
        "data_contract": _domain_contract(),
        "generated_files": {"config/auth.yaml": candidates["config/auth.yaml"]},
    })
    app_build_plan(AppBuildPlan=plan, context_variables=context)
    replay_context = context.snapshot()
    # The scripted worker files are precompiled from the canonical module
    # renderers above. The real admission boundary still validates every task.
    outputs = await execute_file_replay(replay_context, candidates)
    for key, value in replay_context.items():
        context.set(key, value)
    assert not outputs.get("_failed"), outputs.get("_failed")
    assembled = await assemble_app_tasks(context_variables=context)
    assert assembled["success"] is True, assembled.get("error")
    files = {entry["filename"]: entry["content"] for entry in assembled["code_files"]}
    return files, context


@pytest.mark.asyncio
@pytest.mark.parametrize("presentation", ["A", "B"])
async def test_scripted_paired_apps_admit_and_assemble(presentation, tmp_path):
    files, context = await _scripted_replay(presentation)
    assert context.get("app_task_batch_status") == "completed"
    assert "modules/tasks/backend/repo.py" in files
    assert "modules/projects/backend/repo.py" in files
    if presentation == "A":
        assert "ui/pages/tasks.yaml" in files
    else:
        assert "ui/pages/custom/board.jsx" in files
    assert scan_generated_bundle(files) == []
    assert audit_app_ui_bundle_integrity(
        [{"filename": path, "content": content} for path, content in files.items()],
        source_label=f"Paired {presentation} candidate",
    ) == []
    result = validate_generated_app_bundle(GeneratedAppValidationRequest(
        files=files, build_tasks=detach(context.get("app_build_plan"))["build_tasks"],
    ))
    assert result.passed is True, result.diagnostics
    wiring = await validate_wiring({"generated_files": files})
    assert wiring["passed"] is True, wiring["failed_tests"]
    app_root = tmp_path / "app"
    for relative, content in files.items():
        path = app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    loaded = await AppLoader.load(str(app_root))
    assert {module.name for module in loaded.modules} == {"projects", "tasks"}
    assert "projects" in {page.name for page in loaded.definition.pages}
    receipt = {path: hashlib.sha256(content.encode("utf-8")).hexdigest()
               for path, content in sorted(files.items())}
    assert len(receipt) == len(files)
    output_root = os.environ.get("MOZAIKS_PAIRED_DEMO_OUTPUT")
    if output_root:
        destination = Path(output_root) / presentation
        for relative, content in files.items():
            path = destination / "app" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (destination / "files.sha256.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8",
        )
        build_receipt = {
            "evidence_kind": "scripted_worker_outputs_through_real_ag2_task_admission",
            "live_llm_calls": False,
            "presentation": presentation,
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
            ).strip(),
            "ag2_version": importlib.metadata.version("ag2"),
            "mozaiks_runtime_version": mozaiks_version,
            "python_version": platform.python_version(),
            "domain_contract_sha256": hashlib.sha256(
                json.dumps(_domain_contract(), sort_keys=True).encode("utf-8"),
            ).hexdigest(),
            "board_source_sha256": hashlib.sha256(BOARD_SOURCE.read_bytes()).hexdigest(),
            "admitted_task_ids": sorted(
                key for key in detach(context.get("app_task_batch_results")) if not key.startswith("_")
            ),
            "files": receipt,
        }
        (destination / "receipt.json").write_text(
            json.dumps(build_receipt, indent=2), encoding="utf-8",
        )


def test_same_project_task_contract_compiles_canonical_crud():
    contract = _domain_contract()
    files = _module_files(contract)
    for module_id, entity in (("projects", "project"), ("tasks", "task")):
        module = yaml.safe_load(files[f"modules/{module_id}/module.yaml"])
        actions = {action["id"] for action in module["actions"]}
        assert {f"create_{entity}", f"update_{entity}", f"get_{module_id}", f"list_{module_id}"} <= actions
        assert f"modules/{module_id}/backend/repo.py" in files
        assert f"modules/{module_id}/backend/policy.py" in files


@pytest.mark.asyncio
async def test_paired_presentations_admit_identical_domain_files():
    operational, _ = await _scripted_replay("A")
    visual, _ = await _scripted_replay("B")
    shared_paths = {path for path in operational if path.startswith(("modules/", "data/"))}
    assert shared_paths
    assert shared_paths == {path for path in visual if path.startswith(("modules/", "data/"))}
    assert {path: operational[path] for path in shared_paths} == {
        path: visual[path] for path in shared_paths
    }
    assert "ui/pages/tasks.yaml" in operational and "ui/pages/custom/board.jsx" not in operational
    assert "ui/pages/custom/board.jsx" in visual and "ui/pages/tasks.yaml" not in visual
    assert "ui/pages/projects.yaml" in operational and "ui/pages/projects.yaml" in visual


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(url, *, token=None, body=None):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8") if body is not None else None,
        method="POST" if body is not None else "GET",
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        data = error.read()
        try:
            return error.code, json.loads(data)
        except json.JSONDecodeError:
            return error.code, data.decode("utf-8", errors="replace")


@contextmanager
def _local_identity():
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),
           "kid": "paired-demo", "alg": "RS256", "use": "sig"}
    payload = json.dumps({"keys": [jwk]}).encode("utf-8")

    class JWKS(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - standard library handler method
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), JWKS)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    issuer = f"http://127.0.0.1:{server.server_address[1]}"

    def token(user_id):
        now = int(time.time())
        return jwt.encode(
            {"iss": issuer, "sub": user_id, "aud": "paired-demo", "app_id": "paired-projects",
             "iat": now, "nbf": now - 1, "exp": now + 600},
            key, algorithm="RS256", headers={"kid": "paired-demo", "typ": "at+jwt"},
        )

    try:
        yield issuer, token
    finally:
        server.shutdown()
        thread.join(timeout=5)


@contextmanager
def _platform_host(app_root, mongo_uri, issuer, database_name, log_path):
    env = {key: value for key, value in os.environ.items()
           if key not in {"ENV", "PLATFORM_PATH", "REDIS_URL", "DATABASE_URL"}
           and not key.startswith(("MOZAIKS_", "AUTH_", "KEYCLOAK_", "SUPABASE_", "MONGO", "OPENAI_"))}
    env.update({
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONPATH": str(ROOT), "PLATFORM_PATH": str(app_root),
        "MOZAIKS_APP_WORKSPACE_PATH": str(app_root), "MOZAIKS_HOST": "platform",
        "MOZAIKS_APP_DATABASE_NAME": database_name,
        "MONGO_URI": mongo_uri, "AUTH_ENABLED": "true", "AUTH_PROVIDER": "jwt",
        "AUTH_ISSUER": issuer, "AUTH_JWKS_URL": f"{issuer}/jwks",
        "AUTH_AUDIENCE": "paired-demo", "RATE_LIMIT_ENABLED": "false",
    })
    port = _free_port()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-P", "-m", "uvicorn", "mozaiksai.hosts.platform:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=app_root, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 120
            while True:
                if process.poll() is not None:
                    pytest.fail(f"platform host exited {process.returncode}:\n{log_path.read_text(encoding='utf-8')[-4000:]}")
                if time.monotonic() > deadline:
                    pytest.fail(f"platform host did not become healthy:\n{log_path.read_text(encoding='utf-8')[-4000:]}")
                try:
                    if _request(f"{base}/api/health")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.5)
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=20)


@pytest.mark.asyncio
async def test_paired_apps_share_real_host_records_and_enforce_user_scope(tmp_path):
    """Opt-in: real Mongo, platform process, local signed JWTs, and generated bundles."""
    mongo_uri = os.environ.get("MOZAIKS_PAIRED_DEMO_MONGO_URI")
    if not mongo_uri:
        pytest.skip("set MOZAIKS_PAIRED_DEMO_MONGO_URI to a disposable local MongoDB")
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
    try:
        client.admin.command("ping")
    finally:
        client.close()

    bundles = {}
    for presentation in ("A", "B"):
        files, _ = await _scripted_replay(presentation)
        app_root = tmp_path / presentation / "app"
        for relative, content in files.items():
            path = app_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        bundles[presentation] = app_root

    with _local_identity() as (issuer, mint):
        database_name = f"paired_demo_{uuid.uuid4().hex}"
        owner, other = mint("paired-owner"), mint("paired-other")
        with _platform_host(bundles["A"], mongo_uri, issuer, database_name, tmp_path / "host-a.log") as base:
            project_url = f"{base}/api/modules/projects/"
            task_url = f"{base}/api/modules/tasks/"
            assert _request(task_url + "list_tasks")[0] in {401, 403}
            project_response = _request(project_url + "create_project", token=owner, body={"name": "Launch plan"})
            assert project_response[0] == 200, project_response
            project_id = project_response[1]["item"]["project_id"]
            task_ids = []
            for number in range(12):
                created = _request(task_url + "create_task", token=owner, body={
                    "project_id": project_id, "title": f"Task {number:02}",
                    "description": "Same dataset for two presentations", "status": "todo",
                })
                assert created[0] == 200, created
                task_ids.append(created[1]["item"]["task_id"])
            assert _request(task_url + "list_tasks?page=1&page_size=8", token=owner)[1]["total"] == 12
            assert len(_request(task_url + "list_tasks?page=2&page_size=8", token=owner)[1]["items"]) == 4
            assert _request(task_url + "list_tasks?search=Task%2005", token=owner)[1]["total"] == 1
            other_project = _request(project_url + "create_project", token=other, body={"name": "Other plan"})
            assert other_project[0] == 200, other_project
            other_project_id = other_project[1]["item"]["project_id"]
            other_task = _request(task_url + "create_task", token=other, body={
                "project_id": other_project_id, "title": "Other task", "status": "todo",
            })
            assert other_task[0] == 200, other_task
            other_task_id = other_task[1]["item"]["task_id"]
            assert _request(task_url + "list_tasks", token=other)[1]["total"] == 1
            assert _request(task_url + f"get_tasks?id={task_ids[0]}", token=other)[0] == 404
            assert _request(task_url + f"get_tasks?id={other_task_id}", token=owner)[0] == 404
            assert _request(task_url + "update_task", token=other, body={
                "task_id": task_ids[0], "status": "done",
            })[0] == 404
            assert _request(task_url + "create_task", token=other, body={
                "project_id": project_id, "title": "Forged", "user_id": "paired-owner",
            })[0] in {400, 403}

        with _platform_host(bundles["B"], mongo_uri, issuer, database_name, tmp_path / "host-b.log") as base:
            task_url = f"{base}/api/modules/tasks/"
            listed = _request(task_url + "list_tasks", token=owner, body={})
            assert listed[0] == 200 and listed[1]["total"] == 12, listed
            moved = _request(task_url + "update_task", token=owner, body={
                "task_id": task_ids[0], "status": "doing",
            })
            assert moved[0] == 200 and moved[1]["item"]["status"] == "doing", moved
            assert _request(task_url + f"get_tasks?id={task_ids[0]}", token=owner)[1]["item"]["status"] == "doing"
            assert _request(task_url + "list_tasks", token=other)[1]["total"] == 1
            other_moved = _request(task_url + "update_task", token=other, body={
                "task_id": other_task_id, "status": "done",
            })
            assert other_moved[0] == 200 and other_moved[1]["item"]["status"] == "done", other_moved
            assert _request(task_url + "update_task", token=owner, body={
                "task_id": other_task_id, "status": "doing",
            })[0] == 404
    output_root = os.environ.get("MOZAIKS_PAIRED_DEMO_OUTPUT")
    if output_root:
        receipt_path = Path(output_root) / "host-receipt.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({
            "evidence_kind": "real_platform_process_jwt_mongo_api",
            "live_llm_calls": False,
            "ag2_version": importlib.metadata.version("ag2"),
            "mozaiks_runtime_version": mozaiks_version,
            "database_name": database_name,
            "project_id": project_id,
            "task_ids": task_ids,
            "other_project_id": other_project_id,
            "other_task_id": other_task_id,
            "presentation_a_seeded_task_count": 12,
            "presentation_b_loaded_task_count": listed[1]["total"],
            "presentation_b_first_task_status": moved[1]["item"]["status"],
            "presentation_b_other_task_status": other_moved[1]["item"]["status"],
            "cross_owner_read_denied": True,
            "cross_owner_update_denied": True,
            "unauthenticated_read_denied": True,
        }, indent=2), encoding="utf-8")
