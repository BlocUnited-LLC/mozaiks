"""Actual pack-app assembly: repair overlays, page deletion, provenance and workspace paths."""
import json
import logging

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.code_file_utils import admitted_app_file_map
from mozaiksai.core.workflow.context.adapter import create_context_container
from tests.factory_context import factory_context
from tests.test_app_ui_composition import component, compose, contribution, retitled
from tests.test_appgenerator_revision_baseline import _replace_archive
from tests.test_appgenerator_revision_baseline import baseline as revision_baseline  # noqa: F401
from tests.test_messaging_pack_persistence_composition import _assembled_files, _context

EMPTY_CONTRACT = {"version": "1", "surfaces": [], "shared_collections": []}
OLD_PAGE = "ui/pages/custom/old.jsx"


class Context(dict):
    def set(self, key, value):
        self[key] = value


def _files(result):
    return {file["filename"]: file["content"] for file in result.get("code_files") or []}


def _routes(files):
    # The auth contract's sign-in and callback routes carry no id; they are keyed by path.
    return {route.get("id") or route["path"]: route for route in json.loads(files["ui/route_manifest.json"])["pages"]}


def _save(saved, *parts):
    saved.files = {
        **{path: source for part in parts for path, source in part.items()}, **compose(*parts),
        "app.json": json.dumps({"appId": "customer-app", "appName": "Board"}),
    }
    _replace_archive(saved, {})


async def _assemble_revision(saved, revised, *, pages=("board",), **extra):
    selected = _context()
    plan = {**selected["app_build_plan"], "pages": [{"name": name, "route": f"/{name}", "ui_surface": "custom_react_page"} for name in pages],
            "build_tasks": [{"task_id": "pages", "task_type": "page_bundle", "owned_paths": list(revised)}]}
    context = create_context_container(initial={
        **saved.context, **selected, "app_build_plan": plan, "data_contract": EMPTY_CONTRACT,
        "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", revised)["files"]}}, **extra,
    })
    return await assemble_app_tasks(context_variables=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [False, True])
async def test_revision_page_deletion_removes_its_carried_registration_and_route(request, deleted):
    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, component("Old", path=OLD_PAGE, order=20), pack)
    # The task re-emits both roots without Old; the pack page tombstone is ignored.
    extra = {"deleted_files": [OLD_PAGE, "ui/pages/custom/Messages.jsx"]} if deleted else {}
    result = await _assemble_revision(saved, board, **extra)
    assert result["success"], result
    files = _files(result)
    routes = _routes(files)
    assert list(routes) == (["board", "messages"] if deleted else ["board", "old", "messages"])
    assert ("old.jsx" in files["ui/index.js"], OLD_PAGE in files) == (not deleted, not deleted)
    assert routes["messages"] == _routes(pack)["messages"]
    assert files["ui/index.js"].count('registerComponent("Messages"') == 1
    assert files["ui/pages/custom/Messages.jsx"] == pack["ui/pages/custom/Messages.jsx"]


@pytest.mark.asyncio
async def test_revision_task_still_registering_a_deleted_page_fails(request):
    saved = request.getfixturevalue("revision_baseline")
    board, old = component(path="ui/pages/custom/board.jsx"), component("Old", path=OLD_PAGE, order=20)
    _save(saved, board, old, _assembled_files())
    # The task still plans, writes and registers Old, but its file is tombstoned,
    # so the final bundle could not contain it.
    revised = {**board, OLD_PAGE: old[OLD_PAGE], **compose(board, old)}
    result = await _assemble_revision(saved, revised, pages=("board", "old"), deleted_files=[OLD_PAGE])
    assert not result["success"]
    assert "UI import must resolve to one existing file: './pages/custom/old.jsx'" in result["error"]


@pytest.mark.asyncio
async def test_revision_repair_overlay_retitles_a_route_the_task_left_unchanged(request):
    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    revised = {path: saved.files[path] for path in ("ui/index.js", "ui/route_manifest.json", "ui/pages/custom/board.jsx")}
    repair = {"ui/route_manifest.json": retitled(revised["ui/route_manifest.json"], "board", "Repaired board")}
    result = await _assemble_revision(saved, revised, code_files=contribution("repair", repair)["files"])
    assert result["success"], result
    files = _files(result)
    assert _routes(files) == _routes(repair)
    assert _routes(files)["messages"] == _routes(pack)["messages"]
    assert files["ui/index.js"].count('registerComponent("Messages"') == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["title", "pack_path_collision"])
async def test_genesis_repair_overlay_reaches_the_composed_roots(fault):
    task = component(path="ui/pages/custom/board.jsx")
    if fault == "pack_path_collision":
        manifest = json.loads(task["ui/route_manifest.json"])
        manifest["pages"][0]["path"] = "/messages"
        task["ui/route_manifest.json"] = json.dumps(manifest)
    repair = {"ui/route_manifest.json": retitled(component(path="ui/pages/custom/board.jsx")["ui/route_manifest.json"], "board", "Repaired board")}

    def context(code_files):
        return Context(factory_context({
            **_context(), "app_id": "genesis_repair", "data_contract": EMPTY_CONTRACT, "code_files": code_files,
            "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", task)["files"]}},
        }))

    unrepaired = await assemble_app_tasks(context_variables=context([]))
    assert unrepaired["success"] is (fault == "title"), unrepaired
    result = await assemble_app_tasks(context_variables=context(contribution("repair", repair)["files"]))
    assert result["success"], result
    routes = _routes(_files(result))
    assert routes["board"] == _routes(repair)["board"]
    assert routes["messages"] == _routes(_assembled_files())["messages"]


WIDGET = "ui/components/Widget.jsx"


def _genesis_context(task, **extra):
    return Context(factory_context({
        **_context(), "app_id": "genesis_repair", "data_contract": EMPTY_CONTRACT,
        "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", task)["files"]}}, **extra,
    }))


@pytest.mark.asyncio
@pytest.mark.parametrize("barrel", ["composed", "task_style"])
async def test_repair_deleting_a_task_written_component_reassembles(barrel, caplog):
    board = component(path="ui/pages/custom/board.jsx")
    task = {**board, WIDGET: "export default function Widget() { return null; }", "ui/index.js": (
        "import View from './pages/custom/board.jsx';\nimport Widget from './components/Widget.jsx';\n"
        "export function register(registerComponent) { registerComponent('Board', View, {}); registerComponent('Widget', Widget, {}); }"
    )}
    # The FrontendStubAgent repair deletes the forbidden file and rewrites the barrel without it;
    # the task's batch output still writes and registers the file.
    repaired = compose(board, _assembled_files())["ui/index.js"] if barrel == "composed" else board["ui/index.js"]
    context = _genesis_context(task, code_files=[{"filename": "ui/index.js", "content": repaired}], deleted_files=[WIDGET])
    caplog.set_level(logging.INFO, logger="mozaiksai.core.workflow.generator_support.ui_registry_composition")
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    assert any("'Widget'" in record.getMessage() and "task:pages" in record.getMessage() for record in caplog.records)
    for files in (_files(result), admitted_app_file_map(context)):
        assert WIDGET not in files and "Widget" not in files["ui/index.js"]
        assert files["ui/index.js"].count('registerComponent("Messages"') == 1
        assert list(_routes(files)) == ["board", "messages"]


