"""The shared UI roots compose declarations without rewriting implementations."""
import json
from copy import deepcopy
from itertools import permutations
from pathlib import Path

import pytest

from factory_app.workflows._shared.generated_ui_contract import (
    audit_app_ui_bundle_integrity,
    resolve_registered_component_files,
)
from factory_app.workflows.AppGenerator.tools.app_build_plan import release_pack_owned_paths
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    resolve_templates_for_pack,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    discard_pack_owned_outputs,
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    SHARED_PACK_OUTPUT_PATHS,
    pack_owned_output_paths,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import validate_planned_custom_routes
from mozaiksai.core.workflow.generator_support.ui_registry_composition import compose_ui_registry
from tests.test_appgenerator_revision_baseline import baseline as revision_baseline  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def contribution(owner, files, *, baseline=False):
    return {"owner": owner, "baseline": baseline, "files": [{"filename": path, "content": content} for path, content in files.items()]}


def component(key="Board", *, path="ui/pages/custom/Board.jsx", export="default", options="{}", order=10):
    binding = "View" if export == "default" else f"{{ {export} as View }}"
    source = "export default function View() { return null; }" if export == "default" else f"export function {export}() {{ return null; }}"
    return {
        path: source,
        "ui/index.js": f"import {binding} from './{path.removeprefix('ui/')}';\nexport function register(registerComponent) {{ registerComponent('{key}', View, {options}); }}",
        "ui/route_manifest.json": json.dumps({"pages": [{
            "id": key.lower(), "path": "/" + key.lower(), "component": key, "order": order,
            "navigation": {"group": "main", "order": order},
            "meta": {"title": key, "requiresAuth": True, "ask": {"context": ["page", "selection"]}},
        }]}),
    }


def compose(*parts):
    files = {path: source for part in parts for path, source in part.items()}
    return compose_ui_registry([contribution("input:" + ",".join(sorted(part)), part) for part in parts], files)


def test_actual_pack_custom_and_declarative_pages_preserve_bytes_and_metadata():
    pack = {entry["filename"]: entry["content"] for entry in resolve_templates_for_pack(
        ROOT / "factory_app/build_context/messaging", "messaging",
        context_variables={"app_id": "ui-composition", "build_timestamp": "2026-10-14T00:00:00Z"},
    )}
    app = component(options="{description: 'Board', override: false}")
    app["ui/pages/projects.yaml"] = "id: projects\nroute: /projects\nsections: []\n"
    original_app, original_pack = deepcopy(app), deepcopy(pack)
    shared = compose(app, pack)
    files = {**app, **pack, **shared}
    registered, issues = resolve_registered_component_files(files, source_label="composed")
    assert not issues
    assert set(registered) == {"Board", "Messages", "MessagingProfileTab"}
    routes = json.loads(shared["ui/route_manifest.json"])["pages"]
    assert [route["path"] for route in routes] == ["/board", "/messages"]
    assert routes[0] == json.loads(app["ui/route_manifest.json"])["pages"][0]
    assert routes[1] == json.loads(pack["ui/route_manifest.json"])["pages"][0]
    assert files["ui/pages/projects.yaml"] == app["ui/pages/projects.yaml"]
    assert not audit_app_ui_bundle_integrity(contribution("all", files)["files"])
    assert app == original_app and pack == original_pack


def test_typed_route_bundle_survives_exclusive_pack_filter():
    route = json.loads(component()["ui/route_manifest.json"])["pages"][0]
    payload = {"manifest": None, "pages": [], "custom_route_bundle": {
        "route_manifest": [route], "page_files": [{
            "route_id": "board", "path": "ui/pages/custom/Board.jsx", "component_name": "Board",
            "registry_key": "Board", "purpose": "Board purpose", "content": "export default function Board() { return null; }",
        }],
    }}
    kept, dropped = discard_pack_owned_outputs(payload, frozenset({"ui/index.js", "ui/route_manifest.json"}))
    assert not dropped
    materialized = extract_code_file_map_from_payload(kept)
    assert "Board purpose" in compose(materialized)["ui/index.js"]
    assert json.loads(compose(materialized)["ui/route_manifest.json"])["pages"] == [route]


