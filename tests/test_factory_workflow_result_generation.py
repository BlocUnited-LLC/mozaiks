import hashlib
import importlib
import json
import zipfile
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows._shared.workflow_integration import (
    apply_workflow_integration_context,
    extract_workflow_integration_metadata_from_bundle_entries,
    hydrate_workflow_integration_context_from_latest_artifact,
    workflow_integration_metadata_from_artifact,
)
from factory_app.workflows.AgentGenerator.tools.result_write_materialization import (
    materialize_workflow_result_write,
)
from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_workflow_integration_contract,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import (
    assemble_features,
    compiled_pack_module_templates,
)
from factory_app.workflows.AppGenerator.tools.workflow_result_writes import workflow_result_files
from mozaiksai.core.artifacts.models import BuildRecordFileEntry
from mozaiksai.core.workflow import module_tools
from tests.factory_context import factory_context
from tests.test_module_loader_contracts import _write_canonical_module
from tests.test_workflow_integration_metadata import _FakeArtifactStore, _record

EVENT = "domain.tasks.task_created"
WRITE = {"source_action": "create", "action": "save_summary", "collection": "tasks",
         "record_key": "task_id", "fields": ["summary"]}
SPEC = {"name": "ReviewWorkflow", "result_write_plans": [
    {"agent": "ResultAgent", "module_id": "tasks", "event_type": EVENT, "result_write": WRITE},
]}


def entry(*, fields=None, triggers=None, models=None, context=None):
    orchestrator = {"workflow_name": "ReviewWorkflow", "workflow_startup_mode": "BackendOnly", "initial_agent": "ResultAgent",
                    "triggers": [{"type": "event", "event": name, "capability_id": "tasks.review"}
                                 for name in triggers or [EVENT]]}
    outputs = {"schema_version": "mozaiks.structured_outputs.v1", "registry": {"ResultAgent": "Summary"},
               "models": {**(models or {}), "Summary": {"type": "model", "fields": fields or {"summary": {"type": "str"}}}}}
    return {"workflow_name": "ReviewWorkflow", "files": [
        {"filename": path, "content": yaml.safe_dump(document)}
        for path, document in [("tools.yaml", {"tools": []}), ("structured_outputs.yaml", outputs),
                               ("context_variables.yaml", context or {"definitions": {}}),
                               ("transition_graph.yaml", {"transition_rules": []}),
                               ("agents.yaml", {"agents": [{"name": "ResultAgent", "system_message": "Summarize the task.",
                                                            "structured_outputs_required": True}]}),
                               ("orchestrator.yaml", orchestrator)]
    ]}


def workflow_context():
    rendered = materialize_workflow_result_write(entry(), SPEC)
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    context = {}
    bundle = _record("approved-workflow", {"workflow_integration_metadata": metadata})
    apply_workflow_integration_context(context, workflow_integration_metadata_from_artifact(bundle))
    return context


def test_module_worker_compiles_internal_receiver_and_retains_custom_reads():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_contract_task_operations,
    )
    from mozaiksai.core.workflow.generator_support.code_files import (
        extract_code_file_map_from_payload,
    )
    from mozaiksai.core.workflow.generator_support.module_action_inventory import (
        all_module_actions,
        approved_manifest_action_ids,
        pinned_result_action_schemas,
        ungated_module_actions,
    )
    from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
        compile_module_entitlement_gates,
    )
    from mozaiksai.core.workflow.generator_support.module_write_actions import (
        close_module_actions,
        materialize_module_actions,
    )

    ctx = workflow_context()
    design = {"surfaces": [{"owner": "app", "surface_id": "tasks", "surface_kind": "module",
                            "owned_mutations": ["create"], "custom_reads": ["summarize"]}]}
    ctx["design_surface_map"] = design
    plan = {"capability_packs": [{"capability_pack_id": "tasks", "surface_kind": "module", "capability_source": "generated_module"}],
            "build_tasks": [{"task_type": "module_contract", "task_id": "contract", "capability_pack_id": "tasks",
                             "initial_message": "Declare the receiver with a task_id input.", "owned_paths": ["modules/tasks/module.yaml"]}]}
    _repair_contract_task_operations(plan, ctx)
    brief = plan["build_tasks"][0]["initial_message"]
    assert "pinned workflow output schemas" in brief and "Never add a record ID" in brief and "`summarize`" in brief
    assert "nullable: false" in brief and not _repair_contract_task_operations(plan, ctx)
    receivers = pinned_result_action_schemas(ctx)
    manifest = {"schema_version": "mozaiks.module.v1", "module": {"id": "tasks", "handler": "backend.handler:TasksModule"},
                "actions": [{"id": action, "handler_method": action, "description": action, "permissions": [], "emits": []}
                            for action in ("create", "summarize")]}
    output = {"module_contract": {"module_id": "tasks", "module_yaml": manifest}}
    kwargs = dict(app_build_plan=plan, design_surface_map=design, result_action_schemas=receivers)
    closed = close_module_actions(output, **kwargs)
    files = extract_code_file_map_from_payload(closed)
    compiled = yaml.safe_load(files["modules/tasks/module.yaml"])
    actions = {action["id"]: action for action in compiled["actions"]}
    assert set(actions) == {"create", "summarize", "save_summary"}
    assert actions["save_summary"]["input_schema"] == ctx["workflow_integration_metadata"]["workflows"][0]["result_output_schema"]
    assert actions["save_summary"]["api_surface"] == "internal" and actions["save_summary"]["emits"] == []
    assert close_module_actions(closed, **kwargs) == closed
    assert materialize_module_actions(files, **kwargs) == {}
    public = all_module_actions(ctx)
    assert "save_summary" not in approved_manifest_action_ids(ctx, public, http_only=True)["tasks"]
    assert "save_summary" in ungated_module_actions(ctx)["tasks"]
    approved = approved_manifest_action_ids(ctx, public)
    gate_args = dict(gates_by_module={}, approved_actions=public, manifest_actions=approved, ungated_actions=ungated_module_actions(ctx))
    assert compile_module_entitlement_gates(files, **gate_args) == files
    compiled["actions"].append({"id": "unreviewed"})
    with pytest.raises(ValueError, match="unapproved module actions"):
        compile_module_entitlement_gates({"modules/tasks/module.yaml": yaml.safe_dump(compiled)}, **gate_args)
    manifest["actions"].pop()
    with pytest.raises(ValueError, match="declare approved actions.*summarize"):
        close_module_actions(output, **kwargs)