@pytest.mark.asyncio
async def test_admitted_map_composes_repair_roots_onto_pack_composed_roots():
    task = component(path="ui/pages/custom/board.jsx")
    repair = {"ui/route_manifest.json": retitled(task["ui/route_manifest.json"], "board", "Repaired board")}
    context = _genesis_context(task, code_files=contribution("repair", repair)["files"])
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    admitted = admitted_app_file_map(context)
    # The partial repair manifest no longer replaces the composed root and its pack route.
    assert _routes(admitted) == _routes(_files(result))
    assert [(route["id"], route["meta"]["title"]) for route in _routes(admitted).values()] == [("board", "Repaired board"), ("messages", "Messages")]
    # A partial repair barrel saved after this assembly reaches the projection without reassembly.
    context["code_files"] = contribution("repair", {
        **repair, "ui/pages/custom/board_v2.jsx": "export default function Board() { return null; }",
        "ui/index.js": "import B from './pages/custom/board_v2.jsx';\nexport function register(registerComponent) { registerComponent('Board', B, {}); }",
    })["files"]
    admitted = admitted_app_file_map(context)
    assert "./pages/custom/board_v2.jsx" in admitted["ui/index.js"] and "./pages/custom/board.jsx" not in admitted["ui/index.js"]
    assert admitted["ui/index.js"].count('registerComponent("Messages"') == 1
    assert list(_routes(admitted)) == ["board", "messages"]


def test_admitted_map_keeps_last_writer_wins_without_pack_composition(caplog):
    task = component(path="ui/pages/custom/board.jsx")
    repair = {"ui/index.js": "import B from './pages/custom/board_v2.jsx';\nexport function register(registerComponent) { registerComponent('Board', B, {}); }"}
    # No pack provenance records a composed root: the repair copy replaces the root.
    assert admitted_app_file_map({"generated_files": task, "code_files": contribution("repair", repair)["files"]})["ui/index.js"] == repair["ui/index.js"]
    # A copy that cannot be composed replaces the composed root as before and is reported.
    composed = {**task, ".mozaiks/pack_provenance.json": json.dumps({"composed_outputs": {"ui/index.js": [{"pack_id": "messaging"}]}})}
    owner = "factory_app.workflows.AppGenerator.tools.code_file_utils"
    caplog.set_level(logging.WARNING, logger=owner)
    admitted = admitted_app_file_map({"generated_files": composed, "code_files": contribution("repair", repair)["files"]})
    assert admitted["ui/index.js"] == repair["ui/index.js"]
    assert [record.getMessage().split(":", 1)[0] for record in caplog.records if record.name == owner] == ["ADMITTED_UI_ROOT_NOT_COMPOSED"]


@pytest.mark.asyncio
async def test_repair_overlay_cannot_change_a_pack_route():
    task, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    repair = {"ui/route_manifest.json": retitled(compose(task, pack)["ui/route_manifest.json"], "messages", "Hijacked")}
    result = await assemble_app_tasks(context_variables=Context(factory_context({
        **_context(), "app_id": "pack_route_repair", "data_contract": EMPTY_CONTRACT,
        "code_files": contribution("repair", repair)["files"],
        "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", task)["files"]}},
    })))
    assert not result["success"]
    assert "Conflicting route id: messages" in result["error"]


@pytest.mark.asyncio
async def test_pack_ui_assembly_renders_provenance_from_the_pinned_build_timestamp():
    pinned = "2020-01-02T03:04:05+00:00"
    provenance = []
    for _ in range(2):
        result = await assemble_app_tasks(context_variables=Context(factory_context({
            **_context(), "app_id": "pinned_provenance", "build_timestamp": pinned, "data_contract": EMPTY_CONTRACT,
            "app_task_batch_results": {
                "shell": {"manifest": {"app_name": "Board", "default_route": "/board"}, "pages": []},
                "pages": {"code_files": contribution("task:pages", component(path="ui/pages/custom/board.jsx"))["files"]},
            },
        })))
        assert result["success"], result
        assert '"Messages"' in _files(result)["ui/index.js"]
        provenance.append(_files(result)["provenance.yaml"])
    assert provenance[0] == provenance[1]
    assert yaml.safe_load(provenance[0])["created_with"]["timestamp"] == pinned


@pytest.mark.asyncio
async def test_revision_plan_task_edit_of_workspace_pack_path_fails_assembly_explicitly(request):
    saved = request.getfixturevalue("revision_baseline")
    _save(saved, component(path="ui/pages/custom/board.jsx"), _assembled_files())
    handler = "modules/messages/backend/handler.py"
    selected = _context()
    plan = {**selected["app_build_plan"], "build_tasks": [{"task_id": "svc", "task_type": "business_services", "owned_paths": [handler]}]}
    context = create_context_container(initial={
        **saved.context, **selected, "app_build_plan": plan, "data_contract": EMPTY_CONTRACT,
        "app_task_batch_results": {"svc": {"code_files": [{"filename": handler, "content": saved.files[handler] + "\n# revision edit\n"}]}},
    })
    result = await assemble_app_tasks(context_variables=context)
    # The planned change is neither shipped nor silently dropped behind a success.
    assert not result["success"] and "WorkspacePackOutputChangeUnsupported" in result["error"]
    assert result["diagnostic"] == {
        "code": "WORKSPACE_PACK_OUTPUT_CHANGE_UNSUPPORTED", "path": handler, "task_id": "svc",
        "retained": "verified_revision_source",
        "missing_capability": "authorized_backend_refinement", "block_reason": "authorized_backend_refinement_required",
    }
    assert context.get("app_assembly_status") == "failed"
    assert context.get("app_assembly_diagnostic")["block_reason"] == "authorized_backend_refinement_required"
    assert "Studio selected-file patch refinement" in context.get("app_assembly_diagnostic")["error"]


def _revision_context(saved, revised, *, pages=("board",), **extra):
    selected = _context()
    plan = {**selected["app_build_plan"], "pages": [{"name": name, "route": f"/{name}", "ui_surface": "custom_react_page"} for name in pages],
            "build_tasks": [{"task_id": "pages", "task_type": "page_bundle", "owned_paths": list(revised)}]}
    return create_context_container(initial={
        **saved.context, **selected, "app_build_plan": plan, "data_contract": EMPTY_CONTRACT,
        "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", revised)["files"]}}, **extra,
    })


def _stale_baseline_copy(saved):
    return [{"filename": path, "content": saved.files[path]} for path in ("ui/index.js", "ui/route_manifest.json")]


