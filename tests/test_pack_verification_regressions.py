"""Selected-pack verification stays consistent across scans, trust, and upgrades."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools import generated_bundle_scanner as scanner
from factory_app.workflows.AppGenerator.tools import (
    resolve_managed_capability_templates as resolver,
)
from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
    CommunityComponentConflictError,
    CommunityComponentLifecycleError,
    apply_component_upgrade,
    install_verified_local_pack,
    plan_component_upgrade,
)
from mozaiksai.core.validation import validate_generated_app_candidate
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    materialize_data_migrations,
)
from mozaiksai.core.workflow.generator_support.ui_registry_composition import compose_ui_registry

BUILD_CONTEXT = Path(__file__).resolve().parents[1] / "factory_app/build_context"
MESSAGING = BUILD_CONTEXT / "messaging"
GREETINGS = Path(__file__).resolve().parent / "fixtures/community_packs/greetings"
LIFECYCLE_DOCS = Path(__file__).resolve().parents[1] / "docs/architecture/workflows/build-context-packs.md"
DATA_CONTRACT = "data/contract.json"
ROUTE_MANIFEST = "ui/route_manifest.json"
PROVENANCE = ".mozaiks/pack_provenance.json"
EMPTY_CONTRACT = {"version": "1", "surfaces": [], "shared_collections": []}
PROJECTS = {"surface_id": "projects", "surface_kind": "module", "collections": [{
    "name": "projects", "entity": "Project", "scope": "app", "tenancy": "per_user",
    "owner_field": "owner", "fields": [{"name": "owner", "type": "string", "required": True}],
}]}
OTHER_PACK = {"pack_id": "other_pack", "version": "1.0.0", "source": "local", "digest": "sha256:" + "a" * 64}


def _factory_pack(pack_id: str, **extra: str) -> dict[str, str]:
    return {"id": pack_id, "pack_source_path": str(BUILD_CONTEXT / pack_id), **extra}


def _messaging_copy(root: Path, *, version: str, extra_field: bool = False, without_data: bool = False) -> Path:
    shutil.copytree(MESSAGING, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    context = root / "context.yaml"
    text = context.read_text(encoding="utf-8")
    # Relabel the copy whatever the shipped pack's current version is.
    current = re.findall(r'^  version: "([^"]+)"$', text, flags=re.MULTILINE)
    assert len(current) == 1
    context.write_text(text.replace(f'  version: "{current[0]}"', f'  version: "{version}"'), encoding="utf-8")
    template = root / "templates" / DATA_CONTRACT
    if without_data:
        contract = root / "contract.yaml"
        declared = f"  - path: {DATA_CONTRACT}\n    owner: templates\n"
        assert declared in contract.read_text(encoding="utf-8")
        contract.write_text(contract.read_text(encoding="utf-8").replace(declared, ""), encoding="utf-8")
        template.unlink()
    if extra_field:
        data = json.loads(template.read_text(encoding="utf-8"))
        threads = next(row for row in data["surfaces"][0]["collections"] if row["name"] == "threads")
        threads["fields"].append({"name": "archived_at", "type": "string", "required": False})
        template.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return root


def _contributions(pack_root: Path) -> list[dict]:
    selected = [{"id": "messaging", "pack_source_path": str(pack_root)}]
    return resolver.verified_pack_data_contracts(resolver.verified_pack_template_inventory(selected))


def _write_app(app_root: Path, pack_root: Path, *, app_contract: dict | None) -> None:
    """Write pack templates and the composed UI roots and data contract, as assembly does."""
    ui_contributions: list[dict] = []
    files = {
        item["filename"]: item["content"]
        for item in resolver.resolve_managed_capability_templates(
            [{"id": "messaging", "pack_source_path": str(pack_root)}], ui_contributions=ui_contributions,
        )
    }
    files.update(compose_ui_registry(ui_contributions, files))
    contributions = _contributions(pack_root)
    if contributions or app_contract is not None:
        compiled = compile_data_contract(app_contract or EMPTY_CONTRACT, pack_data_contracts=contributions)
        files[DATA_CONTRACT] = json.dumps(compiled, indent=2)
    for path, content in files.items():
        target = app_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _surfaces(app_root: Path) -> dict[str, dict]:
    contract = json.loads((app_root / DATA_CONTRACT).read_text(encoding="utf-8"))
    return {surface["surface_id"]: surface for surface in contract["surfaces"]}


def _snapshot(*roots: Path) -> dict[str, str]:
    return {str(path): path.read_text(encoding="utf-8") for root in roots for path in sorted(root.rglob("*")) if path.is_file()}


def _assert_upgrade_blocked_before_writing(build_context_root: Path, candidate: Path, app_root: Path, conflicts: list) -> None:
    before = _snapshot(build_context_root, app_root)
    plan = plan_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=candidate, workspace_app_root=app_root,
    )
    assert (plan.status, plan.potential_conflicts) == ("blocked", conflicts)
    with pytest.raises(CommunityComponentConflictError):
        apply_component_upgrade(
            build_context_root=build_context_root, candidate_pack_source_path=candidate, workspace_app_root=app_root,
        )
    assert _snapshot(build_context_root, app_root) == before


def _write_composed_greetings_app(app_root: Path, pack_root: Path) -> None:
    """Write the greetings pack as assembly does, with its UI roots built by the canonical composer."""
    contributions: list[dict] = []
    files = {
        item["filename"]: item["content"]
        for item in resolver.resolve_managed_capability_templates(
            [{"id": "greetings", "pack_source_path": str(pack_root)}], ui_contributions=contributions,
        )
    }
    # Assembly passes ui_contributions, so the pack's UI roots are composed roots.
    assert ROUTE_MANIFEST in json.loads(files[PROVENANCE])["composed_outputs"]
    files.update(compose_ui_registry(contributions, files))
    for path, content in files.items():
        (app_root / path).parent.mkdir(parents=True, exist_ok=True)
        (app_root / path).write_text(content, encoding="utf-8")


def _greetings_v2(tmp_path: Path, change: str) -> Path:
    """Copy greetings as 0.2.0 whose route manifest template is unchanged, changed or removed."""
    v2 = tmp_path / "greetings_v2"
    shutil.copytree(GREETINGS, v2)
    (v2 / "context.yaml").write_text(
        (v2 / "context.yaml").read_text(encoding="utf-8").replace('version: "0.1.0"', 'version: "0.2.0"'), encoding="utf-8",
    )
    service = v2 / "templates/modules/greetings/backend/service.py"
    service.write_text(service.read_text(encoding="utf-8") + "# v2\n", encoding="utf-8")
    template = v2 / "templates" / ROUTE_MANIFEST
    if change == "unchanged":
        return v2
    if change == "changed":
        manifest = json.loads(template.read_text(encoding="utf-8"))
        manifest["pages"][0]["label"] = "Greetings v2"
        template.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    else:
        contract = v2 / "contract.yaml"
        declared = f"  - path: {ROUTE_MANIFEST}\n    owner: templates\n"
        assert declared in contract.read_text(encoding="utf-8")
        contract.write_text(contract.read_text(encoding="utf-8").replace(declared, ""), encoding="utf-8")
        template.unlink()
    return v2


def _lifecycle_docs() -> str:
    return " ".join(LIFECYCLE_DOCS.read_text(encoding="utf-8").split())


@pytest.mark.asyncio
async def test_candidate_facade_keeps_explicit_managed_owners_out_of_migrations():
    pack = _factory_pack("mozaikspay", capability_source="managed_capability", status="active")
    path = "data/migrations/001_billing.json"
    migration = json.dumps({"migration_id": "001_billing", "operations": [
        {"type": "ensure_collection", "module_id": "notes", "entity_name": "note"},
        {"type": "ensure_collection", "module_id": "subscriptions", "entity_name": "subscription"},
    ]})
    # Compiled without managed owners, so only the managed-collection exclusion differs.
    files = {
        "app.json": json.dumps({"appId": "billing", "appName": "Billing"}),
        path: materialize_data_migrations({path: migration}, managed_owners=set())[path],
    }
    expected = f"{path}: must contain compiled migration versions, named indexes, and no managed collections."

    assert expected in scanner.scan_generated_bundle(
        files, capability_packs=[pack], context_variables={"app_id": "billing"},
    )
    result = await validate_generated_app_candidate(
        files=files, app_id="billing", validation_strategy="docker", capability_packs=[pack],
    )
    assert result["validation_status"] == "failed"
    assert any(expected in error for error in result["errors"]), result["errors"]


def test_shared_composition_roots_are_not_duplicate_pack_claims():
    # Support requires messaging; both contribute the composed route manifest.
    errors = scanner.scan_generated_bundle(
        {"app.json": "{}"}, capability_packs=[_factory_pack("messaging"), _factory_pack("support")],
    )
    assert not any("duplicate pack output claims" in error for error in errors), errors


def test_non_shared_duplicate_pack_claims_still_fail_closed(monkeypatch):
    claims = {"pack_one": {"ui/route_manifest.json", DATA_CONTRACT}, "pack_two": {"ui/route_manifest.json", DATA_CONTRACT}}
    monkeypatch.setattr(
        scanner, "resolve_declared_pack_output_paths", lambda packs, **_kwargs: frozenset(claims[packs[0]["id"]]),
    )
    packs = [{"id": "pack_one", "capability_source": "config_file"}, {"id": "pack_two", "capability_source": "config_file"}]
    scanner._layout_extensions_for_selected_packs(packs)
    for owned in claims.values():
        owned.add("scripts/shared_output.ps1")
    with pytest.raises(
        resolver.ManagedCapabilityTemplateError, match="'scripts/shared_output.ps1'; duplicate pack output claims",
    ):
        scanner._layout_extensions_for_selected_packs(packs)


def test_upgrade_recomposes_changed_pack_data_contract(tmp_path):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.13")
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.14", extra_field=True)
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract={**EMPTY_CONTRACT, "surfaces": [PROJECTS]})
    projects = _surfaces(app_root)["projects"]
    provenance_path = app_root / ".mozaiks/pack_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    other = OTHER_PACK
    provenance["packs"].append({**other, "materialized_owned_files": []})
    provenance["composed_outputs"][DATA_CONTRACT].append(other)
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    plan = plan_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )
    assert plan.status == "ready", plan.potential_conflicts
    assert DATA_CONTRACT not in plan.added_files + plan.changed_files + plan.removed_files
    assert plan.recomposed_files == [DATA_CONTRACT]

    apply_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )
    surfaces = _surfaces(app_root)
    assert surfaces["projects"] == projects
    threads = next(row for row in surfaces["messages"]["collections"] if row["name"] == "threads")
    assert "archived_at" in {field["name"] for field in threads["fields"]}
    updated = json.loads(provenance_path.read_text(encoding="utf-8"))
    contributors = updated["composed_outputs"][DATA_CONTRACT]
    assert [(row["pack_id"], row["version"]) for row in contributors] == [("other_pack", "1.0.0"), ("messaging", "0.1.14")]
    assert not any(
        file["path"] == DATA_CONTRACT for pack in updated["packs"] for file in pack["materialized_owned_files"]
    )


def test_upgrade_composes_a_pack_first_data_contribution(tmp_path):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.12", without_data=True)
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.13")
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract=None)
    assert not (app_root / DATA_CONTRACT).exists()

    plan = apply_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )

    assert plan.recomposed_files == [DATA_CONTRACT]
    expected = compile_data_contract(EMPTY_CONTRACT, pack_data_contracts=_contributions(v2))
    assert json.loads((app_root / DATA_CONTRACT).read_text(encoding="utf-8")) == expected
    provenance = json.loads((app_root / ".mozaiks/pack_provenance.json").read_text(encoding="utf-8"))
    assert [row["version"] for row in provenance["composed_outputs"][DATA_CONTRACT]] == ["0.1.13"]


def test_upgrade_blocks_locally_modified_pack_data_surface(tmp_path):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.13")
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.14", extra_field=True)
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract=None)
    contract_path = app_root / DATA_CONTRACT
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["surfaces"][0]["collections"][0]["fields"].append({"name": "local_note", "type": "string", "required": False})
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    before = contract_path.read_text(encoding="utf-8")

    plan = plan_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )
    assert {"path": DATA_CONTRACT, "kind": "locally_modified_composed_output"} in plan.potential_conflicts
    with pytest.raises(CommunityComponentConflictError):
        apply_component_upgrade(
            build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
        )
    assert contract_path.read_text(encoding="utf-8") == before


def test_upgrade_keeps_an_app_composed_root_composed(tmp_path):
    build_context_root, app_root, root = tmp_path / "build_context", tmp_path / "app", ROUTE_MANIFEST
    v2 = _greetings_v2(tmp_path, "unchanged")
    install_verified_local_pack(pack_source_path=GREETINGS, build_context_root=build_context_root)
    _write_composed_greetings_app(app_root, GREETINGS)
    # Assembly composed the route manifest from this pack and another contributor.
    provenance_path = app_root / PROVENANCE
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    other = OTHER_PACK
    provenance["packs"].append({**other, "materialized_owned_files": []})
    provenance["composed_outputs"][root].append(other)
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    apply_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )

    updated = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert [(row["pack_id"], row["version"]) for row in updated["composed_outputs"][root]] == [
        ("other_pack", "1.0.0"), ("greetings", "0.2.0"),
    ]
    upgraded = next(pack for pack in updated["packs"] if pack["pack_id"] == "greetings")
    assert root not in {row["path"] for row in upgraded["materialized_owned_files"]}
    assert scanner._scan_pack_provenance_manifest({
        path.relative_to(app_root).as_posix(): path.read_text(encoding="utf-8")
        for path in app_root.rglob("*") if path.is_file()
    }) == []


def _claim_shared_roots_exclusively(app_root: Path, claims: dict[str, list[str]]) -> None:
    """Rewrite the app's provenance in the pre-composition shape: shared roots as exclusive files."""
    provenance_path = app_root / PROVENANCE
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    composed = provenance.pop("composed_outputs")
    entries = {pack["pack_id"]: pack for pack in [*provenance["packs"], {**OTHER_PACK, "materialized_owned_files": []}]}
    for owner, paths in claims.items():
        entries[owner]["materialized_owned_files"].extend({"path": path, "owner": "templates"} for path in paths)
        for path in paths:
            composed.pop(path, None)
    if composed:
        provenance["composed_outputs"] = composed
    provenance["packs"] = list(entries.values())
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")


