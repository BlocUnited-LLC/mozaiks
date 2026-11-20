"""Install, customize, upgrade and verify a capability library in an existing ordinary app.

The fixture library ``tests/fixtures/library_packs/stock_ledger`` ships a module
with an adapter, a seed config file, a workflow with a shared tool, a module UI
barrel, and keyed entries for the data contract, the route manifest, the admin
registry and the workflow registry. The app is built here the way an operator's
app looks: its own formatting, comments and entries in every shared file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import (
    community_component_workspace as workspace_module,
)
from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
    CommunityComponentConflictError,
    CommunityComponentLifecycleError,
)
from factory_app.workflows.AppGenerator.tools.community_component_sources import (
    PackSource,
    PackSourceError,
    export_git_pack,
    sanitized_remote,
)
from factory_app.workflows.AppGenerator.tools.community_component_workspace import (
    CommunityComponentLockedError,
    apply_component_install,
    apply_workspace_upgrade,
    installed_component_status,
    plan_component_install,
    plan_workspace_upgrade,
    verify_installed_components,
    workspace_roots,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    compute_pack_digest,
)

FIXTURE = Path(__file__).parent / "fixtures" / "library_packs" / "stock_ledger"
PACK_ID = "library.stock_ledger"

APP_ROUTES = '{\n    "pages": [\n        { "id": "home", "path": "/", "component": "HomePage" }\n    ]\n}\n'
APP_ADMIN = (
    "schema_version: mozaiks.admin.registry.v1\n\n"
    "# Corner Shop operator pages.\n"
    "pages:\n"
    "  # Orders first.\n"
    "  - id: orders_admin\n"
    "    label: Orders\n"
    "    path: /admin/orders_admin\n"
    "    surfaces: [studio]\n"
)


@pytest.fixture(autouse=True)
def _restore_import_state(tmp_path: Path):
    """Forget the installed apps' own modules (handlers, adapters) and their sys.path entries after each test.

    Only modules loaded from this test's temporary workspace are dropped: removing a
    framework module imported for the first time here would give later test files a
    second copy of it, with different class identities.
    """
    path_snapshot = list(sys.path)
    modules_snapshot = set(sys.modules)
    yield
    sys.path[:] = path_snapshot
    workspace = os.path.normcase(str(tmp_path.resolve()))

    def loaded_from_workspace(module: object) -> bool:
        locations = [getattr(module, "__file__", None) or ""]
        try:
            locations.extend(getattr(module, "__path__", None) or [])
        except (KeyError, ImportError):  # a namespace path recomputed against a parent already gone
            pass
        return any(location and os.path.normcase(os.path.abspath(location)).startswith(workspace) for location in locations)

    # Decide before removing anything: a child namespace package recomputes its path through its parent.
    doomed = [
        key for key, module in list(sys.modules.items())
        if key not in modules_snapshot and module is not None and loaded_from_workspace(module)
    ]
    for key in doomed:
        sys.modules.pop(key, None)


def _ordinary_app(root: Path, *, registry: bool = True):
    app = root / "app"
    for folder in ("data", "ui", "admin", "security"):
        (app / folder).mkdir(parents=True, exist_ok=True)
    (app / "app.json").write_text('{"appName": "Corner Shop"}\n', encoding="utf-8")
    (app / "data" / "contract.json").write_text(json.dumps({
        "version": "1",
        "surfaces": [{
            "surface_id": "orders", "surface_kind": "module", "primary_entities": ["order"],
            "collections": [{
                "name": "order", "entity": "order", "mongo_collection": "orders", "data_alias": "orders.orders",
                "scope": "app", "tenancy": "app_wide", "owner_field": None, "owner_module": "orders",
                "ownership": {"surface_id": "orders", "surface_kind": "module"},
                "fields": [{"name": "total", "type": "number", "required": True}],
            }],
        }],
        "shared_collections": [],
    }, indent=4) + "\n", encoding="utf-8")
    (app / "ui" / "route_manifest.json").write_text(APP_ROUTES, encoding="utf-8")
    (app / "admin" / "admin_registry.yaml").write_text(APP_ADMIN, encoding="utf-8")
    (app / "security" / "secrets.yaml").write_text("version: 1\nsecrets: []\n", encoding="utf-8")
    (app / "brand").mkdir()
    (app / "brand" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff\xfe binary")
    workflows = root / "workflows"
    (workflows / "extended_orchestration").mkdir(parents=True)
    if registry:
        (workflows / "extended_orchestration" / "extension_registry.json").write_text(json.dumps({
            "pack_name": "CornerShop", "version": 3, "extends": "mozaiks.default_workflow_registry", "workflows": [],
        }, indent=2) + "\n", encoding="utf-8")
    return workspace_roots(root)


def _pack(tmp_path: Path, name: str = "v1") -> Path:
    destination = tmp_path / "packs" / name
    shutil.copytree(FIXTURE, destination)
    return destination


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {path}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _version_two(tmp_path: Path) -> Path:
    """Version 2 changes a file, adds an action and a workflow tool, removes a file, and changes a route and the seed."""
    pack = _pack(tmp_path, "v2")
    _edit(pack / "context.yaml", 'version: "1.0.0"', 'version: "2.0.0"')
    module = pack / "templates" / "modules" / "stock_ledger"
    _edit(module / "module.yaml", "  - id: record_item\n", (
        "  - id: count_items\n"
        "    description: Count stock items.\n"
        "    handler_method: count_items\n"
        "    permissions: [stock.read]\n"
        "  - id: record_item\n"
    ))
    _edit(module / "backend" / "handler.py", "    async def record_item(", (
        "    async def count_items(self, ctx, **params):\n"
        "        return {\"count\": len(self.service.list_items()[\"items\"])}\n\n"
        "    async def record_item("
    ))
    (module / "backend" / "legacy_import.py").unlink()
    _edit(pack / "templates" / "ui" / "route_manifest.json", '"title": "Stock ledger"', '"title": "Stock"')
    _edit(pack / "templates" / "config" / "stock_ledger.yaml", "low_stock_threshold: 5", "low_stock_threshold: 10")
    (pack / "workflows" / "StockReview" / "tools" / "summarize.py").write_text(
        "def summarize(items):\n    return f\"{len(items)} items\"\n", encoding="utf-8",
    )
    return pack


def _tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _install(tmp_path: Path, **kwargs):
    roots = _ordinary_app(tmp_path / "shop")
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots, **kwargs)
    return roots


@pytest.mark.parametrize("row_key", ["materialized_owned_files", "materialized_workflow_files"])
@pytest.mark.parametrize("bad_path", ["../../sentinel.txt", "/outside.txt", "C:/outside.txt", "..\\outside.txt"])
def test_recorded_paths_are_confined_before_read_or_removal(tmp_path: Path, monkeypatch, row_key, bad_path) -> None:
    roots = _install(tmp_path)
    provenance = roots.app_root / ".mozaiks/pack_provenance.json"
    document = json.loads(provenance.read_text(encoding="utf-8"))
    document["packs"][0][row_key].append({
        "path": bad_path, "owner": "templates", "sha256": hashlib.sha256(b"sentinel").hexdigest(),
    })
    provenance.write_text(json.dumps(document), encoding="utf-8")
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_bytes(b"sentinel")
    before = _tree(tmp_path / "shop")
    original_read = workspace_module._read_text

    def confined_read(path):
        assert path.resolve().is_relative_to(tmp_path / "shop"), "outside destination was read"
        return original_read(path)

    monkeypatch.setattr(workspace_module, "_read_text", confined_read)
    with pytest.raises(CommunityComponentLifecycleError, match="Unsafe workspace path"):
        apply_workspace_upgrade(source=PackSource(directory=_version_two(tmp_path)), roots=roots)
    with pytest.raises(CommunityComponentLifecycleError, match="Unsafe workspace path"):
        verify_installed_components(roots=roots)
    assert _tree(tmp_path / "shop") == before
    assert sentinel.read_bytes() == b"sentinel"


def _directory_redirect(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("root_name, relative", [
    ("app_root", "modules"), ("app_root", ".mozaiks"),
    ("workflows_root", "StockReview"), ("build_context_root", ".mozaiks"),
])
def test_install_rejects_redirected_destinations(tmp_path: Path, root_name, relative) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"sentinel")
    link = getattr(roots, root_name) / relative
    _directory_redirect(link, outside)
    before = _tree(outside)
    try:
        with pytest.raises(CommunityComponentLifecycleError, match="Unsafe workspace path"):
            apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
        assert _tree(outside) == before
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_explicit_root_redirect_is_not_resolved_away(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    link = tmp_path / "app-link"
    _directory_redirect(link, roots.app_root)
    try:
        with pytest.raises(CommunityComponentLifecycleError, match="Unsafe workspace path"):
            workspace_roots(tmp_path / "shop", app_root=link)
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("explicit_roots", [False, True])
def test_parent_relative_roots_install_and_verify(tmp_path: Path, monkeypatch, explicit_roots) -> None:
    expected = _ordinary_app(tmp_path / "shop")
    launch = tmp_path / "launch"
    launch.mkdir()
    monkeypatch.chdir(launch)
    options = {
        "app_root": "../shop/app", "workflows_root": "../shop/workflows", "build_context_root": "../shop/build_context",
    } if explicit_roots else {}
    roots = workspace_roots("../shop", **options)
    assert roots == expected
    assert apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots).status == "applied"
    assert verify_installed_components(roots=roots)["status"] == "clean"


def test_install_does_not_overwrite_predictable_temporary_path(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    existing = roots.app_root / "modules/stock_ledger/.module.yaml.mozaiks-tmp"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"unrelated app file")
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    assert existing.read_bytes() == b"unrelated app file"


def test_commit_rechecks_a_destination_redirected_after_planning(tmp_path: Path, monkeypatch) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_bytes(b"sentinel")
    before = _tree(outside)
    original_commit = workspace_module._commit
    link = roots.app_root / "modules"

    def redirected_commit(*args, **kwargs):
        _directory_redirect(link, outside)
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(workspace_module, "_commit", redirected_commit)
    try:
        with pytest.raises(CommunityComponentLifecycleError, match="Unsafe workspace path"):
            apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
        assert _tree(outside) == before
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_rollback_does_not_follow_a_redirect_introduced_during_commit(tmp_path: Path, monkeypatch) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    outside = tmp_path / "outside"
    outside.mkdir()
    original_replace = workspace_module.os.replace
    redirected = []

    def redirect_then_fail(source, destination):
        destination = Path(destination)
        saved = destination.parent.with_name(destination.parent.name + "_saved")
        destination.parent.rename(saved)
        _directory_redirect(destination.parent, outside)
        (outside / destination.name).write_bytes(b"outside sentinel")
        redirected.append((destination.parent, saved, destination.name))
        raise OSError("injected failure after redirect")

    monkeypatch.setattr(workspace_module.os, "replace", redirect_then_fail)
    try:
        with pytest.raises(OSError, match="injected failure") as failure:
            apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
        assert (outside / redirected[0][2]).read_bytes() == b"outside sentinel"
        assert any("Rollback could not restore" in note and "Unsafe workspace path" in note for note in failure.value.__notes__)
    finally:
        monkeypatch.setattr(workspace_module.os, "replace", original_replace)
        for link, saved, _ in redirected:
            if os.name == "nt":
                link.rmdir()
            else:
                link.unlink()
            saved.rename(link)


def test_lock_cleanup_preserves_original_error_when_redirected(tmp_path: Path, monkeypatch) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "lifecycle.lock"
    sentinel.write_bytes(b"outside sentinel")
    link = roots.build_context_root / ".mozaiks"
    saved = roots.build_context_root / "saved-state"

    def redirect_then_fail(*args, **kwargs):
        link.rename(saved)
        _directory_redirect(link, outside)
        raise OSError("original commit failure")

    monkeypatch.setattr(workspace_module, "_commit", redirect_then_fail)
    try:
        with pytest.raises(OSError, match="original commit failure") as failure:
            apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
        assert sentinel.read_bytes() == b"outside sentinel"
        assert any("Could not safely remove lifecycle lock" in note for note in failure.value.__notes__)
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
        saved.rename(link)


@pytest.mark.parametrize("owner, primary_alias, permitted", [
    ("stock_ledger", "stock.items", True),
    ("orders", "stock.items", False),
    ("stock_ledger", "orders.orders", False),
])
def test_adoption_preserves_same_owner_shared_collection_grants(tmp_path: Path, owner, primary_alias, permitted) -> None:
    roots = _pre_placed(tmp_path)
    contract_path = roots.app_root / "data/contract.json"
    document = json.loads(contract_path.read_text(encoding="utf-8"))
    document["shared_collections"] = [{
        "mongo_collection": "stock_items", "primary_alias": primary_alias, "owner_module": owner,
        "read_by": [{"module": "orders", "access": "read_only", "purpose": "order correlation"}],
    }]
    contract_path.write_text(json.dumps(document, indent=4), encoding="utf-8")
    before = contract_path.read_bytes()
    source = PackSource(directory=_pack(tmp_path))
    if permitted:
        plan = apply_component_install(source=source, roots=roots, adopt=True)
        assert plan.status == "applied"
        assert contract_path.read_bytes() == before
        assert verify_installed_components(roots=roots)["status"] == "clean"
        assert apply_workspace_upgrade(source=PackSource(directory=_version_two(tmp_path)), roots=roots).status == "applied"
        assert contract_path.read_bytes() == before
    else:
        plan = plan_component_install(source=source, roots=roots, adopt=True)
        assert plan.status == "blocked"
        assert any(row["kind"] == "alias_conflict" for row in plan.conflicts)
        with pytest.raises(CommunityComponentConflictError):
            apply_component_install(source=source, roots=roots, adopt=True)
        assert contract_path.read_bytes() == before


def _capability_pack(tmp_path: Path, name: str, *, requires: str | None = None, provides: str | None = None) -> Path:
    pack = _pack(tmp_path, name)
    context_path = pack / "context.yaml"
    context = yaml.safe_load(context_path.read_text(encoding="utf-8"))
    context["context_id"] = context["pack"]["id"] = f"library.{name}"
    context["capabilities"] = [{"capability_id": provides}] if provides else []
    context["assets"] = [row for row in context["assets"] if row["kind"] != "workflows"]
    context_path.write_text(yaml.safe_dump(context), encoding="utf-8")
    # All deleted trees are disposable fixture copies, never the original pack or workspace.
    shutil.rmtree(pack / "templates")
    shutil.rmtree(pack / "workflows")
    (pack / "templates/modules" / name).mkdir(parents=True)
    (pack / "templates/modules" / name / "README.md").write_text(name, encoding="utf-8")
    contract = {"contract_id": f"library.{name}", "contract_type": "build_pack_instructions"}
    if requires:
        contract["requires"] = {"capabilities": [requires]}
    (pack / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    return pack


@pytest.mark.parametrize("alternative_provider", [False, True])
def test_upgrade_rechecks_capabilities_of_installed_dependents(tmp_path: Path, alternative_provider) -> None:
    roots = _install(tmp_path)
    reports = _capability_pack(tmp_path, "reports", requires="stock.ledger")
    apply_component_install(source=PackSource(directory=reports), roots=roots)
    if alternative_provider:
        other = _capability_pack(tmp_path, "other", provides="stock.ledger")
        apply_component_install(source=PackSource(directory=other), roots=roots)
    candidate = _version_two(tmp_path)
    _edit(candidate / "context.yaml", "  - capability_id: stock.ledger", "  - capability_id: stock.count")
    before = _tree(tmp_path / "shop")
    plan = plan_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    if alternative_provider:
        assert plan.status == "ready"
        assert apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots).status == "applied"
    else:
        assert plan.status == "blocked"
        assert any(row["kind"] == "required_by" and row["owner"] == "library.reports" and "stock.ledger" in row["detail"] for row in plan.conflicts)
        with pytest.raises(CommunityComponentConflictError):
            apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
        assert _tree(tmp_path / "shop") == before


# --------------------------------------------------------------------------------------
# Install
# --------------------------------------------------------------------------------------


def test_dry_run_plans_every_output_and_writes_nothing(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    before = _tree(tmp_path / "shop")

    plan = plan_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots).to_dict()

    assert _tree(tmp_path / "shop") == before
    assert plan["status"] == "ready" and plan["from"] is None
    assert "modules/stock_ledger/module.yaml" in plan["files"]["write"]
    assert "services/adapters/stock/memory.py" in plan["files"]["write"]
    assert plan["files"]["seed"] == ["config/stock_ledger.yaml"]
    assert plan["workflow_files"]["write"] == [
        "StockReview/agents.yaml", "StockReview/orchestrator.yaml", "StockReview/tools/review_stock.py", "_shared/tools/stock_lookup.py",
    ]
    assert {(row["root"], row["key"], row["action"]) for row in plan["composed"]} == {
        ("data/contract.json", "stock_ledger", "add"),
        ("ui/route_manifest.json", "stock-ledger", "add"),
        ("admin/admin_registry.yaml", "stock_admin", "add"),
        ("workflows/extended_orchestration/extension_registry.json", "StockReview", "add"),
    }
    assert plan["permissions_introduced"] == ["stock.manage", "stock.read"]
    assert plan["host_requirements"]["secrets"] == [{"env": "STOCK_SUPPLIER_API_KEY", "declared": False}]
    assert [warning["kind"] for warning in plan["warnings"]] == ["secret_not_declared"]


def test_install_composes_shared_files_without_touching_the_apps_own_bytes(tmp_path: Path) -> None:
    roots = _install(tmp_path)

    routes = (roots.app_root / "ui" / "route_manifest.json").read_text(encoding="utf-8")
    assert routes.startswith('{\n    "pages": [\n        { "id": "home", "path": "/", "component": "HomePage" },\n')
    assert [page["id"] for page in json.loads(routes)["pages"]] == ["home", "stock-ledger"]
    admin = (roots.app_root / "admin" / "admin_registry.yaml").read_text(encoding="utf-8")
    assert admin.startswith(APP_ADMIN)
    assert [page["id"] for page in yaml.safe_load(admin)["pages"]] == ["orders_admin", "stock_admin"]
    contract = json.loads((roots.app_root / "data" / "contract.json").read_text(encoding="utf-8"))
    assert [surface["surface_id"] for surface in contract["surfaces"]] == ["orders", "stock_ledger"]
    registry = json.loads(
        (roots.workflows_root / "extended_orchestration" / "extension_registry.json").read_text(encoding="utf-8")
    )
    assert [entry["id"] for entry in registry["workflows"]] == ["StockReview"]
    assert (roots.workflows_root / "_shared" / "tools" / "stock_lookup.py").is_file()
    assert (roots.app_root / "config" / "stock_ledger.yaml").is_file()
    assert (roots.app_root / "brand" / "logo.png").read_bytes().startswith(b"\x89PNG")


def test_install_records_hashed_ownership_and_a_pin(tmp_path: Path) -> None:
    roots = _install(tmp_path)

    provenance = json.loads((roots.app_root / ".mozaiks" / "pack_provenance.json").read_text(encoding="utf-8"))
    [entry] = provenance["packs"]
    assert entry["ownership"] == "content_hash"
    owners = {row["path"]: row["owner"] for row in entry["materialized_owned_files"]}
    assert owners["config/stock_ledger.yaml"] == "workspace"
    assert owners["modules/stock_ledger/backend/service.py"] == "templates"
    assert all(len(row["sha256"]) == 64 for row in entry["materialized_owned_files"])
    assert {row["path"] for row in entry["materialized_workflow_files"]} == {
        "StockReview/agents.yaml", "StockReview/orchestrator.yaml", "StockReview/tools/review_stock.py", "_shared/tools/stock_lookup.py",
    }
    assert set(provenance["composed_outputs"]) == {"admin/admin_registry.yaml", "data/contract.json", "ui/route_manifest.json"}
    assert provenance["composed_workflow_outputs"]["extended_orchestration/extension_registry.json"][0]["entries"] == [
        {"container": "workflows", "key": "StockReview", "sha256": provenance["composed_workflow_outputs"][
            "extended_orchestration/extension_registry.json"][0]["entries"][0]["sha256"]},
    ]
    pins = json.loads((roots.build_context_root / ".mozaiks" / "installed_components.json").read_text(encoding="utf-8"))
    assert [(pin["pack_id"], pin["version"]) for pin in pins["components"]] == [(PACK_ID, "1.0.0")]
    assert verify_installed_components(roots=roots)["status"] == "clean"
    [status] = installed_component_status(roots=roots)["packs"]
    assert (status["ownership"], status["owned_files"], status["seeded_files"], status["workflow_files"]) == (
        "content_hash", 8, 1, 4,
    )


def test_installing_the_same_version_again_changes_nothing(tmp_path: Path) -> None:
    roots = _install(tmp_path)
    before = _tree(tmp_path / "shop")

    plan = apply_component_install(source=PackSource(directory=_pack(tmp_path, "again")), roots=roots)

    assert plan.status == "unchanged"
    assert _tree(tmp_path / "shop") == before


def test_installing_another_version_points_to_upgrade(tmp_path: Path) -> None:
    roots = _install(tmp_path)

    with pytest.raises(CommunityComponentLifecycleError, match="mozaiks pack upgrade"):
        plan_component_install(source=PackSource(directory=_version_two(tmp_path)), roots=roots)


# --------------------------------------------------------------------------------------
# Customization survives upgrades; edits to library-owned content do not
# --------------------------------------------------------------------------------------


def test_upgrade_keeps_the_apps_customizations(tmp_path: Path) -> None:
    roots = _install(tmp_path)
    seed = roots.app_root / "config" / "stock_ledger.yaml"
    seed.write_text("low_stock_threshold: 2  # the shop's own choice\n", encoding="utf-8")
    routes = roots.app_root / "ui" / "route_manifest.json"
    _edit(routes, '{ "id": "home", "path": "/", "component": "HomePage" },',
          '{ "id": "home", "path": "/", "component": "HomePage" },\n        { "id": "about", "path": "/about", "component": "AboutPage" },')
    app_module = roots.app_root / "modules" / "orders" / "module.yaml"
    app_module.parent.mkdir(parents=True)
    app_module.write_text("schema_version: mozaiks.module.v1\n", encoding="utf-8")

    plan = apply_workspace_upgrade(source=PackSource(directory=_version_two(tmp_path)), roots=roots).to_dict()

    assert plan["status"] == "applied"
    assert (plan["from"]["version"], plan["to"]["version"]) == ("1.0.0", "2.0.0")
    assert "modules/stock_ledger/backend/handler.py" in plan["files"]["write"]
    assert plan["files"]["remove"] == ["modules/stock_ledger/backend/legacy_import.py"]
    assert plan["files"]["keep_seed"] == ["config/stock_ledger.yaml"]
    assert plan["workflow_files"]["write"] == ["StockReview/tools/summarize.py"]
    assert {(row["key"], row["action"]) for row in plan["composed"]} >= {("stock-ledger", "replace"), ("stock_admin", "unchanged")}
    assert "seed_changed_upstream" in [warning["kind"] for warning in plan["warnings"]]
    assert seed.read_text(encoding="utf-8") == "low_stock_threshold: 2  # the shop's own choice\n"
    pages = {page["id"]: page for page in json.loads(routes.read_text(encoding="utf-8"))["pages"]}
    assert set(pages) == {"home", "about", "stock-ledger"} and pages["stock-ledger"]["title"] == "Stock"
    assert '{ "id": "about", "path": "/about", "component": "AboutPage" },' in routes.read_text(encoding="utf-8")
    assert app_module.read_text(encoding="utf-8") == "schema_version: mozaiks.module.v1\n"
    assert not (roots.app_root / "modules" / "stock_ledger" / "backend" / "legacy_import.py").exists()
    assert "count_items" in (roots.app_root / "modules" / "stock_ledger" / "module.yaml").read_text(encoding="utf-8")
    assert verify_installed_components(roots=roots)["status"] == "clean"


def test_editing_a_library_file_fails_verify_and_blocks_the_upgrade(tmp_path: Path) -> None:
    roots = _install(tmp_path)
    service = roots.app_root / "modules" / "stock_ledger" / "backend" / "service.py"
    service.write_text(service.read_text(encoding="utf-8") + "\n# local hotfix\n", encoding="utf-8")
    before = _tree(tmp_path / "shop")

    report = verify_installed_components(roots=roots)
    with pytest.raises(CommunityComponentConflictError) as blocked:
        apply_workspace_upgrade(source=PackSource(directory=_version_two(tmp_path)), roots=roots)

    assert report["status"] == "findings"
    assert report["packs"][0]["findings"] == [{"kind": "modified_owned_file", "path": "modules/stock_ledger/backend/service.py"}]
    assert {"kind": "locally_modified_owned_file", "path": "modules/stock_ledger/backend/service.py"} in blocked.value.plan.conflicts
    assert _tree(tmp_path / "shop") == before


def test_editing_a_library_entry_fails_verify_and_blocks_the_upgrade(tmp_path: Path) -> None:
    roots = _install(tmp_path)
    _edit(roots.app_root / "ui" / "route_manifest.json", '"path": "/stock"', '"path": "/inventory"')

    report = verify_installed_components(roots=roots)
    plan = plan_workspace_upgrade(source=PackSource(directory=_version_two(tmp_path)), roots=roots)

    assert report["packs"][0]["findings"] == [
        {"kind": "modified_composed_entry", "root": "ui/route_manifest.json", "key": "pages:stock-ledger"},
    ]
    assert plan.status == "blocked"
    assert plan.conflicts[0]["kind"] == "locally_modified_composed_entry"


def test_line_ending_conversion_is_not_an_edit(tmp_path: Path) -> None:
    roots = _install(tmp_path)
    handler = roots.app_root / "modules" / "stock_ledger" / "backend" / "handler.py"
    handler.write_bytes(handler.read_bytes().replace(b"\n", b"\r\n"))

    assert verify_installed_components(roots=roots)["status"] == "clean"


# --------------------------------------------------------------------------------------
# Adoption of an app that already holds the library's files
# --------------------------------------------------------------------------------------


def _pre_placed(tmp_path: Path):
    """An app that already runs the library's files and entries, as App Zero does before adoption."""
    staging = _install(tmp_path / "staging")
    roots = _ordinary_app(tmp_path / "shop")
    for base_from, base_to in ((staging.app_root, roots.app_root), (staging.workflows_root, roots.workflows_root)):
        for path in base_from.rglob("*"):
            relative = path.relative_to(base_from)
            if path.is_file() and relative.parts[0] != ".mozaiks":
                (base_to / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, base_to / relative)
    return roots