def test_selected_pack_releases_only_exclusive_implementations_before_page_admission():
    context = {"capability_packs": [{"id": "messaging", "pack_source_path": str(ROOT / "factory_app/build_context/messaging")}]}
    exclusive = pack_owned_output_paths(context)
    assert not exclusive & SHARED_PACK_OUTPUT_PATHS
    assert {"modules/messages/module.yaml", "ui/pages/custom/Messages.jsx"} <= exclusive
    plan = {"build_tasks": [{"task_id": "pages", "owned_paths": [
        "ui/pages/custom/Board.jsx", "ui/index.js", "ui/route_manifest.json", "ui/pages/custom/Messages.jsx",
    ]}, {"task_id": "data", "owned_paths": ["data/contract.json"]}]}
    release_pack_owned_paths(plan, context)
    assert plan["build_tasks"][0]["owned_paths"] == ["ui/pages/custom/Board.jsx", "ui/index.js", "ui/route_manifest.json"]
    assert plan["build_tasks"][1]["owned_paths"] == ["data/contract.json"]


@pytest.mark.parametrize("mutation", [None, "path", "component", "meta", "extra"])
def test_final_plan_closure_accepts_only_exact_verified_pack_routes(mutation):
    app = component(path="ui/pages/custom/board.jsx")
    pack = component("Messages", path="ui/pages/custom/Messages.jsx", order=50)
    files = {**app, **pack, **compose(app, pack)}
    verified = json.loads(pack["ui/route_manifest.json"])["pages"]
    manifest = json.loads(files["ui/route_manifest.json"])
    route = next(route for route in manifest["pages"] if route["component"] == "Messages")
    if mutation == "path":
        route["path"] = "/fabricated"
    elif mutation == "component":
        route["component"] = "Different"
    elif mutation == "meta":
        route["meta"]["requiresAuth"] = False
    elif mutation == "extra":
        manifest["pages"].append({"id": "extra", "path": "/extra", "component": "Board"})
    files["ui/route_manifest.json"] = json.dumps(manifest)
    arguments = dict(
        pages=[{"name": "board", "route": "/board", "ui_surface": "custom_react_page"}],
        owned_paths={"ui/pages/custom/board.jsx", "ui/index.js", "ui/route_manifest.json"},
        verified_pack_routes=verified,
    )
    if mutation:
        with pytest.raises(ValueError, match="unapproved custom route"):
            validate_planned_custom_routes(files, **arguments)
    else:
        validate_planned_custom_routes(files, **arguments)
        arguments.pop("verified_pack_routes")
        with pytest.raises(ValueError, match="unapproved custom route"):
            validate_planned_custom_routes(files, **arguments)


def test_composition_is_order_independent_idempotent_and_preserves_explicit_order():
    parts = [component("Zeta", path="ui/components/Zeta.jsx", order=1), component("Alpha", path="ui/components/Alpha.jsx", order=90)]
    expected = compose(*parts)
    for order in permutations(parts):
        assert compose(*order) == expected
    files = {path: source for part in parts for path, source in part.items()}
    assert compose_ui_registry([
        contribution("prior-composed", {**files, **expected}, baseline=True),
        *(contribution("input:" + ",".join(sorted(part)), part) for part in parts),
    ], files) == expected
    assert [route["id"] for route in json.loads(expected["ui/route_manifest.json"])["pages"]] == ["zeta", "alpha"]


def test_default_named_alias_and_auth_reexport_are_preserved():
    app = component(export="NamedBoard", options="{description: 'A quoted \\\'title\\\'', core: false, override: true}")
    app["ui/auth/authAdapter.js"] = "export function createAuthAdapter() { return {}; }"
    app["ui/index.js"] += "\nexport { createAuthAdapter } from './auth/authAdapter.js';"
    result = compose(app)
    assert "NamedBoard as UiComponent0" in result["ui/index.js"]
    assert '"override": true' in result["ui/index.js"]
    assert '"core": false' in result["ui/index.js"]
    assert "A quoted 'title'" in result["ui/index.js"]
    assert "export { createAuthAdapter as createAuthAdapter }" in result["ui/index.js"]
    assert compose({**app, **result}) == result


def test_default_and_named_imports_from_same_module():
    app = component()
    app["ui/pages/custom/Board.jsx"] += "\nexport function Panel() { return null; }"
    app["ui/index.js"] = "import Default, {Panel as Detail} from './pages/custom/Board.jsx'; export function register(registerComponent) { registerComponent('Board', Default); registerComponent('Panel', Detail); }"
    result = compose(app)
    assert "Panel as UiComponent1" in result["ui/index.js"]
    assert compose({**app, **result}) == result