def _bundle(app_root: Path) -> dict[str, str]:
    return {path.relative_to(app_root).as_posix(): path.read_text(encoding="utf-8") for path in app_root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("contract_owner", ["other_pack", "messaging"])
def test_upgrade_blocks_an_exclusive_data_contract_claim_before_writing(tmp_path, contract_owner):
    """Pre-composition provenance claims the contract as one pack's file; it is never migrated in place."""
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.13")
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.14", extra_field=True)
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract=None)
    _claim_shared_roots_exclusively(app_root, {contract_owner: [DATA_CONTRACT]})

    _assert_upgrade_blocked_before_writing(build_context_root, v2, app_root, [
        {"path": DATA_CONTRACT, "owner": contract_owner, "kind": "exclusive_shared_root_claim"},
    ])
    with pytest.raises(CommunityComponentConflictError, match="Reassemble the app to recompose its shared roots"):
        apply_component_upgrade(
            build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
        )
    errors = scanner._scan_pack_provenance_manifest(_bundle(app_root))
    assert any(
        f"claims shared composition root {DATA_CONTRACT!r}" in error and "Reassemble the app" in error for error in errors
    ), errors


def test_upgrade_blocks_a_pre_composition_ui_root_claim_even_when_the_root_is_unchanged(tmp_path):
    """A flat-era UI root claim blocks the whole upgrade before anything is written, not half way."""
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    install_verified_local_pack(pack_source_path=GREETINGS, build_context_root=build_context_root)
    _write_composed_greetings_app(app_root, GREETINGS)
    _claim_shared_roots_exclusively(app_root, {"greetings": [ROUTE_MANIFEST]})
    # Only service.py changes; the route manifest template is the same.
    v2 = _greetings_v2(tmp_path, "unchanged")

    _assert_upgrade_blocked_before_writing(build_context_root, v2, app_root, [
        {"path": ROUTE_MANIFEST, "owner": "greetings", "kind": "exclusive_shared_root_claim"},
        {"path": ROUTE_MANIFEST, "kind": "composed_output_requires_reassembly"},
    ])
    assert any(
        f"claims shared composition root {ROUTE_MANIFEST!r}" in error
        for error in scanner._scan_pack_provenance_manifest(_bundle(app_root))
    )


