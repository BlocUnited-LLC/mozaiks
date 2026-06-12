"""
End-to-end AppGenerator canonical module generation validation.

Test scenario: "Build a simple task tracking app with projects and tasks.
Users can create projects, create tasks, mark tasks complete, and see a dashboard."

Modules: projects, tasks
Pages: dashboard, projects, tasks

Four validation levels:
    A. Static contract  — prompt files and file_contracts enforce canonical layout
    B. AppBuildPlan     — hand-authored fixture is self-consistent and drift-free
    C. Assembly         — simulated feature outputs produce canonical file tree; drift absent
    D. Runtime load     — ModuleLoader loads fixture modules; handler methods present
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str) -> Any:
    return yaml.safe_load(_read(relative_path))


# ---------------------------------------------------------------------------
# Drift pattern constants
# ---------------------------------------------------------------------------

# Companion manifests that belong under contracts/ — not at the flat module root
_DRIFT_FLAT_MANIFESTS = [
    "events.yaml",
    "subscriptions.yaml",
    "channels.yaml",
    "reactions.yaml",
    "notifications.yaml",
    "settings.yaml",
    "states.yaml",
    "transitions.yaml",
]

# Path fragments that must never appear in generated module output
_DRIFT_PATH_FRAGMENTS = [
    "app/capability_packs/",
    "subscription.yaml",
    "operation.yaml",
]


# ---------------------------------------------------------------------------
# YAML fixtures — task tracking app
# ---------------------------------------------------------------------------

# Minimal module YAML (no emits) — safe to load without contracts/events.yaml
_PROJECTS_MODULE_YAML = """\
schema_version: mozaiks.module.v1
module:
  id: projects
  display_name: Projects
  version: 1.0.0
  description: Project management
  handler: backend.handler:ProjectsModule
permissions:
  - id: projects.write
    description: Create and update projects
  - id: projects.read
    description: Read projects
actions:
  - id: create
    description: Create a new project
    handler_method: create_project
    input_schema:
      type: object
      required: [name]
    output_schema:
      type: object
      required: [project_id]
    permissions: [projects.write]
  - id: list
    description: List all projects
    handler_method: list_projects
    input_schema:
      type: object
    output_schema:
      type: object
    permissions: [projects.read]
capabilities:
  - capability_id: projects.create
    kind: action
    target: create
    title: Create Project
    permissions: [projects.write]
    input_schema:
      type: object
  - capability_id: projects.list
    kind: action
    target: list
    title: List Projects
    permissions: [projects.read]
    input_schema:
      type: object
"""

_TASKS_MODULE_YAML = """\
schema_version: mozaiks.module.v1
module:
  id: tasks
  display_name: Tasks
  version: 1.0.0
  description: Task management
  handler: backend.handler:TasksModule
permissions:
  - id: tasks.write
    description: Create and update tasks
  - id: tasks.read
    description: Read tasks
actions:
  - id: create
    description: Create a new task
    handler_method: create_task
    input_schema:
      type: object
      required: [project_id, title]
    output_schema:
      type: object
      required: [task_id]
    permissions: [tasks.write]
  - id: complete
    description: Mark a task complete
    handler_method: complete_task
    input_schema:
      type: object
      required: [task_id]
    output_schema:
      type: object
    permissions: [tasks.write]
  - id: list
    description: List tasks for a project
    handler_method: list_tasks
    input_schema:
      type: object
      required: [project_id]
    output_schema:
      type: object
    permissions: [tasks.read]
capabilities:
  - capability_id: tasks.create
    kind: action
    target: create
    title: Create Task
    permissions: [tasks.write]
    input_schema:
      type: object
  - capability_id: tasks.complete
    kind: action
    target: complete
    title: Complete Task
    permissions: [tasks.write]
    input_schema:
      type: object
  - capability_id: tasks.list
    kind: action
    target: list
    title: List Tasks
    permissions: [tasks.read]
    input_schema:
      type: object