@pytest.mark.asyncio
async def test_admitted_map_keeps_a_task_revision_change_over_a_stale_baseline_copy(request, caplog):
    from factory_app.workflows.AppGenerator.tools import code_file_utils

    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    revised = {**board, "ui/route_manifest.json": retitled(board["ui/route_manifest.json"], "board", "Approved revision")}
    context = _revision_context(saved, revised, code_files=_stale_baseline_copy(saved))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    assembled = _files(result)
    assert _routes(assembled)["board"]["meta"]["title"] == "Approved revision"
    # Validation, write-back and export see what assembly composed: same baseline, same rules.
    admitted = admitted_app_file_map(context)
    assert _routes(admitted) == _routes(assembled)
    assert admitted["ui/index.js"] == assembled["ui/index.js"]
    # Without the bound baseline the projection cannot apply those rules; it keeps
    # the assembled roots rather than letting the stale copy revert the task.
    code_file_utils._REVISION_UI_BASELINES.clear()
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools.code_file_utils")
    admitted = admitted_app_file_map(context)
    assert _routes(admitted) == _routes(assembled)
    assert any("ADMITTED_UI_ROOT_NOT_COMPOSED" in record.getMessage() and "revision baseline" in record.getMessage()
               for record in caplog.records)


@pytest.mark.asyncio
async def test_admitted_map_applies_a_revision_repair_saved_after_assembly(request, caplog):
    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    revised = {path: saved.files[path] for path in ("ui/index.js", "ui/route_manifest.json", "ui/pages/custom/board.jsx")}
    context = _revision_context(saved, revised)
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    # A repair admitted after assembly reaches validation and export through the
    # admitted map, which must compose it exactly as reassembly would.
    repair = {"ui/route_manifest.json": retitled(_files(result)["ui/route_manifest.json"], "board", "Repaired later")}
    context.set("code_files", contribution("repair", repair)["files"])
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools.code_file_utils")
    admitted = admitted_app_file_map(context)
    assert _routes(admitted)["board"]["meta"]["title"] == "Repaired later"
    assert _routes(admitted)["messages"] == _routes(pack)["messages"]
    assert admitted["ui/index.js"].count('registerComponent("Messages"') == 1
    assert not any("ADMITTED_UI_ROOT_NOT_COMPOSED" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,lost", [
    ("validation_request", "restarted"), ("validation_request", "evicted"),
    ("acceptance_gate", "restarted"), ("build_validation", "restarted"),
])
async def test_validation_composes_a_revision_repair_without_the_assembling_process_memory(
    request, monkeypatch, caplog, entry, lost,
):
    from unittest.mock import AsyncMock

    from factory_app.workflows.AppGenerator.tools import app_validation, code_file_utils
    from mozaiksai.core.workflow.context.frozen import detach

    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    revised = {path: saved.files[path] for path in ("ui/index.js", "ui/route_manifest.json", "ui/pages/custom/board.jsx")}
    context = _revision_context(saved, revised)
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    # A bounded repair of the page task saves a shared UI root after that assembly.
    repair = {"ui/route_manifest.json": retitled(_files(result)["ui/route_manifest.json"], "board", "Repaired later")}
    context.set("code_files", contribution("repair", repair)["files"])
    context.set("bundle_repair_result", {"history": [], "active": {
        "task_id": "pages", "status": "responded", "saved_paths": ["ui/route_manifest.json"],
    }})
    # Its validation runs where assembly's baseline is not in memory: a restarted
    # host, a replayed turn, another worker, or 16 other revision assemblies since.
    if lost == "restarted":
        code_file_utils._REVISION_UI_BASELINES.clear()
    else:
        for index in range(16):
            code_file_utils.remember_revision_baseline({
                "user_id": f"other-{index}", "artifact_version_id": "other-source",
                "run_build_binding": {"target_app_id": "other-app", "build_registry_id": "other-registry"},
            }, {})
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools.code_file_utils")
    if entry == "validation_request":
        passed = {"passed": True}
        acceptance = AsyncMock(return_value={
            **{key: passed for key in (
                "bundle_scan", "agent_backend", "module_wiring", "module_implementation", "module_runtime_quality",
                "functional_completeness", "workflow_integration", "app_runtime_load", "app_runtime_load_worker",
                "app_runtime_smoke",
            )},
            "status": "passed", "passed": True, "skipped_checks": [],
            "bundle_repair": {"status": "passed", "target_agent": None},
        })
        monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", acceptance)
        monkeypatch.setattr(app_validation, "validate_app_build", AsyncMock(return_value={
            "validation_status": "passed", "validation_strategy": "skip", "success": True, "errors": [],
        }))
        await app_validation.validate_app_bundle_from_request({}, context_variables=context)
        # The gates check, and validation writes back, the repaired root composed onto the pack's.
        checked = [acceptance.await_args.kwargs["files"], detach(context.get("generated_files"))]
    elif entry == "acceptance_gate":
        await app_validation.run_app_bundle_acceptance_gate(context_variables=context)
        checked = [detach(context.get("generated_files"))]
    else:
        files, _, _ = await app_validation._resolve_files(files=None, context_variables=context, wf_logger=None)
        checked = [files]
    for files in checked:
        assert _routes(files)["board"]["meta"]["title"] == "Repaired later"
        assert _routes(files)["messages"] == _routes(pack)["messages"]
        assert files["ui/index.js"].count('registerComponent("Messages"') == 1
    assert not any("ADMITTED_UI_ROOT_NOT_COMPOSED" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["retitle_pack_route", "reregister_pack_component"])
async def test_admitted_map_never_patches_a_verified_pack_declaration(repair, caplog):
    task, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    context = _genesis_context(task)
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    composed = _files(result)
    if repair == "retitle_pack_route":
        overlay = {"ui/route_manifest.json": retitled(composed["ui/route_manifest.json"], "messages", "Hijacked")}
    else:
        overlay = {
            "ui/pages/custom/Evil.jsx": "export default function Evil() { return null; }",
            "ui/index.js": "import E from './pages/custom/Evil.jsx';\nexport function register(registerComponent) { registerComponent('Messages', E, {}); }",
        }
    context["code_files"] = contribution("repair", overlay)["files"]
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools.code_file_utils")
    admitted = admitted_app_file_map(context)
    assert _routes(admitted)["messages"] == _routes(pack)["messages"]
    assert "Evil.jsx" not in admitted["ui/index.js"]
    assert admitted["ui/index.js"].count('registerComponent("Messages"') == 1
    assert any(record.getMessage().startswith("ADMITTED_UI_ROOT_NOT_COMPOSED") for record in caplog.records)
    # Assembly rejects the same overlay.
    reassembled = await assemble_app_tasks(context_variables=context)
    assert not reassembled["success"] and "messages" in reassembled["error"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["repair_of_the_task", "none", "repair_of_another_task", "repair_without_the_root"])