@pytest.mark.parametrize("malform", [
    lambda contract: contract["surfaces"].append("bogus"),
    lambda contract: contract.update(surfaces={}),
    lambda contract: next(
        surface for surface in contract["surfaces"] if surface["surface_id"] == "projects"
    )["collections"][0].update(indexes=["bogus"]),
], ids=["surface_entry", "surfaces_object", "index_entry"])
def test_upgrade_blocks_a_malformed_app_data_contract(tmp_path, malform):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.13")
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.14", extra_field=True)
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract={**EMPTY_CONTRACT, "surfaces": [PROJECTS]})
    contract = json.loads((app_root / DATA_CONTRACT).read_text(encoding="utf-8"))
    malform(contract)
    (app_root / DATA_CONTRACT).write_text(json.dumps(contract), encoding="utf-8")

    _assert_upgrade_blocked_before_writing(build_context_root, v2, app_root, [
        {"path": DATA_CONTRACT, "kind": "composed_output_conflict"},
    ])


def test_upgrade_does_not_create_a_data_contract_with_nothing_to_compose(tmp_path):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.13")
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.14", without_data=True)
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract=None)
    (app_root / DATA_CONTRACT).unlink()

    plan = apply_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )

    assert (plan.status, plan.recomposed_files) == ("ready", [])
    assert not (app_root / DATA_CONTRACT).exists()
    composed = json.loads((app_root / PROVENANCE).read_text(encoding="utf-8"))["composed_outputs"]
    assert DATA_CONTRACT not in composed and set(composed) == {"ui/index.js", ROUTE_MANIFEST}