async def test_revision_worker_reads_retained_signatures_only_from_the_selected_app(monkeypatch):
    from factory_app.workflows._shared import revision_app_source
    from mozaiksai.core.workflow.generator_support.module_revision import (
        retained_custom_action_context,
        validate_retained_ask_callers,
    )

    action = {"id": "summarize", "api_surface": "public", "permissions": [], "handler_method": "summarize",
              "output_schema": {"type": "object", "properties": {"last_updated": {"type": ["string", "null"]}},
                                "required": ["last_updated"]}}
    path = "modules/tasks/module.yaml"
    page = "ui/pages/tasks.yaml"
    action["ask_context_safe"] = True
    prior = yaml.safe_dump({"module": {"id": "tasks"}, "actions": [action, {"id": "retired"}]})
    read = AsyncMock(return_value={path: prior, page: yaml.safe_dump({"meta": {"ask_context": [{"module": "tasks", "action": "summarize"}]}})})
    monkeypatch.setattr(revision_app_source, "read_bound_revision_files", read)
    ctx = {"build_mode": "revision", "workflow_sequence": "app_revision", "revision_source_artifact_version_id": "source",
           "design_surface_map": {"surfaces": [{"owner": "app", "surface_id": "tasks", "surface_kind": "module",
                                                 "custom_reads": ["summarize", "new_read"]}]},
           "generated_files": {path: prior.replace("last_updated", "forged")}}
    prompt, callers = await retained_custom_action_context(ctx, "tasks", [])
    read.assert_awaited_once_with(ctx)
    assert json.loads(prompt.splitlines()[-1]) == [action]
    assert "forged" not in prompt and "retired" not in prompt
    assert callers == {"summarize": page}
    assert "Keep ask_context_safe true" in prompt
    changed = {"module_id": "tasks", "module_yaml": {"actions": [{**action, "ask_context_safe": False}]}}
    with pytest.raises(ValueError, match="tasks.yaml.*tasks/summarize"):
        validate_retained_ask_callers(changed, callers)
    assert changed["module_yaml"]["actions"][0]["ask_context_safe"] is False
    validate_retained_ask_callers({"module_id": "tasks", "module_yaml": {"actions": [action]}}, callers)
    for disqualifying in ({"emits": ["domain.tasks.changed"]}, {"permissions": ["admin"]}):
        with pytest.raises(ValueError, match="tasks.yaml.*tasks/summarize"):
            validate_retained_ask_callers({"module_id": "tasks", "module_yaml": {"actions": [{**action, **disqualifying}]}}, callers)
    assert (await retained_custom_action_context(ctx, "tasks", [{"owned_paths": [page]}]))[1] == {}
    manifest = "ui/route_manifest.json"
    read.return_value[manifest] = json.dumps({"pages": [{"meta": {"ask_context": [{"module": "tasks", "action": "summarize"}]}}]})
    assert (await retained_custom_action_context(ctx, "tasks", [{"owned_paths": [page]}]))[1] == {"summarize": manifest}
    assert await retained_custom_action_context({**ctx, "build_mode": "new"}, "tasks", []) == ("", {})


