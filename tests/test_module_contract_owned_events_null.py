"""A module_contract task that owns events.yaml and has no events must have a passing output.

A live greenfield build (OSS ``4769dcf7``, build ``build_314a761d``) planned
``modules/user_authentication/contracts/events.yaml`` into the auth contract
task's owned_paths, and the plan declared no event flows. The worker set
``module_contract.events_yaml`` to null - as its prompt requires - and, because
it owned the path, also emitted the file raw. Rejected:

    module_contract.events_yaml is null but raw output emits
    modules/user_authentication/contracts/events.yaml.

The canonical output, null and no file, was rejected too:

    did not emit required owned_paths:
    ['modules/user_authentication/contracts/events.yaml']

Three checks with an empty intersection: the raw-file guard forbade the file
when the field was null, batch ownership required the file because it was
owned, and the prompt required null because there were no events. Every
sibling manifest except events, settings and admin was already optional -
``reactions.yaml`` was null and unemitted in the same output and drew no
complaint. The optional set was hand-listed and missed three.

The dependents were drained with it: ``app.json`` and both planned pages
depend on every module contract for visibility, so one module with nothing
to declare produced no app at all.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.task_integrity import planned_artifact_diagnostics
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.task_batches import (
    _validate_exclusive_task_paths,
    _validate_task_output_ownership,
    optional_task_output_paths,
    parse_task_batches_config,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = "user_authentication"
EVENTS = f"modules/{MODULE}/contracts/events.yaml"
REACTIONS = f"modules/{MODULE}/contracts/reactions.yaml"
MANIFEST = f"modules/{MODULE}/module.yaml"

# The live task, as the batch saw it after plan review.
AUTH_TASK = {
    "task_id": "auth_pack_module_contract",
    "task_type": "module_contract",
    "capability_pack_id": MODULE,
    "initial_agent": "ConfigMiddlewareAgent",
    "owned_paths": [MANIFEST, EVENTS, REACTIONS],
    "depends_on": [],
}
OTHER_TASK = {
    "task_id": "crud_pack_task_management_module_contract",
    "task_type": "module_contract",
    "capability_pack_id": "task_management",
    "initial_agent": "ConfigMiddlewareAgent",
    "owned_paths": ["modules/task_management/module.yaml"],
    "depends_on": [],
}
INVENTORY = [AUTH_TASK, OTHER_TASK]

MODULE_YAML = {
    "schema_version": "mozaiks.module.v1",
    "module": {
        "id": MODULE,
        "display_name": "User Authentication",
        "version": "1.0.0",
        "type": "standard",
        "owner": "app",
        "visibility": "private",
        "handler": "backend.handler:UserAuthenticationModule",
    },
    "permissions": [],
    "actions": [],
    "capabilities": [],
}


def _batch():
    path = ROOT / "factory_app" / "workflows" / "AppGenerator" / "extended_orchestration" / "task_batches.yaml"
    config = parse_task_batches_config(yaml.safe_load(path.read_text(encoding="utf-8")))
    batch = next(item for item in config.batches if item.id == "app_build_tasks")
    assert batch.result.require_owned_paths, "the live batch requires owned paths; the fixture must too"
    return batch


def _output(events_yaml, *, raw_events_file: bool) -> dict:
    """The live rejected_output, reduced to the fields the checks read."""
    output = {
        "mode": "module_contract_bundle",
        "module_contract": {
            "module_id": MODULE,
            "module_yaml": copy.deepcopy(MODULE_YAML),
            "events_yaml": events_yaml,
            "reactions_yaml": None,
            "notifications_yaml": None,
            "settings_yaml": None,
            "admin_yaml": None,
            "profile_yaml": None,
            "relationships_yaml": None,
            "policy_hooks_yaml": None,
            "python_stubs": [],
            "js_stubs": [],
            "runtime_extensions_yaml": None,
        },
        "code_files": [
            {"filename": MANIFEST, "content": yaml.safe_dump(MODULE_YAML, sort_keys=False)},
        ],
    }
    if raw_events_file:
        output["code_files"].append(
            {"filename": EVENTS, "content": "schema_version: mozaiks.events.v1\nevents: []\n"}
        )
    return output


def _admit(output: dict) -> set[str]:
    """Every check a task output passes through before it is accepted."""
    files = set(extract_code_file_map_from_payload(output))
    _validate_task_output_ownership(_batch(), AUTH_TASK, output)
    _validate_exclusive_task_paths(AUTH_TASK, output, INVENTORY)
    return files


# --------------------------------------------------------------------------
# 1. The canonical output for a module with no events is accepted
# --------------------------------------------------------------------------


def test_null_events_with_no_events_file_passes():
    """The shape the prompt mandates and the guard's own test describes."""
    files = _admit(_output(None, raw_events_file=False))

    assert MANIFEST in files
    assert EVENTS not in files, "a null field emits nothing"


def test_null_reactions_with_no_reactions_file_passes():
    files = _admit(_output(None, raw_events_file=False))

    assert MANIFEST in files
    assert REACTIONS not in files


def test_null_reactions_with_raw_file_remains_rejected():
    output = _output(None, raw_events_file=False)
    output["code_files"].append({"filename": REACTIONS, "content": "schema_version: mozaiks.reactions.v1\nreactions: []\n"})
    with pytest.raises(ValueError, match=f"module_contract.reactions_yaml is null but raw output emits {REACTIONS}"):
        _admit(output)


