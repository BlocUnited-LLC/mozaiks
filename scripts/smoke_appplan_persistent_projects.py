"""Manual smoke script: AppPlanAgent persistent projects/tasks planning.

Usage:
    cd mozaiks
    python scripts/smoke_appplan_persistent_projects.py
    python scripts/smoke_appplan_persistent_projects.py --save-fixture
    python scripts/smoke_appplan_persistent_projects.py --model gpt-4o-mini

What it does:
    1. Builds AppPlanAgent system prompt from agents.yaml.
    2. Injects [FILE CONTRACTS CONTEXT] and [DOMAIN CATALOG CONTEXT].
    3. Calls OpenAI with a project-management CRUD request.
    4. Parses the AppBuildPlan JSON output.
    5. Validates with app_build_plan.py.
    6. Checks persistence shape: projects/tasks modules, canonical database
       intent/migration paths, repo.py/schemas.py paths, and no legacy DB paths.
    7. Reports results. Exits 0 on success, 1 on failure.

--save-fixture  Write output to
                tests/fixtures/appplan_persistent_projects_output.json for
                pytest fixture replay.

Environment:
    OPENAI_API_KEY     Required for live run.
    DEFAULT_LLM_MODEL  Override model (default: gpt-4o-mini).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_YAML = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
_TOOLS_DIR = _REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools"
_FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures"
_FIXTURE_PATH = _FIXTURES_DIR / "appplan_persistent_projects_output.json"

USER_REQUEST = (
    "Build a project management app where users can create projects, create "
    "tasks, assign tasks to projects, mark tasks complete, and view project "
    "and task lists."
)


class _FakeAgent:
    def __init__(self, name: str, context_variables: dict[str, Any]) -> None:
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables

    def update_system_message(self, message: str) -> None:
        self.system_message = message


class _Context:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_agent_system_prompt(agent_name: str) -> str:
    raw = yaml.safe_load(_AGENTS_YAML.read_text(encoding="utf-8"))
    agents = raw.get("agents") if isinstance(raw, dict) else raw
    if not isinstance(agents, list):
        raise ValueError("agents.yaml root must be a list")

    for agent in agents:
        if not isinstance(agent, dict) or agent.get("name") != agent_name:
            continue
        parts: list[str] = []
        for section in agent.get("prompt_sections") or []:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "").strip()
            content = str(section.get("content") or "").strip()
            if heading and content:
                parts.append(f"{heading}\n{content}")
            elif content:
                parts.append(content)
        return "\n\n".join(parts)

    raise ValueError(f"Agent {agent_name!r} not found")


def _apply_hooks(agent: _FakeAgent) -> None:
    file_contract_hook = _load_module(
        _TOOLS_DIR / "hook_file_contract_context.py",
        f"smoke_file_contracts.{id(agent)}",
    )
    file_contract_hook.inject_cookie_cutter_contracts_context(agent, [])

    domain_hook = _load_module(
        _TOOLS_DIR / "hook_domain_catalog_context.py",
        f"smoke_domain_catalog.{id(agent)}",
    )
    domain_hook.inject_domain_catalog_context(agent, [])


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)

    for pattern in (r"```json\s*([\s\S]+?)\s*```", r"```\s*([\s\S]+?)\s*```"):
        match = re.search(pattern, stripped)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith("{"):
                return json.loads(candidate)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if 0 <= start < end:
        return json.loads(stripped[start : end + 1])
    raise ValueError(f"Could not extract JSON from response:\n{text[:600]}")


def _call_openai(system_message: str, user_message: str, model: str) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise ImportError("openai package required: pip install openai") from exc

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable not set")

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        timeout=120,
    )
    return response.choices[0].message.content or ""


def _validate_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], str]:
    validation_mod = _load_module(
        _TOOLS_DIR / "app_build_plan.py",
        f"smoke_persistent_app_build_plan.{id(object())}",
    )
    ctx = _Context()
    result = validation_mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
    return ctx.data, str(result)


def _all_owned_paths(plan: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for task in plan.get("build_tasks") or []:
        if not isinstance(task, dict):
            continue
        for path in task.get("owned_paths") or []:
            paths.append(str(path).replace("\\", "/"))
    return paths


def _task_text(task: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("description", "initial_message"):
        value = task.get(key)
        if value is not None:
            values.append(str(value))
    values.extend(str(path) for path in task.get("owned_paths") or [])
    return "\n".join(values)


def _has_module_task(plan: dict[str, Any], module_id: str) -> bool:
    return any(
        isinstance(task, dict)
        and task.get("task_type") == "module_contract"
        and (
            task.get("capability_pack_id") == module_id
            or task.get("surface_id") == module_id
            or any(str(path).startswith(f"modules/{module_id}/") for path in task.get("owned_paths") or [])
        )
        for task in plan.get("build_tasks") or []
    )


def _page_endpoints_are_app_owned(plan: dict[str, Any]) -> bool:
    text = json.dumps(plan)
    forbidden = (
        "/api/modules/mozaikspay",
        "/api/modules/wallet",
        "/api/modules/hosted_",
    )
    return not any(item in text for item in forbidden)


def check_plan_shape(plan: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    build_tasks = [task for task in plan.get("build_tasks") or [] if isinstance(task, dict)]
    owned_paths = _all_owned_paths(plan)
    all_task_text = "\n".join(_task_text(task) for task in build_tasks)
    plan_json = json.dumps(plan)

    app_kind = str(plan.get("app_kind") or "").lower()
    if not any(token in app_kind for token in ("productivity", "project", "task")):
        violations.append(f"app_kind {plan.get('app_kind')!r} does not look like project/task planning")

    for module_id in ("projects", "tasks"):
        if not _has_module_task(plan, module_id):
            violations.append(f"missing module_contract task for {module_id}")
        for filename in ("repo.py", "schemas.py"):
            expected = f"modules/{module_id}/backend/{filename}"
            if expected not in owned_paths:
                violations.append(f"missing owned_path {expected}")

    if "config/database_intent.json" not in owned_paths:
        violations.append("missing owned_path config/database_intent.json")

    persistence_tasks = [task for task in build_tasks if task.get("task_type") == "persistence_contract"]
    database_tasks = [task for task in build_tasks if task.get("initial_agent") == "DatabaseAgent"]
    if not persistence_tasks and not database_tasks:
        violations.append("missing persistence_contract/DatabaseAgent build task")

    intent = plan.get("database_intent_bundle")
    if isinstance(intent, dict):
        intent_text = json.dumps(intent)
        if "projects" not in intent_text or "tasks" not in intent_text:
            violations.append("database_intent_bundle does not mention projects and tasks")
    else:
        violations.append("missing top-level database_intent_bundle")

    migration_paths = [path for path in owned_paths if "database_migrations" in path]
    if migration_paths and not all(path.startswith("config/database_migrations/") for path in migration_paths):
        violations.append(f"non-canonical database migration path(s): {migration_paths}")

    forbidden_paths = (
        "backend/models.py",
        "backend/models/",
        "backend/database/schema.json",
        "backend/database/seed.json",
    )
    for path in owned_paths:
        if any(item in path for item in forbidden_paths):
            violations.append(f"legacy forbidden path planned: {path}")

    forbidden_text = ("ctx.db", "context.db", "get_mongo_client", "pymongo", "motor")
    for token in forbidden_text:
        if token in all_task_text or token in plan_json:
            violations.append(f"forbidden generated-code guidance found: {token}")

    if "backend/repo.py" not in all_task_text:
        violations.append("task instructions do not mention backend/repo.py")
    if "backend/schemas.py" not in all_task_text:
        violations.append("task instructions do not mention backend/schemas.py")
    if "handler.py" in all_task_text and "repo.py" in all_task_text:
        handler_mentions = [
            _task_text(task)
            for task in build_tasks
            if task.get("task_type") == "module_contract"
            and "handler.py" in _task_text(task)
            and "persistence in handler" in _task_text(task).lower()
        ]
        if handler_mentions:
            violations.append("module task implies persistence belongs in handler.py")

    page_tasks = [task for task in build_tasks if task.get("task_type") == "page_bundle"]
    if not page_tasks:
        violations.append("missing page_bundle task")
    if not _page_endpoints_are_app_owned(plan):
        violations.append("page/task plan binds to hosted module endpoint instead of app-owned modules")

    return violations


def run(*, save_fixture: bool = False, model: str = "gpt-4o-mini") -> int:
    print("=" * 72)
    print("AppPlanAgent Persistent Projects Planning Smoke Test")
    print("=" * 72)

    print("\n[1/6] Building AppPlanAgent system prompt...")
    base_prompt = _build_agent_system_prompt("AppPlanAgent")
    print(f"      Base prompt: {len(base_prompt):,} chars")

    print("[2/6] Applying hooks (file contracts + domain catalog)...")
    agent = _FakeAgent(
        name="AppPlanAgent",
        context_variables={
            "concept_overview": USER_REQUEST,
            "database_setup_mode": "generated_persistence",
        },
    )
    agent.system_message = base_prompt
    _apply_hooks(agent)
    system_message = agent.system_message
    if "[FILE CONTRACTS CONTEXT]" not in system_message:
        print("      ERROR: [FILE CONTRACTS CONTEXT] not injected")
        return 1
    if "[DOMAIN CATALOG CONTEXT]" not in system_message:
        print("      ERROR: [DOMAIN CATALOG CONTEXT] not injected")
        return 1
    if "persistence_contract" not in system_message:
        print("      ERROR: persistence_contract not visible to AppPlanAgent prompt")
        return 1
    print(f"      Final prompt: {len(system_message):,} chars")

    print(f"\n[3/6] Calling OpenAI ({model})...")
    print(f"      User request: {USER_REQUEST}")
    try:
        response_text = _call_openai(system_message, USER_REQUEST, model)
    except Exception as exc:
        print(f"      ERROR: LLM call failed - {exc}")
        return 1

    print("\n[4/6] Parsing AppBuildPlan JSON...")
    try:
        raw = _extract_json(response_text)
    except Exception as exc:
        print(f"      ERROR: JSON parse failed - {exc}")
        print(response_text[:800])
        return 1
    plan = raw.get("AppBuildPlan") or raw
    if not isinstance(plan, dict):
        print("      ERROR: AppBuildPlan payload missing or invalid")
        return 1
    print(f"      Build tasks: {len(plan.get('build_tasks') or [])}")
    print(f"      Pages: {len(plan.get('pages') or [])}")

    print("\n[5/6] Validating with app_build_plan.py...")
    try:
        ctx_data, result_msg = _validate_plan(plan)
    except Exception as exc:
        print(f"      Validation: FAILED - {exc}")
        return 1
    if ctx_data.get("app_plan_ready") is not True:
        print(f"      ERROR: app_plan_ready={ctx_data.get('app_plan_ready')!r}")
        return 1
    print(f"      {result_msg}")

    print("\n[6/6] Checking persistence planning shape...")
    for task in plan.get("build_tasks") or []:
        if isinstance(task, dict):
            print(f"      [{task.get('task_type')}] {task.get('task_id')} -> {task.get('owned_paths') or []}")
    violations = check_plan_shape(plan)
    if violations:
        print(f"\n      VIOLATIONS ({len(violations)}):")
        for violation in violations:
            print(f"      - {violation}")
        return 1
    print("      Shape checks passed")

    if save_fixture:
        _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        _FIXTURE_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        print(f"\n      Fixture saved: {_FIXTURE_PATH}")

    print("\nSMOKE TEST PASSED")
    return 0


def main() -> int:
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv(_REPO_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(
        description="AppPlanAgent persistent projects planning smoke test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--save-fixture",
        action="store_true",
        help="Save plan output to tests/fixtures/appplan_persistent_projects_output.json",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DEFAULT_LLM_MODEL", "gpt-4o-mini"),
        help="OpenAI model to use (default: gpt-4o-mini or DEFAULT_LLM_MODEL env var)",
    )
    args = parser.parse_args()
    return run(save_fixture=args.save_fixture, model=args.model)


if __name__ == "__main__":
    sys.exit(main())