@pytest.mark.parametrize("clause", ["View, { Panel as View }", "{ Panel as View, Other as View }"])
def test_repeated_local_bindings_within_one_import_fail(clause):
    app = component()
    app["ui/pages/custom/Board.jsx"] += "\nexport function Panel() { return null; } export function Other() { return null; }"
    app["ui/index.js"] = f"import {clause} from './pages/custom/Board.jsx'; export function register(registerComponent) {{ registerComponent('Board', View); }}"
    with pytest.raises(ValueError, match="repeated import binding"):
        compose(app)


def test_one_explicit_override_replaces_component_without_renaming_key():
    old = component()
    old.pop("ui/route_manifest.json")
    new = component(path="ui/pages/custom/NewBoard.jsx", options="{override: true, description: 'replacement'}")
    assert compose(old, new) == compose(new, old)
    assert "NewBoard.jsx" in compose(old, new)["ui/index.js"]
    assert compose(old, new)["ui/index.js"].count('registerComponent("Board"') == 1


@pytest.mark.parametrize("options", ["{}", "{override: true}"])
def test_competing_registrations_fail_without_unique_override(options):
    left = component(options=options)
    left.pop("ui/route_manifest.json")
    right = component(path="ui/pages/custom/Other.jsx", options=options)
    with pytest.raises(ValueError, match="Conflicting component registration"):
        compose(left, right)


@pytest.mark.parametrize("path,match", [("ui/index.js", "Competing owners for component"), ("ui/route_manifest.json", "Competing owners for route")])
def test_identical_declarations_from_distinct_active_owners_are_collisions(path, match):
    files = component()
    with pytest.raises(ValueError, match=match):
        compose_ui_registry([
            contribution("pack:first", files),
            contribution("pack:second", {path: files[path]}),
        ], files)


def test_same_owner_repeated_contribution_is_idempotent():
    files = component()
    declared = contribution("task:board", files)
    assert compose_ui_registry([declared, declared], files) == compose_ui_registry([declared], files)


@pytest.mark.parametrize("full_root", [False, True])
def test_admitted_revision_replaces_baseline_metadata_and_carries_pack_entries(full_root):
    app = component(path="ui/pages/custom/board.jsx")
    app["ui/auth/authAdapter.js"] = "export function createAuthAdapter() { return {}; }"
    app["ui/index.js"] += "\nexport { createAuthAdapter } from './auth/authAdapter.js';"
    pack = component("Messages", path="ui/pages/custom/Messages.jsx", order=50)
    baseline = {**app, **pack, **compose(app, pack)}
    revised = deepcopy(baseline if full_root else app)
    manifest = json.loads(revised["ui/route_manifest.json"])
    board = next(route for route in manifest["pages"] if route["id"] == "board")
    board["meta"]["title"] = "Approved revised board"
    board["order"] = board["navigation"]["order"] = 90
    if full_root:
        manifest["pages"].reverse()
    revised["ui/route_manifest.json"] = json.dumps(manifest)
    owned = {"ui/pages/custom/board.jsx", "ui/index.js", "ui/route_manifest.json"}
    arguments = dict(pages=[{"name": "board", "route": "/board", "ui_surface": "custom_react_page"}], owned_paths=owned, baseline_files=baseline)
    validate_planned_custom_routes(revised, **arguments)
    parts = [contribution("app:generated_files", baseline, baseline=True), contribution("task:pages", revised), contribution("pack:messaging", pack)]
    files = {**baseline, **revised}
    result = compose_ui_registry(parts, files, revision_baseline=baseline)
    for order in permutations(parts):
        assert compose_ui_registry(list(order), files, revision_baseline=baseline) == result
    routes = json.loads(result["ui/route_manifest.json"])["pages"]
    assert [route["id"] for route in routes] == ["messages", "board"]
    assert routes[1] == board
    assert routes[0] == json.loads(pack["ui/route_manifest.json"])["pages"][0]
    assert "createAuthAdapter" in result["ui/index.js"]
    assert result["ui/index.js"].count('registerComponent("Messages"') == 1
    validate_planned_custom_routes({**files, **result}, **arguments)
    assert compose_ui_registry([contribution("app:generated_files", {**files, **result}, baseline=True), *parts[1:]], files, revision_baseline=baseline) == result


