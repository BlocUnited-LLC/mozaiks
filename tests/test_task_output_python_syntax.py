"""Model Python that does not parse is a recoverable rejection, never a crash.

A live acceptance run at OSS ``a22cf4dc`` (AppGenerator chat ``ed0d00b0``) lost
``task_tasks_management_business_services`` on its first attempt with::

    invalid syntax. Perhaps you forgot a comma? (<unknown>, line 7)
    failure_kind execution_failed, recoverable false, rejected_output null

``_run_one_task`` turned only a ``ValueError`` into an output rejection. The
model's handler/service/repo source was first parsed by ``_replace_functions``
(the canonical read rendering) with no filename, so the ``SyntaxError``
escaped to the batch as ``execution_failed``: no path, no candidate, nothing
logged. Batch recovery re-runs only recoverable output rejections, so the
services task was never corrected and the bundle shipped without the
model-authored ``summarize_tasks``.

The live candidate was not kept. ``tests/fixtures/task_output_syntax_ed0d00b0.json``
holds the batch-start context and the dependency outputs from that run's WAL,
and the raw ServiceAgent output of a re-run of the same task on that context
with the run's model. The tests put the live error (a missing comma at line 7
of ``service.py``) into that output.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path

import pytest

from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.generator_support import module_authored_code
from mozaiksai.core.workflow.generator_support.module_authored_code import (
    RenderedPythonError,
    parse_rendered_python,
    prune_repository,
    reconcile_emit_literals,
    reject_unparseable_python,
)
from mozaiksai.core.workflow.generator_support.module_read_actions import _replace_functions
from mozaiksai.core.workflow.generator_support.module_write_actions import rendered_schema_names

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "task_output_syntax_ed0d00b0.json").read_text(encoding="utf-8"))
TASK_ID = "task_tasks_management_business_services"
SERVICE = "modules/tasks_management/backend/service.py"
HANDLER = "modules/tasks_management/backend/handler.py"
REPO = "modules/tasks_management/backend/repo.py"
LINE_7 = '    await ctx.emit("domain.task.created", {"task_id": record["task_id"]})\n'
BROKEN_LINE_7 = '    await ctx.emit("domain.task.created" {"task_id": record["task_id"]})\n'


def _batch():
    config = task_batches.load_task_batches_config("AppGenerator", workflows_root=ROOT / "factory_app" / "workflows")
    return next(batch for batch in config.batches if batch.id == "app_build_tasks")


def _task() -> dict:
    return copy.deepcopy(next(item for item in FIXTURE["task_inventory"] if item["task_id"] == TASK_ID))


def _with_file(output: dict, path: str, content: str) -> dict:
    """The worker emits each file in both code_files and python_files; change both copies."""
    output = copy.deepcopy(output)
    for entry in [*output["code_files"], *output["python_files"]]:
        if (entry.get("filename") or entry.get("path")) == path:
            entry["content"] = content
    return output


def _source(output: dict, path: str) -> str:
    return next(entry["content"] for entry in output["code_files"] if entry["filename"] == path)


def _broken_output() -> dict:
    output = FIXTURE["worker_output"]
    lines = _source(output, SERVICE).splitlines(keepends=True)
    assert lines[6] == LINE_7
    lines[6] = BROKEN_LINE_7
    return _with_file(output, SERVICE, "".join(lines))


def _run(monkeypatch, worker_output: dict) -> dict:
    """One attempt of the live task through every check a task output passes."""

    async def run(_runner, request):
        assert request.task_id == TASK_ID
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=copy.deepcopy(worker_output))

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    # The live batch reads a detached snapshot of the context bridge.
    base_context = create_context_container(copy.deepcopy(FIXTURE["context"])).snapshot()
    return asyncio.run(task_batches._run_one_task(
        workflow_name="AppGenerator", batch=_batch(), task=_task(), all_task_items=copy.deepcopy(FIXTURE["task_inventory"]),
        base_context=base_context, completed_task_outputs=copy.deepcopy(FIXTURE["dependency_task_outputs"]),
        current_batch_outputs={}, agents={"ServiceAgent": object()}, chat_id="replay-ed0d00b0",
        app_id="replay-ed0d00b0", user_id="user-1", semaphore=asyncio.Semaphore(1), fresh_agents_per_task=False,
        agents_factory=None, context_authority_policy=None,
    ))


# --------------------------------------------------------------------------
# The recorded run
# --------------------------------------------------------------------------


def test_the_fixture_is_the_live_failure():
    """Guards the replay against a fixture that no longer shows the failure."""
    live = FIXTURE["live_failure"]
    assert live["task_id"] == TASK_ID and live["worker_agent"] == "ServiceAgent"
    assert live["failure_kind"] == "execution_failed" and live["recoverable"] is False
    assert live["rejected_output"] is None
    assert live["error"] == "invalid syntax. Perhaps you forgot a comma? (<unknown>, line 7)"
    assert set(FIXTURE["dependency_task_outputs"]) == set(_task()["depends_on"])


def test_the_recorded_worker_output_is_accepted_as_is(monkeypatch):
    """The fixture context is complete: the unmodified output passes every check."""
    output = _run(monkeypatch, FIXTURE["worker_output"])
    files = {entry["filename"] for entry in output["code_files"]}
    assert {SERVICE, HANDLER, REPO} <= files


def test_a_model_syntax_error_is_a_recoverable_rejection_naming_path_and_line(monkeypatch):
    broken = _broken_output()
    with pytest.raises(task_batches._TaskRejected) as rejected:
        _run(monkeypatch, broken)

    evidence = rejected.value.evidence
    assert evidence["failure_kind"] == "output_rejected"
    assert evidence["recoverable"] is True
    assert evidence["attempts"] == 1
    assert evidence["error"] == (
        f"{SERVICE}:7: invalid syntax. Perhaps you forgot a comma?\n"
        f"    {BROKEN_LINE_7.strip()}"
    )
    # The candidate the correction is shown is the worker's output, unprocessed.
    assert evidence["rejected_output"] == json.loads(json.dumps(broken))
    # Exactly what batch recovery requires of an eligible root.
    assert evidence["error"] and evidence["rejected_output"] is not None


def test_every_model_python_file_is_parsed_before_any_transform_and_reported_at_once():
    output = {
        "code_files": [
            {"filename": "modules/m/backend/handler.py", "content": "class H:\n    def f(self:\n        pass\n"},
            {"filename": "modules/m/module.yaml", "content": "not: [python"},
        ],
        "python_files": [{"path": "modules/m/backend/service.py", "content": "STATES = ['open' 'done' 2]\n"}],
        "model_files": [{"path": "modules/m/backend/schemas.py", "content": "x = 1\n"}],
        "database_files": [{"path": "modules/m/backend/repo.py", "content": "x = (1,\n"}],
        "service_foundation_bundle": {"files": [{"path": "services/config.py", "content": "def g(:\n"}]},
    }
    files = task_batches._authored_python_files(output)
    assert set(files) == {
        "modules/m/backend/handler.py", "modules/m/backend/service.py", "modules/m/backend/schemas.py",
        "modules/m/backend/repo.py", "services/config.py",
    }
    with pytest.raises(ValueError) as rejected:
        reject_unparseable_python(files)
    assert str(rejected.value).splitlines() == [
        "modules/m/backend/handler.py:2: '(' was never closed",
        "    def f(self:",
        "modules/m/backend/repo.py:1: '(' was never closed",
        "    x = (1,",
        "modules/m/backend/service.py:1: invalid syntax. Perhaps you forgot a comma?",
        "    STATES = ['open' 'done' 2]",
        "services/config.py:1: invalid syntax",
        "    def g(:",
    ]


# --------------------------------------------------------------------------
# The bounded correction recovery now gets
# --------------------------------------------------------------------------


def _recovery_config():
    return task_batches.parse_task_batches_config({"batches": [{
        "id": "build", "trigger_agent": "Plan",
        "source": {"kind": "context_variable", "path": "tasks", "task_model": "BuildTask"},
        "execution": {"failure_policy": "continue_with_available", "retry_limit": 3},
        "result": {"context_key": "results", "status_key": "status"},
        "recovery": {"trigger_agent": "Validate", "request_key": "recovery_request",
                     "outcome_key": "recovery_result", "status_key": "recovery_status", "input_keys": ["approved_plan"]},
    }]})


def test_batch_recovery_gives_the_syntax_error_its_one_bounded_correction(monkeypatch):
    broken = 'async def summarize(ctx):\n    return {"open": 1 "done": 2}\n'
    fixed = 'async def summarize(ctx):\n    return {"open": 1, "done": 2}\n'
    path = "modules/tasks/backend/service.py"
    prompts: list[str] = []

    async def run(_runner, request):
        prompts.append(request.prompt)
        content = broken if len(prompts) == 1 else fixed
        return AG2TaskBatchRunnerResult(
            status=RunStatus.COMPLETED, output={"code_files": [{"filename": path, "content": content}]},
        )

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config, checkpoints = _recovery_config(), []

    async def checkpoint(updates):
        checkpoints.append(copy.deepcopy(updates))

    async def execute(context, trigger):
        await task_batches.execute_task_batches_for_trigger(
            workflow_name="SyntaxRecovery", trigger_agent=trigger, batches_config=config,
            agents={"ServiceAgent": object()}, context_variables=context, fresh_agents_per_task=False,
            checkpoint=checkpoint, chat_id="chat", app_id="app", parent_channel_id="parent-channel",
        )

    context = create_context_container({"approved_plan": {"revision": 1}, "tasks": [
        {"task_id": "services", "initial_agent": "ServiceAgent", "initial_message": "Emit services",
         "owned_paths": [path], "depends_on": []},
    ]}).snapshot()
    asyncio.run(execute(context, "Plan"))

    failure = context["results"]["_failed"]["services"]
    assert failure["failure_kind"] == "output_rejected" and failure["recoverable"] is True
    assert failure["error"].startswith(f"{path}:2: invalid syntax. Perhaps you forgot a comma?")
    assert failure["rejected_output"]["code_files"][0]["content"] == broken

    context["recovery_request"] = {
        "batch_id": "build", "request_id": "repair-1", "root_task_ids": ["services"],
        "input_fingerprint": context["results"]["_meta"]["input_fingerprint"],
    }
    asyncio.run(execute(context, "Validate"))

    assert context["recovery_result"]["status"] == "completed"
    assert context["results"]["services"]["code_files"][0]["content"] == fixed
    assert len(prompts) == 2
    assert f"{path}:2: invalid syntax. Perhaps you forgot a comma?" in prompts[1]
    assert json.dumps(failure["rejected_output"], separators=(",", ":")) in prompts[1]


# --------------------------------------------------------------------------
# Our transforms never turn a parseable model file into one that is not
# --------------------------------------------------------------------------


def _real_repo_with_page_break() -> str:
    """The recorded repo.py with a form feed in its docstring.

    ``str.splitlines`` breaks at a form feed (and at ``\\v``, ``\\x1c``-``\\x1e``,
    ``\\x85``, ``\\u2028``, ``\\u2029``); ``ast`` line numbers do not. Edits indexed
    by AST line then cut the wrong lines.
    """
    return '"""Task repository.\x0cPersistence helpers."""\n' + _source(FIXTURE["worker_output"], REPO)


def test_repository_pruning_edits_the_lines_ast_numbers(caplog):
    source = _real_repo_with_page_break()

    def prune(text: str) -> str:
        return prune_repository(
            REPO, text, module_id="tasks_management", code_owned={"create_task", "update_task", "delete_task"},
            business_sources={SERVICE: "from . import repo\n\nasync def x(ctx):\n    return await repo.list_tasks(ctx)\n"},
        )

    with caplog.at_level(logging.WARNING):
        pruned = prune(source)
    # Pruning the same file without the form feed is the reference: the form
    # feed must not move a single edit. Misaligned, the cut kept each removed
    # function's last line, which still parsed, inside the function above it.
    assert pruned == prune(source.replace("\x0c", " ")).replace("repository. Persistence", "repository.\x0cPersistence")
    assert "count_open_tasks" not in pruned and ".count(" not in pruned
    assert "async def list_tasks" in pruned
    compile(pruned, REPO, "exec")
    assert any("REPO_CODE_DISCARDED" in record.getMessage() for record in caplog.records)


def test_function_replacement_edits_the_lines_ast_numbers():
    source = '"""Handler.\u2028Routes actions."""\n' + _source(FIXTURE["worker_output"], HANDLER)
    functions = {"list_tasks": "async def list_tasks(self, ctx, *, page=1):\n    return page\n"}

    def replace(text: str) -> str:
        return _replace_functions(text, functions, path=HANDLER, class_name="TasksManagementModule")

    rendered = replace(source)
    assert rendered == replace(source.replace("\u2028", " ")).replace("Handler. Routes", "Handler.\u2028Routes")
    assert "    async def list_tasks(self, ctx, *, page=1):\n        return page\n" in rendered
    assert "service.list_tasks" not in rendered
    compile(rendered, HANDLER, "exec")


def test_pruning_whose_result_would_not_parse_keeps_the_model_file(monkeypatch, caplog):
    source = _source(FIXTURE["worker_output"], REPO)
    monkeypatch.setattr(module_authored_code, "_drop_unused_imports", lambda text: text + "\ndef broken(:\n")
    with caplog.at_level(logging.WARNING):
        result = prune_repository(REPO, source, module_id="tasks_management", code_owned=set(), business_sources={})
    assert result == source
    warning = next(record.getMessage() for record in caplog.records if "REPO_PRUNE_SKIPPED" in record.getMessage())
    assert warning.startswith(f"REPO_PRUNE_SKIPPED: {REPO}: removing [")
    assert "would leave Python that does not parse (line " in warning and ": invalid syntax)" in warning
    assert not any("REPO_CODE_DISCARDED" in record.getMessage() for record in caplog.records)


def test_emit_normalization_whose_result_would_not_parse_keeps_the_model_file(caplog):
    # Removing the hook's duplicate emit removes its whole line, `if` header included.
    source = (
        "async def after_create_task(ctx, record):\n"
        "    if record: await ctx.emit('domain.task.created', record)\n"
        "    else:\n"
        "        return None\n"
    )
    with caplog.at_level(logging.WARNING):
        result = reconcile_emit_literals(
            SERVICE, source, declared={"domain.task.created"}, aliases={},
            write_events={"create_task": "domain.task.created"},
        )
    assert result == source
    warning = next(
        record.getMessage() for record in caplog.records if "EMIT_LITERAL_NORMALIZATION_SKIPPED" in record.getMessage()
    )
    assert warning.startswith(f"EMIT_LITERAL_NORMALIZATION_SKIPPED: {SERVICE}: after_create_task no longer emits")
    assert "would leave Python that does not parse (line 3: " in warning


def test_function_replacement_rejects_authored_source_that_does_not_parse_by_path_and_line():
    with pytest.raises(ValueError) as rejected:
        _replace_functions("class H:\n    def f(self:\n        pass\n", {}, path=HANDLER, class_name="H")
    assert str(rejected.value) == f"{HANDLER}:2: '(' was never closed\n    def f(self:"


def test_a_rendering_that_does_not_parse_is_a_builder_error_with_path_line_and_snippet():
    with pytest.raises(RenderedPythonError) as failed:
        _replace_functions(
            _source(FIXTURE["worker_output"], HANDLER),
            {"list_tasks": "async def list_tasks(self, ctx:\n    return 1\n"},
            path=HANDLER, class_name="TasksManagementModule",
        )
    message = str(failed.value)
    assert message.startswith(f"code rendering produced Python that does not parse: {HANDLER}:22: ")
    assert "   22 |     async def list_tasks(self, ctx:" in message
    assert "   23 |         return 1" in message
    assert "<unknown>" not in message


def test_a_rendered_schema_that_does_not_parse_names_its_path():
    path = "modules/tasks_management/backend/schemas.py"
    with pytest.raises(RenderedPythonError, match=rf"^code rendering produced Python that does not parse: {path}:1: "):
        rendered_schema_names("class Task(TypedDict:\n", path=path)
    assert parse_rendered_python(path, "x = 1\n").body


# --------------------------------------------------------------------------
# An execution failure always carries its evidence
# --------------------------------------------------------------------------


def test_a_processing_failure_keeps_the_candidate_and_logs_the_traceback(monkeypatch, caplog):
    def explode(*_args, **_kwargs):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(task_batches, "materialize_module_read_implementations", explode)
    with caplog.at_level(logging.ERROR), pytest.raises(task_batches._TaskRejected) as failed:
        _run(monkeypatch, FIXTURE["worker_output"])

    evidence = failed.value.evidence
    assert evidence["failure_kind"] == "execution_failed" and evidence["recoverable"] is False
    assert evidence["error"] == "RuntimeError: renderer exploded"
    assert evidence["rejected_output"] == FIXTURE["worker_output"]
    record = next(record for record in caplog.records if "TASK_OUTPUT_PROCESSING_FAILED" in record.getMessage())
    assert record.levelno == logging.ERROR
    assert f"task={TASK_ID}" in record.getMessage() and "chat=replay-ed0d00b0" in record.getMessage()
    assert record.exc_info is not None and record.exc_info[0] is RuntimeError


def _single_task_context(agent: str = "ServiceAgent") -> dict:
    return create_context_container({"approved_plan": {"revision": 1}, "tasks": [
        {"task_id": "services", "initial_agent": agent, "initial_message": "Emit services",
         "owned_paths": ["modules/tasks/backend/service.py"], "depends_on": []},
    ]}).snapshot()


async def _execute_single(context: dict, agents: dict) -> None:
    async def checkpoint(_updates):
        return None

    await task_batches.execute_task_batches_for_trigger(
        workflow_name="SyntaxRecovery", trigger_agent="Plan", batches_config=_recovery_config(),
        agents=agents, context_variables=context, fresh_agents_per_task=False, checkpoint=checkpoint,
        chat_id="chat-7", app_id="app", parent_channel_id="parent-channel",
    )


def test_a_failure_before_any_output_is_logged_with_its_traceback(caplog):
    context = _single_task_context()
    with caplog.at_level(logging.ERROR):
        asyncio.run(_execute_single(context, agents={}))

    failure = context["results"]["_failed"]["services"]
    assert failure["failure_kind"] == "execution_failed" and failure["rejected_output"] is None
    assert "references unknown agent 'ServiceAgent'" in failure["error"]
    record = next(record for record in caplog.records if "TASK_EXECUTION_FAILED" in record.getMessage())
    assert record.levelno == logging.ERROR
    assert "task=services" in record.getMessage() and "chat=chat-7" in record.getMessage()
    assert record.exc_info is not None and record.exc_info[0] is ValueError


def test_an_exhausted_worker_lifecycle_failure_is_logged(monkeypatch, caplog):
    async def run(_runner, _request):
        return AG2TaskBatchRunnerResult(status=RunStatus.FAILED, error="worker execution failed")

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    context = _single_task_context()
    with caplog.at_level(logging.ERROR):
        asyncio.run(_execute_single(context, agents={"ServiceAgent": object()}))

    failure = context["results"]["_failed"]["services"]
    assert failure["failure_kind"] == "execution_failed"
    message = next(record.getMessage() for record in caplog.records if "TASK_EXECUTION_FAILED" in record.getMessage())
    assert "task=services" in message and "chat=chat-7" in message and "worker execution failed" in message