@pytest.mark.parametrize("change", ["changed", "removed"])
def test_upgrade_blocks_a_pack_change_to_a_composed_ui_root(tmp_path, change):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    install_verified_local_pack(pack_source_path=GREETINGS, build_context_root=build_context_root)
    _write_composed_greetings_app(app_root, GREETINGS)
    v2 = _greetings_v2(tmp_path, change)

    _assert_upgrade_blocked_before_writing(build_context_root, v2, app_root, [
        {"path": ROUTE_MANIFEST, "kind": "composed_output_requires_reassembly"},
    ])


@pytest.mark.parametrize("claim_order", [
    ("other_pack", "messaging"), ("messaging", "other_pack"),
], ids=["upgraded_listed_last", "upgraded_listed_first"])
def test_upgrade_blocks_every_exclusive_data_contract_claim_in_either_order(tmp_path, claim_order):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.13")
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.14", extra_field=True)
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract=None)
    # Older or hand-edited provenance can list the same exclusive claim twice; no claim may hide another.
    _claim_shared_roots_exclusively(app_root, {pack_id: [DATA_CONTRACT] for pack_id in claim_order})
    provenance_path = app_root / PROVENANCE
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["packs"] = sorted(provenance["packs"], key=lambda pack: claim_order.index(pack["pack_id"]))
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    _assert_upgrade_blocked_before_writing(build_context_root, v2, app_root, [
        {"path": DATA_CONTRACT, "owner": "messaging", "kind": "exclusive_shared_root_claim"},
        {"path": DATA_CONTRACT, "owner": "other_pack", "kind": "exclusive_shared_root_claim"},
    ])


def test_an_exclusive_claim_blocks_even_an_upgrade_that_composes_no_data(tmp_path):
    """Another pack's exclusive contract claim blocks before writing whatever the upgraded pack contributes."""
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    install_verified_local_pack(pack_source_path=GREETINGS, build_context_root=build_context_root)
    _write_composed_greetings_app(app_root, GREETINGS)
    (app_root / DATA_CONTRACT).parent.mkdir(parents=True, exist_ok=True)
    (app_root / DATA_CONTRACT).write_text(json.dumps(EMPTY_CONTRACT), encoding="utf-8")
    provenance_path = app_root / PROVENANCE
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["packs"].append({**OTHER_PACK, "materialized_owned_files": [{"path": DATA_CONTRACT, "owner": "templates"}]})
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    _assert_upgrade_blocked_before_writing(build_context_root, _greetings_v2(tmp_path, "unchanged"), app_root, [
        {"path": DATA_CONTRACT, "owner": "other_pack", "kind": "exclusive_shared_root_claim"},
    ])


def test_resolver_never_materializes_a_shared_root_and_records_every_contributor(tmp_path):
    """No caller gets a shared root as a pack file, and a shared-root-only pack still has an identity."""
    shared_only = tmp_path / "shared_only"
    shutil.copytree(GREETINGS, shared_only)
    for path in (shared_only / "templates").rglob("*"):
        if path.is_file() and path.relative_to(shared_only / "templates").as_posix() != ROUTE_MANIFEST:
            path.unlink()
    contract = shared_only / "contract.yaml"
    contract.write_text(re.sub(
        r"  - path: (?!ui/route_manifest\.json)[^\n]*\n    owner: [^\n]*\n", "", contract.read_text(encoding="utf-8"),
    ), encoding="utf-8")

    for ui_contributions in (None, []):
        files = {
            item["filename"]: item["content"]
            for item in resolver.resolve_managed_capability_templates(
                [{"id": "greetings", "pack_source_path": str(shared_only)}], ui_contributions=ui_contributions,
            )
        }
        assert set(files) == {PROVENANCE}, files
        provenance = json.loads(files[PROVENANCE])
        [entry] = provenance["packs"]
        assert (entry["pack_id"], entry["materialized_owned_files"]) == ("greetings", [])
        identity = {key: entry[key] for key in ("pack_id", "version", "source", "digest")}
        assert provenance["composed_outputs"] == {ROUTE_MANIFEST: [identity]}
    manifest = (shared_only / "templates" / ROUTE_MANIFEST).read_text(encoding="utf-8")
    assert scanner._scan_pack_provenance_manifest({PROVENANCE: files[PROVENANCE], ROUTE_MANIFEST: manifest}) == []