async def test_revision_repair_restoring_the_baseline_supersedes_the_task_it_repairs(request, evidence):
    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    revised = {**board, "ui/route_manifest.json": retitled(board["ui/route_manifest.json"], "board", "Faulty title")}
    records = {
        "repair_of_the_task": {"task_id": "pages", "status": "responded", "saved_paths": ["ui/route_manifest.json"]},
        "repair_of_another_task": {"task_id": "shell", "status": "responded", "saved_paths": ["ui/route_manifest.json"]},
        "repair_without_the_root": {"task_id": "pages", "status": "responded", "saved_paths": ["ui/pages/custom/board.jsx"]},
    }
    # The repair restored the verified manifest. Only a recorded repair of this
    # task that saved the root is newer than the task's output; a copy without
    # that evidence (an earlier full-root save) cannot revert the task's change.
    extra = {"bundle_repair_result": {"active": records[evidence], "history": []}} if evidence in records else {}
    context = _revision_context(saved, revised, code_files=_stale_baseline_copy(saved)[1:], **extra)
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    expected = "Board" if evidence == "repair_of_the_task" else "Faulty title"
    assert _routes(_files(result))["board"]["meta"]["title"] == expected
    assert _routes(admitted_app_file_map(context))["board"]["meta"]["title"] == expected


BOARD_BUNDLE = {
    "route_manifest": [{
        "id": "board", "label": "Board", "path": "/board", "component": "Board", "requiresAuth": True,
        "order": 10, "purpose": "Board", "meta": {"title": "Board"},
    }],
    "page_files": [{
        "path": "ui/pages/custom/board.jsx", "route_id": "board", "component_name": "Board",
        "registry_key": "Board", "purpose": "Board", "contract_refs": [],
        "content": "export default function Board() { return null; }",
    }],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", ["lists_the_pack_surface", "app_only"])
async def test_revision_app_schema_repair_restoring_the_baseline_supersedes_the_task_it_repairs(
    request, tmp_path, monkeypatch, approved,
):
    from factory_app.workflows.AppGenerator.tools.code_file_utils import load_revision_baseline
    from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
    from mozaiksai.core.workflow.generator_support.code_files import _custom_route_bundle_code_files

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    saved = request.getfixturevalue("revision_baseline")
    board = {item["filename"]: item["content"] for item in _custom_route_bundle_code_files(BOARD_BUNDLE)}
    pack = _assembled_files()
    saved.files = {**board, **pack, **compose(board, pack), "app.json": json.dumps({
        "appId": "customer-app", "appName": "Board", "startup": {"landing_spot": "/board"},
        "targets": {"web": True, "mobile": False}, "authRequired": False, "admins": [],
    }, indent=2)}
    _replace_archive(saved, {})
    revised = {**board, "ui/route_manifest.json": retitled(board["ui/route_manifest.json"], "board", "Faulty title")}
    task = {"task_id": "pages", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
            "owned_paths": sorted(revised), "depends_on": []}
    selected = _context()
    context = create_context_container(initial={
        **saved.context, **selected, "app_task_batch_items": [task],
        "app_build_plan": {**selected["app_build_plan"], "build_tasks": [task], "pages": [
            {"name": "board", "route": "/board", "ui_surface": "custom_react_page"},
        ]},
        # Whether or not the approved contract names the pack surface, the schema
        # save keeps the bundle's compiled data/contract.json.
        "data_contract": json.loads(pack["data/contract.json"]) if approved == "lists_the_pack_surface" else EMPTY_CONTRACT,
        "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", revised)["files"]}},
    })
    first = await assemble_app_tasks(context_variables=context)
    assert first["success"], first
    assert _routes(_files(first))["board"]["meta"]["title"] == "Faulty title"
    # Validation selects a bounded repair of the page task; AppSchemaAgent's auto
    # tool saves the restored baseline route, then the UI quality gate passes.
    context.set("bundle_repair_target", "AppSchemaAgent")
    context.set("bundle_repair_result", {"active": {
        "task_id": "pages", "target_agent": "AppSchemaAgent", "request_id": "pages-repair-1",
        "allowed_paths": sorted(revised), "status": "selected",
    }, "history": []})
    message = save_app_schema(
        manifest={"app_name": "Board", "default_route": "/board", "pages": [], "custom_routes": ["board"]},
        pages=[], custom_route_bundle=BOARD_BUNDLE, context_variables=context,
    )
    assert "rejected" not in message, message
    assert context.get("bundle_repair_result")["active"]["status"] == "responded"
    assert context.get("generated_files")["data/contract.json"] == _files(first)["data/contract.json"]
    # Validation follows the UI quality gate without reassembly. The save also
    # wrote its uncomposed roots into generated_files; the projection composes them.
    before = admitted_app_file_map(context, revision_baseline=await load_revision_baseline(context))
    assert _routes(before)["board"] == _routes(board)["board"]
    assert _routes(before)["messages"] == _routes(pack)["messages"]
    assert before["ui/index.js"].count('registerComponent("Messages"') == 1
    context.set("app_ui_quality_status", "passed")
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = _files(result)
    assert _routes(files)["board"] == _routes(board)["board"]
    assert _routes(files)["messages"] == _routes(pack)["messages"]
    assert files["ui/index.js"].count('registerComponent("Messages"') == 1
    assert _routes(admitted_app_file_map(context)) == _routes(files) == _routes(before)
    assert {"ui/index.js", "ui/route_manifest.json"} <= set(context.get("bundle_repair_result")["active"]["saved_paths"])


AUTH_ROUTE_PATHS = ["/login", "/auth/callback"]
_CODE_FILE_UTILS = "factory_app.workflows.AppGenerator.tools.code_file_utils"


def _auth_paths(files):
    return [route["path"] for route in json.loads(files["ui/route_manifest.json"])["pages"] if "id" not in route]


def _not_composed(caplog):
    return [record for record in caplog.records
            if record.levelno >= logging.WARNING and "ADMITTED_UI_ROOT_NOT_COMPOSED" in record.getMessage()]


def _validation_mocks(monkeypatch, passed=True):
    from unittest.mock import AsyncMock

    from factory_app.workflows.AppGenerator.tools import app_validation

    result = {"passed": passed}
    acceptance = AsyncMock(return_value={
        **{key: result for key in (
            "bundle_scan", "agent_backend", "module_wiring", "module_implementation", "module_runtime_quality",
            "functional_completeness", "workflow_integration", "app_runtime_load", "app_runtime_load_worker",
            "app_runtime_smoke",
        )},
        "status": "passed" if passed else "failed", "passed": passed, "skipped_checks": [],
        "bundle_repair": {"status": "passed" if passed else "failed", "target_agent": None},
    })
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", acceptance)
    monkeypatch.setattr(app_validation, "validate_app_build", AsyncMock(return_value={
        "validation_status": "passed", "validation_strategy": "skip", "success": True, "errors": [],
    }))
    return acceptance


