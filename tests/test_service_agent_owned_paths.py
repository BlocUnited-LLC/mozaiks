"""A worker must emit every file its task owns.

A live build failed with:

    task batch 'app_build_tasks' failed at task 'habit_service': did not emit
    required owned_paths:
    ['modules/habit_registry/backend/account_data_handler.py']

The coverage repair had correctly added that path - the plan validator requires
it for any pack with user_data_scope true - and the worker received owned_paths
in its task context. It still skipped the file, because the only implementation
guidance for account_data_handler was scoped to "membership-style modules".
A habit registry owns user-scoped records without being a membership module.

The batch checks emitted files against owned_paths; it cannot infer intent from
them. Under failure_policy fail_batch one missing file discards the whole run.
"""

from pathlib import Path

import yaml

APP_GENERATOR = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "AppGenerator"


def _agent_prompt(name: str) -> str:
    agents = yaml.safe_load((APP_GENERATOR / "agents.yaml").read_text(encoding="utf-8"))
    agent = next(a for a in agents["agents"] if a["name"] == name)
    return "\n".join(section["content"] for section in agent["prompt_sections"])


def test_service_agent_must_emit_every_owned_path() -> None:
    prompt = _agent_prompt("ServiceAgent")

    assert "Emit every path listed in `current_build_task.owned_paths`" in prompt
    assert "fails the whole batch" in prompt


def test_account_data_handler_is_not_limited_to_membership_modules() -> None:
    """The planner requires it for any user-scoped module; implementation must match."""
    prompt = _agent_prompt("ServiceAgent")

    assert "ANY module with `user_data_scope: true`" in prompt
    assert "not only membership-style modules" in prompt


def test_the_planner_and_the_worker_agree_on_the_rule() -> None:
    """Both halves must state the same trigger, or plans ask for unbuilt files."""
    planner = _agent_prompt("AppPlanAgent")
    worker = _agent_prompt("ServiceAgent")

    assert "account_data_handler.py" in planner
    assert "account_data_handler.py" in worker
    assert "user_data_scope" in planner
    assert "user_data_scope" in worker