def test_install_refuses_existing_files_and_entries_without_adopt(tmp_path: Path) -> None:
    roots = _pre_placed(tmp_path)

    plan = plan_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)

    kinds = {conflict["kind"] for conflict in plan.conflicts}
    assert kinds == {"workspace_owned_file", "composed_entry_owned_by_app"}


def test_adopt_transfers_ownership_of_identical_files_without_rewriting_them(tmp_path: Path) -> None:
    roots = _pre_placed(tmp_path)
    before = _tree(tmp_path / "shop")

    plan = apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots, adopt=True).to_dict()

    assert plan["files"]["write"] == [] and plan["workflow_files"]["write"] == []
    assert "modules/stock_ledger/backend/service.py" in plan["files"]["adopt"]
    assert {row["action"] for row in plan["composed"]} == {"adopt"}
    after = _tree(tmp_path / "shop")
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    assert changed == {
        "app/.mozaiks/pack_provenance.json",
        "build_context/.mozaiks/installed_components.json",
        "build_context/.mozaiks/installed_component_sources.json",
    }
    assert verify_installed_components(roots=roots)["status"] == "clean"


def test_adopt_refuses_a_file_that_differs_from_the_library(tmp_path: Path) -> None:
    roots = _pre_placed(tmp_path)
    handler = roots.app_root / "modules" / "stock_ledger" / "backend" / "handler.py"
    handler.write_text(handler.read_text(encoding="utf-8") + "# app change\n", encoding="utf-8")

    plan = plan_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots, adopt=True)

    assert plan.status == "blocked"
    assert plan.conflicts == [{"kind": "existing_file_differs", "path": "modules/stock_ledger/backend/handler.py"}]