def test_upgrade_writes_the_empty_contract_assembly_composes_for_a_zero_surface_template(tmp_path):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    v1 = _messaging_copy(tmp_path / "v1", version="0.1.12", without_data=True)
    v2 = _messaging_copy(tmp_path / "v2", version="0.1.13")
    (v2 / "templates" / DATA_CONTRACT).write_text(json.dumps(EMPTY_CONTRACT, indent=2), encoding="utf-8")
    install_verified_local_pack(pack_source_path=v1, build_context_root=build_context_root)
    _write_app(app_root, v1, app_contract=None)
    assert not (app_root / DATA_CONTRACT).exists()

    plan = apply_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )

    # Assembly without an approved DesignDocs contract composes the same empty contract for this pack.
    metadata = resolver.verify_pack_integrity(v2, "messaging")
    selected = {"id": "messaging", "pack_source_path": str(v2), "version": metadata["version"], "digest": metadata["digest"]}
    context = {"capability_packs": [selected]}
    emitted = resolver.resolve_managed_capability_templates(context["capability_packs"], context_variables=context)
    assembled = {item["filename"]: item["content"] for item in _merge_code_files([{"code_files": emitted}], context_variables=context)}
    assert plan.recomposed_files == [DATA_CONTRACT]
    assert json.loads((app_root / DATA_CONTRACT).read_text(encoding="utf-8")) == json.loads(assembled[DATA_CONTRACT]) == EMPTY_CONTRACT
    docs = _lifecycle_docs()
    assert "gets one only when the new version contributes surfaces" not in docs
    assert "creates one whenever the new version ships a data contract template, even one that declares no surfaces" in docs
    assert "If the new version ships no data contract template, no contract is created." in docs


def test_installed_pack_cannot_adopt_a_changed_composed_ui_root_template(tmp_path):
    build_context_root, app_root = tmp_path / "build_context", tmp_path / "app"
    install_verified_local_pack(pack_source_path=GREETINGS, build_context_root=build_context_root)
    _write_composed_greetings_app(app_root, GREETINGS)
    v2 = _greetings_v2(tmp_path, "changed")
    blocked = {"path": ROUTE_MANIFEST, "kind": "composed_output_requires_reassembly"}
    plan = plan_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )
    assert plan.status == "blocked" and blocked in plan.potential_conflicts

    # Installing the new version is refused while the old one is pinned.
    with pytest.raises(CommunityComponentLifecycleError, match="already installed at 0.1.0"):
        install_verified_local_pack(pack_source_path=v2, build_context_root=build_context_root)
    # Reassembly that selects the pack by id resolves the pinned old source.
    by_id = {
        item["filename"]: item["content"]
        for item in resolver.resolve_managed_capability_templates(
            [{"id": "greetings"}], context_variables={"build_context_root": str(build_context_root)}, ui_contributions=[],
        )
    }
    assert [pack["version"] for pack in json.loads(by_id[PROVENANCE])["packs"]] == ["0.1.0"]
    # Reassembly from the new source's explicit path leaves the pin behind, so the next plan blocks again.
    _write_composed_greetings_app(app_root, v2)
    installed = json.loads((build_context_root / ".mozaiks/installed_components.json").read_text(encoding="utf-8"))
    assert [component["version"] for component in installed["components"]] == ["0.1.0"]
    plan = plan_component_upgrade(
        build_context_root=build_context_root, candidate_pack_source_path=v2, workspace_app_root=app_root,
    )
    assert plan.status == "blocked" and blocked in plan.potential_conflicts
    docs = _lifecycle_docs()
    assert "reassemble the app with the upgraded pack selected" not in docs
    assert "the lifecycle cannot adopt that pack version" in docs
    assert "the only operation that moves the installed pin" in docs
    assert "the next upgrade plan still compares against the old version's output and blocks again" in docs


@pytest.mark.parametrize("factory_root_resolves", [True, False])
def test_verified_installed_pin_trusts_complete_template_backend(tmp_path, monkeypatch, factory_root_resolves):
    if not factory_root_resolves:
        monkeypatch.setattr(resolver, "resolve_factory_app_root", lambda: None)
    source = _messaging_copy(tmp_path / "source", version="0.1.13")
    build_context_root = tmp_path / "build_context"
    installed = install_verified_local_pack(pack_source_path=source, build_context_root=build_context_root)
    context = {"build_context_root": str(build_context_root), "capability_packs": [{"id": "messaging"}]}

    [entry] = resolver.verified_selected_pack_sources(context_variables=context)
    assert entry["implementation_trusted"] is True
    assert (entry["version"], entry["digest"]) == (installed.version, installed.digest)
    inventory = resolver.verified_pack_template_inventory(context_variables=context)
    rendered = {file["filename"]: file["content"] for file in inventory[0]["files"]}
    assert resolver.verified_template_module_ids(rendered, inventory) == {"messages"}

    # An explicit path without pins is still untrusted, and a pin never outlives its bytes.
    [unpinned] = resolver.verified_selected_pack_sources([{"id": "messaging", "pack_source_path": str(source)}])
    assert unpinned["implementation_trusted"] is False
    (source / "templates/modules/messages/backend/policy.py").write_text("# replaced policy\n", encoding="utf-8")
    with pytest.raises(resolver.ManagedCapabilityTemplateError, match="no longer matches pinned state"):
        resolver.verified_selected_pack_sources(context_variables=context)


MOZAIKSPAY = BUILD_CONTEXT / "mozaikspay"
# Exactly the row mozaiks-app's generated_bundle_gate infers from services/integrations/mozaikspay_client.py.
HOSTED_MOZAIKSPAY_ROW = {
    "id": "mozaikspay", "capability_source": "managed_capability", "provides_capabilities": ["subscription_write_path"],
}
HOSTED_SUBSCRIPTIONS = """
schema_version: mozaiks.subscriptions.v1
label: Plans
default_plan_id: free
assignment_store:
  data_alias: subscriptions.assignments
  user_id_field: user_id
  active_statuses: [active, pending]
plans:
  - plan_id: free
    label: Free
    capabilities: []
  - plan_id: pro
    label: Pro
    capabilities: []
"""