def test_typed_reactions_materialize_without_raw_mirror():
    output = _output(None, raw_events_file=False)
    output["module_contract"]["reactions_yaml"] = {
        "schema_version": "mozaiks.reactions.v1",
        "reactions": [{
            "id": "workflow_review",
            "event_type": "domain.documents.analysis_requested",
            "target": {"kind": "capability", "capability_id": "auth-review"},
            "idempotency_key": "analysis:{event.id}",
        }],
    }
    files = _admit(output)
    assert REACTIONS in files
    rendered = yaml.safe_load(files and extract_code_file_map_from_payload(output)[REACTIONS])
    assert rendered["reactions"][0]["target"]["capability_id"] == "auth-review"


# --------------------------------------------------------------------------
# 2. The live output is still rejected - the guard is untouched
# --------------------------------------------------------------------------


def test_null_events_with_raw_events_file_is_still_rejected():
    with pytest.raises(ValueError) as error:
        _admit(_output(None, raw_events_file=True))

    message = str(error.value)
    assert f"module_contract.events_yaml is null but raw output emits {EVENTS}" in message
    assert "omit contracts/events.yaml from code_files" in message


# --------------------------------------------------------------------------
# 3. A typed manifest still materializes through the typed pipeline
# --------------------------------------------------------------------------


def test_typed_events_materialize_through_the_typed_pipeline():
    typed = {
        "schema_version": "mozaiks.events.v1",
        "events": [
            {
                "type": "domain.user.logged_in",
                "version": 1,
                "producer": "login_user",
                "payload_schema": {
                    "type": "object",
                    "properties": [{"name": "user_id", "type": "string", "required": True}],
                },
            }
        ],
    }
    files = _admit(_output(typed, raw_events_file=False))

    assert EVENTS in files
    rendered = yaml.safe_load(extract_code_file_map_from_payload(_output(typed, raw_events_file=False))[EVENTS])
    assert rendered["schema_version"] == "mozaiks.events.v1"
    assert rendered["events"][0]["type"] == "domain.user.logged_in"
    # The generator's property list was compiled into a schema mapping, which
    # is what a raw file would have skipped.
    assert isinstance(rendered["events"][0]["payload_schema"]["properties"], dict)
    assert "user_id" in rendered["events"][0]["payload_schema"]["properties"]


# --------------------------------------------------------------------------
# 4. An empty typed manifest stays valid
# --------------------------------------------------------------------------


def test_empty_typed_events_remains_valid():
    """The other zero-events shape. The successful module in the same live run
    used it, and nothing here may start refusing it."""
    files = _admit(_output({"schema_version": "mozaiks.events.v1", "events": []}, raw_events_file=False))

    assert EVENTS in files


# --------------------------------------------------------------------------
# 5. Optional is not unowned: a foreign task still cannot write the file
# --------------------------------------------------------------------------


def test_foreign_task_still_cannot_write_events_file():
    intruder = {
        "module_contract": None,
        "code_files": [{"filename": EVENTS, "content": "schema_version: mozaiks.events.v1\nevents: []\n"}],
    }
    with pytest.raises(ValueError, match="emitted paths owned by task 'auth_pack_module_contract'"):
        _validate_exclusive_task_paths(OTHER_TASK, intruder, INVENTORY)


def test_foreign_task_cannot_claim_events_file_as_its_own_optional_path():
    """The optional set is keyed by the task's own module, never another's."""
    assert EVENTS not in optional_task_output_paths(OTHER_TASK)
    with pytest.raises(ValueError, match="emitted files outside owned_paths"):
        intruder = {
            "module_contract": None,
            "code_files": [
                {"filename": "modules/task_management/module.yaml", "content": "x"},
                {"filename": EVENTS, "content": "schema_version: mozaiks.events.v1\nevents: []\n"},
            ],
        }
        _validate_task_output_ownership(_batch(), OTHER_TASK, intruder)


# --------------------------------------------------------------------------
# 6. Planned completeness and batch ownership read the same optional set
# --------------------------------------------------------------------------


class _Context(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


def test_planned_completeness_agrees_with_batch_ownership():
    """Both gates subtract optional_task_output_paths. If they ever diverge, a
    task can be accepted by the batch and then reported as incomplete."""
    context = _Context(
        workflow_name="AppGenerator",
        app_plan_ready=True,
        app_build_plan={"build_tasks": copy.deepcopy(INVENTORY), "pages": [], "modules": []},
        app_task_batch_results={task["task_id"]: {"ok": True} for task in INVENTORY},
    )
    canonical = _output(None, raw_events_file=False)
    files = {path: content for path, content in extract_code_file_map_from_payload(canonical).items()}
    files["modules/task_management/module.yaml"] = "x"

    _admit(canonical)
    diagnostics = [
        item for item in planned_artifact_diagnostics(context, files)
        if item.get("task_id") == AUTH_TASK["task_id"]
    ]

    assert diagnostics == [], diagnostics


# --------------------------------------------------------------------------
# 7. The live task's owned companions are all optional - the concrete instance
#    of the set-equality guard in test_task_batches_extended_helpers.py
# --------------------------------------------------------------------------


def test_the_live_tasks_owned_companions_are_all_optional():
    optional = optional_task_output_paths(AUTH_TASK)

    assert EVENTS in optional
    assert REACTIONS in optional
    assert MANIFEST not in optional, "module.yaml is the one required output"