# --------------------------------------------------------------------------------------
# Collisions, closure and dependencies
# --------------------------------------------------------------------------------------


def test_route_path_and_collection_collisions_block_the_install(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    _edit(roots.app_root / "ui" / "route_manifest.json", '"path": "/"', '"path": "/stock"')
    _edit(roots.app_root / "data" / "contract.json", '"mongo_collection": "orders"', '"mongo_collection": "stock_items"')

    plan = plan_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)

    kinds = sorted(conflict["kind"] for conflict in plan.conflicts)
    assert kinds == ["alias_conflict", "route_path_conflict"]


def test_undeclared_host_imports_block_and_declared_ones_must_exist(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    pack = _pack(tmp_path)
    _edit(pack / "templates" / "modules" / "stock_ledger" / "backend" / "handler.py",
          "from .service import StockLedgerService\n",
          "from .service import StockLedgerService\nfrom app.services.shop_policy import allowed\n")

    undeclared = plan_component_install(source=PackSource(directory=pack), roots=roots)
    _edit(pack / "contract.yaml", "requires:\n", "requires:\n  imports:\n    - app.services.shop_policy\n")
    missing = plan_component_install(source=PackSource(directory=pack), roots=roots)
    (roots.app_root / "services").mkdir()
    (roots.app_root / "services" / "shop_policy.py").write_text("def allowed():\n    return True\n", encoding="utf-8")
    present = plan_component_install(source=PackSource(directory=pack), roots=roots)

    assert undeclared.conflicts == [{
        "kind": "undeclared_host_import", "path": "modules/stock_ledger/backend/handler.py", "key": "app.services.shop_policy",
    }]
    assert missing.conflicts == [{"kind": "missing_host_import", "key": "app.services.shop_policy"}]
    assert present.status == "ready"
    assert present.host_requirements["imports"] == [{"name": "app.services.shop_policy", "status": "present"}]


def test_required_host_modules_and_actions_are_checked(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    pack = _pack(tmp_path)
    _edit(pack / "contract.yaml", "requires:\n", "requires:\n  modules:\n    - module_id: orders\n      actions: [list_orders]\n")

    missing = plan_component_install(source=PackSource(directory=pack), roots=roots)
    module = roots.app_root / "modules" / "orders" / "module.yaml"
    module.parent.mkdir(parents=True)
    module.write_text("actions:\n  - id: create_order\n", encoding="utf-8")
    missing_action = plan_component_install(source=PackSource(directory=pack), roots=roots)

    assert missing.conflicts == [{"kind": "missing_host_module", "key": "orders"}]
    assert missing_action.conflicts == [{"kind": "missing_host_action", "key": "orders.list_orders"}]


def test_pack_dependencies_are_checked_both_ways(tmp_path: Path) -> None:
    roots = _install(tmp_path)
    reports = _pack(tmp_path, "reports")
    _edit(reports / "context.yaml", "library.stock_ledger", "library.stock_reports")
    _edit(reports / "contract.yaml", "library.stock_ledger", "library.stock_reports")
    shutil.rmtree(reports / "templates")
    shutil.rmtree(reports / "workflows")
    (reports / "templates" / "modules" / "stock_reports").mkdir(parents=True)
    (reports / "templates" / "modules" / "stock_reports" / "README.md").write_text("Reports.\n", encoding="utf-8")
    _edit(reports / "context.yaml", "  - path: workflows/\n    kind: workflows\n", "")
    _edit(reports / "contract.yaml", "requires:\n", 'requires:\n  packs:\n    - pack_id: library.stock_ledger\n      version: "1.0.0"\n')
    apply_component_install(source=PackSource(directory=reports), roots=roots)

    plan = plan_workspace_upgrade(source=PackSource(directory=_version_two(tmp_path)), roots=roots)

    assert {"kind": "required_by", "key": "library.stock_reports", "owner": "library.stock_reports", "detail": "requires 1.0.0"} in plan.conflicts
    fresh = _ordinary_app(tmp_path / "other")
    orphan = plan_component_install(source=PackSource(directory=reports), roots=fresh)
    assert {"kind": "missing_pack", "key": "library.stock_ledger"} in orphan.conflicts


def test_a_pack_that_writes_the_ui_barrel_is_refused(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    pack = _pack(tmp_path)
    (pack / "templates" / "ui" / "index.js").write_text("export function register() {}\n", encoding="utf-8")

    plan = plan_component_install(source=PackSource(directory=pack), roots=roots)

    assert plan.conflicts == [{"kind": "ui_barrel_contribution_unsupported", "path": "ui/index.js"}]


def test_an_app_without_a_workflow_registry_gets_a_warning_not_a_new_file(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop", registry=False)

    plan = apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)

    assert plan.status == "applied"
    assert "workflow_registry_absent" in [warning["kind"] for warning in plan.warnings]
    assert not (roots.workflows_root / "extended_orchestration" / "extension_registry.json").exists()
    assert verify_installed_components(roots=roots)["status"] == "clean"


# --------------------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------------------


def test_a_failed_write_restores_every_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    before = _tree(tmp_path / "shop")
    real_replace = os.replace
    calls = {"count": 0}

    def flaky_replace(source, destination):
        calls["count"] += 1
        if calls["count"] == 6:
            raise OSError("disk full")
        return real_replace(source, destination)

    monkeypatch.setattr(workspace_module.os, "replace", flaky_replace)
    with pytest.raises(OSError, match="disk full"):
        apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)

    assert _tree(tmp_path / "shop") == before
    assert not (roots.app_root / "modules").exists()
    assert not list((tmp_path / "shop").rglob("*.mozaiks-tmp"))


def test_a_held_lock_refuses_a_second_operation(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    lock = roots.build_context_root / ".mozaiks" / "lifecycle.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("12345\n", encoding="utf-8")

    with pytest.raises(CommunityComponentLockedError, match="delete that file"):
        apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    assert not (roots.app_root / "modules" / "stock_ledger").exists()


# --------------------------------------------------------------------------------------
# Immutable git sources
# --------------------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_a_git_revision_installs_the_exact_committed_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "library-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    shutil.copytree(FIXTURE, repo / "packs" / "stock_ledger")
    _git(repo, "remote", "add", "origin", "https://user:secret-token@example.com/org/library.git")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "stock ledger 1.0.0")
    commit = _git(repo, "rev-parse", "HEAD")
    # A Windows checkout with CRLF line endings would change a directory digest, never the committed one.
    service = repo / "packs" / "stock_ledger" / "templates" / "modules" / "stock_ledger" / "backend" / "service.py"
    service.write_bytes(service.read_bytes().replace(b"\n", b"\r\n"))
    roots = _ordinary_app(tmp_path / "shop")

    plan = apply_component_install(
        source=PackSource(git_repository=repo, git_revision=commit, git_path="packs/stock_ledger"), roots=roots,
    )

    assert plan.to_digest == compute_pack_digest(FIXTURE, PACK_ID)
    assert plan.source == f"git+https://example.com/org/library.git@{commit}#packs/stock_ledger"
    pins = (roots.build_context_root / ".mozaiks" / "installed_components.json").read_text(encoding="utf-8")
    assert "secret-token" not in pins and commit in pins
    sidecar = json.loads((roots.build_context_root / ".mozaiks" / "installed_component_sources.json").read_text(encoding="utf-8"))
    cached = Path(sidecar["sources"][0]["local_source_path"])
    assert cached.parent == roots.build_context_root / ".mozaiks/source-cache"
    # Short enough for Windows path limits; the full digest stays in the pin.
    assert cached.name == plan.to_digest.split(":", 1)[1][:16]
    assert compute_pack_digest(cached, PACK_ID) == plan.to_digest
    assert (cached / "templates/modules/stock_ledger/backend/service.py").read_bytes() == subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{commit}:packs/stock_ledger/templates/modules/stock_ledger/backend/service.py"],
    )


def _git_pack_source(tmp_path: Path) -> PackSource:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    shutil.copytree(FIXTURE, repo / "pack")
    _git(repo, "remote", "add", "origin", "https://example.com/library.git")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return PackSource(git_repository=repo, git_revision=_git(repo, "rev-parse", "HEAD"), git_path="pack")


def test_git_install_resolves_through_existing_lifecycle(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        resolve_installed_component_descriptors,
    )

    roots = _ordinary_app(tmp_path / "shop")
    plan = apply_component_install(source=_git_pack_source(tmp_path), roots=roots)
    [descriptor] = resolve_installed_component_descriptors(build_context_root=roots.build_context_root, selected_pack_ids=[PACK_ID])
    assert descriptor["source"] == plan.source
    assert descriptor["digest"] == plan.to_digest
    assert Path(descriptor["pack_source_path"]).is_dir()


@pytest.mark.parametrize("missing", ["sidecar", "snapshot"])
def test_unchanged_git_install_repairs_missing_source_binding(tmp_path: Path, missing: str) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        resolve_installed_component_descriptors,
    )

    roots = _ordinary_app(tmp_path / "shop")
    source = _git_pack_source(tmp_path)
    apply_component_install(source=source, roots=roots)
    before = _tree(roots.app_root)
    pin_path = roots.build_context_root / ".mozaiks/installed_components.json"
    pin_before = pin_path.read_bytes()
    sidecar = roots.build_context_root / ".mozaiks/installed_component_sources.json"
    if missing == "sidecar":
        sidecar.write_text('{"schema_version":"mozaiks.installed_component_sources.v1","sources":[]}')
    else:
        [entry] = json.loads(sidecar.read_text())["sources"]
        shutil.rmtree(Path(entry["local_source_path"]))
    assert apply_component_install(source=source, roots=roots).status == "unchanged"
    assert pin_path.read_bytes() == pin_before and _tree(roots.app_root) == before
    assert resolve_installed_component_descriptors(build_context_root=roots.build_context_root, selected_pack_ids=[PACK_ID])