def _with_auth_scaffold(files, data_contract=None):
    """The bundle as assembly writes an auth-required app: its manifest ends with the auth contract's routes."""
    from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import (
        materialize_auth_scaffold,
    )

    return {**files, **materialize_auth_scaffold(files, data_contract=data_contract)}


def _responded_repair(context, *paths):
    context.set("bundle_repair_result", {"history": [], "active": {
        "task_id": "pages", "status": "responded", "saved_paths": list(paths),
    }})


def _saved_roots(saved):
    return {path: saved.files[path] for path in ("ui/index.js", "ui/route_manifest.json", "ui/pages/custom/board.jsx")}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["revision", "genesis"])
async def test_auth_required_pack_app_reassembles_and_composes_a_repair_with_its_auth_routes(request, caplog, mode):
    from factory_app.workflows.AppGenerator.tools import code_file_utils

    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    if mode == "revision":
        saved = request.getfixturevalue("revision_baseline")
        saved.files = _with_auth_scaffold({**board, **pack, **compose(board, pack), "app.json": json.dumps({
            "appId": "customer-app", "appName": "Board", "authRequired": True, "startup": {"landing_spot": "/board"},
        })})
        assert _auth_paths(saved.files) == AUTH_ROUTE_PATHS
        _replace_archive(saved, {})
        # The page task restates the saved roots, auth routes included.
        context = _revision_context(saved, _saved_roots(saved))
    else:
        context = _genesis_context(board, app_task_batch_results={
            "pages": {"code_files": contribution("task:pages", board)["files"]},
            "shell": {"manifest": {"app_name": "Board", "default_route": "/board", "auth_strategy": "basic-login"}, "pages": []},
        })
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    assembled = _files(result)
    assert _auth_paths(assembled) == AUTH_ROUTE_PATHS
    # Reassembly carries a manifest that already holds the auth routes.
    again = await assemble_app_tasks(context_variables=context)
    assert again["success"], again
    assert _files(again)["ui/route_manifest.json"] == assembled["ui/route_manifest.json"]
    # A bounded repair restates the whole assembled manifest and retitles the board.
    repair = {"ui/route_manifest.json": retitled(assembled["ui/route_manifest.json"], "board", "Repaired later")}
    context.set("code_files", contribution("repair", repair)["files"])
    _responded_repair(context, "ui/route_manifest.json")
    code_file_utils._REVISION_UI_BASELINES.clear()
    caplog.set_level(logging.INFO, logger=_CODE_FILE_UTILS)
    admitted = admitted_app_file_map(context, revision_baseline=await code_file_utils.load_revision_baseline(context))
    assert _routes(admitted)["board"]["meta"]["title"] == "Repaired later"
    assert _routes(admitted)["messages"] == _routes(pack)["messages"]
    assert admitted["ui/index.js"].count('registerComponent("Messages"') == 1
    assert _auth_paths(admitted) == AUTH_ROUTE_PATHS
    assert not _not_composed(caplog)
    reassembled = await assemble_app_tasks(context_variables=context)
    assert reassembled["success"], reassembled
    assert _files(reassembled)["ui/route_manifest.json"] == admitted["ui/route_manifest.json"]


@pytest.mark.asyncio
async def test_validation_composes_the_auth_routes_a_repair_turns_on_in_a_pack_revision(request, monkeypatch):
    from factory_app.workflows.AppGenerator.tools import app_validation, code_file_utils

    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    context = _revision_context(saved, _saved_roots(saved))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    assert "config/auth.yaml" not in _files(result) and _auth_paths(_files(result)) == []
    # An admitted app.json repair turns sign-in on; validation scaffolds the auth contract.
    manifest = {**json.loads(_files(result)["app.json"]), "authRequired": True, "startup": {"landing_spot": "/board"}}
    context.set("code_files", [{"filename": "app.json", "content": json.dumps(manifest)}])
    _responded_repair(context, "app.json")
    code_file_utils._REVISION_UI_BASELINES.clear()
    acceptance = _validation_mocks(monkeypatch)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    checked = acceptance.await_args.kwargs["files"]
    assert "config/auth.yaml" in checked
    assert _auth_paths(checked) == AUTH_ROUTE_PATHS
    assert _routes(checked)["board"] == _routes(board)["board"]
    assert _routes(checked)["messages"] == _routes(pack)["messages"]
    assert checked["ui/index.js"].count('registerComponent("Messages"') == 1


@pytest.mark.asyncio
async def test_a_transient_baseline_read_failure_never_replaces_the_admitted_repair_copy(request, monkeypatch):
    from factory_app.workflows.AppGenerator.tools import app_validation, code_file_utils
    from mozaiksai.core.workflow.context.frozen import detach

    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    context = _revision_context(saved, _saved_roots(saved))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    # The repair retitles the board and turns sign-in on, so validation scaffolds auth.
    manifest = {**json.loads(_files(result)["app.json"]), "authRequired": True, "startup": {"landing_spot": "/board"}}
    repair = {
        "app.json": json.dumps(manifest),
        "ui/route_manifest.json": retitled(_files(result)["ui/route_manifest.json"], "board", "Repaired later"),
    }
    context.set("code_files", contribution("repair", repair)["files"])
    _responded_repair(context, "app.json", "ui/route_manifest.json")
    # The first validation round cannot read the bound source.
    code_file_utils._REVISION_UI_BASELINES.clear()
    readable = saved.store.get_build_record.return_value
    saved.store.get_build_record.side_effect = OSError("transient store outage")
    _validation_mocks(monkeypatch, passed=False)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    overlay = {item["filename"]: item["content"] for item in detach(context.get("code_files"))}
    assert overlay["ui/route_manifest.json"] == repair["ui/route_manifest.json"]
    assert "config/auth.yaml" in overlay
    # The source is readable again in the next round, in another process.
    saved.store.get_build_record.side_effect = None
    saved.store.get_build_record.return_value = readable
    code_file_utils._REVISION_UI_BASELINES.clear()
    acceptance = _validation_mocks(monkeypatch)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    checked = acceptance.await_args.kwargs["files"]
    assert _routes(checked)["board"]["meta"]["title"] == "Repaired later"
    assert _routes(checked)["messages"] == _routes(pack)["messages"]
    assert _auth_paths(checked) == AUTH_ROUTE_PATHS


@pytest.mark.asyncio
async def test_a_projection_without_the_baseline_reports_only_a_dropped_repair(request, monkeypatch, caplog):
    from factory_app.workflows.AppGenerator.tools import app_validation, code_file_utils
    from mozaiksai.core.workflow.context.frozen import detach

    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    context = _revision_context(saved, _saved_roots(saved))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    repair = {"ui/route_manifest.json": retitled(_files(result)["ui/route_manifest.json"], "board", "Repaired later")}
    context.set("code_files", contribution("repair", repair)["files"])
    _responded_repair(context, "ui/route_manifest.json")
    caplog.set_level(logging.INFO, logger=_CODE_FILE_UTILS)
    # Before validation writes back, a projection in a process without the
    # baseline (save_generated_code, assembly's auth scopes) drops the repair: loud.
    code_file_utils._REVISION_UI_BASELINES.clear()
    assert _routes(admitted_app_file_map(context))["board"]["meta"]["title"] == "Board"
    assert len(_not_composed(caplog)) == 1
    _validation_mocks(monkeypatch)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    validated = detach(context.get("generated_files"))
    assert _routes(validated)["board"]["meta"]["title"] == "Repaired later"
    # After it, the kept roots are the validated composition: nothing is dropped, nothing to report.
    code_file_utils._REVISION_UI_BASELINES.clear()
    caplog.clear()
    assert admitted_app_file_map(context) == validated
    assert not _not_composed(caplog)
    assert any(record.getMessage().startswith("ADMITTED_UI_ROOT_KEPT") for record in caplog.records)