@pytest.mark.parametrize("other_owner", ["task:other", "pack:other"])
def test_baseline_revision_does_not_hide_competing_current_route_owners(other_owner):
    baseline = component()
    revised = deepcopy(baseline)
    manifest = json.loads(revised["ui/route_manifest.json"])
    manifest["pages"][0]["meta"]["title"] = "Admitted update"
    revised["ui/route_manifest.json"] = json.dumps(manifest)
    with pytest.raises(ValueError, match="Competing owners for route"):
        compose_ui_registry([
            contribution("app:generated_files", baseline, baseline=True),
            contribution("task:pages", revised), contribution(other_owner, revised),
        ], revised, revision_baseline=baseline)


def test_pack_alone_cannot_replace_a_conflicting_saved_app_route():
    baseline = component()
    pack = deepcopy(baseline)
    manifest = json.loads(pack["ui/route_manifest.json"])
    manifest["pages"][0]["meta"]["requiresAuth"] = False
    pack["ui/route_manifest.json"] = json.dumps(manifest)
    with pytest.raises(ValueError, match="Conflicting route id"):
        compose_ui_registry([contribution("app:generated_files", baseline, baseline=True), contribution("pack:other", pack)], pack)


def test_partial_title_revision_preserves_prior_relative_positions():
    zeta = component("Zeta", path="ui/pages/custom/zeta.jsx")
    board = component(path="ui/pages/custom/board.jsx")
    pack = component("Messages", path="ui/pages/custom/Messages.jsx", order=50)
    baseline = {**zeta, **board, **pack, **compose(zeta, board, pack)}
    by_id = {route["id"]: route for route in json.loads(baseline["ui/route_manifest.json"])["pages"]}
    baseline["ui/route_manifest.json"] = json.dumps({"pages": [by_id[key] for key in ("zeta", "board", "messages")]})
    changed = deepcopy(by_id["board"])
    changed["meta"]["title"] = "Revised title only"
    revised = {**board, "ui/route_manifest.json": json.dumps({"pages": [changed]})}
    parts = [contribution("app:generated_files", baseline, baseline=True), contribution("task:board", revised), contribution("pack:messaging", pack)]
    for order in permutations(parts):
        result = compose_ui_registry(list(order), {**baseline, **revised}, revision_baseline=baseline)
        routes = json.loads(result["ui/route_manifest.json"])["pages"]
        assert [route["id"] for route in routes] == ["zeta", "board", "messages"]
        assert routes[1] == changed


@pytest.mark.parametrize("other_owner", ["task:other", "pack:other"])
def test_current_accumulated_snapshot_cannot_hide_competing_active_owners(other_owner):
    files = component()
    with pytest.raises(ValueError, match="Competing owners for route"):
        compose_ui_registry([
            contribution("app:accumulated_files", files, baseline=True),
            contribution("task:pages", files), contribution(other_owner, files),
        ], files)


@pytest.mark.asyncio
@pytest.mark.parametrize("other_owner", ["task", "pack"])
async def test_actual_genesis_accumulated_copy_cannot_hide_competing_ui_owners(other_owner):
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from tests.factory_context import factory_context
    from tests.test_messaging_pack_persistence_composition import _assembled_files, _context

    class Context(dict):
        def set(self, key, value):
            self[key] = value

    files = component() if other_owner == "task" else _assembled_files()
    output = {"code_files": contribution("task:pages", files)["files"]}
    context = Context(factory_context({
        **_context(), "app_id": "genesis_collision", "code_files": output["code_files"],
        "data_contract": {"version": "1", "surfaces": [], "shared_collections": []},
        "app_task_batch_results": {"pages": output, **({"other": output} if other_owner == "task" else {})},
    }))
    result = await assemble_app_tasks(context_variables=context)
    assert not result["success"]
    assert "Competing owners for route" in result["error"]