def test_git_source_drift_is_refused_and_not_repaired_over(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        resolve_installed_component_descriptors,
    )
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        PackIntegrityError,
    )

    roots = _ordinary_app(tmp_path / "shop")
    source = _git_pack_source(tmp_path)
    apply_component_install(source=source, roots=roots)
    [entry] = json.loads((roots.build_context_root / ".mozaiks/installed_component_sources.json").read_text())["sources"]
    target = Path(entry["local_source_path"]) / "contract.yaml"
    target.write_bytes(target.read_bytes() + b"\n# changed\n")
    with pytest.raises(PackIntegrityError, match="no longer matches"):
        resolve_installed_component_descriptors(build_context_root=roots.build_context_root, selected_pack_ids=[PACK_ID])
    with pytest.raises(CommunityComponentLifecycleError, match="source cache has changed"):
        apply_component_install(source=source, roots=roots)
    assert target.read_bytes().endswith(b"# changed\n")


def test_unchanged_git_install_cannot_relabel_portable_source(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    source = _git_pack_source(tmp_path)
    apply_component_install(source=source, roots=roots)
    before = _tree(roots.build_context_root)
    _git(source.git_repository, "remote", "set-url", "origin", "https://example.com/another.git")
    with pytest.raises(CommunityComponentLifecycleError, match="source does not match"):
        apply_component_install(source=source, roots=roots)
    assert _tree(roots.build_context_root) == before


def test_git_source_cache_keeps_declared_empty_directories(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        resolve_installed_component_descriptors,
    )

    roots = _ordinary_app(tmp_path / "shop")
    source = _git_pack_source(tmp_path)
    pack = source.git_repository / "pack"
    context = yaml.safe_load((pack / "context.yaml").read_text())
    context["assets"].append({"path": "empty_templates", "kind": "templates"})
    (pack / "context.yaml").write_text(yaml.safe_dump(context))
    (pack / "empty_templates").mkdir()
    (pack / "empty_templates/.keep").write_bytes(b"")
    _git(source.git_repository, "add", ".")
    _git(source.git_repository, "commit", "-q", "-m", "empty declared directory")
    source = PackSource(git_repository=source.git_repository, git_revision=_git(source.git_repository, "rev-parse", "HEAD"), git_path="pack")
    apply_component_install(source=source, roots=roots)
    [descriptor] = resolve_installed_component_descriptors(build_context_root=roots.build_context_root, selected_pack_ids=[PACK_ID])
    assert (Path(descriptor["pack_source_path"]) / "empty_templates").is_dir()


def test_git_source_cache_write_failure_rolls_back_the_install(tmp_path: Path, monkeypatch) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    source = _git_pack_source(tmp_path)
    before = {root: _tree(root) for root in (roots.app_root, roots.workflows_root, roots.build_context_root)}
    replace = workspace_module.os.replace

    def fail_in_cache(src, dst):
        if "source-cache" in Path(dst).parts:
            raise OSError("cache write failed")
        return replace(src, dst)

    monkeypatch.setattr(workspace_module.os, "replace", fail_in_cache)
    with pytest.raises(OSError, match="cache write failed"):
        apply_component_install(source=source, roots=roots)
    assert all(_tree(root) == files for root, files in before.items())


def test_transaction_preserves_binary_source_bytes(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    destination = roots.build_context_root / ".mozaiks/source-cache/blob"
    payload = b"\x00\xff\r\n\x80"
    workspace_module._commit({destination: payload}, roots)
    assert destination.read_bytes() == payload


def test_a_branch_name_is_not_an_immutable_revision() -> None:
    with pytest.raises(PackSourceError, match="full commit id"):
        PackSource(git_repository=Path("."), git_revision="main", git_path="packs/stock_ledger")


def test_remote_credentials_never_reach_a_locator() -> None:
    assert sanitized_remote("https://x-access-token:abc@github.com/org/repo.git") == "https://github.com/org/repo.git"
    assert sanitized_remote("git@github.com:org/repo.git") == "git@github.com:org/repo.git"


def _commit_pack_files(repo: Path, files: dict[str, bytes]) -> str:
    """Commit ``pack/<name>`` files as raw git objects, so names this OS cannot create still reach the tree."""

    def run(*args: str, stdin: bytes = b"") -> str:
        return subprocess.run(["git", "-C", str(repo), *args], input=stdin, check=True, capture_output=True).stdout.decode().strip()

    rows = [f"100644 blob {run('hash-object', '-w', '--stdin', stdin=content)}\t{name}" for name, content in files.items()]
    pack_tree = run("mktree", "-z", stdin="".join(f"{row}\0" for row in rows).encode())
    root_tree = run("mktree", "-z", stdin=f"040000 tree {pack_tree}\tpack\0".encode())
    return run("commit-tree", root_tree, "-m", "pack")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.parametrize(
    "files",
    [
        {"evil\\..\\..\\escaped.txt": b"outside"},
        {"C:escaped.txt": b"drive-relative"},
        {"CON": b"device"},
        {"notes.": b"trailing dot"},
        {"Notes.txt": b"one", "notes.txt": b"two"},
    ],
    ids=["backslash-traversal", "drive-prefix", "device-name", "trailing-dot", "case-only-duplicate"],
)
def test_a_git_tree_with_names_windows_reads_differently_is_refused_before_any_write(
    tmp_path: Path, files: dict[str, bytes],
) -> None:
    # Git on Linux stores these names as ordinary files. Written as-is on Windows they
    # would leave the export folder, open a device, or silently overwrite each other.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    commit = _commit_pack_files(repo, files)
    export_root = tmp_path / "export"
    destination = export_root / "pack"
    destination.mkdir(parents=True)

    with pytest.raises(PackSourceError, match="not portable"):
        export_git_pack(PackSource(git_repository=repo, git_revision=commit, git_path="pack"), destination)

    assert list(export_root.rglob("*")) == [destination]


# --------------------------------------------------------------------------------------
# The installed library runs in the app's own process
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_installed_library_loads_and_enforces_its_permissions(tmp_path: Path) -> None:
    from mozaiksai.core.runtime.app.loader import AppLoader
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
    from mozaiksai.core.runtime.persistence.app_data import aliases_from_data_contract
    from mozaiksai.core.runtime.persistence.intent_loader import load_data_contract
    from mozaiksai.core.workflow.pack.config import list_workflow_ids, load_global_pack_graph
    from mozaiksai.core.workflow.paths import discover_workflow_paths
    from tests.module_authority_test_helpers import enforce_authority

    roots = _install(tmp_path)
    (roots.app_root / "config" / "stock_ledger.yaml").write_text("low_stock_threshold: 3\n", encoding="utf-8")

    loaded = await AppLoader.load(str(roots.app_root))
    [module] = [item for item in loaded.modules if item.name == "stock_ledger"]
    executor = ModuleExecutor()
    executor.register_loaded_module(module)
    allowed = await executor.execute(ModuleRequest(
        module="stock_ledger", action="list_items", app_id="shop", authority=enforce_authority("stock.read"),
    ))
    denied = await executor.execute(ModuleRequest(
        module="stock_ledger", action="record_item", app_id="shop", params={"sku": "A1"},
        authority=enforce_authority("stock.read"),
    ))

    assert loaded.failed_module_names == []
    assert allowed.success is True
    assert allowed.data == {"items": [], "low_stock_threshold": 3}
    assert denied.success is False
    assert aliases_from_data_contract(load_data_contract(roots.app_root))["stock.items"] == "stock_items"
    assert "StockReview" in discover_workflow_paths(roots.workflows_root)
    assert "StockReview" in list_workflow_ids(load_global_pack_graph(roots.workflows_root))


# --------------------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mozaiks_cli.main", "pack", *args],
        capture_output=True, text=True, env={**os.environ, "MOZAIKS_LOAD_DOTENV": "0"},
        cwd=Path(__file__).resolve().parents[1], check=False,
    )


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_reports_incomplete_rollback_without_traceback(tmp_path: Path, monkeypatch, capsys, as_json) -> None:
    from mozaiks_cli.commands import pack as pack_cli

    _ordinary_app(tmp_path / "shop")
    parser = argparse.ArgumentParser()
    pack_cli.add_parser(parser.add_subparsers())
    command = ["pack", "install", str(_pack(tmp_path)), "--workspace", str(tmp_path / "shop")]
    if as_json:
        command.append("--json")
    failure = CommunityComponentLifecycleError("Unsafe workspace path: redirected destination")
    note = "Rollback could not restore the destination: Unsafe workspace path"
    failure.add_note(note)
    cause = OSError("destination changed")

    def fail_install(**kwargs):
        raise failure from cause

    monkeypatch.setattr(workspace_module, "apply_component_install", fail_install)
    assert pack_cli.run(parser.parse_args(command)) == pack_cli.EXIT_INVALID
    output = capsys.readouterr().out
    rendered = json.loads(output)["error"] if as_json else output
    assert str(failure) in rendered and note in rendered
    assert "Traceback" not in rendered
    assert failure.__cause__ is cause


def test_the_cli_reports_plans_and_drift_with_contract_exit_codes(tmp_path: Path) -> None:
    roots = _ordinary_app(tmp_path / "shop")
    workspace = str(tmp_path / "shop")
    pack = str(_pack(tmp_path))

    dry = _cli("install", pack, "--workspace", workspace, "--dry-run", "--json")
    applied = _cli("install", pack, "--workspace", workspace, "--json")
    clean = _cli("verify", "--workspace", workspace, "--json")
    (roots.app_root / "modules" / "stock_ledger" / "module.yaml").write_text("edited\n", encoding="utf-8")
    drift = _cli("verify", "--workspace", workspace)
    blocked = _cli("upgrade", str(_version_two(tmp_path)), "--workspace", workspace, "--json")
    invalid = _cli("status", "--workspace", str(tmp_path / "nowhere"))

    assert (dry.returncode, json.loads(dry.stdout)["status"]) == (0, "ready")
    assert (applied.returncode, json.loads(applied.stdout)["status"]) == (0, "applied")
    assert (clean.returncode, json.loads(clean.stdout)["status"]) == (0, "clean")
    assert drift.returncode == 1 and "modified_owned_file modules/stock_ledger/module.yaml" in drift.stdout
    assert blocked.returncode == 1 and json.loads(blocked.stdout)["status"] == "blocked"
    assert invalid.returncode == 2 and "app.json not found" in invalid.stdout


# --------------------------------------------------------------------------------------
# The library's workflow tool calls its module in process, with the live session's authority
# --------------------------------------------------------------------------------------


async def _live_stock_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, scopes: list[str], surface=None):
    """Wire the installed library's shared workflow tool to a live socket principal, as a workflow run does."""
    import importlib.util
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from starlette.websockets import WebSocketState

    from mozaiksai.core.auth.adapters.base import UserClaims
    from mozaiksai.core.auth.websocket_auth import WebSocketUser
    from mozaiksai.core.runtime.app.loader import AppLoader
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import module_tools
    from mozaiksai.core.workflow.agents.factory import (
        ContextVariablesBridge,
        _wrap_tool_with_context,
    )
    from mozaiksai.core.workflow.context.authority import build_context_authority_policy
    from mozaiksai.core.workflow.context.schema import load_context_variables_config

    roots = _install(tmp_path)
    loaded = await AppLoader.load(str(roots.app_root))
    [module] = [item for item in loaded.modules if item.name == "stock_ledger"]
    executor = ModuleExecutor()
    executor.register_loaded_module(module)
    surfaces = {action.id: action.api_surface for action in module.definition.actions}
    if surface is not None:
        surfaces["list_items"] = surface
    principal = WebSocketUser.from_claims(UserClaims(
        user_id="clerk_1", app_id="shop", tenant_id="tenant_1", workspace_id="workspace_1",
        scopes=scopes, provider="keycloak", raw_claims={"exp": 9_999_999_999},
    ))
    app = SimpleNamespace(state=SimpleNamespace(
        executor_registry=SimpleNamespace(module_executor=executor),
        module_action_surfaces={"stock_ledger": surfaces},
    ))
    socket = SimpleNamespace(
        state=SimpleNamespace(user=principal), app=app,
        client_state=WebSocketState.CONNECTED, application_state=WebSocketState.CONNECTED,
    )
    transport = SimpleNamespace(connections={"chat_1": {"websocket": socket, "active": True, "app_id": "shop", "user_id": "clerk_1"}})
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(module_tools, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(module_tools, "get_platform_hooks", PlatformHookRegistry)
    policy = build_context_authority_policy(workflow_name="StockReview", definitions=load_context_variables_config({}).definitions)
    bridge = ContextVariablesBridge({"app_id": "shop", "user_id": "clerk_1"}, authority_policy=policy)
    bridge._bind_run(("StockReview", "shop", "chat_1"), policy)
    spec = importlib.util.spec_from_file_location(
        "installed_stock_lookup", roots.workflows_root / "_shared" / "tools" / "stock_lookup.py",
    )
    tool_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool_module)
    return _wrap_tool_with_context(tool_module.stock_lookup, bridge)


