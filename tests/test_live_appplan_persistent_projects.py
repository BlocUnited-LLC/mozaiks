"""AppPlanAgent persistent projects/tasks planning smoke tests.

Live LLM mode is manual only:
    python scripts/smoke_appplan_persistent_projects.py
    python scripts/smoke_appplan_persistent_projects.py --save-fixture

Fixture replay is CI-compatible and skips until
tests/fixtures/appplan_persistent_projects_output.json exists.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
FIXTURE_PATH = WORKSPACE / "tests" / "fixtures" / "appplan_persistent_projects_output.json"


class _Context:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_fixture_plan() -> dict[str, Any]:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return raw.get("AppBuildPlan") or raw


def _owned_paths(plan: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for task in plan.get("build_tasks") or []:
        if not isinstance(task, dict):
            continue
        paths.extend(str(path).replace("\\", "/") for path in task.get("owned_paths") or [])
    return paths


def _task_text(plan: dict[str, Any]) -> str:
    chunks: list[str] = []
    for task in plan.get("build_tasks") or []:
        if not isinstance(task, dict):
            continue
        for key in ("description", "initial_message"):
            if task.get(key) is not None:
                chunks.append(str(task[key]))
        chunks.extend(str(path) for path in task.get("owned_paths") or [])
    return "\n".join(chunks)


@pytest.mark.skip(
    reason=(
        "Live LLM test is manual. Run: "
        "python scripts/smoke_appplan_persistent_projects.py --save-fixture"
    )
)
class TestLiveAppPlanPersistentProjectsRun:
    def test_live_run_placeholder(self) -> None:
        raise AssertionError("Run the smoke script manually instead of CI.")


def test_app_build_plan_accepts_persistence_contract_task() -> None:
    mod = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_persistence_contract_acceptance",
    )
    ctx = _Context()
    plan = {
        "agent_message": "Plan persistent project management app.",
        "app_kind": "project_management",
        "pages": [{"name": "Projects", "route": "/projects", "purpose": "List projects"}],
        "entities": [{"name": "Project"}, {"name": "Task"}],
        "roles": ["user"],
        "backend_scope": ["projects", "tasks"],
        "frontend_scope": ["projects_page"],
        "capability_packs": [],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": [
            {
                "task_id": "database_intent",
                "task_type": "persistence_contract",
                "capability_pack_id": None,
                "surface_id": "database",
                "surface_kind": None,
                "execution_target": "app",
                "initial_agent": "DatabaseAgent",
                "description": "Create config/database_intent.json.",
                "initial_message": "Plan projects/tasks database intent.",
                "owned_paths": ["config/database_intent.json"],
                "depends_on": [],
                "acceptance_criteria": [],
            }
        ],
        "generation_order": ["data-models"],
    }

    mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    assert ctx.data["app_plan_ready"] is True
    assert ctx.data["app_build_plan"]["build_tasks"][0]["task_type"] == "persistence_contract"


def test_appplan_prompt_injects_persistence_contract_for_planning() -> None:
    hook_mod = _load_module(
        "factory_app/workflows/AppGenerator/tools/hook_file_contract_context.py",
        "tests.hook_file_contract_persistence_planning",
    )
    script_mod = _load_module(
        "scripts/smoke_appplan_persistent_projects.py",
        "tests.smoke_appplan_persistent_projects_prompt",
    )
    agent = script_mod._FakeAgent("AppPlanAgent", {})
    agent.system_message = script_mod._build_agent_system_prompt("AppPlanAgent")

    hook_mod.inject_cookie_cutter_contracts_context(agent, [])

    assert "[FILE CONTRACTS CONTEXT]" in agent.system_message
    assert "persistence_contract" in agent.system_message
    assert "config/database_intent.json" in agent.system_message


@pytest.mark.skipif(
    not FIXTURE_PATH.exists(),
    reason=(
        "Fixture not present. Generate with: "
        "python scripts/smoke_appplan_persistent_projects.py --save-fixture"
    ),
)
class TestAppPlanPersistentProjectsFixtureReplay:
    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        self.plan = _load_fixture_plan()
        self.paths = _owned_paths(self.plan)
        self.text = _task_text(self.plan)

    def test_fixture_validates_with_app_build_plan_tool(self) -> None:
        mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            f"tests.appplan_persistent_fixture_validation.{id(self)}",
        )
        ctx = _Context()
        mod.app_build_plan(AppBuildPlan=self.plan, context_variables=ctx)
        assert ctx.data.get("app_plan_ready") is True

    def test_fixture_passes_persistence_shape_checks(self) -> None:
        script_mod = _load_module(
            "scripts/smoke_appplan_persistent_projects.py",
            f"tests.appplan_persistent_shape.{id(self)}",
        )
        assert script_mod.check_plan_shape(self.plan) == []

    def test_projects_and_tasks_modules_exist(self) -> None:
        module_tasks = [
            task for task in self.plan.get("build_tasks") or []
            if isinstance(task, dict) and task.get("task_type") == "module_contract"
        ]
        text = json.dumps(module_tasks)
        assert "projects" in text
        assert "tasks" in text

    def test_database_intent_and_canonical_backend_paths_exist(self) -> None:
        assert isinstance(self.plan.get("database_intent_bundle"), dict)
        assert "config/database_intent.json" in self.paths
        for module_id in ("projects", "tasks"):
            assert f"modules/{module_id}/backend/repo.py" in self.paths
            assert f"modules/{module_id}/backend/schemas.py" in self.paths

    def test_removed_paths_and_db_guidance_are_absent(self) -> None:
        all_text = json.dumps(self.plan)
        assert "backend/models.py" not in all_text
        assert "backend/models/" not in all_text
        assert "backend/database/schema.json" not in all_text
        assert "backend/database/seed.json" not in all_text
        assert "ctx.db" not in all_text
        assert "context.db" not in all_text
        assert "get_mongo_client" not in all_text

    def test_database_migration_paths_are_canonical_when_present(self) -> None:
        migration_paths = [path for path in self.paths if "database_migrations" in path]
        assert all(path.startswith("config/database_migrations/") for path in migration_paths)

    def test_persistence_stays_in_repo_layer(self) -> None:
        assert "backend/repo.py" in self.text
        assert "backend/schemas.py" in self.text
        assert "handler.py" in self.text
        assert "service.py" in self.text

    def test_page_endpoints_bind_to_app_owned_modules(self) -> None:
        all_text = json.dumps(self.plan)
        assert "/api/modules/mozaikspay" not in all_text
        assert "/api/modules/wallet" not in all_text
        assert "/api/modules/hosted_" not in all_text