"""

_PROJECTS_HANDLER_PY = """\
class ProjectsModule:
    async def create_project(self, ctx, *, name):
        await ctx.emit("domain.projects.project_created", {"project_id": "proj_1", "name": name})
        return {"project_id": "proj_1", "name": name}

    async def list_projects(self, ctx):
        return {"projects": []}
"""

_TASKS_HANDLER_PY = """\
class TasksModule:
    async def create_task(self, ctx, *, project_id, title):
        await ctx.emit("domain.tasks.task_created", {"task_id": "t_1", "project_id": project_id, "title": title})
        return {"task_id": "t_1", "project_id": project_id, "title": title}

    async def complete_task(self, ctx, *, task_id):
        await ctx.emit("domain.tasks.task_completed", {"task_id": task_id})
        return {"task_id": task_id, "status": "completed"}

    async def list_tasks(self, ctx, *, project_id):
        return {"tasks": []}
"""

_PROJECTS_EVENTS_YAML = """\
schema_version: mozaiks.events.v1
events:
  - type: domain.projects.project_created
    version: 1
    description: Emitted after a project is created
    producer: projects
    payload_schema:
      type: object
      required: [project_id, name]
"""

_TASKS_EVENTS_YAML = """\
schema_version: mozaiks.events.v1
events:
  - type: domain.tasks.task_created
    version: 1
    description: Emitted after a task is created
    producer: tasks
    payload_schema:
      type: object
      required: [task_id, project_id, title]
  - type: domain.tasks.task_completed
    version: 1
    description: Emitted when a task is marked complete
    producer: tasks
    payload_schema:
      type: object
      required: [task_id]