@pytest.mark.asyncio
async def test_the_installed_workflow_tool_reads_through_the_module_with_the_session_principal(tmp_path, monkeypatch) -> None:
    tool = await _live_stock_lookup(tmp_path, monkeypatch, scopes=["stock.read"])

    assert json.loads(await tool()) == {"status": "success", "data": {"items": [], "low_stock_threshold": 5}}


@pytest.mark.asyncio
async def test_a_denied_workflow_read_is_reported_never_returned_as_empty_data(tmp_path, monkeypatch) -> None:
    tool = await _live_stock_lookup(tmp_path, monkeypatch, scopes=[])

    result = json.loads(await tool())

    assert result["status"] == "error" and "stock.read" in result["error"]
    assert "data" not in result


@pytest.mark.asyncio
async def test_operator_only_actions_are_not_dispatchable_from_workflows_yet(tmp_path, monkeypatch) -> None:
    tool = await _live_stock_lookup(tmp_path, monkeypatch, scopes=["stock.read"], surface="admin_internal")

    assert json.loads(await tool()) == {"status": "error", "error": "workflow_module_action_unavailable"}


@pytest.mark.asyncio
async def test_workflow_context_cannot_grant_a_permission(tmp_path, monkeypatch) -> None:
    from mozaiksai.core.workflow.context.authority import ContextAuthorityError

    tool = await _live_stock_lookup(tmp_path, monkeypatch, scopes=[])

    with pytest.raises(ContextAuthorityError):
        await tool(context_variables={"permissions": ["stock.read"]})