@pytest.mark.asyncio
async def test_actual_revision_assembly_updates_app_route_and_retains_verified_pack(request):
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from mozaiksai.core.workflow.context.adapter import create_context_container
    from tests.test_appgenerator_revision_baseline import _replace_archive
    from tests.test_messaging_pack_persistence_composition import _assembled_files, _context

    saved = request.getfixturevalue("revision_baseline")
    app = component(path="ui/pages/custom/board.jsx")
    pack = _assembled_files()
    saved.files = {**app, **pack, **compose(app, pack), "app.json": json.dumps({"appId": "customer-app", "appName": "Board"})}
    _replace_archive(saved, {})
    revised = {path: saved.files[path] for path in ("ui/index.js", "ui/route_manifest.json", "ui/pages/custom/board.jsx")}
    manifest = json.loads(revised["ui/route_manifest.json"])
    board = next(route for route in manifest["pages"] if route["id"] == "board")
    board["meta"]["title"] = "Approved revised board"
    board["order"] = board["navigation"]["order"] = 90
    manifest["pages"].reverse()
    revised["ui/route_manifest.json"] = json.dumps(manifest)
    selected = _context()
    plan = {**selected["app_build_plan"], "pages": [{"name": "board", "route": "/board", "ui_surface": "custom_react_page"}],
            "build_tasks": [{"task_id": "pages", "task_type": "page_bundle", "owned_paths": list(revised)}]}
    context = create_context_container(initial={
        **saved.context, **selected, "app_build_plan": plan,
        "data_contract": {"version": "1", "surfaces": [], "shared_collections": []},
        "app_task_batch_results": {"pages": {"code_files": contribution("task:pages", revised)["files"]}},
    })
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = {file["filename"]: file["content"] for file in result["code_files"]}
    assert json.loads(files["ui/route_manifest.json"])["pages"] == manifest["pages"]
    assert files["ui/index.js"].count('registerComponent("Messages"') == 1
    for path in ("modules/messages/backend/policy.py", "ui/pages/custom/Messages.jsx", "ui/pages/custom/board.jsx"):
        assert files[path] == saved.files[path]
    assert saved.content.get_bundle.await_count == 2
    saved.content.get_bundle.assert_awaited_with("immutable-archive")
    repeated = await assemble_app_tasks(context_variables=context)
    assert repeated["success"], repeated
    repeated_files = {file["filename"]: file["content"] for file in repeated["code_files"]}
    for path in ("ui/index.js", "ui/route_manifest.json"):
        assert repeated_files[path] == files[path]
    assert saved.content.get_bundle.await_count == 4
    saved.content.get_bundle.assert_awaited_with("immutable-archive")


def test_declared_array_and_navigation_order_survive_discovery_order():
    one, two = component("Zeta", path="ui/components/Zeta.jsx"), component("Alpha", path="ui/components/Alpha.jsx")
    zeta = json.loads(one.pop("ui/route_manifest.json"))["pages"][0]
    alpha = json.loads(two.pop("ui/route_manifest.json"))["pages"][0]
    for route in (zeta, alpha):
        route.pop("order")
    zeta["navigation"]["order"], alpha["navigation"]["order"] = 1, 90
    manifest = {"ui/route_manifest.json": json.dumps({"pages": [zeta, alpha]})}
    expected = compose(one, two, manifest)
    assert compose(manifest, two, one) == expected
    assert json.loads(expected["ui/route_manifest.json"])["pages"] == [zeta, alpha]
    # An authored sequence remains intact even without numeric navigation order.
    zeta["navigation"].pop("order")
    alpha["navigation"].pop("order")
    manifest["ui/route_manifest.json"] = json.dumps({"pages": [zeta, alpha]})
    assert json.loads(compose(two, one, manifest)["ui/route_manifest.json"])["pages"] == [zeta, alpha]


@pytest.mark.parametrize("change,match", [
    ({"path": "/other"}, "Conflicting route id"),
    ({"id": "other"}, "Conflicting route path"),
    ({"meta": {"requiresAuth": False}}, "Conflicting route id"),
])
def test_conflicting_route_identity_or_metadata_fails(change, match):
    left, right = component(), component()
    manifest = json.loads(right["ui/route_manifest.json"])
    manifest["pages"][0].update(change)
    right["ui/route_manifest.json"] = json.dumps(manifest)
    with pytest.raises(ValueError, match=match):
        compose(left, right)


