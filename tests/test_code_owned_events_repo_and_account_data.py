"""Canonical writes own their events, repository and account data; model leftovers never reach runtime.

Live chat fdfa818e (OSS 57c78c5f), module task_management: the actions emitted
``task.created`` while events.yaml declared ``domain.task.created`` (and the repair
advice asked for an undeclarable name), repo.py kept a dead model ``TaskRepo``
class calling Motor methods, and ``user_data_scope: true`` had no
account-data handler. Each is now built or reconciled by code; only a genuine
judgment gap is rejected, with one message naming both sides.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from types import SimpleNamespace

import pytest
import yaml

import mozaiksai.core.account as account
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_user_data_scope,
    _required_module_paths,
)
from factory_app.workflows.AppGenerator.tools.module_persistence_guard import (
    scan_module_persistence,
)
from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    audit_module_runtime_quality,
)
from mozaiksai.core.account import AccountDataRegistry
from mozaiksai.core.runtime.app.module_loader import ModuleEventsManifest, ModuleLoader
from mozaiksai.core.runtime.composition.schema_validation import validate_json_schema
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.module_account_data import (
    materialize_module_account_handlers,
    materialize_task_module_account_handlers,
    render_account_data_handler,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    canonical_write_event_aliases,
    canonical_write_event_for,
    canonical_write_event_type,
)
from mozaiksai.core.workflow.generator_support.module_authored_code import (
    prune_repository,
    reconcile_emit_literals,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    materialize_module_actions,
    materialize_module_write_implementations,
)
from tests.test_appgenerator_module_write_actions import (
    BACKEND,
    MANIFEST,
    MODULE,
    _closed,
    _collection,
    _context,
    _contract,
    _design,
    _files,
    _import_backend,
    _output,
    _plan,
    _RawCollection,
)

EVENTS = f"modules/{MODULE}/contracts/events.yaml"
REPO = f"{BACKEND}/repo.py"
SERVICE = f"{BACKEND}/service.py"
HANDLER = f"modules/{MODULE}/backend/account_data_handler.py"

# The model's repository class the live business_services output kept beside
# the code-rendered functions (verbatim from chat fdfa818e).
LIVE_TASK_REPO = """class TaskRepo:
    async def _collection(self, ctx):
        return ctx.persistence.collection('task_management', 'tasks')

    async def insert_one(self, ctx, task):
        collection = await self._collection(ctx)
        task['created_at'] = ctx.timestamp()
        task['updated_at'] = ctx.timestamp()
        return await collection.insert_one(task)

    async def list_tasks(self, ctx, page, page_size, search):
        collection = await self._collection(ctx)
        query = {'$match': {'user_id': ctx.user_id}}
        total = await collection.count_documents(query)
        items = await collection.find(query).skip((page - 1) * page_size).limit(page_size).to_list()
        return {'items': items, 'total': total}