# --------------------------------------------------------------------------------------
# A pack AppGenerator materialized keeps the base lifecycle's upgrade path
# --------------------------------------------------------------------------------------


def test_a_generator_materialized_pack_upgrades_through_the_base_lifecycle(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        install_verified_local_pack,
    )
    from tests.test_community_component_lifecycle import (
        GREETINGS,
        _greetings_v2,
        _write_app_root_from_pack,
    )

    roots = _ordinary_app(tmp_path / "shop")
    install_verified_local_pack(pack_source_path=GREETINGS, build_context_root=roots.build_context_root)
    _write_app_root_from_pack(roots.app_root, GREETINGS, "greetings")
    candidate = _greetings_v2(tmp_path)

    plan = plan_workspace_upgrade(source=PackSource(directory=candidate), roots=roots).to_dict()
    report = verify_installed_components(roots=roots)
    [status] = installed_component_status(roots=roots)["packs"]
    applied = apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)

    assert plan["status"] == "ready" and plan["to"]["version"] == "0.2.0"
    assert "modules/greetings/backend/service.py" in plan["files"]["write"]
    assert [warning["kind"] for warning in plan["warnings"]] == ["generator_provenance"]
    assert report == {"status": "clean", "packs": [{"pack_id": "greetings", "version": "0.1.0", "findings": [], "verifiable": False}]}
    assert status["ownership"] == "generator"
    assert applied.status == "applied"
    pins = json.loads((roots.build_context_root / ".mozaiks" / "installed_components.json").read_text(encoding="utf-8"))
    assert pins["components"][0]["version"] == "0.2.0"


