"""A raw companion contract whose typed field is null is dropped, not rejected.

A live release run (OSS ``c8b9ea2e``, AppGenerator chat ``4fa752fa``) lost its
``task_management_module_contract`` task on its first attempt:

    module_contract.reactions_yaml is null but raw output emits
    modules/task_management/contracts/reactions.yaml. ...

The worker had done what its prompt asked. The manifest guard listed the
task's owned files, ``module.yaml``, ``contracts/events.yaml`` and
``contracts/reactions.yaml``. The worker left every typed companion field null,
because the module declares no events or reactions of its own, and also wrote
the files raw into ``code_files`` as ``events: []`` and ``reactions: []``. Code
then rendered the canonical write events into ``events_yaml``, so the
remaining raw ``reactions.yaml`` was rejected. An earlier chat, ``4f2ccf27``,
failed the same way on ``user_authentication``.

A null typed companion field is determined: the module declares none, and the
typed side wins. Code therefore removes the raw duplicate instead of failing
the run on an optional companion, and the guard now names the typed field that
produces each owned file.

``tests/fixtures/module_contract_raw_companions_c8b9ea2e.json`` holds the
model's rejected output verbatim, with the context keys the task-output
acceptance path reads, taken from the ``ag2.context.set`` envelope the batch
started from.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "module_contract_raw_companions_c8b9ea2e.json").read_text(encoding="utf-8")
)
TASK_ID = "task_management_module_contract"
MANIFEST = "modules/task_management/module.yaml"
EVENTS = "modules/task_management/contracts/events.yaml"
REACTIONS = "modules/task_management/contracts/reactions.yaml"
DROPPED = "MODULE_CONTRACT_RAW_COMPANION_DROPPED"


def _dropped(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if DROPPED in record.getMessage()]


def _plan_task() -> dict:
    return next(
        task for task in FIXTURE["context"]["app_build_plan"]["build_tasks"] if task["task_id"] == TASK_ID
    )


def _batch():
    config = task_batches.load_task_batches_config("AppGenerator", workflows_root=ROOT / "factory_app" / "workflows")
    return next(batch for batch in config.batches if batch.id == "app_build_tasks")


# --------------------------------------------------------------------------
# The recorded output
# --------------------------------------------------------------------------


def test_the_fixture_is_the_live_rejection():
    """Guards the replay against a fixture that no longer shows the failure."""
    live = FIXTURE["live_failure"]
    assert live["task_id"] == TASK_ID
    assert live["failure_kind"] == "output_rejected"
    assert live["error"].startswith(f"module_contract.reactions_yaml is null but raw output emits {REACTIONS}.")

    output = FIXTURE["rejected_output"]
    bundle = output["module_contract"]
    assert isinstance(bundle["module_yaml"], dict)
    companions = [key for key in bundle if key.endswith("_yaml") and key != "module_yaml"]
    assert companions and all(bundle[key] is None for key in companions)
    raw = {entry["filename"]: entry["content"] for entry in output["code_files"]}
    assert raw[EVENTS] == "schema_version: mozaiks.events.v1\nevents: []"
    assert raw[REACTIONS] == "schema_version: mozaiks.reactions.v1\nreactions: []"
    assert MANIFEST in raw


def test_recorded_output_extracts_module_yaml_from_the_typed_field_and_drops_raw_companions(caplog):
    output = copy.deepcopy(FIXTURE["rejected_output"])
    raw_manifest = next(entry["content"] for entry in output["code_files"] if entry["filename"] == MANIFEST)

    with caplog.at_level(logging.INFO):
        files = extract_code_file_map_from_payload(output)

    assert set(files) == {MANIFEST}
    # module.yaml comes from module_contract.module_yaml: its action schemas
    # are compiled into closed JSON Schema maps, which the raw copy is not.
    manifest = yaml.safe_load(files[MANIFEST])
    assert files[MANIFEST] != raw_manifest
    create = next(action for action in manifest["actions"] if action["id"] == "create_task")
    assert isinstance(create["input_schema"]["properties"], dict)
    assert create["input_schema"]["additionalProperties"] is False

    messages = sorted(record.getMessage() for record in _dropped(caplog))
    assert len(messages) == 2, messages
    assert messages[0].startswith(f"{DROPPED}: path={EVENTS};")
    assert messages[1].startswith(f"{DROPPED}: path={REACTIONS};")
    assert "module_contract.reactions_yaml is null" in messages[1]


def test_recorded_output_is_accepted_by_the_task_batch(monkeypatch, caplog):
    """Runs the live task through _run_one_task, which is every check a task
    output passes before it is accepted: pack discard, action closure, file
    extraction, module materializers, owned-path and exclusive-path checks.
    Before this change the same call raised the live rejection verbatim."""
    task = copy.deepcopy(_plan_task())
    inventory = copy.deepcopy(FIXTURE["context"]["app_build_plan"]["build_tasks"])
    assert task["owned_paths"] == [MANIFEST, EVENTS, REACTIONS]
    assert task["depends_on"] == []

    async def run(_runner, request):
        assert request.task_id == TASK_ID
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=copy.deepcopy(FIXTURE["rejected_output"]))

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    batch = _batch()

    with caplog.at_level(logging.INFO):
        output = asyncio.run(task_batches._run_one_task(
            workflow_name="AppGenerator", batch=batch, task=task, all_task_items=inventory,
            base_context=copy.deepcopy(FIXTURE["context"]), completed_task_outputs={}, current_batch_outputs={},
            agents={"ConfigMiddlewareAgent": object()}, chat_id="replay-c8b9ea2e", app_id="replay-c8b9ea2e",
            user_id="user-1", semaphore=asyncio.Semaphore(1), fresh_agents_per_task=False, agents_factory=None,
            context_authority_policy=None,
        ))

    files = extract_code_file_map_from_payload(output)
    # events.yaml is still produced: code renders the canonical write events
    # into the typed field. The raw `events: []` copy is not what ships.
    assert set(files) == {MANIFEST, EVENTS}
    assert {entry["filename"] for entry in output["code_files"]} == {MANIFEST, EVENTS}
    events = yaml.safe_load(files[EVENTS])
    assert [event["type"] for event in events["events"]] == [
        "domain.task.created", "domain.task.updated", "domain.task.deleted",
    ]
    assert output["module_contract"]["reactions_yaml"] is None
    assert REACTIONS not in files

    # Optional companions: an owned reactions.yaml that is not produced is not missing.
    task_batches._validate_task_output_ownership(batch, task, output)
    task_batches._validate_exclusive_task_paths(task, output, inventory)

    messages = [record.getMessage() for record in _dropped(caplog)]
    assert messages == [
        f"{DROPPED}: path={REACTIONS}; module_contract.reactions_yaml is null and the raw "
        "file declares nothing, so the module declares none"
    ]


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


def _payload(reactions_yaml, raw_content: str | None) -> dict:
    payload: dict = {
        "module_contract": {
            "module_id": "orders",
            "module_yaml": {"module": {"id": "orders"}},
            "events_yaml": None,
            "reactions_yaml": reactions_yaml,
        },
        "code_files": [],
    }
    if raw_content is not None:
        payload["code_files"].append({"filename": "modules/orders/contracts/reactions.yaml", "content": raw_content})
    return payload


RAW_REACTION = (
    "schema_version: mozaiks.reactions.v1\n"
    "reactions:\n"
    "- id: notify\n"
    "  event_type: domain.order.created\n"
    "  target: {kind: capability, capability_id: notify}\n"
)


def test_null_typed_field_with_a_raw_companion_that_declares_something_is_rejected(caplog):
    """A raw reaction is a contract the model meant to declare; dropping it would lose it silently."""
    with caplog.at_level(logging.INFO), pytest.raises(ValueError) as rejected:
        extract_code_file_map_from_payload(_payload(None, RAW_REACTION))

    message = str(rejected.value)
    assert "module_contract.reactions_yaml is null but raw output emits modules/orders/contracts/reactions.yaml" in message
    assert "Put that contract in module_contract.reactions_yaml" in message
    assert "omit contracts/reactions.yaml from code_files" in message
    assert _dropped(caplog) == []


def test_null_typed_field_with_empty_raw_companion_is_dropped_at_info(caplog):
    with caplog.at_level(logging.INFO):
        files = extract_code_file_map_from_payload(_payload(None, "schema_version: mozaiks.reactions.v1\nreactions: []\n"))

    assert "modules/orders/contracts/reactions.yaml" not in files
    [record] = _dropped(caplog)
    assert record.levelno == logging.INFO
    assert record.getMessage().startswith(f"{DROPPED}: path=modules/orders/contracts/reactions.yaml;")


def test_an_unparseable_raw_companion_is_rejected_not_dropped(caplog):
    """Content that cannot be read cannot be shown to declare nothing."""
    with caplog.at_level(logging.INFO), pytest.raises(ValueError, match="module_contract.reactions_yaml is null"):
        extract_code_file_map_from_payload(_payload(None, "reactions: [\n"))

    assert _dropped(caplog) == []


def test_set_typed_field_still_wins_over_a_raw_duplicate(caplog):
    """Unchanged: the typed serialization replaces the raw copy, and nothing is dropped."""
    typed = {
        "schema_version": "mozaiks.reactions.v1",
        "reactions": [{
            "id": "typed",
            "event_type": "domain.order.created",
            "target": {"kind": "capability", "capability_id": "typed"},
        }],
    }
    with caplog.at_level(logging.INFO):
        files = extract_code_file_map_from_payload(_payload(typed, RAW_REACTION))

    rendered = yaml.safe_load(files["modules/orders/contracts/reactions.yaml"])
    assert rendered == typed
    assert _dropped(caplog) == []


def test_a_raw_module_yaml_without_its_typed_field_is_still_rejected():
    """module.yaml is required, so a null module_yaml determines nothing."""
    payload = {
        "module_contract": {"module_id": "orders", "module_yaml": None},
        "code_files": [{"filename": "modules/orders/module.yaml", "content": "module: {id: orders}\n"}],
    }
    with pytest.raises(ValueError, match="module_contract.module_yaml is null but raw output emits modules/orders/module.yaml"):
        extract_code_file_map_from_payload(payload)


def test_a_raw_companion_from_another_file_lane_is_dropped_too():
    payload = _payload(None, None)
    payload["python_files"] = [
        {"path": "modules/orders/contracts/reactions.yaml", "content": "reactions: []\n"},
    ]

    assert "modules/orders/contracts/reactions.yaml" not in extract_code_file_map_from_payload(payload)


# --------------------------------------------------------------------------
# The prompt agrees with the code
# --------------------------------------------------------------------------


def test_the_manifest_guard_names_the_typed_field_for_each_owned_file():
    from factory_app.workflows.AppGenerator.tools.hook_domain_catalog_context import (
        inject_module_file_manifest_guard,
    )

    class Agent:
        name = "ConfigMiddlewareAgent"
        system_message = ""

        def __init__(self, context_variables: dict):
            self.context_variables = context_variables

        def update_system_message(self, message: str) -> None:
            self.system_message = message

    agent = Agent({"current_build_task": copy.deepcopy(_plan_task())})
    inject_module_file_manifest_guard(agent, [])
    guard = agent.system_message.split("[MODULE FILE MANIFEST GUARD]", 1)[1]

    assert f"{MANIFEST}  ← module_contract.module_yaml" in guard
    assert f"{EVENTS}  ← module_contract.events_yaml, optional - null emits no file" in guard
    assert f"{REACTIONS}  ← module_contract.reactions_yaml, optional - null emits no file" in guard
    assert "emitted ONLY through their typed module_contract.<name>_yaml fields" in guard
    assert "Leave module_contract.<name>_yaml null when the module declares none" in guard
    assert "Never write a companion contract as a raw code_files entry" in guard
    assert "Code removes an empty raw companion whose typed field is null, rejects one that carries content" in guard
    for name in ("events", "reactions", "notifications", "settings", "admin", "profile", "relationships",
                 "policy_hooks", "runtime_extensions"):
        assert name in guard.split("Companion contracts (", 1)[1].split(")", 1)[0]


def test_the_guard_labels_every_typed_companion_from_a_file_manifest():
    from factory_app.workflows.AppGenerator.tools.hook_domain_catalog_context import (
        inject_module_file_manifest_guard,
    )

    class Agent:
        name = "ConfigMiddlewareAgent"
        system_message = ""

        def __init__(self, context_variables: dict):
            self.context_variables = context_variables

        def update_system_message(self, message: str) -> None:
            self.system_message = message

    task = {**copy.deepcopy(_plan_task()), "file_manifest": {
        "yaml_files": ["module.yaml", "profile.yaml", "runtime_extensions.yaml"],
    }}
    agent = Agent({"current_build_task": task})
    inject_module_file_manifest_guard(agent, [])
    guard = agent.system_message.split("[MODULE FILE MANIFEST GUARD]", 1)[1]

    assert "module_contract.profile_yaml" in guard
    assert "module_contract.runtime_extensions_yaml" in guard
    assert "no module_contract field" not in guard

