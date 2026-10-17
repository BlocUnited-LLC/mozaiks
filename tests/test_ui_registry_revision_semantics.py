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
    return {route["id"]: route for route in json.loads(files["ui/route_manifest.json"])["pages"]}


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
async def test_revision_app_schema_repair_restoring_the_baseline_supersedes_the_task_it_repairs(
    request, tmp_path, monkeypatch,
):
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
        # The approved contract already names the pack surface, so the schema save re-renders it unchanged.
        "data_contract": json.loads(pack["data/contract.json"]),
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
    context.set("app_ui_quality_status", "passed")
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = _files(result)
    assert _routes(files)["board"] == _routes(board)["board"]
    assert _routes(files)["messages"] == _routes(pack)["messages"]
    assert files["ui/index.js"].count('registerComponent("Messages"') == 1
    assert _routes(admitted_app_file_map(context)) == _routes(files)
    assert {"ui/index.js", "ui/route_manifest.json"} <= set(context.get("bundle_repair_result")["active"]["saved_paths"])