@pytest.mark.parametrize("operation", ["install", "upgrade"])
def test_registry_reference_closure_blocks_before_writing(tmp_path, operation):
    roots = _ordinary_app(tmp_path / "shop")
    if operation == "upgrade":
        apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
        registry = roots.workflows_root / "extended_orchestration/extension_registry.json"
        doc = json.loads(registry.read_text(encoding="utf-8"))
        doc["entrypoints"] = [{"id": "app-review", "path": "/review", "workflow": "StockReview"}]
        registry.write_text(json.dumps(doc), encoding="utf-8")
        pack = _version_two(tmp_path)
        (pack / "workflows/StockReview").rename(pack / "workflows/StockReviewV2")
        _edit(pack / "workflows/StockReviewV2/orchestrator.yaml", "workflow_name: StockReview", "workflow_name: StockReviewV2")
        _edit(pack / "workflows/extended_orchestration/extension_registry.json", "StockReview", "StockReviewV2")
    else:
        pack = _pack(tmp_path)
        registry = pack / "workflows/extended_orchestration/extension_registry.json"
        doc = json.loads(registry.read_text(encoding="utf-8"))
        doc["entrypoints"] = [{"id": "orphan", "path": "/orphan", "workflow": "DoesNotExist"}]
        registry.write_text(json.dumps(doc), encoding="utf-8")
    before = _tree(tmp_path / "shop")
    planner = plan_workspace_upgrade if operation == "upgrade" else plan_component_install
    apply = apply_workspace_upgrade if operation == "upgrade" else apply_component_install
    plan = planner(source=PackSource(directory=pack), roots=roots)
    assert plan.status == "blocked"
    assert any(row["kind"] == "invalid_workflow_registry" for row in plan.conflicts)
    with pytest.raises(CommunityComponentConflictError):
        apply(source=PackSource(directory=pack), roots=roots)
    assert _tree(tmp_path / "shop") == before


def test_verify_reports_an_invalid_composed_workflow_graph(tmp_path):
    roots = _install(tmp_path)
    registry = roots.workflows_root / "extended_orchestration/extension_registry.json"
    doc = json.loads(registry.read_text(encoding="utf-8"))
    doc["entrypoints"] = [{"id": "orphan", "path": "/orphan", "workflow": "DoesNotExist"}]
    registry.write_text(json.dumps(doc), encoding="utf-8")
    report = verify_installed_components(roots=roots)
    assert report["status"] == "findings"
    assert any(row["kind"] == "invalid_workflow_registry" for row in report["packs"][0]["findings"])


def test_upgrade_keeps_missing_workflow_registry_ownership_visible(tmp_path):
    roots = _install(tmp_path)
    (roots.workflows_root / "extended_orchestration/extension_registry.json").unlink()
    before = _tree(tmp_path / "shop")
    candidate = PackSource(directory=_version_two(tmp_path))
    plan = plan_workspace_upgrade(source=candidate, roots=roots)
    assert plan.status == "blocked"
    assert any(row["kind"] == "locally_modified_composed_entry" for row in plan.conflicts)
    with pytest.raises(CommunityComponentConflictError):
        apply_workspace_upgrade(source=candidate, roots=roots)
    assert _tree(tmp_path / "shop") == before
    assert verify_installed_components(roots=roots)["status"] == "findings"


@pytest.mark.parametrize("requirement", ["action", "import"])
def test_upgrade_preserves_installed_dependents_host_requirements(tmp_path, requirement):
    roots = _install(tmp_path)
    dependent = _capability_pack(tmp_path, "reports")
    requires = ({"modules": [{"module_id": "stock_ledger", "actions": ["record_item"]}]}
                if requirement == "action" else
                {"imports": ["app.modules.stock_ledger.backend.legacy_import"]})
    contract = dependent / "contract.yaml"
    doc = yaml.safe_load(contract.read_text(encoding="utf-8"))
    doc["requires"] = requires
    contract.write_text(yaml.safe_dump(doc), encoding="utf-8")
    apply_component_install(source=PackSource(directory=dependent), roots=roots)
    pins = json.loads((roots.build_context_root / ".mozaiks/installed_components.json").read_text(encoding="utf-8"))
    pin = next(item for item in pins["components"] if item["pack_id"] == "library.reports")
    assert pin["host_requirements"]["modules" if requirement == "action" else "imports"]
    candidate = _version_two(tmp_path)
    if requirement == "action":
        manifest = candidate / "templates/modules/stock_ledger/module.yaml"
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        doc["actions"] = [row for row in doc["actions"] if row["id"] != "record_item"]
        manifest.write_text(yaml.safe_dump(doc), encoding="utf-8")
    before = _tree(tmp_path / "shop")
    plan = plan_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    assert plan.status == "blocked"
    assert any(row["kind"] == "required_by" and row["owner"] == "library.reports" for row in plan.conflicts)
    with pytest.raises(CommunityComponentConflictError):
        apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    assert _tree(tmp_path / "shop") == before
    assert verify_installed_components(roots=roots)["status"] == "clean"
    # Verification also checks live host requirements, independently of an upgrade.
    if requirement == "action":
        manifest = roots.app_root / "modules/stock_ledger/module.yaml"
        doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        doc["actions"] = [row for row in doc["actions"] if row["id"] != "record_item"]
        manifest.write_text(yaml.safe_dump(doc), encoding="utf-8")
    else:
        (roots.app_root / "modules/stock_ledger/backend/legacy_import.py").unlink()
    report = verify_installed_components(roots=roots)
    findings = next(row["findings"] for row in report["packs"] if row["pack_id"] == "library.reports")
    assert findings and findings[0]["kind"] in {"missing_host_action", "missing_host_import"}