@pytest.mark.parametrize("field,value", [("api_surface", "public"), ("permissions", ["extra"]), ("emits", [EVENT]),
                                         ("ask_context_safe", True), ("entitlement_gate", "feature.test"),
                                         ("input_schema", {"type": "object", "properties": {"task_id": {"type": "string"}},
                                                           "required": ["task_id"], "additionalProperties": False})])
def test_authored_result_action_cannot_expand_its_pinned_grant(field, value):
    from mozaiksai.core.workflow.generator_support.module_action_inventory import (
        pinned_result_action_schemas,
    )
    from mozaiksai.core.workflow.generator_support.module_write_actions import close_module_actions

    ctx = workflow_context()
    actions = [{"id": "save_summary", "handler_method": "store", "api_surface": "internal",
                "input_schema": ctx["workflow_integration_metadata"]["workflows"][0]["result_output_schema"], field: value}]
    payload = {"module_contract": {"module_id": "tasks", "module_yaml": {"module": {"id": "tasks"}, "actions": actions}}}
    with pytest.raises(ValueError, match="internal action|receiver input"):
        close_module_actions(payload, app_build_plan={}, result_action_schemas=pinned_result_action_schemas(ctx))


def test_planned_module_receives_its_missing_result_capability_and_reaction(tmp_path):
    from factory_app.workflows.AppGenerator.tools.workflow_result_writes import (
        compile_result_task_bindings,
    )

    ctx = workflow_context()
    ctx["app_build_plan"] = {"event_flows": [{"event_type": EVENT, "producing_action": "create", "producer_pack_id": "tasks",
                                              "workflow_capability_ids": ["tasks.review"]}]}
    task = {"task_type": "module_contract", "capability_pack_id": "tasks",
            "owned_paths": ["modules/tasks/module.yaml", "modules/tasks/contracts/reactions.yaml"]}
    files = app_files(tmp_path)
    contract = {"module_id": "tasks", "module_yaml": yaml.safe_load(files["modules/tasks/module.yaml"]),
                "events_yaml": yaml.safe_load(files["modules/tasks/contracts/events.yaml"]), "reactions_yaml": None}
    original_caps = [c for c in contract["module_yaml"]["capabilities"] if c["kind"] != "workflow"]
    contract["module_yaml"]["capabilities"] = deepcopy(original_caps)
    worker = {"module_contract": contract}
    compile_result_task_bindings(worker, task, ctx)
    reaction, = contract["reactions_yaml"]["reactions"]
    assert reaction["target"] == {"kind": "capability", "capability_id": "tasks.review", "result_write": WRITE}
    assert contract["module_yaml"]["capabilities"][:-1] == original_caps
    assert contract["module_yaml"]["capabilities"][-1]["target"] == "ReviewWorkflow"
    unrelated = {"id": "other", "event_type": "domain.other", "target": {"kind": "handler", "handler_method": "other"}}
    contract["reactions_yaml"]["reactions"].append(unrelated)
    before = deepcopy(worker)
    compile_result_task_bindings(worker, task, ctx)
    assert worker == before
    path = "modules/tasks/contracts/reactions.yaml"
    files["modules/tasks/module.yaml"] = yaml.safe_dump(contract["module_yaml"])
    files[path] = yaml.safe_dump(contract["reactions_yaml"])
    files.update(workflow_result_files(files, ctx["workflow_integration_metadata"]))
    assert workflow_result_files(files, ctx["workflow_integration_metadata"]) == {}
    with pytest.raises(ValueError, match="must own"):
        compile_result_task_bindings(worker, {**task, "owned_paths": [task["owned_paths"][0]]}, ctx)
    for field, value, error in [("capability", "WrongWorkflow", "conflicting workflow capability"),
                                ("event", "domain.unapproved", "conflicting event"),
                                ("output", ["unapproved"], "Conflicting result reaction"),
                                ("source", [], "must emit the declared result event")]:
        altered = deepcopy(worker)
        target = altered["module_contract"]
        if field == "capability":
            target["module_yaml"]["capabilities"][-1]["target"] = value
        elif field == "event":
            target["reactions_yaml"]["reactions"][0]["event_type"] = value
        elif field == "output":
            target["reactions_yaml"]["reactions"][0]["target"]["result_write"]["fields"] = value
        else:
            target["module_yaml"]["actions"][0]["emits"] = value
        with pytest.raises(ValueError, match=error):
            compile_result_task_bindings(altered, task, ctx)
    ctx["app_build_plan"]["event_flows"] = []
    with pytest.raises(ValueError, match="build plan must bind"):
        compile_result_task_bindings(worker, task, ctx)