def _hosted_mozaikspay_bundle() -> dict[str, str]:
    """A MozaiksPay bundle materialized from the shipped catalog, as AppGenerator assembles it."""
    import yaml

    from mozaiksai.core.workflow.generator_support.subscription_data_contract import (
        ensure_subscription_assignment_stores,
    )

    files = {
        item["filename"]: item["content"]
        for item in resolver.resolve_managed_capability_templates(
            [{"id": "mozaikspay", "capability_source": "managed_capability", "pack_source_path": str(MOZAIKSPAY)}],
            context_variables={"build_timestamp": "2026-10-17T00:00:00Z"},
        )
    }
    files["app.json"] = json.dumps({"appId": "billing", "appName": "Billing"})
    files["config/subscriptions.yaml"] = HOSTED_SUBSCRIPTIONS
    files[DATA_CONTRACT] = json.dumps(ensure_subscription_assignment_stores(
        dict(EMPTY_CONTRACT), {"contract_required": True, "subscription_config_file": yaml.safe_load(HOSTED_SUBSCRIPTIONS)},
    ))
    return files


def _hosted_facade(files: dict[str, str], rows: list[dict] | None = None):
    from mozaiksai.core.validation import (
        GeneratedAppValidationRequest,
        validate_generated_app_bundle,
    )

    return validate_generated_app_bundle(GeneratedAppValidationRequest(
        files=files, capability_packs=[dict(row) for row in rows or [HOSTED_MOZAIKSPAY_ROW]],
    ))


def _errors(result) -> list[str]:
    return [item.message for item in result.diagnostics if item.severity == "error"]


def test_the_hosted_id_only_mozaikspay_row_resolves_to_the_shipped_catalog_identity(caplog):
    """The installed package's shipped catalog is the authority for the hosted gate's id-only row."""
    caplog.set_level("DEBUG", logger=resolver.__name__)
    shipped = resolver.verify_pack_integrity(MOZAIKSPAY, "mozaikspay")
    identity = {**{key: shipped[key] for key in ("pack_id", "version", "digest", "source")}, "resolution": "factory_build_context"}

    [entry] = resolver.verified_selected_pack_sources([dict(HOSTED_MOZAIKSPAY_ROW)])
    assert resolver.pack_identity(entry) == identity
    assert (Path(entry["source_path"]), entry["implementation_trusted"]) == (MOZAIKSPAY.resolve(), True)
    assert not [record for record in caplog.records if "UNVERIFIED" in record.getMessage()]

    # A clean materialized bundle passes the facade, which returns the identity it verified.
    result = _hosted_facade(_hosted_mozaikspay_bundle())
    assert result.passed, _errors(result)
    assert result.resolved_capability_packs == [identity]


def test_a_selection_whose_pins_match_the_shipped_catalog_passes():
    shipped = resolver.verify_pack_integrity(MOZAIKSPAY, "mozaikspay")
    pinned = {**HOSTED_MOZAIKSPAY_ROW, "version": shipped["version"], "digest": shipped["digest"]}
    [entry] = resolver.verified_selected_pack_sources([pinned])
    assert (entry["resolution"], entry["digest"]) == ("factory_build_context", shipped["digest"])
    result = _hosted_facade(_hosted_mozaikspay_bundle(), [pinned])
    assert result.passed, _errors(result)


@pytest.mark.parametrize(("row", "reason"), [
    ({"id": "acme_pay", "capability_source": "managed_capability"}, "ships no pack at build_context/acme_pay"),
    ({"id": "acme", "version": "1.0.0"}, "ships no pack at build_context/acme"),
    ({"id": "acme", "digest": "sha256:" + "1" * 64}, "ships no pack at build_context/acme"),
    ({"id": "../mozaikspay", "capability_source": "managed_capability"}, "'../mozaikspay' is not a safe pack identifier"),
    # Normalizes onto the shipped mozaikspay directory, but is still no safe catalog id.
    ({"id": "mozaikspay/.", "capability_source": "managed_capability"}, "'mozaikspay/.' is not a safe pack identifier"),
    ({"id": "AppGenerator", "capability_source": "operator_pack"}, "ships no pack at build_context/AppGenerator"),
], ids=[
    "unknown_managed_id", "unknown_pinned_version", "unknown_pinned_digest", "unsafe_id", "unsafe_alias_of_a_shipped_pack",
    "catalog_context_not_a_pack",
])
def test_a_claimed_pack_that_resolves_to_no_verified_source_fails(row, reason):
    """No warning admits an unresolvable pack: the resolver, declared outputs and every scan refuse it."""
    with pytest.raises(resolver.PackIntegrityError, match=re.escape(reason)):
        resolver.verified_selected_pack_sources([row])
    with pytest.raises(resolver.PackIntegrityError, match=re.escape(reason)):
        resolver.resolve_declared_pack_output_paths([row])
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[row])
    assert len(errors) == 1 and errors[0].startswith("Selected CapabilityPack output contract is invalid"), errors
    result = _hosted_facade(_hosted_mozaikspay_bundle(), [row])
    assert not result.passed and result.resolved_capability_packs == []


def test_the_hosted_row_fails_when_no_factory_catalog_resolves(monkeypatch):
    monkeypatch.setattr(resolver, "resolve_factory_app_root", lambda: None)
    message = "the installed factory catalog cannot be located"
    with pytest.raises(resolver.PackIntegrityError, match=message):
        resolver.verified_selected_pack_sources([dict(HOSTED_MOZAIKSPAY_ROW)])
    result = _hosted_facade(_hosted_mozaikspay_bundle())
    assert not result.passed and any(message in error for error in _errors(result)), _errors(result)