@pytest.mark.parametrize("invalid", ["missing_agents", "unknown_initial"])
def test_invalid_workflow_agents_are_rejected_before_install(tmp_path, invalid):
    roots = _ordinary_app(tmp_path / "shop")
    pack = _pack(tmp_path)
    if invalid == "missing_agents":
        (pack / "workflows/StockReview/agents.yaml").unlink()
    else:
        _edit(pack / "workflows/StockReview/orchestrator.yaml", "initial_agent: StockReviewAgent", "initial_agent: Missing")
    before = _tree(tmp_path / "shop")
    with pytest.raises(CommunityComponentLifecycleError, match="workflow StockReview is invalid"):
        apply_component_install(source=PackSource(directory=pack), roots=roots)
    assert _tree(tmp_path / "shop") == before
    assert not roots.build_context_root.exists()


def test_plain_plan_lists_permissions_host_requirements_and_dependencies(tmp_path, capsys):
    from mozaiks_cli.commands.pack import _print_plan

    roots = _install(tmp_path)
    dependent = _capability_pack(tmp_path, "reports", requires="stock.ledger")
    plan = plan_component_install(source=PackSource(directory=dependent), roots=roots).to_dict()
    _print_plan(plan)
    assert "dependency added_capabilities" in capsys.readouterr().out
    fresh = _ordinary_app(tmp_path / "other")
    _print_plan(plan_component_install(source=PackSource(directory=_pack(tmp_path, "fresh")), roots=fresh).to_dict())
    output = capsys.readouterr().out
    assert "permission stock.read" in output and "requires secrets" in output


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_returns_invalid_for_operating_system_failures(tmp_path, monkeypatch, capsys, as_json):
    from mozaiks_cli.commands import pack as pack_cli

    _ordinary_app(tmp_path / "shop")
    parser = argparse.ArgumentParser()
    pack_cli.add_parser(parser.add_subparsers())
    command = ["pack", "install", str(_pack(tmp_path)), "--workspace", str(tmp_path / "shop")]
    if as_json:
        command.append("--json")
    failure = OSError("disk full")
    failure.add_note("Rollback could not restore a file")
    def fail(**kwargs):
        raise failure
    monkeypatch.setattr(workspace_module, "apply_component_install", fail)
    assert pack_cli.run(parser.parse_args(command)) == pack_cli.EXIT_INVALID
    output = capsys.readouterr().out
    assert "disk full" in output and "Rollback could not restore" in output
    if as_json:
        assert json.loads(output)["status"] == "invalid"


@pytest.mark.parametrize("dry_run", [False, True])
def test_generator_materialized_conflicts_keep_blocked_exit_code(tmp_path, dry_run):
    from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
        install_verified_local_pack,
    )
    from tests.test_community_component_lifecycle import (
        GREETINGS,
        _greetings_v2,
        _write_app_root_from_pack,
    )

    roots = _ordinary_app(tmp_path / "shop")
    install_verified_local_pack(pack_source_path=GREETINGS, build_context_root=roots.build_context_root)
    _write_app_root_from_pack(roots.app_root, GREETINGS, "greetings")
    service = roots.app_root / "modules/greetings/backend/service.py"
    service.write_text(service.read_text(encoding="utf-8") + "# local edit\n", encoding="utf-8")
    before = _tree(tmp_path / "shop")
    command = ["upgrade", str(_greetings_v2(tmp_path)), "--workspace", str(tmp_path / "shop"), "--json"]
    if dry_run:
        command.append("--dry-run")
    result = _cli(*command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "blocked"
    assert _tree(tmp_path / "shop") == before


@pytest.mark.parametrize("module_id", ["host-ledger", "host_ledger", "7host"])
def test_host_requirement_pins_preserve_existing_module_identifier_contract(tmp_path, module_id):
    roots = _ordinary_app(tmp_path / "shop")
    module = roots.app_root / "modules" / module_id / "module.yaml"
    module.parent.mkdir(parents=True)
    module.write_text("actions:\n  - id: lookup\n", encoding="utf-8")
    pack = _pack(tmp_path)
    contract = pack / "contract.yaml"
    document = yaml.safe_load(contract.read_text(encoding="utf-8"))
    document["requires"]["modules"] = [{"module_id": module_id, "actions": ["lookup"]}]
    contract.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert apply_component_install(source=PackSource(directory=pack), roots=roots).status == "applied"
    pins = json.loads((roots.build_context_root / ".mozaiks/installed_components.json").read_text(encoding="utf-8"))
    assert pins["components"][0]["host_requirements"]["modules"] == [{"module_id": module_id, "actions": ["lookup"]}]
    assert verify_installed_components(roots=roots)["status"] == "clean"


@pytest.mark.parametrize("operation", ["install", "upgrade"])
def test_registry_workflow_source_must_exist_after_the_change(tmp_path, operation):
    roots = _ordinary_app(tmp_path / "shop")
    if operation == "upgrade":
        apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
        pack = _version_two(tmp_path)
        # The registry keeps StockReview, but the candidate removes its files.
        shutil.rmtree(pack / "workflows/StockReview")
    else:
        pack = _pack(tmp_path)
        registry = pack / "workflows/extended_orchestration/extension_registry.json"
        doc = {"workflows": [{"id": "PhantomReview"}],
               "entrypoints": [{"id": "phantom", "path": "/phantom", "workflow": "PhantomReview"}]}
        registry.write_text(json.dumps(doc), encoding="utf-8")
    before = _tree(tmp_path / "shop")
    planner = plan_workspace_upgrade if operation == "upgrade" else plan_component_install
    apply = apply_workspace_upgrade if operation == "upgrade" else apply_component_install
    source = PackSource(directory=pack)
    plan = planner(source=source, roots=roots)
    assert plan.status == "blocked"
    assert any(row["kind"] == "missing_workflow_source" for row in plan.conflicts)
    with pytest.raises(CommunityComponentConflictError):
        apply(source=source, roots=roots)
    assert _tree(tmp_path / "shop") == before


def test_verify_reports_a_missing_registered_workflow_source(tmp_path):
    roots = _install(tmp_path)
    (roots.workflows_root / "StockReview/orchestrator.yaml").unlink()
    report = verify_installed_components(roots=roots)
    assert report["status"] == "findings"
    assert any(row["kind"] == "missing_workflow_source" for row in report["packs"][0]["findings"])


def test_registry_source_closure_preserves_packaged_default_workflows(tmp_path):
    from mozaiksai.core.workflow.pack.config import load_global_pack_graph
    from mozaiksai.core.workflow.paths import resolve_workflow_path

    roots = _ordinary_app(tmp_path / "shop")
    default_graph = load_global_pack_graph(roots.workflows_root)
    inherited = next(row.id for row in default_graph.workflows if row.startup_mode != "BackendOnly")
    assert resolve_workflow_path(inherited, roots.workflows_root) is not None
    assert not (roots.workflows_root / inherited).exists()
    pack = _pack(tmp_path)
    registry = pack / "workflows/extended_orchestration/extension_registry.json"
    doc = json.loads(registry.read_text(encoding="utf-8"))
    doc["entrypoints"] = [{"id": "default-review", "path": "/default-review", "workflow": inherited}]
    registry.write_text(json.dumps(doc), encoding="utf-8")
    assert apply_component_install(source=PackSource(directory=pack), roots=roots).status == "applied"
    assert verify_installed_components(roots=roots)["status"] == "clean"