def test_task_or_caller_cannot_choose_the_workflow_that_admits_an_internal_action():
    from pathlib import Path

    from mozaiksai.core.workflow.context.authority import (
        ContextAuthorityError,
        build_context_authority_policy,
    )
    from mozaiksai.core.workflow.generator_support.module_action_inventory import (
        pinned_result_action_schemas,
    )

    path = Path(__file__).parents[1] / "factory_app/workflows/AppGenerator/context_variables.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=cfg["definitions"])
    for key in ("workflow_integration_metadata", "workflow_bundle_artifact_version_id"):
        policy.require_can_write(key, writer_id="lifecycle_tool", operation="hydrate")
        for writer in ("caller_input", "agent_text"):
            with pytest.raises(ContextAuthorityError):
                policy.require_can_write(key, writer_id=writer, operation="task_context")
    ctx = workflow_context()
    for key in ("workflow_bundle_artifact_version_id", "upstream_workflow_bundle_artifact_version_id"):
        with pytest.raises(ValueError, match="registered workflow bundle"):
            pinned_result_action_schemas({**ctx, key: "wrong-record"})


def app_files(tmp_path):
    folder = _write_canonical_module(tmp_path, reaction_target="capability")
    module = yaml.safe_load((folder / "module.yaml").read_text())
    module["actions"].append({
        "id": "save_summary", "handler_method": "receive_summary", "description": "Save the summary",
        "api_surface": "internal", "permissions": [], "emits": [],
        "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}},
                         "required": ["summary"], "additionalProperties": False},
    })
    events = yaml.safe_load((folder / "contracts/events.yaml").read_text())
    events["events"][0]["payload_schema"]["properties"] = {"task_id": {"type": "string"}, "title": {"type": "string"}}
    data = {"version": "1", "surfaces": [{"surface_id": "tasks", "surface_kind": "module", "collections": [
        {"name": "tasks", "scope": "app", "entity": "Task", "tenancy": "per_user", "owner_field": "owner",
         "ownership": {"surface_id": "tasks", "surface_kind": "module"},
         "fields": [{"name": name, "type": "string"} for name in ["owner", "summary", "task_id"]]}
    ]}]}
    files = {p.relative_to(tmp_path).as_posix(): p.read_text() for p in folder.rglob("*") if p.is_file()}
    files["modules/tasks/module.yaml"] = yaml.safe_dump(module)
    files["modules/tasks/contracts/events.yaml"] = yaml.safe_dump(events)
    files["data/contract.json"] = json.dumps(data)
    files["modules/tasks/backend/service.py"] = "def existing_service():\n    return 13\n"
    return files


@pytest.mark.parametrize("existing", [False, True])
def test_approved_result_wires_host_input_to_the_agent_and_retains_other_context(existing, monkeypatch):
    from mozaiksai.core.session.launcher import validate_context_for_workflow
    from mozaiksai.core.workflow.context.authority import ContextAuthorityError
    from mozaiksai.core.workflow.context.context_utils import apply_context_exposures
    from mozaiksai.core.workflow.context.schema import load_context_variables_config
    from mozaiksai.core.workflow.workflow_manager import workflow_manager
    from mozaiksai.hosts.platform import _build_workflow_trigger_context

    context = {"definitions": {"label": {"type": "string", "source": {"type": "state", "default": "local"}}},
               "agents": {"Other": {"variables": ["label"]}, "ResultAgent": {"variables": ["label"]}}}
    if existing:
        context["definitions"]["event_payload"] = {"description": "Input record", "type": "dict",
                                                    "source": {"type": "state"}, "tool_visible": False}
    candidate = entry(context=context)
    before = deepcopy(candidate)
    compiled = materialize_workflow_result_write(candidate, SPEC)
    assert candidate == before and materialize_workflow_result_write(compiled, SPEC) == compiled
    manifests = {file["filename"]: yaml.safe_load(file["content"]) for file in compiled["files"] if file["filename"].endswith(".yaml")}
    config = manifests["context_variables.yaml"]
    assert config["definitions"]["label"] == context["definitions"]["label"]
    assert config["agents"]["Other"] == context["agents"]["Other"]
    payload = config["definitions"]["event_payload"]
    assert payload["model_visible"] is True and payload["writer_ids"] == ["transition_router"]
    if existing:
        assert payload["description"] == "Input record" and payload["tool_visible"] is False and payload["type"] == "dict"
    monkeypatch.setattr(workflow_manager, "get_config", lambda _: {"context_variables": config})
    trigger, = manifests["orchestrator.yaml"]["triggers"]
    event = {"type": EVENT, "payload": {"task_id": "t-17", "title": "Stockroom inventory"}}
    seed = _build_workflow_trigger_context(capability_id=trigger["capability_id"], source_event=event, trigger=trigger)
    admitted = validate_context_for_workflow("ReviewWorkflow", seed)
    assert admitted == {"event_payload": event["payload"]}
    with pytest.raises(ContextAuthorityError):
        validate_context_for_workflow("ReviewWorkflow", admitted, writer_id="caller_input")
    view = load_context_variables_config(config).agents["ResultAgent"].variables
    assert view == ["label", "event_payload"] and event["payload"]["title"] in apply_context_exposures("", [], admitted, view)