@pytest.mark.parametrize("pins", [
    {"version": "0.0.1"}, {"digest": "sha256:" + "0" * 64}, {"version": "9.9.9", "digest": "sha256:" + "1" * 64},
], ids=["stale_version", "stale_digest", "both"])
@pytest.mark.parametrize("pack_id", ["mozaikspay", "messaging"])
def test_a_stale_or_mismatched_pin_fails_against_the_resolved_source(pack_id, pins):
    row = {"id": pack_id, **pins}
    with pytest.raises(resolver.PackIntegrityError, match="selected (version|digest) does not match freshly verified source"):
        resolver.verified_selected_pack_sources([row])
    with pytest.raises(resolver.PackIntegrityError, match="does not match freshly verified source"):
        resolver.resolve_declared_pack_output_paths([row])
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[row])
    assert any("does not match freshly verified source" in error for error in errors), errors


@pytest.mark.parametrize("field", ["version", "digest"])
def test_bundle_provenance_that_differs_from_the_resolved_identity_fails(field):
    files = _hosted_mozaikspay_bundle()
    provenance = json.loads(files[PROVENANCE])
    provenance["packs"][0][field] = "0.0.9" if field == "version" else "sha256:" + "2" * 64
    files[PROVENANCE] = json.dumps(provenance)
    result = _hosted_facade(files)
    assert not result.passed
    assert any(
        error.startswith(f"{PROVENANCE}: packs[0] records pack 'mozaikspay' with {field}") and "factory_build_context" in error
        for error in _errors(result)
    ), _errors(result)