"""


def _live_events_yaml() -> dict:
    """The live events.yaml: canonical types with a model-invented payload."""
    def entry(verb, actor):
        return {
            "type": f"domain.task.{verb}", "version": 1, "producer": MODULE,
            "description": f"Event emitted when a task is {verb}.",
            "payload_schema": {"type": "object", "properties": {"task_id": {"type": "string"}, actor: {"type": "string"}},
                               "required": ["task_id", actor]},
        }
    return {"schema_version": "mozaiks.events.v1",
            "events": [entry("created", "created_by"), entry("updated", "updated_by"), entry("deleted", "deleted_by")]}


def _live_output() -> dict:
    output = _output([
        {"id": "create_task", "handler_method": "create_task", "emits": ["task.created"]},
        {"id": "update_task", "handler_method": "update_task", "emits": ["task.updated"]},
        {"id": "delete_task", "handler_method": "delete_task", "emits": ["task.deleted"]},
    ])
    output["module_contract"]["events_yaml"] = _live_events_yaml()
    return output


def _events(closed) -> dict[str, dict]:
    return {event["type"]: event for event in closed["module_contract"]["events_yaml"]["events"]}


# --------------------------------------------------------------------------- events


def test_one_naming_rule_names_canonical_write_events():
    assert canonical_write_event_type("Task", "create") == "domain.task.created"
    assert canonical_write_event_type("ProjectMilestone", "delete") == "domain.project_milestone.deleted"
    aliases = canonical_write_event_aliases(_contract())
    for spelling in ("task.created", "domain.task.created", "Task_Created", "tasks.created",
                     "domain.task_management.task_created", "domain.tasks.task_created", "task_management.created"):
        assert canonical_write_event_for(spelling, aliases) == "domain.task.created", spelling
    assert canonical_write_event_for("domain.task.completed", aliases) is None


def test_a_spelling_two_collections_share_maps_to_neither():
    contract = _contract()
    second = _collection()
    second.update(name="task_archive", entity="TaskArchive")
    contract["surfaces"][0]["collections"].append(second)
    aliases = canonical_write_event_aliases(contract)
    assert canonical_write_event_for("task_management.created", aliases) is None  # which collection?
    assert canonical_write_event_for("task.created", aliases) == "domain.task.created"
    assert canonical_write_event_for("task_archive.created", aliases) == "domain.task_archive.created"


def test_live_emits_and_declarations_are_reconciled_to_the_canonical_events(caplog):
    with caplog.at_level(logging.INFO):
        closed = _closed(_live_output(), design_surface_map=_design(["task.created", "task.updated", "task.deleted"]))
    actions = {action["id"]: action for action in closed["module_contract"]["module_yaml"]["actions"]}
    assert {name: actions[name]["emits"] for name in ("create_task", "update_task", "delete_task")} == {
        "create_task": ["domain.task.created"], "update_task": ["domain.task.updated"],
        "delete_task": ["domain.task.deleted"],
    }
    events = _events(closed)
    assert list(events) == ["domain.task.created", "domain.task.updated", "domain.task.deleted"]
    # The payload is the stored record, not the invented {task_id, created_by}.
    record = actions["create_task"]["output_schema"]["properties"]["item"]
    assert all(event["payload_schema"] == record and event["producer"] == MODULE for event in events.values())
    ModuleEventsManifest.model_validate(closed["module_contract"]["events_yaml"])
    lines = [record.getMessage() for record in caplog.records if "CANONICAL_EVENTS_NORMALIZED" in record.getMessage()]
    assert any("'create_task' emits ['task.created'] -> ['domain.task.created']" in line for line in lines)
    assert any("are rendered from the record contract" in line for line in lines)


def test_design_approval_alone_makes_the_canonical_write_emit():
    closed = _closed(design_surface_map=_design(["task.deleted"]))
    actions = {action["id"]: action for action in closed["module_contract"]["module_yaml"]["actions"]}
    assert actions["delete_task"]["emits"] == ["domain.task.deleted"]
    assert actions["create_task"]["emits"] == [] and actions["update_task"]["emits"] == []
    assert list(_events(closed)) == ["domain.task.deleted"]
    # Without any approval nothing is emitted and no events companion is invented.
    assert _closed()["module_contract"].get("events_yaml") is None


def test_a_custom_event_keeps_its_name_and_the_domain_prefix_is_reconciled_on_both_sides():
    output = _output([
        {"id": "complete_task", "handler_method": "complete_task", "emits": ["task.completed"]},
        {"id": "archive_task", "handler_method": "archive_task", "emits": ["task.archived"]},
    ])
    custom = {"version": 1, "producer": MODULE, "payload_schema": {"type": "object"}}
    output["module_contract"]["events_yaml"] = {"schema_version": "mozaiks.events.v1", "events": [
        {"type": "domain.task.completed", **custom}, {"type": "task.archived", **custom},
    ]}
    closed = _closed(output)
    actions = {action["id"]: action for action in closed["module_contract"]["module_yaml"]["actions"]}
    assert actions["complete_task"]["emits"] == ["domain.task.completed"]
    assert actions["archive_task"]["emits"] == ["domain.task.archived"]
    assert set(_events(closed)) == {"domain.task.completed", "domain.task.archived"}


def test_an_emit_no_declaration_can_match_is_one_message_naming_both_sides():
    output = _output([{"id": "complete_task", "handler_method": "complete_task", "emits": ["task.completed"]}])
    output["module_contract"]["events_yaml"] = {"schema_version": "mozaiks.events.v1", "events": [
        {"type": "domain.task.finished", "version": 1, "producer": MODULE, "payload_schema": {"type": "object"}},
    ]}
    with pytest.raises(ValueError) as error:
        _closed(output)
    message = str(error.value)
    assert "action 'complete_task' emits 'task.completed'" in message
    assert "contracts/events.yaml declares ['domain.task.finished']" in message
    assert "declare 'domain.task.completed' in module_contract.events_yaml" in message


def test_assembly_renders_the_events_companion_from_the_admitted_files():
    live = _live_output()
    events_source = yaml.safe_dump(live["module_contract"].pop("events_yaml"), sort_keys=False)
    files = {MANIFEST: yaml.safe_dump(live["module_contract"]["module_yaml"], sort_keys=False), EVENTS: events_source}
    changed = materialize_module_actions(
        files, app_build_plan=_plan(), data_contract=_contract(),
        design_surface_map=_design(["task.created", "task.updated", "task.deleted"]),
    )
    events = ModuleEventsManifest.model_validate(yaml.safe_load(changed[EVENTS]))
    assert sorted(events.event_types) == ["domain.task.created", "domain.task.deleted", "domain.task.updated"]
    manifest = yaml.safe_load(changed[MANIFEST])
    assert {a["id"]: a["emits"] for a in manifest["actions"] if a["id"].endswith("_task")}["create_task"] == [
        "domain.task.created",
    ]
    again = {**files, **changed}
    assert materialize_module_actions(
        again, app_build_plan=_plan(), data_contract=_contract(),
        design_surface_map=_design(["task.created", "task.updated", "task.deleted"]),
    ) == {}


@pytest.mark.asyncio
async def test_the_rendered_service_emits_payloads_the_declared_schemas_accept(tmp_path, monkeypatch):
    design = _design(["task.created", "task.updated", "task.deleted"])
    files = _files(model_files={SERVICE: (
        "async def after_create_task(ctx, record):\n"
        "    await ctx.emit('task.created', {'task_id': record['task_id']})\n"
    )}, design_surface_map=design)
    assert "after_create_task" in files[SERVICE] and "ctx.emit('task.created'" not in files[SERVICE]
    closed = _closed(design_surface_map=design)
    schemas = {event["type"]: event["payload_schema"] for event in closed["module_contract"]["events_yaml"]["events"]}
    handler_module, _service = _import_backend(tmp_path, monkeypatch, files, "code_owned_events_runtime")
    handler = handler_module.TaskManagementHandler()
    ctx = _context(_RawCollection(), "a")
    created = (await handler.create_task(ctx, title="First"))["item"]
    await handler.update_task(ctx, task_id=created["task_id"], title="Second")
    await handler.delete_task(ctx, task_id=created["task_id"])
    assert [event for event, _payload in ctx.events] == ["domain.task.created", "domain.task.updated", "domain.task.deleted"]
    for event_type, payload in ctx.events:
        assert validate_json_schema(payload, schemas[event_type]) is None, event_type


def test_emit_literals_are_pointed_at_declared_types_and_a_hook_never_re_emits_its_write_event(caplog):
    source = (
        "async def after_create_task(ctx, record):\n"
        "    await ctx.emit('task.created', {'task_id': record['task_id']})\n\n\n"
        "async def complete_task(ctx, task_id):\n"
        "    await ctx.emit('task.completed', {'task_id': task_id})\n"
        "    await ctx.emit('tasks.updated', {'task_id': task_id})\n"
    )
    with caplog.at_level(logging.INFO):
        rendered = reconcile_emit_literals(
            SERVICE, source, declared={"domain.task.created", "domain.task.updated", "domain.task.completed"},
            aliases=canonical_write_event_aliases(_contract()), write_events={"create_task": "domain.task.created"},
            canonical_events={"domain.task.created", "domain.task.updated", "domain.task.deleted"},
        )
    assert "async def after_create_task(ctx, record):\n    return None\n" in rendered
    assert "ctx.emit('domain.task.completed'" in rendered and "ctx.emit('domain.task.updated'" in rendered
    assert sum("EMIT_LITERAL_NORMALIZED" in record.getMessage() for record in caplog.records) == 3


def test_a_hook_emitting_an_undeclared_canonical_event_is_rejected_with_the_site():
    source = "async def after_delete_task(ctx, record):\n    await ctx.emit('task.deleted', {})\n"
    with pytest.raises(ValueError, match=r"service.py:2: after_delete_task emits 'task.deleted', which names the "
                                         r"canonical write event 'domain.task.deleted'"):
        reconcile_emit_literals(
            SERVICE, source, declared=set(), aliases=canonical_write_event_aliases(_contract()), write_events={},
            canonical_events={"domain.task.created", "domain.task.updated", "domain.task.deleted"},
        )


# --------------------------------------------------------------------------- repository


def test_the_live_dead_repository_class_is_discarded_with_a_logged_normalization(caplog):
    with caplog.at_level(logging.WARNING):
        files = _files(model_files={REPO: LIVE_TASK_REPO})
    assert "class TaskRepo" not in files[REPO] and "count_documents" not in files[REPO]
    for name in ("load_task", "create_task", "update_task", "delete_task", "get_tasks", "list_tasks"):
        assert f"async def {name}(" in files[REPO], name
    assert any("REPO_CODE_DISCARDED" in record.getMessage() and "['TaskRepo']" in record.getMessage()
               for record in caplog.records)
    backend = [{"filename": path, "content": source} for path, source in files.items() if path.startswith(BACKEND)]
    assert audit_module_runtime_quality(backend) == []
    assert scan_module_persistence(files) == []


def test_a_repository_class_business_logic_uses_is_rejected_naming_the_site():
    service = "from .repo import TaskRepo\n\n\nasync def complete_task(ctx, task_id):\n    return await TaskRepo().list_tasks(ctx, 1, 20, None)\n"
    with pytest.raises(ValueError) as error:
        _files(model_files={REPO: LIVE_TASK_REPO, SERVICE: service})
    message = str(error.value)
    assert message.startswith(f"{SERVICE}:1: uses repo.TaskRepo, a model-authored repository class ({REPO}:1).")
    assert "module-level functions; code renders create_task, delete_task, get_tasks, list_tasks, load_task, update_task" in message


def test_a_referenced_repository_function_calling_motor_methods_is_rejected():
    repo = "async def count_open(ctx):\n    return await ctx.persistence.collection('task_management', 'tasks').count_documents({})\n"
    service = "from . import repo\n\n\nasync def summarize_tasks(ctx):\n    return {'open': await repo.count_open(ctx)}\n"
    with pytest.raises(ValueError, match=r"repo.py:2: count_open \(used by .*service.py:5\) calls count_documents"):
        _files(model_files={REPO: repo, SERVICE: service})


def test_custom_repository_functions_business_logic_uses_survive_with_their_helpers():
    repo = (
        "import re\n\nimport json\n\n\n"
        "def _open_filter():\n    return {'is_completed': False}\n\n\n"
        "async def count_open(ctx):\n"
        "    return await ctx.persistence.collection('task_management', 'tasks').count(_open_filter())\n\n\n"
        "async def unused_export(ctx):\n    return json.dumps({})\n"
    )
    service = "from . import repo\n\n\nasync def summarize_tasks(ctx):\n    return {'open': await repo.count_open(ctx)}\n"
    files = _files(model_files={REPO: repo, SERVICE: service})
    assert "async def count_open(ctx)" in files[REPO] and "def _open_filter()" in files[REPO]
    assert "unused_export" not in files[REPO] and "import json" not in files[REPO]


def test_an_opaque_use_of_the_repository_proves_nothing_dead():
    source = LIVE_TASK_REPO + "\n\nasync def get_tasks(ctx, *, id):\n    return {}\n"
    business = {SERVICE: "from . import repo\n\n\ndef lookup(name):\n    return getattr(repo, name)\n"}
    assert prune_repository(REPO, source, module_id=MODULE, code_owned={"get_tasks"}, business_sources=business) == source


_PROBE_RENDERED = (
    "async def insert_task(ctx, record):\n"
    "    return await ctx.persistence.collection('tm', 'tasks').insert_one(record)\n\n\n"
    "async def load_task(ctx, task_id):\n"
    "    return await ctx.persistence.collection('tm', 'tasks').find_one({'task_id': task_id})\n"
)
_PROBE_SERVICE = "modules/tm/backend/service.py"


@pytest.mark.parametrize(("extra", "service", "live"), [
    pytest.param(  # a registration statement is not a definition, but it runs at import
        "\n\nHANDLERS = {}\n\n\nasync def archive_docs(ctx, ids):\n    return len(ids)\n\n\n"
        "HANDLERS['archive'] = archive_docs\n",
        "from . import repo\n\nasync def go(ctx):\n    return await repo.HANDLERS['archive'](ctx, [])\n",
        {"HANDLERS", "archive_docs"}, id="top-level-registration",
    ),
    pytest.param(  # a decorated definition registers itself
        "\n\nREGISTRY = {}\n\n\ndef register(fn):\n    REGISTRY[fn.__name__] = fn\n    return fn\n\n\n"
        "@register\nasync def summarize(ctx):\n    return 1\n",
        "from .repo import REGISTRY\n\nasync def go(ctx):\n    return await REGISTRY['summarize'](ctx)\n",
        {"REGISTRY", "register", "summarize"}, id="decorator-registry",
    ),
    pytest.param(  # importlib reaches the module by a computed name
        "\n\nasync def custom_query(ctx):\n    return await ctx.persistence.collection('tm', 'tasks').find_many({})\n",
        "import importlib\n\nrepo = importlib.import_module(__package__ + '.repo')\n\n"
        "async def go(ctx):\n    return await repo.custom_query(ctx)\n",
        {"custom_query"}, id="importlib",
    ),
    pytest.param(
        "\n\nasync def custom_query(ctx):\n    return 1\n",
        "from . import repo\n\nasync def go(ctx):\n    return await getattr(repo, 'custom_query')(ctx)\n",
        {"custom_query"}, id="getattr",
    ),
    pytest.param(
        "\n\nasync def custom_query(ctx):\n    return 1\n",
        "from .repo import custom_query as cq\n\nasync def go(ctx):\n    return await cq(ctx)\n",
        {"custom_query"}, id="import-alias",
    ),
    pytest.param(  # a module-level if block runs at import
        "\n\nasync def fast(ctx):\n    return 1\n\n\nasync def slow(ctx):\n    return 2\n\n\n"
        "if True:\n    chosen = fast\nelse:\n    chosen = slow\n",
        "from . import repo\n\nasync def go(ctx):\n    return await repo.chosen(ctx)\n",
        {"fast", "slow", "chosen"}, id="module-level-if",
    ),
])
def test_code_used_at_import_or_reached_opaquely_survives_pruning_and_imports(extra, service, live):
    """The verifier's probes (PR #765 review): pruning must never delete used code."""
    source = _PROBE_RENDERED + extra
    pruned = prune_repository(
        "modules/tm/backend/repo.py", source, module_id="tm", code_owned={"insert_task", "load_task"},
        business_sources={_PROBE_SERVICE: service},
    )
    assert pruned == source
    namespace: dict = {}
    exec(compile(pruned, "repo.py", "exec"), namespace)
    assert live <= set(namespace)


