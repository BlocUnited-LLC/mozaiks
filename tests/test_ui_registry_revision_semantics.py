"""Actual pack-app assembly: repair overlays, page deletion, provenance and workspace paths."""
import json
import logging

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
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
async def test_revision_task_edit_of_workspace_pack_path_is_reported_and_admitted_copy_kept(request, caplog):
    saved = request.getfixturevalue("revision_baseline")
    _save(saved, component(path="ui/pages/custom/board.jsx"), _assembled_files())
    handler = "modules/messages/backend/handler.py"
    selected = _context()
    plan = {**selected["app_build_plan"], "build_tasks": [{"task_id": "svc", "task_type": "business_services", "owned_paths": [handler]}]}
    context = create_context_container(initial={
        **saved.context, **selected, "app_build_plan": plan, "data_contract": EMPTY_CONTRACT,
        "app_task_batch_results": {"svc": {"code_files": [{"filename": handler, "content": saved.files[handler] + "\n# revision edit\n"}]}},
    })
    caplog.set_level(logging.WARNING, logger="factory_app.workflows.AppGenerator.tools.assemble_app_tasks")
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    # Which bytes win is unchanged: raw worker output cannot supersede admitted source.
    assert _files(result)[handler] == saved.files[handler]
    assert [record.getMessage() for record in caplog.records if "WORKSPACE_PACK_OUTPUT_REPLACED" in record.getMessage()] == [
        f"[AppGenerator] WORKSPACE_PACK_OUTPUT_REPLACED at assembly: path={handler} discarded_owner=task:svc "
        "retained=admitted; raw worker output cannot supersede admitted source"
    ]