@pytest.mark.parametrize("overrides", [
    {"writer_ids": ["caller_input"]}, {"writer_ids": ["transition_router", "agent_text"]},
    {"type": "string"}, {"source": {"type": "external", "service": "other"}},
    {"source": {"type": "state", "default": {"title": "different"}}}, {"model_visible": False},
    {"authority_class": "tool_only_information"}, {"persisted": False}, {"authorization": True},
])
def test_result_event_input_rejects_conflicting_authority(overrides):
    declaration = {"type": "object", "source": {"type": "state"}, **overrides}
    with pytest.raises(ValueError, match="event_payload as model-visible object state written only by transition_router"):
        materialize_workflow_result_write(entry(context={"definitions": {"event_payload": declaration}}), SPEC)


@pytest.mark.parametrize("existing_events", ["omitted", "empty", "missing_capability", "one_event_and_webhook"])
def test_missing_reviewed_event_wiring_uses_one_canonical_capability(existing_events):
    second = deepcopy(SPEC["result_write_plans"][0])
    second["event_type"] = "domain.tasks.submitted"
    second["result_write"]["source_action"] = "submit"
    purpose = "Summarize the task for an owner reviewing a submission."
    spec = {"description": purpose, "result_write_plans": [*SPEC["result_write_plans"], second]}
    candidate = entry()
    manifest = next(file for file in candidate["files"] if file["filename"] == "orchestrator.yaml")
    orchestrator = yaml.safe_load(manifest["content"])
    webhook = {"type": "route", "endpoint": "/inbound", "method": "POST"}
    if existing_events == "omitted":
        orchestrator.pop("triggers")
    elif existing_events == "empty":
        orchestrator["triggers"] = []
    elif existing_events == "missing_capability":
        orchestrator["triggers"] = [{"type": "event", "event": EVENT}]
    else:
        orchestrator["triggers"][0]["description"] = "Review newly created tasks."
        orchestrator["triggers"].append(webhook)
    manifest["content"] = yaml.safe_dump(orchestrator)
    assert materialize_workflow_result_write(candidate, {}) == candidate
    compiled = materialize_workflow_result_write(candidate, spec)
    assert materialize_workflow_result_write(compiled, spec) == compiled
    metadata = extract_workflow_integration_metadata_from_bundle_entries([compiled])["workflows"][0]
    assert metadata["capability_id"] == ("tasks.review" if existing_events == "one_event_and_webhook" else "review-workflow")
    assert {e["event_type"] for e in metadata["trigger_events"]} == {EVENT, second["event_type"]}
    doc = next(file for file in compiled["files"] if file["filename"] == "orchestrator.yaml")
    triggers = yaml.safe_load(doc["content"])["triggers"]
    assert next(trigger for trigger in triggers if trigger.get("event") == second["event_type"])["description"] == purpose
    if existing_events == "one_event_and_webhook":
        assert webhook in triggers and next(trigger for trigger in triggers if trigger.get("event") == EVENT)["description"] == "Review newly created tasks."


def test_result_materialization_passes_full_gate_only_for_reviewed_events():
    from factory_app.workflows.AgentGenerator.tools.pattern_selection import selection_fingerprint
    from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
        run_workflow_bundle_quality_gate,
    )

    candidate = entry()
    candidate["files"].extend([
        {"filename": "ui_config.yaml", "content": "visual_agents: []"},
        {"filename": "middleware.yaml", "content": "prompt_middleware: []"},
    ])
    manifest = next(file for file in candidate["files"] if file["filename"] == "orchestrator.yaml")
    orchestrator = {**yaml.safe_load(manifest["content"]), "schema_version": "mozaiks.orchestrator.v1", "triggers": []}
    manifest["content"] = yaml.safe_dump(orchestrator)
    spec = {**SPEC, "description": "Summarize task submissions for the owner.", "context_variables": {"expected_event_trigger": EVENT}}
    selection = {"workflows": [spec]}
    context = {"PatternSelection": selection, "workflow_plan_review": {"status": "approved", "selection_hash": selection_fingerprint(selection)}}
    result = run_workflow_bundle_quality_gate(bundle_entries=[candidate], expected_workflows=[spec], context_variables=context)
    assert result["passed"] and result["errors"] == []

    orchestrator["triggers"] = [{"type": "event", "event": "domain.tasks.unrelated", "capability_id": "review-workflow",
                                 "description": "Summarize unrelated task submissions."}]
    manifest["content"] = yaml.safe_dump(orchestrator)
    rejected = run_workflow_bundle_quality_gate(bundle_entries=[candidate], expected_workflows=[spec], context_variables=context)
    assert not rejected["passed"] and any("event_trigger_missing" in err for err in rejected["errors"])