class _ExportProjected(Exception):
    pass


async def _export_projection(monkeypatch, context):
    """The admitted map generate_and_download bundles, captured at its call site."""
    from factory_app.workflows.AppGenerator.tools import code_file_utils, generate_and_download

    projected = []

    def capture(context_variables, **kwargs):
        projected.append(code_file_utils.admitted_app_file_map(context_variables, **kwargs))
        raise _ExportProjected

    monkeypatch.setattr(generate_and_download, "admitted_app_file_map", capture)
    with pytest.raises(_ExportProjected):
        await generate_and_download.generate_and_download({}, "Ready", context_variables=context)
    return projected[0]


@pytest.mark.asyncio
async def test_export_composes_a_revision_repair_from_the_verified_source(request, monkeypatch, caplog):
    from factory_app.workflows.AppGenerator.tools import code_file_utils

    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    context = _revision_context(saved, _saved_roots(saved), chat_id="revision-chat")
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    repair = {"ui/route_manifest.json": retitled(_files(result)["ui/route_manifest.json"], "board", "Repaired later")}
    context.set("code_files", contribution("repair", repair)["files"])
    _responded_repair(context, "ui/route_manifest.json")
    # Export runs in a process that does not hold the bound source.
    code_file_utils._REVISION_UI_BASELINES.clear()
    caplog.set_level(logging.WARNING, logger=_CODE_FILE_UTILS)
    exported = await _export_projection(monkeypatch, context)
    assert _routes(exported)["board"]["meta"]["title"] == "Repaired later"
    assert _routes(exported)["messages"] == _routes(pack)["messages"]
    assert not _not_composed(caplog)


@pytest.mark.asyncio
async def test_auth_required_messaging_revision_repair_reaches_validation_and_export(
    request, tmp_path, monkeypatch, caplog,
):
    """A page repair of an auth-required Messaging revision, from diagnostic to export, without model calls."""
    from copy import deepcopy

    from factory_app.workflows.AppGenerator.tools import app_validation, code_file_utils
    from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )
    from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
    from mozaiksai.core.workflow.context.frozen import detach
    from mozaiksai.core.workflow.generator_support.code_files import _custom_route_bundle_code_files
    from tests.test_messaging_pack_persistence_composition import CARRIED_APP_CONTRACT

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    # DesignDocs approved per-user projects, so the app requires sign-in, and its
    # contract does not list the Messaging surface: assembly composes that in.
    approved = deepcopy(CARRIED_APP_CONTRACT)
    selected = _context()
    templates = resolve_managed_capability_templates(selected["capability_packs"], context_variables=selected)
    pack = {file["filename"]: file["content"] for file in _merge_code_files(
        [{"code_files": templates}], app_build_plan=selected["app_build_plan"], data_contract=approved,
        context_variables={**selected, "data_contract": approved},
    )}
    board = {item["filename"]: item["content"] for item in _custom_route_bundle_code_files(BOARD_BUNDLE)}
    app_manifest = {
        "appId": "customer-app", "appName": "Board", "startup": {"landing_spot": "/board"},
        "targets": {"web": True, "mobile": False}, "authRequired": True, "admins": [],
    }
    # The revision source is the bundle as an earlier assembly wrote it, auth routes included.
    saved = request.getfixturevalue("revision_baseline")
    saved.files = _with_auth_scaffold(
        {**board, **pack, **compose(board, pack), "app.json": json.dumps(app_manifest, indent=2)}, approved,
    )
    _replace_archive(saved, {})
    revised = {**board, "ui/route_manifest.json": retitled(board["ui/route_manifest.json"], "board", "Faulty title")}
    task = {"task_id": "pages", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
            "owned_paths": sorted(revised), "depends_on": []}
    context = create_context_container(initial={
        **saved.context, **selected, "chat_id": "revision-chat", "app_task_batch_items": [task],
        "app_build_plan": {**selected["app_build_plan"], "build_tasks": [task], "pages": [
            {"name": "board", "route": "/board", "ui_surface": "custom_react_page"},
        ]},
        "data_contract": approved,
        "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", revised)["files"]}},
    })

    assembled = await assemble_app_tasks(context_variables=context)
    assert assembled["success"], assembled
    files = _files(assembled)
    assert _routes(files)["board"]["meta"]["title"] == "Faulty title"
    assert _auth_paths(files) == AUTH_ROUTE_PATHS
    surfaces = [surface["surface_id"] for surface in json.loads(files["data/contract.json"])["surfaces"]]
    assert surfaces == ["projects", "messages"]

    # A path-owned diagnostic routes a bounded repair to the page task's AppSchemaAgent.
    repair = prepare_bundle_repair({"passed": False, "errors": [
        "ui/route_manifest.json: route 'board' must keep the approved page title",
    ]}, context)
    assert (repair["target_agent"], repair["active"]["allowed_paths"]) == ("AppSchemaAgent", sorted(revised))
    repaired = deepcopy(BOARD_BUNDLE)
    repaired["route_manifest"][0]["meta"]["title"] = "Repaired board"
    message = save_app_schema(
        manifest={"app_name": "Board", "default_route": "/board", "pages": [], "custom_routes": ["board"]},
        pages=[], custom_route_bundle=repaired, context_variables=context,
    )
    assert "rejected" not in message, message
    assert context.get("bundle_repair_result")["active"]["status"] == "responded"
    context.set("app_ui_quality_status", "passed")

    # Validation follows without reassembly, in a process that never held the bound source.
    code_file_utils._REVISION_UI_BASELINES.clear()
    caplog.set_level(logging.INFO, logger=_CODE_FILE_UTILS)
    acceptance = _validation_mocks(monkeypatch)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    validated = acceptance.await_args.kwargs["files"]
    assert validated == detach(context.get("generated_files"))
    assert _routes(validated)["board"]["meta"]["title"] == "Repaired board"
    assert _routes(validated)["messages"] == _routes(pack)["messages"]
    assert validated["ui/index.js"].count('registerComponent("Messages"') == 1
    assert _auth_paths(validated) == AUTH_ROUTE_PATHS
    assert [surface["surface_id"] for surface in json.loads(validated["data/contract.json"])["surfaces"]] == surfaces
    assert not _not_composed(caplog)

    # Export, in yet another process, bundles exactly what validation checked.
    code_file_utils._REVISION_UI_BASELINES.clear()
    assert await _export_projection(monkeypatch, context) == validated
    assert not _not_composed(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("subscription", [False, True])
async def test_genesis_app_schema_save_keeps_the_assembled_data_contributions_in_the_admitted_bundle(
    tmp_path, monkeypatch, subscription,
):
    from copy import deepcopy

    from factory_app.workflows.AppGenerator.tools import app_validation
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_planned_data_fields,
    )
    from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
    from tests.test_messaging_pack_persistence_composition import (
        CARRIED_APP_CONTRACT,
        _assignment_store_subscription,
    )

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    approved = deepcopy(CARRIED_APP_CONTRACT)
    selected = _context()
    context = Context(factory_context({
        **selected, "app_id": "genesis_schema", "chat_id": "genesis-chat", "data_contract": approved,
        "app_build_plan": {**selected["app_build_plan"], "pages": [
            {"name": "board", "route": "/board", "ui_surface": "custom_react_page"},
        ]},
        **({"subscription_contract": _assignment_store_subscription()} if subscription else {}),
    }))
    # AppSchemaAgent's main-context save, before assembly, puts data/contract.json in the overlay.
    message = save_app_schema(
        manifest={"app_name": "Board", "default_route": "/board", "pages": [], "custom_routes": ["board"]},
        pages=[], custom_route_bundle=deepcopy(BOARD_BUNDLE), context_variables=context,
    )
    assert "rejected" not in message, message
    assert "data/contract.json" in {item["filename"] for item in context["code_files"]}
    context["app_ui_quality_status"] = "passed"
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    assembled = _files(result)
    surfaces = ["projects", "messages", *(["subscription_assignments"] if subscription else [])]
    assert [surface["surface_id"] for surface in json.loads(assembled["data/contract.json"])["surfaces"]] == surfaces
    # Validation and export read the admitted projection: the overlay copy keeps the pack's collections.
    admitted = admitted_app_file_map(context)
    assert admitted["data/contract.json"] == assembled["data/contract.json"]
    assert _scan_planned_data_fields(
        admitted, approved, subscription_contract=context.get("subscription_contract"), context_variables=context,
    ) == []
    acceptance = _validation_mocks(monkeypatch)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    assert acceptance.await_args.kwargs["files"]["data/contract.json"] == assembled["data/contract.json"]
    assert await _export_projection(monkeypatch, context) == acceptance.await_args.kwargs["files"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["conflicting_contribution", "unverifiable_pack"])
