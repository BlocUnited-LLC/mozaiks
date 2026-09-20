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

import json
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


def _service_output_example() -> dict:
    """The worked example the service worker is shown, parsed as it will read it."""
    agents = yaml.safe_load((APP_GENERATOR / "agents.yaml").read_text(encoding="utf-8"))
    agent = next(a for a in agents["agents"] if a["name"] == "ServiceAgent")
    text = next(s for s in agent["prompt_sections"] if s["id"] == "output_format")["content"]
    start = text.index("{")
    depth = 0
    for index, char in enumerate(text[start:], start):
        depth += (char == "{") - (char == "}")
        if depth == 0:
            return json.loads(text[start:index + 1])
    raise AssertionError("could not find the ServiceOutput example")


def test_the_worked_example_emits_only_files_the_task_owns() -> None:
    """A live build died because this example showed otherwise.

    ServiceAgent emitted modules/task_management/backend/schemas.py, which the
    plan gives to the data_models task, and the batch failed the task:

        emitted files outside owned_paths:
        ['modules/task_management/backend/schemas.py']

    Its dependent page_bundle drained, the batch went partial, and the build
    ended with no pages. The prompt forbade that file in prose and then handed
    the worker an example emitting it. An example outranks a rule.
    """
    example = _service_output_example()
    emitted = [entry["path"] for entry in example.get("python_files", [])]
    emitted += [entry["filename"] for entry in example.get("code_files", [])]

    assert emitted, "the example must still show output"
    offenders = [path for path in emitted if path.endswith("backend/schemas.py")]
    assert not offenders, f"example emits files this task does not own: {offenders}"


def test_the_worked_example_still_shows_the_schemas_import() -> None:
    """Removing the file must not remove the demonstration of how to use it."""
    example = _service_output_example()
    service = next(
        entry for entry in example["code_files"] if entry["filename"].endswith("backend/service.py")
    )

    assert "from .schemas import" in service["content"]


def test_the_serialization_helper_is_imported_not_authored() -> None:
    """The list_* allowlist rule used to say 'define a helper in schemas.py'.

    That is the same file the rule two paragraphs above forbids, so following
    either half of the prompt broke the other.
    """
    prompt = _agent_prompt("ServiceAgent")

    assert "Never create or edit `backend/schemas.py`" in prompt
    assert "define it in a file this task owns" in prompt
    assert "Define a module-local helper in backend/schemas.py" not in prompt


def test_upstream_shapes_are_named_by_where_they_arrive() -> None:
    """These workers are stateless: they do not know their own name or anyone's.

    Guidance has to point at the data -- the context key it arrives in and the
    module it is importable from -- not at the worker that produced it.
    """
    prompt = _agent_prompt("ServiceAgent")

    assert "dependency_task_outputs" in prompt
    assert "importable from the module's `.schemas` module" in prompt


def test_the_plan_still_withholds_schemas_from_the_service_task() -> None:
    """The correction is to the guidance; ownership itself must not move."""
    prompt = _agent_prompt("AppPlanAgent")

    assert "Do not include `backend/schemas.py` in this task." in prompt
    assert '`owned_paths: ["modules/{module_id}/backend/schemas.py"]`' in prompt