async def test_reviewed_agent_result_is_flat_and_automatic_for_multiple_triggers(monkeypatch):
    second = deepcopy(SPEC["result_write_plans"][0])
    second["event_type"] = "domain.tasks.submitted"
    second["result_write"]["source_action"] = "submit"
    spec = {**SPEC, "result_write_plans": [*SPEC["result_write_plans"], second]}
    rendered = materialize_workflow_result_write(entry(triggers=[EVENT, second["event_type"]]), spec)
    assert materialize_workflow_result_write(rendered, spec) == rendered
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    assert metadata["workflows"][0]["result_write_plans"] == spec["result_write_plans"]
    files = {f["filename"]: f["content"] for f in rendered["files"]}
    binding, = yaml.safe_load(files["tools.yaml"])["tools"]
    assert (binding["agent"], binding["auto_tool_call"], binding["bind_to_agent"]) == ("ResultAgent", True, False)
    commit = AsyncMock(return_value={"status": "committed"})
    monkeypatch.setattr(module_tools, "commit_workflow_result", commit)
    scope = {}
    exec(compile(files[binding["file"]], binding["file"], "exec"), scope)
    receipt = await scope[binding["function"]]({"structured_output": {"summary": "Ready"}, "task_id": "ignored"})
    commit.assert_awaited_once_with({"summary": "Ready"})
    assert receipt == {"status": "committed"}


@pytest.mark.parametrize("fields", [
    {"summary": {"type": "str"}, "task_id": {"type": "str"}},
    {"summary": {"type": "str", "default": ""}},
    {"nested_result": {"type": "str"}},
])
def test_result_agent_cannot_select_record_or_weaken_approved_output(fields):
    with pytest.raises(ValueError, match="ResultAgent.*exactly the approved.*summary.*host supplies the record key task_id"):
        materialize_workflow_result_write(entry(fields=fields), SPEC)


def test_builder_cannot_add_an_unreviewed_result_or_leave_an_unbound_trigger():
    with pytest.raises(ValueError, match="cannot change"):
        materialize_workflow_result_write({**entry(), "result_write_plans": SPEC["result_write_plans"]}, {})
    rendered = materialize_workflow_result_write(entry(triggers=[EVENT, "domain.tasks.other"]), SPEC)
    with pytest.raises(ValueError, match="each declared"):
        extract_workflow_integration_metadata_from_bundle_entries([rendered])


def test_result_actor_receives_actionable_repair_for_worker_authored_save():
    candidate = entry()
    candidate["outcome_plans"] = [{
        "agent": "ResultAgent", "function": "save_summary", "context_key": "save_status",
        "attempts_key": "save_attempts", "error_value": "failed",
        "routes": [{"value": "failed", "target_agent": "terminate"}],
    }]
    with pytest.raises(ValueError) as error:
        materialize_workflow_result_write(candidate, SPEC)
    message = str(error.value)
    assert "Factory supplies the save operation for ResultAgent" in message
    assert "outcome_plan" in message and "generated save tool binding and its Python implementation" in message
    assert "structured output and one after_turn success continuation" in message


def test_required_result_preserves_the_success_continuation_and_refuses_a_bypass():
    candidate = entry()
    files = {file["filename"]: file for file in candidate["files"]}
    orchestrator = yaml.safe_load(files["orchestrator.yaml"]["content"])
    files["orchestrator.yaml"]["content"] = yaml.safe_dump({**orchestrator, "initial_agent": "TriageAgent"})
    rules = [
        {"source_agent": "TriageAgent", "target_agent": "ResultAgent", "transition_type": "after_turn"},
        {"source_agent": "ResultAgent", "target_agent": "ClosingAgent", "transition_type": "after_turn"},
        {"source_agent": "ClosingAgent", "target_agent": "terminate", "transition_type": "after_turn"},
    ]
    files["transition_graph.yaml"]["content"] = yaml.safe_dump({"transition_rules": rules})
    compiled = materialize_workflow_result_write(candidate, SPEC)
    assert materialize_workflow_result_write(compiled, SPEC) == compiled
    graph = next(file for file in compiled["files"] if file["filename"] == "transition_graph.yaml")
    result_routes = [rule for rule in yaml.safe_load(graph["content"])["transition_rules"] if rule["source_agent"] == "ResultAgent"]
    assert {rule.get("condition_value"): rule["target_agent"] for rule in result_routes} == {
        "applied": "ClosingAgent", "already_applied": "ClosingAgent", "failed": "terminate", None: "terminate",
    }
    assert all(rule["termination_reason"] == "workflow_failed" for rule in result_routes if rule["target_agent"] == "terminate")
    rules.append({"source_agent": "TriageAgent", "target_agent": "terminate", "transition_type": "condition",
                  "condition_type": "context_equals", "condition_key": "finish_early", "condition_value": True})
    files["transition_graph.yaml"]["content"] = yaml.safe_dump({"transition_rules": rules})
    with pytest.raises(ValueError, match="bypasses the approved result agent"):
        materialize_workflow_result_write(candidate, SPEC)