async def test_genesis_app_schema_save_leaves_pack_data_contract_failures_to_assembly(
    tmp_path, monkeypatch, caplog, failure,
):
    from copy import deepcopy

    from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema
    from tests.test_messaging_pack_persistence_composition import CARRIED_APP_CONTRACT

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    approved = deepcopy(CARRIED_APP_CONTRACT)
    selected = _context()
    if failure == "conflicting_contribution":
        # The approved contract declares its own surface under the pack's surface id.
        approved["surfaces"].append({"surface_id": "messages", "surface_kind": "module", "collections": [{
            **deepcopy(CARRIED_APP_CONTRACT["surfaces"][0]["collections"][0]), "name": "inbox", "entity": "Inbox",
        }]})
        expected = "ValueError: Conflicting pack data contribution for 'messages'"
    else:
        digest = selected["capability_packs"][0]["digest"]
        selected["capability_packs"][0]["digest"] = digest[:-1] + ("0" if digest[-1] != "0" else "1")
        expected = "PackIntegrityError: Pack 'messaging' selected digest does not match freshly verified source"
    context = Context(factory_context({
        **selected, "app_id": "genesis_schema", "chat_id": "genesis-chat", "data_contract": approved,
        "app_build_plan": {**selected["app_build_plan"], "pages": [
            {"name": "board", "route": "/board", "ui_surface": "custom_react_page"},
        ]},
    }))
    caplog.set_level(logging.WARNING, logger="tools.save_app_schema")
    # AppSchemaAgent cannot repair the packs or the approved contract: its save writes the approved
    # contract unchanged, and assembly reports the failure itself.
    message = save_app_schema(
        manifest={"app_name": "Board", "default_route": "/board", "pages": [], "custom_routes": ["board"]},
        pages=[], custom_route_bundle=deepcopy(BOARD_BUNDLE), context_variables=context,
    )
    assert "rejected" not in message, message
    overlay = {item["filename"]: item["content"] for item in context["code_files"]}
    assert json.loads(overlay["data/contract.json"]) == approved
    assert any("APP_SCHEMA_PACK_DATA_CONTRACT_DEFERRED" in record.getMessage() for record in caplog.records)
    context["app_ui_quality_status"] = "passed"
    result = await assemble_app_tasks(context_variables=context)
    assert not result["success"]
    assert result["error"] == expected, result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