@pytest.mark.parametrize("marker", [
    "import importlib\n", "import sys\nMODULES = sys.modules\n", "LOADER = __import__\n",
])
def test_a_dynamic_import_in_the_repository_itself_prunes_nothing(marker):
    source = marker + LIVE_TASK_REPO
    assert prune_repository(REPO, source, module_id=MODULE, code_owned=set(), business_sources={}) == source


def test_dead_definitions_beside_a_registration_are_still_removed(caplog):
    source = (
        _PROBE_RENDERED + "\n\nHANDLERS = {}\n\n\nasync def archive_docs(ctx, ids):\n    return len(ids)\n\n\n"
        "HANDLERS['archive'] = archive_docs\n\n\n" + LIVE_TASK_REPO
    )
    with caplog.at_level(logging.WARNING):
        pruned = prune_repository(
            "modules/tm/backend/repo.py", source, module_id="tm", code_owned={"insert_task", "load_task"},
            business_sources={_PROBE_SERVICE: "from . import repo\n\nasync def go(ctx):\n    return repo.HANDLERS\n"},
        )
    assert "class TaskRepo" not in pruned and "HANDLERS['archive'] = archive_docs" in pruned
    namespace: dict = {}
    exec(compile(pruned, "repo.py", "exec"), namespace)
    assert namespace["HANDLERS"]["archive"] is namespace["archive_docs"]
    assert any("['TaskRepo']" in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- account data


def test_a_per_user_module_gets_user_data_scope_and_a_code_rendered_account_handler(tmp_path, monkeypatch):
    closed = _closed()
    assert closed["module_contract"]["module_yaml"]["module"]["user_data_scope"] is True
    files = _files()
    files.update(materialize_module_account_handlers(files, app_build_plan=_plan(), data_contract=_contract()))
    handler = files[HANDLER]
    assert handler == render_account_data_handler(MODULE, _contract()["surfaces"][0]["collections"])
    assert "self.persistence.collection(_MODULE_ID, name)" in handler and "delete_many({owner_field: user_id})" in handler
    assert scan_module_persistence(files) == []
    assert audit_module_runtime_quality([{"filename": HANDLER, "content": handler}]) == []
    # The real loader accepts it and registers it for account export/deletion.
    for path, source in files.items():
        target = tmp_path / "app" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    registry = AccountDataRegistry()
    monkeypatch.setattr(account, "account_data_registry", registry)
    ModuleLoader(str(tmp_path / "app")).load(MODULE)
    assert registry.registered_module_ids() == [MODULE]


def test_a_model_authored_handler_is_replaced_with_a_log_and_owned_paths_scope_rendering(caplog):
    files = {HANDLER: "class AccountDataHandler:\n    def __init__(self, db):\n        self._db = db\n"}
    with caplog.at_level(logging.WARNING):
        rendered = materialize_task_module_account_handlers(
            files, task={"owned_paths": [HANDLER]}, app_build_plan=_plan(), data_contract=_contract(),
        )
    assert "def __init__(self, persistence" in rendered[HANDLER]
    assert any("ACCOUNT_DATA_HANDLER_OVERWRITTEN" in record.getMessage() for record in caplog.records)
    assert materialize_task_module_account_handlers(
        {}, task={"owned_paths": [SERVICE]}, app_build_plan=_plan(), data_contract=_contract(),
    ) == {}


@pytest.mark.parametrize("tenancy", ["per_workspace", "app_wide"])
def test_rows_that_do_not_belong_to_one_account_get_no_rendered_handler(tenancy):
    contract = _contract(tenancy)
    files = extract_code_file_map_from_payload(_closed(contract=contract))
    assert materialize_module_account_handlers(files, app_build_plan=_plan(), data_contract=contract) == {}
    assert "user_data_scope" not in _closed(contract=contract)["module_contract"]["module_yaml"]["module"]


def test_plan_review_records_the_scope_and_assigns_the_handler_to_business_services():
    plan = {"capability_packs": [{
        "capability_pack_id": MODULE, "capability_source": "generated_module", "primary_entities": ["Task"],
        "user_data_scope": None,
    }]}
    context = ContextVariablesBridge({"data_contract": _contract()})
    assert _repair_user_data_scope(plan, context) == [f"{MODULE}: user_data_scope None -> true (owns per_user collections)"]
    assert plan["capability_packs"][0]["user_data_scope"] is True
    assert HANDLER in _required_module_paths(plan["capability_packs"][0], context)["business_services"]
    assert _repair_user_data_scope(plan, context) == []
    workspace = ContextVariablesBridge({"data_contract": _contract("per_workspace")})
    pack = {"capability_pack_id": MODULE, "capability_source": "generated_module", "primary_entities": ["Task"]}
    assert _repair_user_data_scope({"capability_packs": [pack]}, workspace) == []


@pytest.mark.asyncio
async def test_the_registry_injects_the_resources_each_handler_declares():
    seen = {}

    class ByDb:
        def __init__(self, db):
            seen["db"] = db

        async def delete_user_data(self, *, app_id, user_id):
            return {"deleted_count": 0}

        async def export_user_data(self, *, app_id, user_id):
            return {"by_db": []}

    class ByPersistence(ByDb):
        def __init__(self, persistence):
            seen["persistence"] = persistence

    registry = AccountDataRegistry()
    registry.register("by_db", ByDb)
    registry.register("by_persistence", ByPersistence)
    persistence = SimpleNamespace(app_id="app")
    result = await registry.delete_all(app_id="app", user_id="u", db="the-db", persistence=persistence)
    assert result == {"by_db": {"deleted_count": 0}, "by_persistence": {"deleted_count": 0}}
    assert seen == {"db": "the-db", "persistence": persistence}
    # A handler that needs persistence fails alone, never silently with None.
    missing = await registry.delete_all(app_id="app", user_id="u", db="the-db")
    assert missing["by_db"] == {"deleted_count": 0} and "runtime persistence" in missing["by_persistence"]["error"]


def test_the_write_materializer_keeps_normalization_inside_owned_paths():
    files = extract_code_file_map_from_payload(_closed())
    files[REPO] = LIVE_TASK_REPO
    changed = materialize_module_write_implementations(
        files, app_build_plan=_plan(), data_contract=_contract(), owned_paths=[SERVICE],
    )
    assert REPO not in changed and set(changed) == {SERVICE}
    assert deepcopy(files[REPO]) == LIVE_TASK_REPO