@pytest.mark.parametrize(("receiver", "storage", "diagnostic"), [
    ("integer", "integer", "compiled workflow result schema"),
    ("string", "integer", "collection field type"),
])
def test_actual_workflow_artifact_must_match_result_action_and_storage(tmp_path, receiver, storage, diagnostic):
    context = workflow_context()
    files = app_files(tmp_path)
    path = "modules/tasks/module.yaml"
    module = yaml.safe_load(files[path])
    module["actions"][-1]["input_schema"]["properties"]["summary"]["type"] = receiver
    files[path] = yaml.safe_dump(module)
    contract = json.loads(files["data/contract.json"])
    field = next(field for field in contract["surfaces"][0]["collections"][0]["fields"] if field["name"] == "summary")
    field["type"] = storage
    files["data/contract.json"] = json.dumps(contract)
    with pytest.raises(ValueError, match=diagnostic):
        workflow_result_files(files, context["workflow_integration_metadata"])
    verdict = validate_workflow_integration_contract(files, context)
    assert verdict["passed"] is False and diagnostic in verdict["failed_tests"][0]["error"]


def test_nested_compiled_result_shapes_survive_pinning_and_reject_incompatible_items(tmp_path):
    local = entry(fields={"summary": {"type": "list", "items": "Review"}}, models={
        "Review": {"type": "model", "fields": {"rating": {"type": "int", "description": "Review rating"}}},
    })
    rendered = materialize_workflow_result_write(local, SPEC)
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    record = _record("typed-workflow", {"workflow_integration_metadata": metadata})
    pinned = workflow_integration_metadata_from_artifact(record)
    expected = {"type": "array", "items": {"type": "object", "properties": {"rating": {"type": "integer"}},
                                           "required": ["rating"], "additionalProperties": False}}
    assert pinned["workflows"][0]["result_output_schema"]["properties"]["summary"] == expected
    files = app_files(tmp_path)
    data = json.loads(files["data/contract.json"])
    next(f for f in data["surfaces"][0]["collections"][0]["fields"] if f["name"] == "summary")["type"] = "array"
    files["data/contract.json"] = json.dumps(data)
    path = "modules/tasks/module.yaml"
    module = yaml.safe_load(files[path])
    props = module["actions"][-1]["input_schema"]["properties"]
    props["summary"] = deepcopy(expected)
    files[path] = yaml.safe_dump(module)
    files.update(workflow_result_files(files, pinned))
    assert workflow_result_files(files, pinned) == {}
    props["summary"]["items"]["properties"]["rating"]["type"] = "string"
    files[path] = yaml.safe_dump(module)
    with pytest.raises(ValueError, match="compiled workflow result schema"):
        workflow_result_files(files, pinned)


async def test_historical_producer_schema_is_recovered_only_from_verified_workflow_bytes(tmp_path):
    rendered = materialize_workflow_result_write(entry(), SPEC)
    metadata = extract_workflow_integration_metadata_from_bundle_entries([rendered])
    expected = metadata["workflows"][0].pop("result_output_schema")
    path = tmp_path / "TicketOps.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for file in rendered["files"]:
            archive.writestr(f"ReviewWorkflow/{file['filename']}", file["content"])
    record = _record("archived-result-workflow", {"artifact_path": str(path), "workflow_integration_metadata": metadata})
    raw = path.read_bytes()
    record.files_manifest = [BuildRecordFileEntry(path="TicketOps/TicketOps.zip", sha256=hashlib.sha256(raw).hexdigest(),
                                                  size_bytes=len(raw), content_type="application/zip")]
    for saved in (record, record.model_dump(by_alias=True)):
        pinned = workflow_integration_metadata_from_artifact(saved)
        assert pinned["workflows"][0]["result_output_schema"] == expected
        assert pinned["source_artifact_version_id"] == record.id
    context = factory_context({"artifact_version_refs": {"workflow_bundle": record.id},
                               "workflow_integration_metadata": metadata})
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=record))
    await hydrate_workflow_integration_context_from_latest_artifact(context, artifact_store=store)
    assert context["workflow_integration_metadata"]["workflows"][0]["result_output_schema"] == expected
    assert "result_output_schema" not in metadata["workflows"][0]
    path.write_bytes(raw + b"tampered")
    with pytest.raises(ValueError, match="digest differs"):
        workflow_integration_metadata_from_artifact(record)
    path.unlink()
    with pytest.raises(ValueError, match="Could not read"):
        workflow_integration_metadata_from_artifact(record)