@pytest.mark.parametrize("source,match", [
    ("import View from './missing.jsx'; export function register(registerComponent) { registerComponent('Board', View); }", "existing file"),
    ("import View from '../../outside.jsx'; export function register(registerComponent) { registerComponent('Board', View); }", "escapes"),
    ("import View from '@unverified/ui'; export function register(registerComponent) { registerComponent('Board', View); }", "app-local"),
    ("import View from './pages/custom/Board.jsx'; export function register(registerComponent) { registerComponent(key, View); }", "literal strings"),
    ("import View from './pages/custom/Board.jsx'; export function register(registerComponent) { registerComponent('Board', make(View)); }", "unresolved"),
    ("import View from './pages/custom/Board.jsx'; export function register(registerComponent) { registerComponent('Board', View); registerComponent('Board', View); }", "repeated key"),
    ("import View from './pages/custom/Board.jsx'; export function register(registerComponent) { registerComponent('Board', View, {...options}); }", "spreads"),
    ("import View from './pages/custom/Board.jsx'; export function register(registerComponent) { registerComponent('Board', View, {override: allowOverride}); }", "literal boolean"),
    ("import View from './pages/custom/Board.jsx'; export function register(registerComponent) { registerComponent('Board', View, {unknown: true}); }", "Unsupported"),
    ("import View from './pages/custom/Board.jsx'; sideEffect(); export function register(registerComponent) { registerComponent('Board', View); }", "executable"),
    ("import View from './pages/custom/Board.jsx'; export function register(registerComponent) { if (allowed) registerComponent('Board', View); }", "static registrations"),
])
def test_dynamic_or_unresolved_declarations_fail_closed(source, match):
    app = component()
    app["ui/index.js"] = source
    with pytest.raises(ValueError, match=match):
        compose(app)


@pytest.mark.parametrize("source", [
    "// export default is not an export\nconst View = () => null;",
    "export const Different = () => null;",
    "export default Missing;",
    "export default function Broken( {",
])
def test_missing_or_invalid_default_export_fails(source):
    app = component()
    app["ui/pages/custom/Board.jsx"] = source
    with pytest.raises(ValueError, match="missing component export|invalid JavaScript"):
        compose(app)


def test_missing_named_export_fails():
    app = component(export="Missing")
    app["ui/pages/custom/Board.jsx"] = "export const Other = () => null;"
    with pytest.raises(ValueError, match="missing component export"):
        compose(app)


def test_namespace_reexport_does_not_expose_its_source_named_component():
    app = component(export="Board", path="ui/pages/custom/Board.jsx")
    app["ui/pages/custom/Board.jsx"] = "export * as Namespace from '../../components/source.jsx';"
    app["ui/components/source.jsx"] = "export function Board() { return null; }"
    with pytest.raises(ValueError, match="missing component export"):
        compose(app)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_component_export_star_forwarding_is_rejected(ambiguous):
    app = component(export="Board")
    app["ui/pages/custom/Board.jsx"] = "export * from '../../components/one.jsx';"
    app["ui/components/one.jsx"] = "export function Board() { return null; }"
    if ambiguous:
        app["ui/pages/custom/Board.jsx"] += "export * from '../../components/two.jsx';"
        app["ui/components/two.jsx"] = "export function Board() { return null; }"
    with pytest.raises(ValueError, match="export-star forwarding is unsupported"):
        compose(app)


@pytest.mark.parametrize("export,forward", [("Board", "Board"), ("default", "Board as default")])
def test_explicit_named_and_default_component_forwarding_remain_supported(export, forward):
    app = component(export=export)
    app["ui/pages/custom/Board.jsx"] = f"export {{ {forward} }} from '../../components/one.jsx';"
    app["ui/components/one.jsx"] = "export function Board() { return null; }"
    assert 'registerComponent("Board"' in compose(app)["ui/index.js"]


def test_named_export_must_resolve_its_local_binding():
    app = component(export="Missing")
    app["ui/pages/custom/Board.jsx"] = "export {Missing};"
    with pytest.raises(ValueError, match="missing component export"):
        compose(app)


def test_exported_local_component_with_external_react_import_is_supported():
    app = component()
    app["ui/pages/custom/Board.jsx"] = "import React from 'react'; const Board = () => <div />; export default Board;"
    assert 'registerComponent("Board"' in compose(app)["ui/index.js"]


def test_unregistered_route_component_fails():
    app = component()
    app["ui/index.js"] = "export function register() {}"
    with pytest.raises(ValueError, match="Unregistered route component"):
        compose(app)


def test_missing_barrel_reexport_fails():
    app = component()
    app["ui/auth/authAdapter.js"] = "export const other = true;"
    app["ui/index.js"] += "export {createAuthAdapter} from './auth/authAdapter.js';"
    with pytest.raises(ValueError, match="missing barrel export"):
        compose(app)