"""


# ---------------------------------------------------------------------------
# Workspace builders
# ---------------------------------------------------------------------------


def _write_module(root: Path, module_id: str, module_yaml: str, handler_py: str) -> Path:
    """Write module.yaml and backend/handler.py for a module. Returns the module dir."""
    module_dir = root / "modules" / module_id
    (module_dir / "backend").mkdir(parents=True)
    module_dir.joinpath("module.yaml").write_text(module_yaml, encoding="utf-8")
    module_dir.joinpath("backend", "handler.py").write_text(handler_py, encoding="utf-8")
    return module_dir


def _write_contracts(module_dir: Path, **files: str) -> None:
    """Write named contract files into contracts/. Keys are bare names; .yaml is appended."""
    contracts_dir = module_dir / "contracts"
    contracts_dir.mkdir(exist_ok=True)
    for name, content in files.items():
        (contracts_dir / f"{name}.yaml").write_text(content, encoding="utf-8")


def _write_workspace(root: Path) -> None:
    """Write a minimal task-tracker workspace with projects + tasks modules."""
    (root / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")
    _write_module(root, "projects", _PROJECTS_MODULE_YAML, _PROJECTS_HANDLER_PY)
    _write_module(root, "tasks", _TASKS_MODULE_YAML, _TASKS_HANDLER_PY)


# ---------------------------------------------------------------------------
# Level A: Static contract checks
# ---------------------------------------------------------------------------


class TestStaticContractChecks:
    """Level A — AppGenerator prompt files and file_contracts enforce canonical layout."""

    def test_appplanagent_declares_backend_helper_declaration_rule(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "backend helper file declaration rule" in source

    def test_appplanagent_requires_explicit_declaration_before_generation(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "do not let ServiceAgent invent" in source or "Do not declare helper files for" in source

    def test_serviceagent_prohibited_from_inventing_helper_files(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        # Either the explicit prohibition or the injected file_contracts context
        assert "Do not invent helper files" in source or "[FILE CONTRACTS CONTEXT]" in source

    def test_file_contracts_module_contract_required_output_is_module_yaml(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        assert fc["task_contracts"]["module_contract"]["required_outputs"] == [
            "modules/{pack_name}/module.yaml"
        ]

    def test_file_contracts_module_contract_optional_outputs_use_contracts_subdir(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        opts = fc["task_contracts"]["module_contract"]["optional_outputs"]
        companion_opts = [
            o for o in opts
            if any(m.replace(".yaml", "") in o for m in _DRIFT_FLAT_MANIFESTS)
        ]
        for path in companion_opts:
            assert "contracts/" in path, (
                f"Companion manifest not in contracts/ subdirectory: {path}"
            )

    def test_file_contracts_module_contract_no_flat_companions_at_module_root(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        module_contract = fc["task_contracts"]["module_contract"]
        all_outputs = (
            module_contract.get("required_outputs", [])
            + module_contract.get("optional_outputs", [])
        )
        for path in all_outputs:
            for drift in _DRIFT_FLAT_MANIFESTS:
                if path.endswith(drift) and "contracts/" not in path:
                    raise AssertionError(
                        f"Flat companion manifest at module root in file_contracts: {path}"
                    )

    def test_file_contracts_backend_helper_section_exists(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        assert "backend_helper_files" in fc

    def test_file_contracts_backend_helper_rules_require_declaration_before_generation(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        rules = fc["backend_helper_files"]["helper_contract"]["rules"]
        assert any(
            "declared" in r and ("owned_paths" in r or "python_stubs" in r)
            for r in rules
        ), "No rule requiring helper files to be declared in owned_paths or python_stubs"

    def test_file_contracts_backend_helper_prohibited_for_arbitrary_file_splitting(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        prohibited = fc["backend_helper_files"]["helper_contract"]["prohibited_for"]
        assert any("arbitrary" in p and "splitting" in p for p in prohibited)

    def test_file_contracts_backend_helper_prohibited_for_generic_service_logic(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        prohibited = fc["backend_helper_files"]["helper_contract"]["prohibited_for"]
        assert any("service.py" in p for p in prohibited)

    def test_agents_yaml_references_module_contract_task_type(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "task_type: module_contract" in source

    def test_agents_yaml_has_no_flat_states_yaml_references(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "states.yaml" not in source

    def test_agents_yaml_has_no_flat_transitions_yaml_references(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "transitions.yaml" not in source

    def test_structured_outputs_has_no_page_component_or_shell_extension(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")
        assert "page_component" not in source
        assert "shell_extension" not in source

    def test_structured_outputs_module_contract_task_type_present(self) -> None:
        so = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
        values = so["models"]["AppBuildTask"]["fields"]["task_type"]["values"]
        assert "module_contract" in values

    def test_structured_outputs_has_no_admin_config_or_platform_config_task_type(self) -> None:
        so = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
        values = so["models"]["AppBuildTask"]["fields"]["task_type"]["values"]
        assert "admin_config" not in values
        assert "platform_config" not in values

    def test_page_type_enum_aligns_with_quality_gate(self) -> None:
        """AppPageSchema.page_type values in structured_outputs.yaml must exactly
        match VALID_PAGE_TYPES enforced by the shared generated_ui_contract module."""
        from factory_app.workflows._shared.generated_ui_contract import VALID_PAGE_TYPES
        so = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
        schema_values = set(so["models"]["AppPageSchema"]["fields"]["page_type"]["values"])
        assert schema_values == VALID_PAGE_TYPES, (
            f"page_type mismatch.\n"
            f"  Only in structured_outputs.yaml: {schema_values - VALID_PAGE_TYPES}\n"
            f"  Only in VALID_PAGE_TYPES:         {VALID_PAGE_TYPES - schema_values}"
        )

    def test_page_type_hint_aligns_with_page_type(self) -> None:
        """AppBuildPage.page_type_hint must enumerate exactly the valid page types plus null."""
        from factory_app.workflows._shared.generated_ui_contract import VALID_PAGE_TYPES
        so = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
        hint_values = set(so["models"]["AppBuildPage"]["fields"]["page_type_hint"]["values"])
        # null is allowed as an opt-out sentinel; strip it for comparison
        hint_types = hint_values - {"null"}
        assert hint_types == VALID_PAGE_TYPES, (
            f"page_type_hint mismatch with VALID_PAGE_TYPES.\n"
            f"  Only in page_type_hint: {hint_types - VALID_PAGE_TYPES}\n"
            f"  Only in VALID_PAGE_TYPES: {VALID_PAGE_TYPES - hint_types}"
        )

    def test_appschema_agent_guidance_mentions_all_page_types(self) -> None:
        """agents.yaml AppSchemaAgent instructions must reference every valid page_type
        so the agent knows what structural archetype to apply."""
        from factory_app.workflows._shared.generated_ui_contract import VALID_PAGE_TYPES
        agents_text = _read("factory_app/workflows/AppGenerator/agents.yaml")
        missing = [pt for pt in VALID_PAGE_TYPES if pt not in agents_text]
        assert not missing, (
            f"AppSchemaAgent guidance in agents.yaml is missing page_type values: {missing}"
        )


# ---------------------------------------------------------------------------
# Level B: AppBuildPlan fixture validation
# ---------------------------------------------------------------------------

_TASK_TRACKER_BUILD_TASKS: list[dict[str, Any]] = [
    {
        "task_id": "task_tracker.projects.module_contract",
        "task_type": "module_contract",
        "capability_pack_id": "projects",
        "surface_id": "projects",
        "surface_kind": "module",
        "execution_target": "AppGenerator",
        "initial_agent": "ConfigMiddlewareAgent",
        "description": "Define the projects module contract: create and list projects.",
        "initial_message": (
            "Generate the module contract for the `projects` module. "
            "Actions: create_project, list_projects. "
            "Events: domain.projects.project_created. "
            "Python stubs: backend/handler.py, backend/service.py, backend/repo.py, "
            "backend/policy.py, backend/schemas.py."
        ),
        "owned_paths": [
            "modules/projects/module.yaml",
            "modules/projects/contracts/events.yaml",
            "modules/projects/backend/handler.py",
            "modules/projects/backend/service.py",
            "modules/projects/backend/repo.py",
            "modules/projects/backend/policy.py",
            "modules/projects/backend/schemas.py",
        ],
        "depends_on": [],
        "acceptance_criteria": [
            "modules/projects/module.yaml is generated with actions create and list",
            "modules/projects/contracts/events.yaml declares domain.projects.project_created",
        ],
    },
    {
        "task_id": "task_tracker.tasks.module_contract",
        "task_type": "module_contract",
        "capability_pack_id": "tasks",
        "surface_id": "tasks",
        "surface_kind": "module",
        "execution_target": "AppGenerator",
        "initial_agent": "ConfigMiddlewareAgent",
        "description": "Define the tasks module contract: create, complete, and list tasks.",
        "initial_message": (
            "Generate the module contract for the `tasks` module. "
            "Actions: create_task, complete_task, list_tasks. "
            "Events: domain.tasks.task_created, domain.tasks.task_completed. "
            "Python stubs: backend/handler.py, backend/service.py, backend/repo.py, "
            "backend/policy.py, backend/schemas.py."
        ),
        "owned_paths": [
            "modules/tasks/module.yaml",
            "modules/tasks/contracts/events.yaml",
            "modules/tasks/backend/handler.py",
            "modules/tasks/backend/service.py",
            "modules/tasks/backend/repo.py",
            "modules/tasks/backend/policy.py",
            "modules/tasks/backend/schemas.py",
        ],
        "depends_on": ["task_tracker.projects.module_contract"],
        "acceptance_criteria": [
            "modules/tasks/module.yaml is generated with actions create, complete, and list",
            "modules/tasks/contracts/events.yaml declares domain.tasks.task_created and task_completed",
        ],
    },
    {
        "task_id": "task_tracker.pages",
        "task_type": "page_bundle",
        "capability_pack_id": None,
        "surface_id": "app_shell",
        "surface_kind": "ui_only",
        "execution_target": "AppGenerator",
        "initial_agent": "AppSchemaAgent",
        "description": "Generate dashboard, projects, and tasks pages.",
        "initial_message": "Generate pages for: dashboard, projects, tasks.",
        "owned_paths": [
            "app.json",
            "ui/pages/dashboard.yaml",
            "ui/pages/projects.yaml",
            "ui/pages/tasks.yaml",
        ],
        "depends_on": [
            "task_tracker.projects.module_contract",
            "task_tracker.tasks.module_contract",
        ],
        "acceptance_criteria": [
            "app.json references dashboard, projects, and tasks pages",
        ],
    },
]


class TestAppBuildPlanFixture:
    """Level B — Hand-authored AppBuildPlan for the task tracking scenario is self-consistent."""

    def test_all_task_types_are_valid_structured_output_values(self) -> None:
        so = _read_yaml("factory_app/workflows/AppGenerator/structured_outputs.yaml")
        valid_types = set(so["models"]["AppBuildTask"]["fields"]["task_type"]["values"])
        for task in _TASK_TRACKER_BUILD_TASKS:
            assert task["task_type"] in valid_types, (
                f"Task {task['task_id']} has unknown task_type: {task['task_type']}"
            )

    def test_module_contract_tasks_start_with_config_middleware_agent(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            if task["task_type"] == "module_contract":
                assert task["initial_agent"] == "ConfigMiddlewareAgent", (
                    f"Task {task['task_id']}: initial_agent must be ConfigMiddlewareAgent"
                )

    def test_page_bundle_task_starts_with_app_schema_agent(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            if task["task_type"] == "page_bundle":
                assert task["initial_agent"] == "AppSchemaAgent"

    def test_module_contract_first_owned_path_is_module_yaml(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            if task["task_type"] == "module_contract":
                paths = task["owned_paths"]
                assert paths[0].endswith("module.yaml"), (
                    f"Task {task['task_id']}: first owned_path must be module.yaml, got {paths[0]}"
                )

    def test_companion_manifests_in_owned_paths_use_contracts_subdir(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            for path in task.get("owned_paths") or []:
                basename = path.split("/")[-1]
                if basename in _DRIFT_FLAT_MANIFESTS:
                    assert "contracts/" in path, (
                        f"Task {task['task_id']}: companion manifest not in contracts/: {path}"
                    )

    def test_no_flat_companion_manifests_at_module_root_in_owned_paths(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            for path in task.get("owned_paths") or []:
                parts = path.split("/")
                # Pattern modules/{id}/{filename}.yaml with no contracts/ in path
                if len(parts) == 3 and parts[0] == "modules" and parts[2].endswith(".yaml"):
                    assert parts[2] in ("module.yaml", "runtime_extensions.yaml"), (
                        f"Flat companion manifest at module root in task {task['task_id']}: {path}"
                    )

    def test_no_drift_path_fragments_in_owned_paths(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            for path in task.get("owned_paths") or []:
                for fragment in _DRIFT_PATH_FRAGMENTS:
                    assert fragment not in path, (
                        f"Drift fragment '{fragment}' in owned_path of task {task['task_id']}: {path}"
                    )
                assert "states.yaml" not in path
                assert "transitions.yaml" not in path
                assert "channels.yaml" not in path

    def test_no_backend_models_py_in_canonical_fixture(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            for path in task.get("owned_paths") or []:
                assert not path.endswith("backend/models.py"), (
                    f"Task {task['task_id']}: use backend/schemas.py, not backend/models.py"
                )

    def test_task_dependency_ids_reference_known_tasks(self) -> None:
        task_ids = {t["task_id"] for t in _TASK_TRACKER_BUILD_TASKS}
        for task in _TASK_TRACKER_BUILD_TASKS:
            for dep in task.get("depends_on") or []:
                assert dep in task_ids, (
                    f"Task {task['task_id']} depends on unknown task: {dep}"
                )

    def test_event_namespaces_in_initial_messages_are_domain_scoped(self) -> None:
        for task in _TASK_TRACKER_BUILD_TASKS:
            msg = task.get("initial_message", "")
            # Find dot-separated identifiers that look like event types
            for match in re.finditer(r"\b([a-z_]+)\.[a-z_]+\.[a-z_]+\b", msg):
                ns = match.group(1)
                assert ns in ("domain", "hosted", "platform"), (
                    f"Task {task['task_id']}: event namespace '{ns}' is not domain/hosted/platform"
                )

    def test_module_contract_tasks_declare_all_canonical_backend_stubs(self) -> None:
        canonical = {"handler.py", "service.py", "repo.py", "policy.py", "schemas.py"}
        for task in _TASK_TRACKER_BUILD_TASKS:
            if task["task_type"] != "module_contract":
                continue
            stub_files = {
                path.split("/")[-1]
                for path in task["owned_paths"]
                if "/backend/" in path
            }
            assert canonical.issubset(stub_files), (
                f"Task {task['task_id']}: missing canonical stubs: {canonical - stub_files}"
            )


# ---------------------------------------------------------------------------
# Level C: Assembly fixture test
# ---------------------------------------------------------------------------


def _canonical_feature_output(
    module_id: str, module_yaml: str, events_yaml: str
) -> dict[str, Any]:
    """Simulate ConfigMiddlewareAgent + ServiceAgent canonical output for a module."""
    cap = module_id.capitalize()
    return {
        "code_files": [
            {"filename": f"modules/{module_id}/module.yaml", "content": module_yaml},
            {"filename": f"modules/{module_id}/contracts/events.yaml", "content": events_yaml},
            {"filename": f"modules/{module_id}/backend/handler.py",
             "content": f"class {cap}Module:\n    pass\n"},
            {"filename": f"modules/{module_id}/backend/service.py",
             "content": f"# {module_id} service\n"},
            {"filename": f"modules/{module_id}/backend/repo.py",
             "content": f"# {module_id} repo\n"},
            {"filename": f"modules/{module_id}/backend/policy.py",
             "content": f"# {module_id} policy\n"},
            {"filename": f"modules/{module_id}/backend/schemas.py",
             "content": f"# {module_id} schemas\n"},
        ]
    }


def _drifted_feature_output(module_id: str) -> dict[str, Any]:
    """Simulate a drifted generator output with flat manifests and removed paths."""
    return {
        "code_files": [
            {"filename": f"modules/{module_id}/module.yaml",
             "content": "schema_version: mozaiks.module\n"},
            # DRIFT: events.yaml at flat module root (not in contracts/)
            {"filename": f"modules/{module_id}/events.yaml", "content": "drift: events\n"},
            # DRIFT: reactions.yaml at flat module root
            {"filename": f"modules/{module_id}/reactions.yaml",
             "content": "drift: reactions\n"},
            # DRIFT: states.yaml at flat module root
            {"filename": f"modules/{module_id}/states.yaml", "content": "drift: states\n"},
            # DRIFT: backend/models.py instead of backend/schemas.py
            {"filename": f"modules/{module_id}/backend/models.py", "content": "# drift\n"},
            # DRIFT: capability_packs directory
            {"filename": "app/capability_packs/config.yaml", "content": "drift: packs\n"},
        ]
    }


class TestAssemblyFixture:
    """Level C — Assembly phase produces canonical file tree; drift files absent."""

    def test_canonical_outputs_use_contracts_subdir_for_companion_manifests(self) -> None:
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        outputs = [
            _canonical_feature_output("projects", _PROJECTS_MODULE_YAML, _PROJECTS_EVENTS_YAML),
            _canonical_feature_output("tasks", _TASKS_MODULE_YAML, _TASKS_EVENTS_YAML),
        ]
        result = asyncio.run(assemble_features("task-tracker-test", outputs))

        assert result["success"]
        filenames = {f["filename"] for f in result["code_files"]}

        assert "modules/projects/module.yaml" in filenames
        assert "modules/tasks/module.yaml" in filenames
        assert "modules/projects/contracts/events.yaml" in filenames
        assert "modules/tasks/contracts/events.yaml" in filenames

    def test_canonical_outputs_have_no_flat_companion_manifests(self) -> None:
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        outputs = [
            _canonical_feature_output("projects", _PROJECTS_MODULE_YAML, _PROJECTS_EVENTS_YAML),
            _canonical_feature_output("tasks", _TASKS_MODULE_YAML, _TASKS_EVENTS_YAML),
        ]
        result = asyncio.run(assemble_features("task-tracker-test", outputs))
        filenames = {f["filename"] for f in result["code_files"]}

        for module_id in ("projects", "tasks"):
            for drift in _DRIFT_FLAT_MANIFESTS:
                flat_path = f"modules/{module_id}/{drift}"
                assert flat_path not in filenames, (
                    f"Flat companion manifest in assembled output: {flat_path}"
                )

    def test_canonical_outputs_have_no_backend_models_py(self) -> None:
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        outputs = [
            _canonical_feature_output("projects", _PROJECTS_MODULE_YAML, _PROJECTS_EVENTS_YAML),
            _canonical_feature_output("tasks", _TASKS_MODULE_YAML, _TASKS_EVENTS_YAML),
        ]
        result = asyncio.run(assemble_features("task-tracker-test", outputs))
        filenames = {f["filename"] for f in result["code_files"]}

        for module_id in ("projects", "tasks"):
            assert f"modules/{module_id}/backend/models.py" not in filenames

    def test_canonical_outputs_have_no_capability_packs_path(self) -> None:
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        outputs = [
            _canonical_feature_output("projects", _PROJECTS_MODULE_YAML, _PROJECTS_EVENTS_YAML),
        ]
        result = asyncio.run(assemble_features("task-tracker-test", outputs))
        filenames = {f["filename"] for f in result["code_files"]}

        assert not any("capability_packs" in f for f in filenames)

    def test_assembly_deduplication_last_writer_wins(self) -> None:
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        first = {"code_files": [{"filename": "modules/projects/module.yaml", "content": "v: 1"}]}
        second = {"code_files": [{"filename": "modules/projects/module.yaml", "content": "v: 2"}]}
        result = asyncio.run(assemble_features("task-tracker-dedup", [first, second]))

        assert result["success"]
        file_map = {f["filename"]: f["content"] for f in result["code_files"]}
        assert file_map["modules/projects/module.yaml"] == "v: 2"

    def test_drift_output_is_detectable_by_drift_guards(self) -> None:
        """Confirm drifted output produces identifiable paths — validates that the drift guards above catch real drift."""
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        result = asyncio.run(
            assemble_features("task-tracker-drift", [_drifted_feature_output("drift_module")])
        )
        filenames = {f["filename"] for f in result["code_files"]}

        # Confirm drift patterns are present (so the absence checks above are meaningful).
        # Note: flat events.yaml / reactions.yaml are normalized to contracts/ by the
        # runtime extractor — they are not drift signals. Remaining drift signals:
        assert "modules/drift_module/states.yaml" in filenames
        assert "modules/drift_module/backend/models.py" in filenames
        assert "app/capability_packs/config.yaml" in filenames

    def test_canonical_outputs_all_backend_files_under_backend_dir(self) -> None:
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        outputs = [
            _canonical_feature_output("tasks", _TASKS_MODULE_YAML, _TASKS_EVENTS_YAML),
        ]
        result = asyncio.run(assemble_features("task-tracker-test", outputs))
        filenames = [f["filename"] for f in result["code_files"]]

        py_files = [f for f in filenames if f.endswith(".py")]
        for f in py_files:
            assert "/backend/" in f, f"Python file outside backend/: {f}"


# ---------------------------------------------------------------------------
# Level D: Runtime load fixture
# ---------------------------------------------------------------------------


class TestRuntimeLoadFixture:
    """Level D — ModuleLoader loads canonical task-tracker modules without error."""

    def test_projects_module_loads_from_canonical_layout(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        _write_module(tmp_path, "projects", _PROJECTS_MODULE_YAML, _PROJECTS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")

        loaded = ModuleLoader(str(tmp_path)).load("projects")

        assert loaded.name == "projects"
        assert loaded.definition.schema_version == "mozaiks.module.v1"

    def test_tasks_module_loads_from_canonical_layout(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        _write_module(tmp_path, "tasks", _TASKS_MODULE_YAML, _TASKS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")

        loaded = ModuleLoader(str(tmp_path)).load("tasks")

        assert loaded.name == "tasks"
        assert loaded.definition.schema_version == "mozaiks.module.v1"

    def test_projects_handler_exposes_declared_action_methods(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        _write_module(tmp_path, "projects", _PROJECTS_MODULE_YAML, _PROJECTS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")

        loaded = ModuleLoader(str(tmp_path)).load("projects")
        handler = loaded.handler

        assert callable(getattr(handler, "create_project", None))
        assert callable(getattr(handler, "list_projects", None))

    def test_tasks_handler_exposes_all_three_declared_action_methods(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        _write_module(tmp_path, "tasks", _TASKS_MODULE_YAML, _TASKS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")

        loaded = ModuleLoader(str(tmp_path)).load("tasks")
        handler = loaded.handler

        assert callable(getattr(handler, "create_task", None))
        assert callable(getattr(handler, "complete_task", None))
        assert callable(getattr(handler, "list_tasks", None))

    def test_module_loads_cleanly_without_optional_contracts(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        _write_module(tmp_path, "projects", _PROJECTS_MODULE_YAML, _PROJECTS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")

        loaded = ModuleLoader(str(tmp_path)).load("projects")

        assert loaded.manifests.events is None
        assert loaded.manifests.reactions is None
        assert loaded.manifests.notifications is None
        assert loaded.manifests.admin is None
        assert loaded.manifests.runtime_extensions is None

    def test_module_loads_events_from_contracts_subdir(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        module_dir = _write_module(tmp_path, "projects", _PROJECTS_MODULE_YAML, _PROJECTS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")
        _write_contracts(module_dir, events=_PROJECTS_EVENTS_YAML)

        loaded = ModuleLoader(str(tmp_path)).load("projects")

        assert loaded.manifests.events is not None
        assert "domain.projects.project_created" in loaded.manifests.events.event_types

    def test_tasks_module_loads_events_from_contracts_subdir(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        module_dir = _write_module(tmp_path, "tasks", _TASKS_MODULE_YAML, _TASKS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")
        _write_contracts(module_dir, events=_TASKS_EVENTS_YAML)

        loaded = ModuleLoader(str(tmp_path)).load("tasks")

        assert loaded.manifests.events is not None
        assert "domain.tasks.task_created" in loaded.manifests.events.event_types
        assert "domain.tasks.task_completed" in loaded.manifests.events.event_types

    def test_flat_reactions_yaml_at_module_root_is_ignored(self, tmp_path: Path) -> None:
        """Flat reactions.yaml at the module root must not be loaded — canonical location is contracts/."""
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        module_dir = _write_module(tmp_path, "tasks", _TASKS_MODULE_YAML, _TASKS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")
        # Write drift file at flat root
        (module_dir / "reactions.yaml").write_text(
            "schema_version: mozaiks.reactions.v1\nreactions: []\n",
            encoding="utf-8",
        )

        loaded = ModuleLoader(str(tmp_path)).load("tasks")

        # Flat-root reactions.yaml must be ignored — canonical location is contracts/
        assert loaded.manifests.reactions is None

    def test_action_method_map_covers_all_declared_actions(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.module_loader import ModuleLoader

        _write_module(tmp_path, "tasks", _TASKS_MODULE_YAML, _TASKS_HANDLER_PY)
        (tmp_path / "app.json").write_text('{"appName": "Task Tracker"}', encoding="utf-8")

        loaded = ModuleLoader(str(tmp_path)).load("tasks")

        assert "create" in loaded.action_method_map
        assert "complete" in loaded.action_method_map
        assert "list" in loaded.action_method_map

    @pytest.mark.asyncio
    async def test_both_modules_discovered_by_app_loader(self, tmp_path: Path) -> None:
        from mozaiksai.core.runtime.app.loader import AppLoader

        _write_workspace(tmp_path)

        result = await AppLoader.load(str(tmp_path))

        module_names = {m.name for m in result.modules}
        assert "projects" in module_names
        assert "tasks" in module_names