def test_a_string_producer_cannot_write_unrestricted_values_to_an_enum_field(tmp_path):
    files = app_files(tmp_path)
    data = json.loads(files["data/contract.json"])
    summary = next(f for f in data["surfaces"][0]["collections"][0]["fields"] if f["name"] == "summary")
    summary["enum"] = ["draft", "complete"]
    files["data/contract.json"] = json.dumps(data)
    with pytest.raises(ValueError, match="action enum must fit"):
        workflow_result_files(files, workflow_context()["workflow_integration_metadata"])


async def test_app_assembly_compiles_the_actual_internal_receiver(tmp_path, monkeypatch):
    files = app_files(tmp_path)
    ctx = workflow_context()
    result = await assemble_features("generated-app", [{"code_files": [{"filename": path, "content": source}
                                    for path, source in files.items()]}], context_variables=ctx,
                                    data_contract=json.loads(files["data/contract.json"]))
    built = {f["filename"]: f["content"] for f in result["code_files"]}
    assert workflow_result_files(built, ctx["workflow_integration_metadata"]) == {}
    assert validate_workflow_integration_contract(built, ctx)["passed"]
    assert yaml.safe_load(built["modules/tasks/contracts/reactions.yaml"])["reactions"][0]["target"]["result_write"] == WRITE
    package = tmp_path / "compiled_result_receiver"
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name in ["handler.py", "service.py"]:
        (package / name).write_text(built[f"modules/tasks/backend/{name}"], encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    service = importlib.import_module("compiled_result_receiver.service")
    handler = importlib.import_module("compiled_result_receiver.handler").TasksModule()
    assert service.existing_service() == 13
    assert hasattr(handler, "create_task")
    host = SimpleNamespace(commit_workflow_result=AsyncMock(return_value={"status": "committed"}))
    assert await handler.receive_summary(host, summary="Written") == {"status": "committed"}
    host.commit_workflow_result.assert_awaited_once_with({"summary": "Written"})
    assert not validate_workflow_integration_contract(built, {})["passed"]


async def test_unrelated_app_revision_retains_only_the_selected_parents_result_grant(tmp_path):
    original = workflow_context()
    files = app_files(tmp_path)
    files.update(workflow_result_files(files, original["workflow_integration_metadata"]))
    parent = _record("accepted-app", {}, family="app_bundle", inputs={"workflow_bundle": "approved-workflow"})
    workflow = _record("approved-workflow", {"workflow_integration_metadata": original["workflow_integration_metadata"]})
    store = _FakeArtifactStore(None)
    store.get_build_record = AsyncMock(side_effect=[parent, workflow])
    ctx = factory_context({"artifact_kind": "app_bundle", "artifact_version_id": "accepted-app",
                           "artifact_version_refs": {"workflow_bundle": "unrelated-current"}})
    await hydrate_workflow_integration_context_from_latest_artifact(ctx, artifact_store=store)
    assert ctx["workflow_integration_metadata"]["source_artifact_version_id"] == "approved-workflow"
    assert not store.list_calls
    files["brand/theme_config.json"] = '{"accent":"blue"}'
    assert workflow_result_files(files, ctx["workflow_integration_metadata"]) == {}
    assert validate_workflow_integration_contract(files, ctx)["passed"]
    assert compiled_pack_module_templates(context_variables=ctx, data_contract=None, subscription_contract=None) == {}
    path = "modules/tasks/contracts/reactions.yaml"
    altered = yaml.safe_load(files[path])
    altered["reactions"][0]["target"]["result_write"]["fields"].append("title")
    files[path] = yaml.safe_dump(altered)
    assert not validate_workflow_integration_contract(files, ctx)["passed"]
    detached = {**original["workflow_integration_metadata"], "source_artifact_version_id": None}
    with pytest.raises(ValueError, match="registered workflow bundle ID"):
        workflow_result_files(files, detached)


@pytest.mark.parametrize("removed", ["modules/tasks/contracts/reactions.yaml", "modules/tasks/module.yaml",
                                     "data/contract.json", "modules/tasks/backend/service.py"])
def test_missing_result_owner_fails_closed(tmp_path, removed):
    files = app_files(tmp_path)
    files.pop(removed)
    with pytest.raises(ValueError):
        workflow_result_files(files, workflow_context()["workflow_integration_metadata"])
