"""No pack operation deletes or overwrites what another pack holds.

Two lifecycles write one app: libraries (``mozaiks pack``) and packs AppGenerator
materialized. A file can be recorded by several packs: one seeds it, another
adopts it. Dropping a file from a pack gives up that pack's record and nothing
else, as it does for a seed, so a removal never blocks and never deletes what
another pack records. Overwriting is refused where another pack owns the content.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.community_component_composition import (
    content_sha256,
    entry_sha256,
)
from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
    CommunityComponentConflictError,
    apply_component_upgrade,
    install_verified_local_pack,
    plan_component_upgrade,
)
from factory_app.workflows.AppGenerator.tools.community_component_sources import (
    PackSource,
)
from factory_app.workflows.AppGenerator.tools.community_component_state import (
    installed_component_sources_path,
)
from factory_app.workflows.AppGenerator.tools.community_component_workspace import (
    apply_component_install,
    apply_workspace_upgrade,
    plan_component_install,
    plan_workspace_upgrade,
    verify_installed_components,
    workspace_roots,
)
from tests.test_pack_lifecycle_existing_app import (
    PACK_ID,
    _capability_pack,
    _edit,
    _file_pack,
    _generator_pack,
    _materialize,
    _orders_module,
    _ordinary_app,
    _pack,
    _tree,
    _version_two,
)

HELD = "services/shared.yaml"
HELD_WORKFLOW = "_shared/shared.yaml"
TEXT = "shared: content\n"
CHANGED = "shared: changed\n"


def _generator_file_pack(tmp_path: Path, version: str, *, path: str | None, text: str = TEXT, owner: str = "templates") -> Path:
    pack = _generator_pack(tmp_path, version, modules=("ledger",))
    if path is not None:
        (pack / "templates" / path).parent.mkdir(parents=True, exist_ok=True)
        (pack / "templates" / path).write_text(text, encoding="utf-8")
        contract = yaml.safe_load((pack / "contract.yaml").read_text(encoding="utf-8"))
        contract["required_outputs"].append({"path": path, "owner": owner})
        (pack / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")
    return pack


def _library_file_pack(tmp_path: Path, version: str, *, path: str | None, text: str = TEXT, owner: str = "templates", workflow: bool) -> Path:
    if path is None:
        pack = _capability_pack(tmp_path / version, "actor")
    else:
        pack = _file_pack(tmp_path / version, "actor", path, owner=owner, workflow=workflow)
        (pack / ("workflows" if workflow else "templates") / path).write_text(text, encoding="utf-8")
    _edit(pack / "context.yaml", "version: 1.0.0", f"version: {version}")
    return pack


def _provenance(roots) -> Path:
    return roots.app_root / ".mozaiks/pack_provenance.json"


def _record_holder(roots, tmp_path: Path, holder: str, path: str, *, workflow: bool) -> None:
    """Make another pack hold the file, the way each lifecycle records it."""
    if holder == "sidecar-only":
        # Pinned, with a bound source, and absent from provenance.
        install_verified_local_pack(
            pack_source_path=_file_pack(tmp_path / "holder", "holder", path, workflow=workflow), build_context_root=roots.build_context_root,
        )
        return
    lifecycle, record = holder.split("-")
    document = json.loads(_provenance(roots).read_text(encoding="utf-8"))
    row = {"path": path, "owner": "workspace" if record == "seed" else "templates"}
    if lifecycle == "library":
        row["sha256"] = content_sha256(TEXT)
    entry = {
        "pack_id": "library.holder", "version": "1.0.0", "source": "local", "digest": "sha256:" + "a" * 64,
        "materialized_owned_files": [] if workflow else [row],
    }
    if lifecycle == "library":
        entry["ownership"] = "content_hash"
    if workflow:
        entry["materialized_workflow_files"] = [row]
    document["packs"].append(entry)
    _provenance(roots).write_text(json.dumps(document), encoding="utf-8")


ACTORS = ["library-owned", "library-seed", "generator-owned", "generator-seed", "library-workflow"]
HOLDERS = ["library-owned", "library-seed", "generator-owned", "generator-seed", "sidecar-only"]
CHANGES = ["remove", "replace", "respell"]


def _expected(actor: str, holder: str, change: str) -> tuple[str, str]:
    """(what happens to the plan, what happens to the file)."""
    lifecycle, record = actor.split("-")
    if record == "seed" and lifecycle == "library":
        # A seed is the app's from the moment it is written.
        return ("blocked", "kept") if change == "respell" else ("ready", "kept")
    if change == "remove":
        return "ready", "kept"
    if change == "respell":
        return "blocked", "kept"
    if holder == "sidecar-only":
        # The actor's own record is the stronger claim to the content it wrote.
        return "ready", "replaced"
    if lifecycle == "library" and holder.endswith("seed") and record != "workflow":
        # The actor adopted a file the other pack only ever seeded.
        return "ready", "replaced"
    return "blocked", "kept"


@pytest.mark.parametrize("change", CHANGES)
@pytest.mark.parametrize("holder", HOLDERS)
@pytest.mark.parametrize("actor", ACTORS)
def test_an_upgrade_leaves_what_another_pack_holds(tmp_path, actor, holder, change):
    lifecycle, record = actor.split("-")
    workflow = record == "workflow"
    if workflow and holder.endswith("seed"):
        pytest.skip("workflow files are never seeded")
    owner = "workspace" if record == "seed" else "templates"
    path = HELD_WORKFLOW if workflow else HELD
    new = {"remove": None, "replace": path, "respell": path.replace("shared.yaml", "Shared.yaml")}[change]
    roots = _ordinary_app(tmp_path / "shop")
    if lifecycle == "generator":
        _materialize(roots, _generator_file_pack(tmp_path, "1.0.0", path=path, owner=owner))
        candidate = _generator_file_pack(tmp_path, "2.0.0", path=new, text=CHANGED if change == "replace" else TEXT, owner=owner)
        pack_id = "library.generated"
    else:
        apply_component_install(source=PackSource(directory=_library_file_pack(
            tmp_path, "1.0.0", path=path, owner=owner, workflow=workflow,
        )), roots=roots)
        candidate = _library_file_pack(
            tmp_path, "2.0.0", path=new, text=CHANGED if change == "replace" else TEXT, owner=owner, workflow=workflow,
        )
        pack_id = "library.actor"
    _record_holder(roots, tmp_path, holder, path, workflow=workflow)
    target = (roots.workflows_root if workflow else roots.app_root) / path
    source = PackSource(directory=candidate)
    before = _tree(tmp_path / "shop")
    status, file = _expected(actor, holder, change)

    plan = plan_workspace_upgrade(source=source, roots=roots)
    assert _tree(tmp_path / "shop") == before
    assert plan.status == status, plan.conflicts
    if status == "blocked":
        with pytest.raises(CommunityComponentConflictError):
            apply_workspace_upgrade(source=source, roots=roots)
        assert _tree(tmp_path / "shop") == before
    else:
        assert apply_workspace_upgrade(source=source, roots=roots).status == "applied"
    assert target.read_text(encoding="utf-8") == (CHANGED if file == "replaced" else TEXT)
    if change == "remove" and actor != "library-seed":
        [warning] = [row for row in plan.warnings if row["kind"] == "kept_for_other_pack"]
        assert path in warning["detail"] and "library.holder" in warning["detail"]
    # The holder's own record is untouched, and the actor claims the file only while it ships it.
    packs = {row["pack_id"]: row for row in json.loads(_provenance(roots).read_text(encoding="utf-8"))["packs"]}
    if holder != "sidecar-only":
        held = packs["library.holder"]
        assert [row["path"] for row in held.get("materialized_workflow_files" if workflow else "materialized_owned_files")] == [path]
    if status == "ready" and change == "remove":
        rows = [*packs[pack_id]["materialized_owned_files"], *packs[pack_id].get("materialized_workflow_files", [])]
        assert path not in {row["path"] for row in rows}
    for report in verify_installed_components(roots=roots)["packs"]:
        if report["pack_id"] == "library.holder":
            assert [row for row in report["findings"] if row["kind"] in {"missing_owned_file", "modified_owned_file"}] == []


@pytest.mark.parametrize("actor", ["library-owned", "generator-owned", "library-workflow"])
@pytest.mark.parametrize("change", ["remove", "replace"])
def test_an_upgrade_still_changes_what_only_its_own_pack_holds(tmp_path, actor, change):
    lifecycle, record = actor.split("-")
    workflow = record == "workflow"
    path = HELD_WORKFLOW if workflow else HELD
    new = None if change == "remove" else path
    roots = _ordinary_app(tmp_path / "shop")
    if lifecycle == "generator":
        _materialize(roots, _generator_file_pack(tmp_path, "1.0.0", path=path))
        candidate = _generator_file_pack(tmp_path, "2.0.0", path=new, text=CHANGED)
    else:
        first = _library_file_pack(tmp_path, "1.0.0", path=path, workflow=workflow)
        apply_component_install(source=PackSource(directory=first), roots=roots)
        candidate = _library_file_pack(tmp_path, "2.0.0", path=new, text=CHANGED, workflow=workflow)
    plan = apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    assert [row for row in plan.warnings if row["kind"] == "kept_for_other_pack"] == []
    target = (roots.workflows_root if workflow else roots.app_root) / path
    assert target.exists() is (change == "replace")


# --------------------------------------------------------------------------------------
# Whole sequences
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("through", ["workspace", "base"])
@pytest.mark.parametrize("seeded_by", ["generator", "library"])
def test_a_file_one_pack_seeds_and_another_adopts_survives_the_first_dropping_it(tmp_path, seeded_by, through):
    if seeded_by == "library" and through == "base":
        pytest.skip("the base lifecycle upgrades generator packs only")
    roots = _ordinary_app(tmp_path / "shop")
    if seeded_by == "generator":
        _materialize(roots, _generator_file_pack(tmp_path, "1.0.0", path=HELD, owner="workspace"))
        candidate = _generator_file_pack(tmp_path, "2.0.0", path=None)
    else:
        seed = _library_file_pack(tmp_path, "1.0.0", path=HELD, owner="workspace", workflow=False)
        apply_component_install(source=PackSource(directory=seed), roots=roots)
        candidate = _library_file_pack(tmp_path, "2.0.0", path=None, workflow=False)
    adopter = _file_pack(tmp_path, "adopter", HELD)
    apply_component_install(source=PackSource(directory=adopter), roots=roots, adopt=True)
    if through == "base":
        arguments = {
            "build_context_root": roots.build_context_root, "candidate_pack_source_path": candidate, "workspace_app_root": roots.app_root,
        }
        plan = plan_component_upgrade(**arguments)
        assert (plan.status, plan.removed_files) == ("ready", [])
        assert plan.released_files == [{"path": HELD, "owner": "library.adopter"}]
        apply_component_upgrade(**arguments)
    else:
        assert apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots).status == "applied"
    assert (roots.app_root / HELD).read_text(encoding="utf-8") == TEXT
    assert verify_installed_components(roots=roots)["status"] == "clean"
    # The adopter still owns the file: it can change and then remove it.
    changed = _file_pack(tmp_path / "v2", "adopter", HELD)
    _edit(changed / "context.yaml", "version: 1.0.0", "version: 2.0.0")
    (changed / "templates" / HELD).write_text(CHANGED, encoding="utf-8")
    apply_workspace_upgrade(source=PackSource(directory=changed), roots=roots)
    assert (roots.app_root / HELD).read_text(encoding="utf-8") == CHANGED
    dropped = _capability_pack(tmp_path / "v3", "adopter")
    _edit(dropped / "context.yaml", "version: 1.0.0", "version: 3.0.0")
    apply_workspace_upgrade(source=PackSource(directory=dropped), roots=roots)
    assert not (roots.app_root / HELD).exists()


def test_an_adopter_that_drops_a_file_leaves_the_seed_another_pack_records(tmp_path):
    roots = _ordinary_app(tmp_path / "shop")
    _materialize(roots, _generator_file_pack(tmp_path, "1.0.0", path=HELD, owner="workspace"))
    apply_component_install(source=PackSource(directory=_file_pack(tmp_path, "adopter", HELD)), roots=roots, adopt=True)
    dropped = _capability_pack(tmp_path / "v2", "adopter")
    _edit(dropped / "context.yaml", "version: 1.0.0", "version: 2.0.0")
    apply_workspace_upgrade(source=PackSource(directory=dropped), roots=roots)
    assert (roots.app_root / HELD).read_text(encoding="utf-8") == TEXT
    assert verify_installed_components(roots=roots)["status"] == "clean"


@pytest.mark.parametrize("owner", ["generator", "library"])
def test_adoption_never_takes_a_file_another_pack_owns(tmp_path, owner):
    roots = _ordinary_app(tmp_path / "shop")
    if owner == "generator":
        _materialize(roots, _generator_file_pack(tmp_path, "1.0.0", path=HELD))
    else:
        owned = _library_file_pack(tmp_path, "1.0.0", path=HELD, workflow=False)
        apply_component_install(source=PackSource(directory=owned), roots=roots)
    for kind in ("templates", "workspace"):
        pack = _file_pack(tmp_path / kind, "adopter", HELD, owner=kind)
        plan = plan_component_install(source=PackSource(directory=pack), roots=roots, adopt=True)
        assert [row["kind"] for row in plan.conflicts] == ["owned_by_other_pack"]


@pytest.mark.parametrize("workflow", [False, True], ids=["app", "workflow"])
def test_adoption_never_takes_a_file_of_a_pinned_pack_without_provenance(tmp_path, workflow):
    roots = _ordinary_app(tmp_path / "shop")
    path = HELD_WORKFLOW if workflow else HELD
    owner = _library_file_pack(tmp_path, "1.0.0", path=path, workflow=workflow)
    apply_component_install(source=PackSource(directory=owner), roots=roots)
    _provenance(roots).unlink()
    adopter = _file_pack(tmp_path, "adopter", path, workflow=workflow)
    plan = plan_component_install(source=PackSource(directory=adopter), roots=roots, adopt=True)
    assert [(row["kind"], row["owner"]) for row in plan.conflicts] == [("owned_by_other_pack", "library.actor")]


def test_a_pinned_pack_that_was_never_materialized_does_not_block_an_install(tmp_path):
    roots = _ordinary_app(tmp_path / "shop")
    unselected = _file_pack(tmp_path / "unselected", "unselected", HELD)
    install_verified_local_pack(pack_source_path=unselected, build_context_root=roots.build_context_root)
    assert apply_component_install(source=PackSource(directory=_file_pack(tmp_path, "other", HELD)), roots=roots).status == "applied"


def test_one_file_reached_through_both_roots_has_one_owner(tmp_path):
    roots = _flows_inside_the_app(tmp_path)
    as_workflow = _library_file_pack(tmp_path, "1.0.0", path=HELD_WORKFLOW, workflow=True)
    apply_component_install(source=PackSource(directory=as_workflow), roots=roots)
    as_file = _file_pack(tmp_path, "adopter", f"flows/{HELD_WORKFLOW}")
    plan = plan_component_install(source=PackSource(directory=as_file), roots=roots, adopt=True)
    assert [(row["kind"], row["owner"]) for row in plan.conflicts] == [("owned_by_other_pack", "library.actor")]


def _flows_inside_the_app(tmp_path: Path):
    base = _ordinary_app(tmp_path / "shop")
    shutil.move(base.workflows_root, base.app_root / "flows")
    return workspace_roots(tmp_path / "shop", workflows_root=base.app_root / "flows")


@pytest.mark.parametrize("change", ["remove", "replace"])
def test_a_generator_pack_leaves_a_workflow_file_inside_the_app_to_its_owner(tmp_path, change):
    roots = _flows_inside_the_app(tmp_path)
    path = f"flows/{HELD_WORKFLOW}"
    _materialize(roots, _generator_file_pack(tmp_path, "1.0.0", path=path))
    _record_holder(roots, tmp_path, "library-owned", HELD_WORKFLOW, workflow=True)
    candidate = _generator_file_pack(tmp_path, "2.0.0", path=None if change == "remove" else path, text=CHANGED)
    plan = plan_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    if change == "replace":
        assert [(row["kind"], row["owner"]) for row in plan.conflicts] == [("owned_by_other_pack", "library.holder")]
    else:
        assert apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots).status == "applied"
    assert (roots.workflows_root / HELD_WORKFLOW).read_text(encoding="utf-8") == TEXT


@pytest.mark.parametrize("record", ["generator-owned", "generator-seed"])
def test_a_library_leaves_a_workflow_file_another_pack_records_as_an_app_file(tmp_path, record):
    roots = _flows_inside_the_app(tmp_path)
    first = _library_file_pack(tmp_path, "1.0.0", path=HELD_WORKFLOW, workflow=True)
    apply_component_install(source=PackSource(directory=first), roots=roots)
    _record_holder(roots, tmp_path, record, f"flows/{HELD_WORKFLOW}", workflow=False)
    candidate = _library_file_pack(tmp_path, "2.0.0", path=None, workflow=True)
    assert apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots).status == "applied"
    assert (roots.workflows_root / HELD_WORKFLOW).read_text(encoding="utf-8") == TEXT


# --------------------------------------------------------------------------------------
# Entries in shared files
# --------------------------------------------------------------------------------------


CONTRIBUTIONS = {
    "data/contract.json": ("templates/data/contract.json", ("surfaces", "stock_ledger")),
    "ui/route_manifest.json": ("templates/ui/route_manifest.json", ("pages", "stock-ledger")),
    "admin/admin_registry.yaml": ("templates/admin/admin_registry.yaml", None),
    "extended_orchestration/extension_registry.json": (
        "workflows/extended_orchestration/extension_registry.json", ("workflows", "StockReview"),
    ),
}


def _recorded_rows(roots, root: str) -> list[dict]:
    document = json.loads(_provenance(roots).read_text(encoding="utf-8"))
    field = "composed_workflow_outputs" if root.startswith("extended") else "composed_outputs"
    [contributor] = [row for row in document[field][root] if row["pack_id"] == PACK_ID]
    return contributor["entries"]


def _share_entries(roots, root: str) -> None:
    """Another library records the same entries of one shared file."""
    document = json.loads(_provenance(roots).read_text(encoding="utf-8"))
    identity = {"pack_id": "library.holder", "version": "1.0.0", "source": "local", "digest": "sha256:" + "a" * 64}
    document["packs"].append({**identity, "ownership": "content_hash", "materialized_owned_files": []})
    field = "composed_workflow_outputs" if root.startswith("extended") else "composed_outputs"
    document[field][root].append({**identity, "entries": _recorded_rows(roots, root)})
    _provenance(roots).write_text(json.dumps(document), encoding="utf-8")


def _changed_contribution(pack: Path, root: str) -> None:
    relative, _address = CONTRIBUTIONS[root]
    if root == "data/contract.json":
        _edit(pack / relative, '"name": "sku", "type": "string", "required": true', '"name": "sku", "type": "string", "required": false')
    elif root == "ui/route_manifest.json":
        _edit(pack / relative, '"path": "/stock"', '"path": "/stocked"')
    elif root == "admin/admin_registry.yaml":
        registry = yaml.safe_load((pack / relative).read_text(encoding="utf-8"))
        registry["pages"][0]["label"] = "Changed label"
        (pack / relative).write_text(yaml.safe_dump(registry), encoding="utf-8")
    else:
        _edit(pack / relative, "Review stock levels", "Look at stock levels")


@pytest.mark.parametrize("change", ["remove", "replace", "unchanged"])
@pytest.mark.parametrize("root", CONTRIBUTIONS)
def test_an_upgrade_leaves_an_entry_another_pack_records(tmp_path, root, change):
    roots = _ordinary_app(tmp_path / "shop")
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    _share_entries(roots, root)
    shared = (roots.workflows_root if root.startswith("extended") else roots.app_root) / root
    text = shared.read_text(encoding="utf-8")
    candidate = _pack(tmp_path, "v2")
    _edit(candidate / "context.yaml", 'version: "1.0.0"', 'version: "2.0.0"')
    if change == "remove":
        (candidate / CONTRIBUTIONS[root][0]).unlink()
    elif change == "replace":
        _changed_contribution(candidate, root)
    source = PackSource(directory=candidate)
    plan = plan_workspace_upgrade(source=source, roots=roots)
    if change == "replace":
        assert {(row["kind"], row["owner"]) for row in plan.conflicts} == {("composed_entry_owned_by_other_pack", "library.holder")}
        with pytest.raises(CommunityComponentConflictError):
            apply_workspace_upgrade(source=source, roots=roots)
    else:
        assert plan.conflicts == []
        apply_workspace_upgrade(source=source, roots=roots)
        kept = [row for row in plan.warnings if row["kind"] == "kept_for_other_pack"]
        assert bool(kept) is (change == "remove")
    assert shared.read_text(encoding="utf-8") == text
    [holder] = [row for row in verify_installed_components(roots=roots)["packs"] if row["pack_id"] == "library.holder"]
    assert [row for row in holder["findings"] if "composed_entry" in row["kind"]] == []


def _generated_holder(roots, tmp_path: Path, *, readable: bool) -> None:
    """A generator pack composes the same data surface and route; its provenance names no entries."""
    source = _pack(tmp_path, "generated-holder")
    _edit(source / "context.yaml", "context_id: library.stock_ledger", "context_id: library.holder")
    _edit(source / "context.yaml", "  id: library.stock_ledger", "  id: library.holder")
    _edit(source / "contract.yaml", "contract_id: library.stock_ledger", "contract_id: library.holder")
    install_verified_local_pack(pack_source_path=source, build_context_root=roots.build_context_root)
    pins = json.loads((roots.build_context_root / ".mozaiks/installed_components.json").read_text(encoding="utf-8"))
    [pin] = [row for row in pins["components"] if row["pack_id"] == "library.holder"]
    identity = {key: pin[key] for key in ("pack_id", "version", "source", "digest")}
    path = _provenance(roots)
    document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "schema_version": "mozaiks.pack_provenance.v1", "framework_version": "1", "generated_at": "2026-01-01T00:00:00Z", "packs": [],
    }
    document["packs"].append({**identity, "materialized_owned_files": []})
    for root in ("data/contract.json", "ui/route_manifest.json"):
        document.setdefault("composed_outputs", {}).setdefault(root, []).append(identity)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    if not readable:
        shutil.rmtree(source)


@pytest.mark.parametrize("root", ["data/contract.json", "ui/route_manifest.json"])
def test_a_library_that_drops_an_entry_leaves_what_a_generator_pack_composes(tmp_path, root):
    roots = _ordinary_app(tmp_path / "shop")
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    _generated_holder(roots, tmp_path, readable=True)
    text = (roots.app_root / root).read_text(encoding="utf-8")
    candidate = _pack(tmp_path, "v2")
    _edit(candidate / "context.yaml", 'version: "1.0.0"', 'version: "2.0.0"')
    (candidate / CONTRIBUTIONS[root][0]).unlink()
    plan = apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    assert any("library.holder" in row["detail"] for row in plan.warnings if row["kind"] == "kept_for_other_pack")
    assert (roots.app_root / root).read_text(encoding="utf-8") == text


@pytest.mark.parametrize("readable", [True, False], ids=["source", "source-gone"])
def test_adoption_never_takes_an_entry_a_generator_pack_composes(tmp_path, readable):
    roots = _ordinary_app(tmp_path / "shop")
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    # The same entries, now recorded only by a generator pack.
    for path in ("build_context", "app/.mozaiks"):
        shutil.rmtree(tmp_path / "shop" / path)
    _generated_holder(roots, tmp_path, readable=readable)
    plan = plan_component_install(source=PackSource(directory=_pack(tmp_path, "again")), roots=roots, adopt=True)
    taken = {(row["root"], row["owner"]) for row in plan.conflicts if row["kind"] == "composed_entry_owned_by_other_pack"}
    assert taken == {("data/contract.json", "library.holder"), ("ui/route_manifest.json", "library.holder")}


def test_adoption_of_the_apps_own_entries_still_works_beside_a_generator_pack(tmp_path):
    roots = _ordinary_app(tmp_path / "shop")
    _materialize(roots, _generator_pack(tmp_path, "1.0.0", modules=("ledger",)))
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    document = json.loads(_provenance(roots).read_text(encoding="utf-8"))
    document["packs"] = [row for row in document["packs"] if row["pack_id"] != PACK_ID]
    for field in ("composed_outputs", "composed_workflow_outputs"):
        document[field] = {root: [row for row in rows if row["pack_id"] != PACK_ID] for root, rows in document[field].items()}
        document[field] = {root: rows for root, rows in document[field].items() if rows}
    _provenance(roots).write_text(json.dumps(document), encoding="utf-8")
    pins = roots.build_context_root / ".mozaiks/installed_components.json"
    state = json.loads(pins.read_text(encoding="utf-8"))
    state["components"] = [row for row in state["components"] if row["pack_id"] != PACK_ID]
    pins.write_text(json.dumps(state), encoding="utf-8")
    assert plan_component_install(source=PackSource(directory=_pack(tmp_path, "again")), roots=roots, adopt=True).conflicts == []


def _hold_the_journal_surface(roots, tmp_path: Path, holder: str, surface: dict) -> None:
    """Another pack composes the journal module and its data surface."""
    files = ["modules/journal/module.yaml", "modules/journal/backend/handler.py"]
    identity = {"pack_id": "library.holder", "version": "1.0.0", "source": "local", "digest": "sha256:" + "a" * 64}
    source = None
    if holder != "library":
        source = _generator_pack(tmp_path / "held", "1.0.0", modules=("journal",))
        for name in ("context.yaml", "contract.yaml"):
            text = (source / name).read_text(encoding="utf-8")
            (source / name).write_text(text.replace("library.generated", "library.holder"), encoding="utf-8")
        install_verified_local_pack(pack_source_path=source, build_context_root=roots.build_context_root)
        pins = json.loads((roots.build_context_root / ".mozaiks/installed_components.json").read_text(encoding="utf-8"))
        [pin] = [row for row in pins["components"] if row["pack_id"] == "library.holder"]
        identity = {key: pin[key] for key in ("pack_id", "version", "source", "digest")}
    if holder == "pinned-only":
        return
    document = json.loads(_provenance(roots).read_text(encoding="utf-8"))
    rows = [{"path": path, "owner": "templates"} for path in files]
    entry = {**identity, "materialized_owned_files": rows}
    contributor = dict(identity)
    if holder == "library":
        for row in rows:
            row["sha256"] = content_sha256((roots.app_root / row["path"]).read_text(encoding="utf-8"))
        entry["ownership"] = "content_hash"
        contributor["entries"] = [{"container": "surfaces", "key": "journal", "sha256": entry_sha256(surface)}]
    document["packs"].append(entry)
    document["composed_outputs"]["data/contract.json"].append(contributor)
    _provenance(roots).write_text(json.dumps(document), encoding="utf-8")
    if holder == "generator-source-gone":
        shutil.rmtree(source)


@pytest.mark.parametrize("change", ["remove", "replace", "unchanged"])
@pytest.mark.parametrize("holder", ["library", "generator", "generator-source-gone", "pinned-only"])
def test_a_generator_upgrade_leaves_a_data_surface_another_pack_composes(tmp_path, holder, change):
    roots = _ordinary_app(tmp_path / "shop")
    _orders_module(roots)
    _materialize(roots, _generator_pack(tmp_path, "1.0.0", modules=("ledger", "journal")))
    contract = roots.app_root / "data/contract.json"
    surface = next(row for row in json.loads(contract.read_text(encoding="utf-8"))["surfaces"] if row["surface_id"] == "journal")
    _hold_the_journal_surface(roots, tmp_path, holder, surface)
    candidate = _generator_pack(tmp_path, "2.0.0", modules=("ledger",) if change == "remove" else ("ledger", "journal"))
    if change == "replace":
        data = candidate / "templates/data/contract.json"
        changed = json.loads(data.read_text(encoding="utf-8"))
        next(row for row in changed["surfaces"] if row["surface_id"] == "journal")["collections"][0]["fields"].append(
            {"name": "extra", "type": "string", "required": False},
        )
        data.write_text(json.dumps(changed), encoding="utf-8")
    source = PackSource(directory=candidate)
    plan = plan_workspace_upgrade(source=source, roots=roots)
    if change == "replace" and holder != "pinned-only":
        assert [row["kind"] for row in plan.conflicts] == ["composed_entry_owned_by_other_pack"]
    else:
        assert plan.conflicts == []
        apply_workspace_upgrade(source=source, roots=roots)
    surfaces = {row["surface_id"]: row for row in json.loads(contract.read_text(encoding="utf-8"))["surfaces"]}
    # The pack's own record is the stronger claim against a pin alone.
    assert (surfaces["journal"] == surface) is not (change == "replace" and holder == "pinned-only")
    assert (roots.app_root / "modules/journal/module.yaml").is_file()
    for report in verify_installed_components(roots=roots)["packs"]:
        if report["pack_id"] == "library.holder":
            assert not {row["kind"] for row in report["findings"]} & {"missing_owned_file", "missing_composed_entry"}


def test_a_generator_upgrade_still_retires_the_surface_only_it_composes(tmp_path):
    roots = _ordinary_app(tmp_path / "shop")
    _orders_module(roots)
    _materialize(roots, _generator_pack(tmp_path, "1.0.0", modules=("ledger", "journal")))
    # Another contributor to the contract, whose source is gone and which records no journal module.
    document = json.loads(_provenance(roots).read_text(encoding="utf-8"))
    identity = {"pack_id": "library.holder", "version": "1.0.0", "source": "local", "digest": "sha256:" + "a" * 64}
    document["packs"].append({**identity, "materialized_owned_files": []})
    document["composed_outputs"]["data/contract.json"].append(identity)
    _provenance(roots).write_text(json.dumps(document), encoding="utf-8")
    candidate = _generator_pack(tmp_path, "2.0.0", modules=("ledger",))
    apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    surfaces = {row["surface_id"] for row in json.loads((roots.app_root / "data/contract.json").read_text(encoding="utf-8"))["surfaces"]}
    assert "journal" not in surfaces and "ledger" in surfaces
    assert not (roots.app_root / "modules/journal/module.yaml").exists()


@pytest.mark.parametrize("root", ["data/contract.json", "ui/route_manifest.json"])
def test_entries_of_a_pinned_pack_without_provenance_are_not_free(tmp_path, root):
    roots = _ordinary_app(tmp_path / "shop")
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    holder = _pack(tmp_path, "holder")
    for name in ("context.yaml", "contract.yaml"):
        text = (holder / name).read_text(encoding="utf-8")
        (holder / name).write_text(text.replace("library.stock_ledger", "library.holder"), encoding="utf-8")
    install_verified_local_pack(pack_source_path=holder, build_context_root=roots.build_context_root)
    text = (roots.app_root / root).read_text(encoding="utf-8")
    candidate = _pack(tmp_path, "v2")
    _edit(candidate / "context.yaml", 'version: "1.0.0"', 'version: "2.0.0"')
    (candidate / CONTRIBUTIONS[root][0]).unlink()
    plan = apply_workspace_upgrade(source=PackSource(directory=candidate), roots=roots)
    assert any("library.holder" in row["detail"] for row in plan.warnings if row["kind"] == "kept_for_other_pack")
    assert (roots.app_root / root).read_text(encoding="utf-8") == text
    # Nor may the same entry be taken over again.
    again = _pack(tmp_path, "v3")
    _edit(again / "context.yaml", 'version: "1.0.0"', 'version: "3.0.0"')
    retaken = plan_workspace_upgrade(source=PackSource(directory=again), roots=roots)
    taken = {(row["root"], row["owner"]) for row in retaken.conflicts if row["kind"] == "composed_entry_owned_by_other_pack"}
    assert taken == {(root, "library.holder")}


def test_losing_the_source_of_a_generator_pack_does_not_stop_other_upgrades(tmp_path):
    roots = _ordinary_app(tmp_path / "shop")
    apply_component_install(source=PackSource(directory=_pack(tmp_path)), roots=roots)
    _generated_holder(roots, tmp_path, readable=False)
    sidecar = installed_component_sources_path(roots.build_context_root)
    assert "library.holder" in sidecar.read_text(encoding="utf-8")
    # The library's own recorded entries are still its own to change.
    assert apply_workspace_upgrade(source=PackSource(directory=_version_two(tmp_path)), roots=roots).status == "applied"