async def test_a_malformed_repair_route_is_a_rejected_copy_never_a_crash(request, caplog, bound):
    from factory_app.workflows.AppGenerator.tools import code_file_utils

    saved = request.getfixturevalue("revision_baseline")
    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    _save(saved, board, pack)
    context = _revision_context(saved, _saved_roots(saved))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    assembled = _files(result)
    manifest = json.loads(assembled["ui/route_manifest.json"])
    for route in manifest["pages"]:
        if route.get("id") == "board":
            route["navigation"] = "top"
    context.set("code_files", contribution("repair", {"ui/route_manifest.json": json.dumps(manifest)})["files"])
    _responded_repair(context, "ui/route_manifest.json")
    code_file_utils._REVISION_UI_BASELINES.clear()
    caplog.set_level(logging.INFO, logger=_CODE_FILE_UTILS)
    baseline = await code_file_utils.load_revision_baseline(context) if bound else None
    assert (baseline is not None) is bound
    # Sync callers (save_generated_code, the assembly auth-scope read) handle only ValueError and TypeError.
    admitted = admitted_app_file_map(context, revision_baseline=baseline)
    assert admitted["ui/route_manifest.json"] == assembled["ui/route_manifest.json"]
    warnings = _not_composed(caplog)
    assert len(warnings) == 1
    if bound:
        assert "route 'board' navigation must be an object or null" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_an_auth_route_binding_error_raised_by_reassembly_routes_to_the_route_manifest_owner(monkeypatch, tmp_path):
    """Reassembly composes the auth routes too; its binding error names ui/route_manifest.json, as the gate's does.

    The repair loop dispatches it to that file's owner, AppSchemaAgent, whose
    own save of a manifest without the broken binding makes reassembly and
    validation pass, with the auth contract's routes and the pack's route kept.
    """
    from factory_app.workflows.AppGenerator.tools import app_validation
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_app_contracts
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
    from factory_app.workflows.AppGenerator.tools.save_app_schema import save_app_schema

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path))
    board = component(path="ui/pages/custom/board.jsx")
    pages = {"task_id": "pages", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
             "owned_paths": ["ui/index.js", "ui/route_manifest.json", "ui/pages/custom/board.jsx"], "depends_on": []}
    selected = _context()
    context = _genesis_context(board, app_task_batch_results={
        "pages": {"code_files": contribution("task:pages", board)["files"]},
        "shell": {"manifest": {"app_name": "Board", "default_route": "/board", "auth_strategy": "basic-login"}, "pages": []},
    }, app_task_batch_items=[pages], app_build_plan={**selected["app_build_plan"], "build_tasks": [pages], "pages": [
        {"name": "board", "route": "/board", "ui_surface": "custom_react_page"},
    ]})
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    # A repair that also declares the sign-in route behind sign-in, then the reassembly validation runs.
    manifest = json.loads(retitled(board["ui/route_manifest.json"], "board", "Repaired"))
    manifest["pages"].append({
        "id": "login", "path": "/login", "component": "LoginPage", "meta": {"requiresAuth": True, "title": "Sign in"},
    })
    context.set("code_files", contribution("repair", {"ui/route_manifest.json": json.dumps(manifest)})["files"])
    _responded_repair(context, "ui/route_manifest.json")
    again = await assemble_app_tasks(context_variables=context)
    binding_error = "ui/route_manifest.json: Auth routes.login must declare meta.requiresAuth=false"
    assert not again["success"] and again["error"] == f"AppAuthContractError: {binding_error}", again
    diagnostic = context.get("app_assembly_diagnostic")
    assert diagnostic == {"code": "APP_ASSEMBLY_FAILED", "error": binding_error}
    # The diagnostic names the route manifest, so the repair loop dispatches it to that file's owner.
    repair = prepare_bundle_repair({"passed": False, "diagnostics": [diagnostic]}, context)
    assert (repair["status"], repair["target_agent"]) == ("needs_revision", "AppSchemaAgent"), repair
    assert "ui/route_manifest.json" in repair["active"]["allowed_paths"]
    assert context.get("bundle_repair_result")["active"]["status"] == "selected"
    # AppSchemaAgent's save restates the board route without the broken sign-in binding.
    fixed = {**BOARD_BUNDLE, "route_manifest": [{**BOARD_BUNDLE["route_manifest"][0], "meta": {"title": "Repaired"}}]}
    message = save_app_schema(
        manifest={"app_name": "Board", "default_route": "/board", "auth_strategy": "basic-login", "pages": [],
                  "custom_routes": ["board"]},
        pages=[], custom_route_bundle=fixed, context_variables=context,
    )
    assert "rejected" not in message, message
    assert context.get("bundle_repair_result")["active"]["status"] == "responded"
    context.set("app_ui_quality_status", "passed")
    repaired = await assemble_app_tasks(context_variables=context)
    assert repaired["success"], repaired
    files = _files(repaired)
    assert scan_app_contracts(files) == []
    routes = _routes(files)
    assert routes["board"]["meta"]["title"] == "Repaired"
    assert routes["messages"] == _routes(_assembled_files())["messages"]
    assert _auth_paths(files) == AUTH_ROUTE_PATHS
    assert all(route["meta"]["requiresAuth"] is False for path, route in routes.items() if path in AUTH_ROUTE_PATHS)
    # Validation checks exactly that admitted bundle and passes.
    acceptance = _validation_mocks(monkeypatch)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    checked = acceptance.await_args.kwargs["files"]
    assert checked == admitted_app_file_map(context) and checked["ui/route_manifest.json"] == files["ui/route_manifest.json"]
    assert scan_app_contracts(checked) == []


@pytest.mark.asyncio
async def test_a_repair_breaking_an_auth_route_binding_reaches_the_gate_with_that_error(monkeypatch, caplog):
    from factory_app.workflows.AppGenerator.tools import app_validation
    from factory_app.workflows.AppGenerator.tools.code_file_utils import compose_bundle_auth_routes
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_auth_app_contract,
        scan_app_contracts,
    )
    from factory_app.workflows.AppGenerator.tools.repair_policy import prepare_bundle_repair
    from mozaiksai.core.runtime.app.auth_contract import AppAuthContractError

    board, pack = component(path="ui/pages/custom/board.jsx"), _assembled_files()
    context = _genesis_context(board, app_task_batch_results={
        "pages": {"code_files": contribution("task:pages", board)["files"]},
        "shell": {"manifest": {"app_name": "Board", "default_route": "/board", "auth_strategy": "basic-login"}, "pages": []},
    })
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    assert _scan_auth_app_contract(_files(result), require_generated_adapter=False) == []
    # One repair retitles the board and also declares the sign-in route behind sign-in.
    manifest = json.loads(retitled(board["ui/route_manifest.json"], "board", "Repaired"))
    manifest["pages"].append({
        "id": "login", "path": "/login", "component": "LoginPage", "meta": {"requiresAuth": True, "title": "Sign in"},
    })
    context.set("code_files", contribution("repair", {"ui/route_manifest.json": json.dumps(manifest)})["files"])
    _responded_repair(context, "ui/route_manifest.json")
    caplog.set_level(logging.INFO, logger=_CODE_FILE_UTILS)
    acceptance = _validation_mocks(monkeypatch)
    await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    checked = acceptance.await_args.kwargs["files"]
    assert checked == admitted_app_file_map(context)
    # The gate checks the repair as written, its unrelated change included, and names the broken binding.
    assert _routes(checked)["board"]["meta"]["title"] == "Repaired"
    assert _routes(checked)["messages"] == _routes(pack)["messages"]
    binding_error = "ui/route_manifest.json: Auth routes.login must declare meta.requiresAuth=false"
    assert _scan_auth_app_contract(checked, require_generated_adapter=False) == [binding_error]
    assert binding_error in scan_app_contracts(checked)
    assert any("ADMITTED_AUTH_ROUTE_BINDING_INVALID" in record.getMessage() for record in caplog.records)
    # The diagnostic names the route manifest, so the repair loop dispatches it to that file's owner.
    pages = {"task_id": "pages", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
             "owned_paths": ["ui/index.js", "ui/route_manifest.json", "ui/pages/custom/board.jsx"], "depends_on": []}
    plan = {"app_build_plan": {"build_tasks": [pages]}, "app_task_batch_items": [pages],
            "app_task_batch_results": {"pages": {"code_files": []}}}
    repair = prepare_bundle_repair({"passed": False, "errors": scan_app_contracts(checked)}, plan)
    assert (repair["status"], repair["target_agent"]) == ("needs_revision", "AppSchemaAgent"), repair
    assert binding_error in repair["target_errors"]
    assert "ui/route_manifest.json" in repair["active"]["allowed_paths"]
    # It never passes: export's own auth route composition rejects the same binding.
    with pytest.raises(AppAuthContractError, match=r"routes\.login must declare meta\.requiresAuth=false"):
        compose_bundle_auth_routes(dict(checked))