def test_a_modified_copy_of_the_catalog_source_fails_the_fresh_digest(tmp_path, monkeypatch):
    """Changed catalog bytes are rechecked through the digest: pins, provenance and template bytes all fail."""
    shipped = resolver.verify_pack_integrity(MOZAIKSPAY, "mozaikspay")
    genuine = _hosted_mozaikspay_bundle()
    factory_root = tmp_path / "factory_app"
    copy = factory_root / "build_context" / "mozaikspay"
    shutil.copytree(MOZAIKSPAY, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    service = copy / "templates/modules/billing_portal/backend/service.py"
    service.write_text(service.read_text(encoding="utf-8") + "\n# changed after release\n", encoding="utf-8")
    monkeypatch.setattr(resolver, "resolve_factory_app_root", lambda: factory_root)

    pinned = {**HOSTED_MOZAIKSPAY_ROW, "version": shipped["version"], "digest": shipped["digest"]}
    with pytest.raises(resolver.PackIntegrityError, match="selected digest does not match freshly verified source"):
        resolver.verified_selected_pack_sources([pinned])
    [entry] = resolver.verified_selected_pack_sources([dict(HOSTED_MOZAIKSPAY_ROW)])
    assert entry["digest"] != shipped["digest"]
    errors = _errors(_hosted_facade(genuine))
    assert any(error.startswith(f"{PROVENANCE}: packs[0] records pack 'mozaikspay' with digest") for error in errors), errors
    assert any(error.startswith("modules/billing_portal/backend/service.py: differs from the verified template") for error in errors)


@pytest.mark.parametrize("forge_provenance", [False, True], ids=["provenance_untouched", "provenance_forged"])
def test_a_tampered_template_owned_file_fails_even_with_forged_provenance(forge_provenance):
    files = _hosted_mozaikspay_bundle()
    path = "modules/billing_portal/backend/base_handler.py"
    files[path] += "\nimport os  # tampered\n"
    if forge_provenance:
        provenance = json.loads(files[PROVENANCE])
        provenance["packs"][0]["materialized_owned_files"] = [
            row for row in provenance["packs"][0]["materialized_owned_files"] if row["path"] != path
        ] + [{"path": path, "owner": "workspace"}]
        files[PROVENANCE] = json.dumps(provenance)
    result = _hosted_facade(files)
    assert not result.passed
    assert any(error.startswith(f"{path}: differs from the verified template of the selected pack 'mozaikspay'") for error in _errors(result))
    # A deleted template-owned output fails the same way.
    del files[path]
    assert any(error.startswith(f"{path}: template-owned output of the selected pack 'mozaikspay'") for error in _errors(_hosted_facade(files)))


def test_the_hosted_row_rejects_a_forbidden_output_from_the_verified_contract():
    """modules/wallet/ is forbidden by the shipped mozaikspay contract, with the id-only row and no build context."""
    files = {**_hosted_mozaikspay_bundle(), "modules/wallet/module.yaml": "module:\n  id: wallet\nactions: []\n"}
    errors = scanner.scan_generated_bundle(files, capability_packs=[dict(HOSTED_MOZAIKSPAY_ROW)])
    assert any(
        "Selected managed capability 'mozaikspay' declares forbidden output prefixes" in error
        and "modules/wallet/module.yaml" in error
        for error in errors
    ), errors


def test_an_inline_provides_capabilities_claim_grants_nothing_beyond_the_verified_contract():
    """Only the verified contract can exempt a bundle from the self-hosted entitlement_dispatch module."""
    files = {"config/subscriptions.yaml": HOSTED_SUBSCRIPTIONS}
    required = "Self-hosted apps require an entitlement_dispatch module"
    # mozaiks_cloud is a verified managed pack whose contract provides only the cloud write paths.
    claimed = {"id": "mozaiks_cloud", "capability_source": "managed_capability", "provides_capabilities": ["subscription_write_path"]}
    assert any(required in error for error in scanner.scan_generated_bundle(files, capability_packs=[claimed]))
    # A planned module that is no pack grants nothing either.
    planned = {"capability_pack_id": "billing_tools", "capability_source": "generated_module", "provides_capabilities": ["subscription_write_path"]}
    assert any(required in error for error in scanner.scan_generated_bundle(files, capability_packs=[planned]))
    # The shipped mozaikspay contract does provide it, with or without the inline claim.
    for row in (HOSTED_MOZAIKSPAY_ROW, {key: value for key, value in HOSTED_MOZAIKSPAY_ROW.items() if key != "provides_capabilities"}):
        assert not any(required in error for error in scanner.scan_generated_bundle(files, capability_packs=[dict(row)]))


def test_a_generated_module_plan_row_that_matches_no_pack_selects_and_grants_nothing(caplog):
    caplog.set_level("DEBUG", logger=resolver.__name__)
    planned = [
        {"capability_pack_id": "tasks", "capability_source": "generated_module"},
        {"capability_pack_id": "billing_tools", "capability_source": "generated_module", "provides_capabilities": ["subscription_write_path"]},
    ]
    assert resolver.verified_selected_pack_sources(planned) == []
    assert resolver.resolve_declared_pack_output_paths(planned) == frozenset()
    assert caplog.records == []
    resolved: list[dict] = []
    files = {"app.json": "{}", "services/integrations/billing_tools_client.py": "x = 1\n"}
    errors = scanner.scan_generated_bundle(files, capability_packs=planned, resolved_capability_packs=resolved)
    assert resolved == [] and not any("CapabilityPack output contract" in error for error in errors), errors


def test_a_template_rendered_from_build_context_is_verified_only_with_that_context():
    """operator_readiness renders .j2 templates from build context: without it they fail closed."""
    row = {"id": "operator_readiness", "capability_source": "config_file", "pack_source_path": str(BUILD_CONTEXT / "operator_readiness")}
    context = {"readiness_profile": "host_operator_platform", "capability_packs": [row]}
    files = {
        "app.json": "{}",
        **{item["filename"]: item["content"] for item in resolver.resolve_templates_for_pack(
            BUILD_CONTEXT / "operator_readiness", "operator_readiness", context_variables=context,
        )},
    }
    assert scanner.scan_generated_bundle(files, capability_packs=[row], context_variables=context) == []
    without_context = scanner.scan_generated_bundle(files, capability_packs=[row])
    assert sorted(error.split(":", 1)[0] for error in without_context) == sorted(path for path in files if path != "app.json")
    assert all("renders from build context, and this scan has none" in error for error in without_context)
    changed = {**files, "config/operator_readiness.yaml": files["config/operator_readiness.yaml"] + "# edited\n"}
    assert any(
        error.startswith("config/operator_readiness.yaml: differs from the verified template")
        for error in scanner.scan_generated_bundle(changed, capability_packs=[row], context_variables=context)
    )


def test_a_caller_cannot_promote_a_verified_pack_to_a_managed_capability():
    """The pack's declared capability_source governs the checks, never the caller's claimed one."""
    claimed = {"id": "messaging", "capability_source": "managed_capability"}
    [entry] = resolver.verified_selected_pack_sources([claimed])
    assert (entry["resolution"], entry["capability_source"]) == ("factory_build_context", "generated_module")
    config = "config/integrations/messaging.yaml"
    errors = scanner.scan_generated_bundle({"app.json": "{}", config: "service: messaging\n"}, capability_packs=[claimed])
    # No managed-capability layout extension admits the managed config path, and no managed checks run.
    assert any(config in error for error in errors), errors
    assert not any("Selected managed capability 'messaging'" in error for error in errors), errors


def test_one_pack_identity_precedence_across_resolver_scanner_and_layout():
    from mozaiksai.core.runtime.app.layout_validation import layout_extensions_from_selected_packs

    rows = [
        {"id": "a", "pack_id": "b", "capability_pack_id": "c"},
        {"pack_id": "b", "capability_pack_id": "c"},
        {"capability_pack_id": "c"},
        {"id": "a", "capability_pack_id": "c"},
    ]
    for row in rows:
        expected = resolver.selected_pack_id(row)
        assert scanner._pack_id_from_descriptor(row) == expected
        [extension, *_] = layout_extensions_from_selected_packs([{**row, "capability_source": "managed_capability"}])
        assert extension.pack_id == expected
    assert [resolver.selected_pack_id(row) for row in rows] == ["a", "b", "c", "a"]


def test_a_pack_selected_twice_resolves_once_or_fails_on_different_identities(tmp_path):
    rows = [dict(HOSTED_MOZAIKSPAY_ROW), _factory_pack("mozaikspay", capability_source="managed_capability")]
    assert [entry["pack_id"] for entry in resolver.verified_selected_pack_sources(rows)] == ["mozaikspay"]
    other = _messaging_copy(tmp_path / "messaging", version="0.0.1")
    with pytest.raises(resolver.PackIntegrityError, match="selected more than once with different identities"):
        resolver.verified_selected_pack_sources([_factory_pack("messaging"), {"id": "messaging", "pack_source_path": str(other)}])


def test_an_explicit_row_outside_the_verified_selection_cannot_supply_its_own_source():
    """Context selection is authoritative: a caller row with a path or pins it does not select is refused."""
    context = {"capability_packs": [_factory_pack("messaging")]}
    explicit = [_factory_pack("messaging"), _factory_pack("operator_readiness", digest="sha256:" + "0" * 64)]
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=explicit, context_variables=context)
    assert any(
        "explicit capability_packs rows ['operator_readiness']" in error and "outside the verified pack selection" in error
        for error in errors
    ), errors
    # The same row, selected by the context itself, is verified against its pins.
    selected = {"capability_packs": explicit}
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=explicit, context_variables=selected)
    assert any("selected digest does not match freshly verified source" in error for error in errors), errors


def test_an_explicit_row_matches_its_verified_selection_by_the_resolver_pack_id():
    """A row naming the pack by ``id`` and a different ``capability_pack_id`` takes the verified source of its ``id``."""
    explicit = [{**_factory_pack("messaging"), "capability_pack_id": "messages", "capability_source": "generated_module"}]
    context = {"capability_packs": [_factory_pack("messaging")]}
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=explicit, context_variables=context)
    assert not any("duplicate pack output claims" in error or "CapabilityPack output contract" in error for error in errors), errors
