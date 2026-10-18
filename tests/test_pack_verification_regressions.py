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


def _pinned(pack_id: str, root: Path) -> dict[str, str]:
    """An external source selected as any external pack must be: with the version and digest its bytes verify to."""
    metadata = resolver.verify_pack_integrity(root, pack_id)
    return {"id": pack_id, "pack_source_path": str(root), "version": metadata["version"], "digest": metadata["digest"]}


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
    selected = [_pinned("messaging", pack_root)]
    return resolver.verified_pack_data_contracts(resolver.verified_pack_template_inventory(selected))


def _write_app(app_root: Path, pack_root: Path, *, app_contract: dict | None) -> None:
    """Write pack templates and the composed UI roots and data contract, as assembly does."""
    ui_contributions: list[dict] = []
    files = {
        item["filename"]: item["content"]
        for item in resolver.resolve_managed_capability_templates(
            [_pinned("messaging", pack_root)], ui_contributions=ui_contributions,
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
            [_pinned("greetings", pack_root)], ui_contributions=contributions,
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
                [_pinned("greetings", shared_only)], ui_contributions=ui_contributions,
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
    assert (entry["resolution"], entry["authority"]) == ("installed", "installed")
    assert (entry["version"], entry["digest"]) == (installed.version, installed.digest)
    inventory = resolver.verified_pack_template_inventory(context_variables=context)
    rendered = {file["filename"]: file["content"] for file in inventory[0]["files"]}
    assert resolver.verified_template_module_ids(rendered, inventory) == {"messages"}

    # The installed source named by its own path is vouched for by installed state, without pins.
    explicit = {**context, "capability_packs": [{"id": "messaging", "pack_source_path": str(source)}]}
    [named] = resolver.verified_selected_pack_sources(context_variables=explicit)
    assert (named["resolution"], named["authority"], named["digest"]) == ("explicit", "installed", installed.digest)
    # Outside that workspace, the same path is an external source and needs pins.
    with pytest.raises(resolver.PackIntegrityError, match="without version and digest pins"):
        resolver.verified_selected_pack_sources([{"id": "messaging", "pack_source_path": str(source)}])
    # A pin never outlives its bytes.
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
    identity = {
        **{key: shipped[key] for key in ("pack_id", "version", "digest", "source")},
        "resolution": "factory_build_context", "authority": "factory_build_context",
    }

    [entry] = resolver.verified_selected_pack_sources([dict(HOSTED_MOZAIKSPAY_ROW)])
    assert resolver.pack_identity(entry) == identity
    assert Path(entry["source_path"]) == MOZAIKSPAY.resolve()
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
    genuine = _hosted_mozaikspay_bundle()
    monkeypatch.setattr(resolver, "resolve_factory_app_root", lambda: None)
    message = "the installed factory catalog cannot be located"
    with pytest.raises(resolver.PackIntegrityError, match=message):
        resolver.verified_selected_pack_sources([dict(HOSTED_MOZAIKSPAY_ROW)])
    result = _hosted_facade(genuine)
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


def test_bundle_provenance_that_records_another_version_fails():
    files = _hosted_mozaikspay_bundle()
    provenance = json.loads(files[PROVENANCE])
    provenance["packs"][0]["version"] = "0.0.9"
    files[PROVENANCE] = json.dumps(provenance)
    result = _hosted_facade(files)
    assert not result.passed
    assert any(
        error.startswith(f"{PROVENANCE}: packs[0] records pack 'mozaikspay' with version '0.0.9'")
        and "factory_build_context" in error and "run an AppGenerator revision" in error
        for error in _errors(result)
    ), _errors(result)


def test_the_provenance_digest_is_not_the_cross_host_identity():
    """The digest hashes raw source bytes, so it is no portable identity: id, version and content are checked."""
    files = _hosted_mozaikspay_bundle()
    provenance = json.loads(files[PROVENANCE])
    provenance["packs"][0]["digest"] = "sha256:" + "2" * 64
    files[PROVENANCE] = json.dumps(provenance)
    result = _hosted_facade(files)
    assert result.passed, _errors(result)


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
    assert [error.split(":", 1)[0] for error in errors] == ["modules/billing_portal/backend/service.py"], errors
    assert errors[0].startswith("modules/billing_portal/backend/service.py: differs from the verified template")


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


@pytest.mark.parametrize("profile", ["host_operator_platform", "marketplace_platform"])
def test_a_template_rendered_from_build_context_is_verified_through_its_declared_outputs(profile):
    """operator_readiness renders only .j2 templates: validation never renders them, with or without context."""
    row = {"id": "operator_readiness", "capability_source": "config_file", "pack_source_path": str(BUILD_CONTEXT / "operator_readiness")}
    build = {"readiness_profile": profile, "capability_packs": [row]}
    files = {
        "app.json": "{}",
        **{item["filename"]: item["content"] for item in resolver.resolve_templates_for_pack(
            BUILD_CONTEXT / "operator_readiness", "operator_readiness", context_variables=build,
        )},
    }
    # The build's context, a Studio candidate's {"app_id": ...} and no context at all verify the same way.
    for context in (build, {"app_id": "ops"}, None):
        assert scanner.scan_generated_bundle(files, capability_packs=[row], context_variables=context) == [], context
    # Each declared template-owned output must still be there.
    missing = {path: content for path, content in files.items() if path != "config/operator_readiness.yaml"}
    errors = scanner.scan_generated_bundle(missing, capability_packs=[row], context_variables={"app_id": "ops"})
    assert [error.split(":", 1)[0] for error in errors] == ["config/operator_readiness.yaml"], errors
    assert "template-owned output of the selected pack 'operator_readiness'" in errors[0]


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
        resolver.verified_selected_pack_sources([_factory_pack("messaging"), _pinned("messaging", other)])


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


# ------------------------------------------------------------------ explicit sources need an authority (rule 1)


REQUIRED_DISPATCH = "Self-hosted apps require an entitlement_dispatch module"


def _acme_billing(root: Path) -> Path:
    """An external pack whose own contract claims the subscription write path."""
    root.mkdir(parents=True)
    (root / "context.yaml").write_text(
        "context_id: acme_billing\n"
        "applies_to_workflows:\n  - AppGenerator\n"
        "assets:\n  - path: contract.yaml\n    kind: contract\n"
        "pack:\n  id: acme_billing\n  version: \"1.0.0\"\n  status: active\n  capability_source: managed_capability\n",
        encoding="utf-8",
    )
    (root / "contract.yaml").write_text(
        "contract_id: acme_billing\ncontract_type: build_pack_instructions\n"
        "provides_capabilities:\n  - subscription_write_path\n",
        encoding="utf-8",
    )
    return root


def test_an_external_pack_without_both_pins_is_refused_and_its_contract_grants_nothing(tmp_path):
    source = _acme_billing(tmp_path / "acme_billing")
    metadata = resolver.verify_pack_integrity(source, "acme_billing")
    files = {"config/subscriptions.yaml": HOSTED_SUBSCRIPTIONS}
    for pins in ({}, {"version": metadata["version"]}, {"digest": metadata["digest"]}):
        row = {"id": "acme_billing", "pack_source_path": str(source), **pins}
        with pytest.raises(resolver.PackIntegrityError, match="names the external source .* without .*pin"):
            resolver.verified_selected_pack_sources([row])
        with pytest.raises(resolver.PackIntegrityError, match="without .*pin"):
            resolver.resolve_declared_pack_output_paths([row])
        # The scan refuses the selection outright: the directory's own contract exempts nothing.
        errors = scanner.scan_generated_bundle(files, capability_packs=[row])
        assert len(errors) == 1 and "without" in errors[0] and "An unverified pack is never admitted" in errors[0], errors
        result = _hosted_facade(files, [row])
        assert not result.passed and result.resolved_capability_packs == []


def test_an_external_pack_with_matching_pins_is_vouched_for_by_them(tmp_path):
    source = _acme_billing(tmp_path / "acme_billing")
    metadata = resolver.verify_pack_integrity(source, "acme_billing")
    pinned = {"id": "acme_billing", "pack_source_path": str(source), "version": metadata["version"], "digest": metadata["digest"]}
    [entry] = resolver.verified_selected_pack_sources([pinned])
    assert (entry["resolution"], entry["authority"]) == ("explicit", "selected_pins")
    resolved: list[dict] = []
    errors = scanner.scan_generated_bundle(
        {"config/subscriptions.yaml": HOSTED_SUBSCRIPTIONS}, capability_packs=[pinned], resolved_capability_packs=resolved,
    )
    assert not any(REQUIRED_DISPATCH in error for error in errors), errors
    assert resolved == [{**{key: metadata[key] for key in ("pack_id", "version", "digest", "source")},
                         "resolution": "explicit", "authority": "selected_pins"}]
    # A pin the bytes do not verify to is refused.
    with pytest.raises(resolver.PackIntegrityError, match="selected digest does not match freshly verified source"):
        resolver.verified_selected_pack_sources([{**pinned, "digest": "sha256:" + "0" * 64}])


def test_an_explicit_path_inside_the_shipped_catalog_needs_no_pins_and_only_there(tmp_path):
    [entry] = resolver.verified_selected_pack_sources([_factory_pack("mozaikspay", capability_source="managed_capability")])
    assert (entry["resolution"], entry["authority"]) == ("explicit", "factory_build_context")
    # A path that normalizes onto a shipped pack directory is that directory.
    [alias] = resolver.verified_selected_pack_sources([{"id": "mozaikspay", "pack_source_path": str(MESSAGING / ".." / "mozaikspay")}])
    assert alias["authority"] == "factory_build_context"
    # A directory nested inside a shipped pack is not a catalog pack.
    with pytest.raises(resolver.PackIntegrityError, match="without version and digest pins"):
        resolver.verified_selected_pack_sources([{"id": "mozaikspay", "pack_source_path": str(MOZAIKSPAY / "templates")}])
    # An unpinned copy whose contract drops forbidden_outputs never becomes the identity.
    copy = tmp_path / "mozaikspay"
    shutil.copytree(MOZAIKSPAY, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    contract = copy / "contract.yaml"
    contract.write_text(re.sub(r"(?m)^forbidden_outputs:\n(?:  .*\n)*", "",contract.read_text(encoding="utf-8")), encoding="utf-8")
    row = {"id": "mozaikspay", "capability_source": "managed_capability", "pack_source_path": str(copy)}
    files = {**_hosted_mozaikspay_bundle(), "modules/wallet/module.yaml": "module:\n  id: wallet\nactions: []\n"}
    errors = scanner.scan_generated_bundle(files, capability_packs=[row])
    assert len(errors) == 1 and "without version and digest pins" in errors[0], errors


# ------------------------------------------------------------------ registered pack sources and shipped ids are claims


def _tampered_mozaikspay_bundle() -> dict[str, str]:
    files = _hosted_mozaikspay_bundle()
    files["modules/billing_portal/backend/base_handler.py"] += "\nimport os  # tampered\n"
    provenance = json.loads(files[PROVENANCE])
    provenance["packs"][0]["version"] = "9.9.9"
    provenance["packs"][0]["digest"] = "sha256:" + "3" * 64
    files[PROVENANCE] = json.dumps(provenance)
    files["modules/wallet/module.yaml"] = "module:\n  id: wallet\nactions: []\n"
    return files


@pytest.mark.parametrize("row", [
    {"id": "mozaikspay"},
    {"id": "mozaikspay", "capability_source": "config_file"},
    {"id": "mozaikspay", "capability_source": "operator_extension"},
    {"capability_pack_id": "mozaikspay", "capability_source": "host_universal"},
], ids=["no_capability_source", "config_file", "operator_extension", "other_non_module_source"])
def test_a_row_naming_a_shipped_pack_is_verified_and_never_silently_omitted(row):
    resolved: list[dict] = []
    errors = scanner.scan_generated_bundle(
        _tampered_mozaikspay_bundle(), capability_packs=[dict(row)], resolved_capability_packs=resolved,
    )
    assert [(item["pack_id"], item["authority"]) for item in resolved] == [("mozaikspay", "factory_build_context")]
    assert any(error.startswith(f"{PROVENANCE}: packs[0] records pack 'mozaikspay' with version '9.9.9'") for error in errors), errors
    assert any(error.startswith("modules/billing_portal/backend/base_handler.py: differs from the verified template") for error in errors)
    assert any("declares forbidden output prefixes" in error and "modules/wallet/module.yaml" in error for error in errors)


@pytest.mark.parametrize("source", ["config_file", "operator_extension"])
def test_a_registered_pack_source_that_names_no_pack_fails(source):
    row = {"id": "acme_settings", "capability_source": source}
    with pytest.raises(resolver.PackIntegrityError, match=f"capability_source '{source}'.*ships no pack at build_context/acme_settings"):
        resolver.verified_selected_pack_sources([row])


def test_only_planned_modules_and_unregistered_sources_naming_no_pack_select_nothing(caplog):
    caplog.set_level("DEBUG", logger=resolver.__name__)
    rows = [
        # A generated_module row is an app-owned module, whatever its id.
        {"capability_pack_id": "mozaikspay", "capability_source": "generated_module"},
        # No registered source and no shipped or installed pack of that id: there is no pack to omit.
        {"id": "acme", "capability_source": "community_pack"},
        {"id": "acme"},
    ]
    assert resolver.verified_selected_pack_sources(rows) == []
    assert caplog.records == []


# ------------------------------------------------------------------ identity does not depend on line endings


def _catalog_checkout(root: Path, newline: bytes) -> Path:
    """A factory root whose mozaikspay pack is checked out with ``newline`` line endings."""
    factory_root = root / "factory_app"
    copy = factory_root / "build_context" / "mozaikspay"
    shutil.copytree(MOZAIKSPAY, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for path in copy.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline))
    return factory_root


def test_crlf_and_lf_checkouts_of_a_pack_validate_the_same_bundle(tmp_path, monkeypatch):
    genuine = _hosted_mozaikspay_bundle()
    crlf_bundle = {path: content.replace("\n", "\r\n") for path, content in genuine.items()}
    identities = {}
    for name, newline in (("lf", b"\n"), ("crlf", b"\r\n")):
        factory_root = _catalog_checkout(tmp_path / name, newline)
        monkeypatch.setattr(resolver, "resolve_factory_app_root", lambda root=factory_root: root)
        for files in (genuine, crlf_bundle):
            result = _hosted_facade(files)
            assert result.passed, (name, _errors(result))
        [identities[name]] = result.resolved_capability_packs
    # The portable identity is the same; the digest hashes this host's raw bytes.
    assert identities["lf"]["version"] == identities["crlf"]["version"]
    assert identities["lf"]["digest"] != identities["crlf"]["digest"]
    # So a row-level digest pin keeps same-host semantics: one recorded on the LF checkout fails on the CRLF one.
    pinned = {**HOSTED_MOZAIKSPAY_ROW, "version": identities["lf"]["version"], "digest": identities["lf"]["digest"]}
    with pytest.raises(resolver.PackIntegrityError, match="selected digest does not match freshly verified source"):
        resolver.verified_selected_pack_sources([pinned])
    # A real content change still fails, whatever the line endings.
    service = tmp_path / "crlf/factory_app/build_context/mozaikspay/templates/modules/billing_portal/backend/service.py"
    service.write_bytes(service.read_bytes() + b"# changed after release\r\n")
    errors = _errors(_hosted_facade(crlf_bundle))
    assert [error.split(":", 1)[0] for error in errors] == ["modules/billing_portal/backend/service.py"], errors


# ------------------------------------------------------------------ the facade's functional checks see resolved packs


def test_the_facade_applies_functional_pack_requirements_only_to_resolved_packs():
    files = {"app.json": json.dumps({"appId": "billing", "appName": "Billing"})}
    facade_missing = "CAPABILITY_FACADE_MISSING"
    # A planned module that merely names mozaikspay selects no pack, so no MozaiksPay facade is required.
    planned = _hosted_facade(files, [{"capability_pack_id": "mozaikspay", "capability_source": "generated_module"}])
    assert planned.resolved_capability_packs == []
    assert not any(item.code == facade_missing for item in planned.diagnostics), planned.diagnostics
    # The resolved hosted row does require it.
    hosted = _hosted_facade(files)
    assert [item["pack_id"] for item in hosted.resolved_capability_packs] == ["mozaikspay"]
    assert any(item.code == facade_missing for item in hosted.diagnostics), hosted.diagnostics


@pytest.mark.asyncio
async def test_the_acceptance_gate_applies_functional_pack_requirements_only_to_resolved_packs():
    """Studio candidates go through the acceptance gate, whose functional checks also see only resolved packs."""
    from factory_app.workflows.AppGenerator.tools.app_validation import (
        run_app_bundle_acceptance_gate,
    )

    files = {"app.json": json.dumps({"appId": "billing", "appName": "Billing"})}

    def facade_missing(result: dict) -> list[dict]:
        return [item for item in result["failed_tests"] if item.get("test") == "CAPABILITY_FACADE_MISSING"]

    planned = await run_app_bundle_acceptance_gate(
        files=dict(files), context_variables={"app_id": "billing"},
        capability_packs=[{"capability_pack_id": "mozaikspay", "capability_source": "generated_module"}],
    )
    assert not facade_missing(planned), planned["failed_tests"]
    hosted = await run_app_bundle_acceptance_gate(
        files=dict(files), context_variables={"app_id": "billing"}, capability_packs=[dict(HOSTED_MOZAIKSPAY_ROW)],
    )
    assert facade_missing(hosted), hosted["failed_tests"]


# ------------------------------------------------------------------ workspace build-context operator packs


def _workspace_operator_pack(workspace: Path, *, status: str = "active", source: Path = GREETINGS) -> Path:
    """A pack (the greetings fixture by default) registered as an operator pack directly under a workspace build context."""
    root = workspace / source.name
    shutil.copytree(source, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    context = root / "context.yaml"
    text = context.read_text(encoding="utf-8").replace(
        "capability_source: generated_module", "capability_source: operator_pack",
    )
    context.write_text(text.replace("status: active", f"status: {status}"), encoding="utf-8")
    return root


def _registered_rows(workspace: Path) -> list[dict]:
    """The capability_packs rows the trusted build-context loader projects for AppGenerator from the workspace."""
    import yaml

    from mozaiksai.core.session.build_context import load_trusted_build_context
    from mozaiksai.core.workflow.context.authority import build_context_authority_policy

    definitions = yaml.safe_load(
        (BUILD_CONTEXT.parent / "workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"),
    )["definitions"]
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=definitions)
    return [dict(row) for row in load_trusted_build_context(policy, build_context_root=workspace)["capability_packs"]]


def _workspace_root_from(monkeypatch, workspace: Path, root_from: str) -> dict | None:
    """The trusted workspace root as a caller's build_context_root, or as the environment the launch loader reads."""
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    if root_from == "context":
        monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH", raising=False)
        return {"build_context_root": str(workspace)}
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(workspace))
    return None


@pytest.mark.parametrize("root_from", ["context", "environment"])
def test_a_registered_workspace_operator_pack_assembles_and_validates_through_the_workspace_registry(
    tmp_path, monkeypatch, root_from,
):
    """The documented flow: the projection carries pack_source_path and version, never a digest (the pack block
    cannot hold one), and the workspace registry vouches for the pack; the fresh digest is recorded."""
    from mozaiksai.control_plane.implementations.coding_worker import parent_workspace_pack_paths

    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace)
    [row] = _registered_rows(workspace)
    assert (row["pack_source_path"], row["version"], row["capability_source"]) == (str(root), "0.1.0", "operator_pack")
    assert "digest" not in row
    context = _workspace_root_from(monkeypatch, workspace, root_from)
    fresh = resolver.verify_pack_integrity(root, "greetings")
    identity = {
        **{key: fresh[key] for key in ("pack_id", "version", "digest", "source")},
        "resolution": "explicit", "authority": "workspace_build_context",
    }
    [entry] = resolver.verified_selected_pack_sources([row], context_variables=context)
    assert resolver.pack_identity(entry) == identity

    contributions: list[dict] = []
    files = {
        item["filename"]: item["content"]
        for item in resolver.resolve_managed_capability_templates(
            [row], context_variables=context, ui_contributions=contributions,
        )
    }
    files.update(compose_ui_registry(contributions, files))
    assert "modules/greetings/module.yaml" in files and "ui/pages/custom/GreetingsPage.jsx" in files
    resolved: list[dict] = []
    errors = scanner.scan_generated_bundle(
        files, capability_packs=[row], context_variables=context, resolved_capability_packs=resolved,
    )
    assert resolved == [identity]
    assert not any(
        "Selected CapabilityPack" in error or "verified template" in error or "template-owned output" in error
        or "pack_provenance" in error
        for error in errors
    ), errors
    # Studio refinement reads the saved row as a recorded selection: the unpinned projection row shows no bytes, so
    # it grants no ownership; the row the build records, pinned to the identity acceptance verified, does, with or
    # without the workspace root in the environment (its own pins then vouch for it).
    assert parent_workspace_pack_paths([row]) is None
    [recorded] = resolver.pin_workspace_pack_selections([row], resolved, context_variables=context)
    assert parent_workspace_pack_paths([recorded]) == frozenset({"modules/greetings/backend/handler.py"})


def test_a_workspace_pack_selection_whose_version_differs_from_the_registry_fails(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace)
    [row] = _registered_rows(workspace)
    context = _workspace_root_from(monkeypatch, workspace, "context")
    with pytest.raises(resolver.PackIntegrityError, match="selected version does not match freshly verified source"):
        resolver.verified_selected_pack_sources([{**row, "version": "0.0.9"}], context_variables=context)
    # The registry moved on after the selection was recorded: the recorded version no longer verifies.
    manifest = root / "context.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace('version: "0.1.0"', 'version: "0.2.0"'), encoding="utf-8")
    with pytest.raises(resolver.PackIntegrityError, match="selected version does not match freshly verified source"):
        resolver.resolve_declared_pack_output_paths([row], context_variables=context)
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[row], context_variables=context)
    assert len(errors) == 1 and "does not match freshly verified source" in errors[0], errors
    # A selection without the registered version is refused, never trusted on the registry alone.
    unversioned = {key: value for key, value in row.items() if key != "version"}
    with pytest.raises(resolver.PackIntegrityError, match="without the version its registration declares"):
        resolver.verified_selected_pack_sources([unversioned], context_variables=context)


def test_a_workspace_pack_outside_the_registry_needs_both_pins(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace)
    [row] = _registered_rows(workspace)
    context = _workspace_root_from(monkeypatch, workspace, "context")
    elsewhere = tmp_path / "elsewhere" / "greetings"
    nested = workspace / "vendor" / "greetings"
    for copy in (elsewhere, nested):
        shutil.copytree(root, copy)
        selection = {**row, "pack_source_path": str(copy)}
        with pytest.raises(resolver.PackIntegrityError, match="without digest pin") as refused:
            resolver.verified_selected_pack_sources([selection], context_variables=context)
        assert "not a pack registered directly under the workspace build context" in str(refused.value)
        errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[selection], context_variables=context)
        assert len(errors) == 1 and "An unverified pack is never admitted" in errors[0], errors
        # With both pins the copy is an ordinary pinned external source.
        [entry] = resolver.verified_selected_pack_sources([{**selection, **_pinned("greetings", copy)}], context_variables=context)
        assert entry["authority"] == "selected_pins"
    # Without a workspace root, the registered pack is an external source like any other.
    monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH", raising=False)
    with pytest.raises(resolver.PackIntegrityError, match="not in a workspace build context"):
        resolver.verified_selected_pack_sources([row])


def test_an_inactive_workspace_registration_vouches_for_nothing(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace, status="inactive")
    context = _workspace_root_from(monkeypatch, workspace, "context")
    row = {"id": "greetings", "capability_source": "operator_pack", "pack_source_path": str(root), "version": "0.1.0"}
    with pytest.raises(resolver.PackIntegrityError, match="load_trusted_build_context registers no pack 'greetings' at this path"):
        resolver.verified_selected_pack_sources([row], context_variables=context)


def _loader_registers(workspace: Path) -> list[str]:
    """The pack ids load_trusted_build_context registers for AppGenerator from ``workspace``; none when it refuses."""
    from mozaiksai.core.session.build_context import BuildContextError

    try:
        return [row["id"] for row in _registered_rows(workspace)]
    except (BuildContextError, KeyError):
        return []


def _sibling_context(workspace: Path, name: str, text: str) -> None:
    (workspace / name).mkdir(parents=True)
    # Schema-valid apart from what each case varies.
    (workspace / name / "context.yaml").write_text("assets: []\n" + text, encoding="utf-8")


_APPGENERATOR_SIBLING = "context_id: {name}\napplies_to_workflows:\n  - AppGenerator\n"


@pytest.mark.parametrize("registration", [
    "registered", "other_workflow_only", "no_projection", "projection_from_value", "inactive",
    "sibling_other_workflow_bad_projection",
    "own_undeclared_projection", "sibling_undeclared_projection", "sibling_capability_packs_value",
    "sibling_operator_capabilities_scalar", "pack_id_registered_twice",
])
def test_the_workspace_authority_vouches_exactly_for_what_the_trusted_loader_registers(
    tmp_path, monkeypatch, registration,
):
    """The trusted loader itself decides. A context retargeted to another workflow, one that no longer projects its
    packs, an inactive pack, and any root the loader refuses (an undeclared or malformed projection anywhere in it,
    an invalid operator_capabilities value, a pack id registered twice) register nothing for AppGenerator; the
    workspace authority then vouches for nothing either, and only both pins select the copy."""
    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace, status="inactive" if registration == "inactive" else "active")
    manifest = root / "context.yaml"
    text = manifest.read_text(encoding="utf-8")
    projection = "projections:\n  context_variables:\n    capability_packs:\n      from: capability_packs\n"
    assert projection in text
    if registration == "other_workflow_only":
        text = text.replace("applies_to_workflows:\n  - AppGenerator\n", "applies_to_workflows:\n  - AgentGenerator\n")
    elif registration == "no_projection":
        text = text.replace(projection, "")
    elif registration == "projection_from_value":
        text = text.replace(projection, "projections:\n  context_variables:\n    capability_packs:\n      value: []\n")
    elif registration == "own_undeclared_projection":
        text = text.replace(projection, projection + "    bogus_key: foo\n")
    elif registration == "sibling_other_workflow_bad_projection":
        # A context for another workflow is not part of AppGenerator's registration, so it does not refuse the root.
        _sibling_context(workspace, "other", "context_id: other\napplies_to_workflows:\n  - AgentGenerator\n"
                         "projections:\n  context_variables:\n    bogus_key: foo\n")
    elif registration == "sibling_undeclared_projection":
        _sibling_context(workspace, "other", _APPGENERATOR_SIBLING.format(name="other")
                         + "projections:\n  context_variables:\n    bogus_key: foo\n")
    elif registration == "sibling_capability_packs_value":
        _sibling_context(workspace, "other", _APPGENERATOR_SIBLING.format(name="other")
                         + "projections:\n  context_variables:\n    capability_packs:\n      value: []\n")
    elif registration == "sibling_operator_capabilities_scalar":
        _sibling_context(workspace, "ops", _APPGENERATOR_SIBLING.format(name="ops")
                         + "projections:\n  context_variables:\n    operator_capabilities: some_scalar\n"
                         "values:\n  some_scalar: 5\n")
    elif registration == "pack_id_registered_twice":
        _workspace_operator_pack(workspace / "copy")
        shutil.move(str(workspace / "copy" / "greetings"), str(workspace / "greetings_copy"))
        (workspace / "copy").rmdir()
    manifest.write_text(text, encoding="utf-8")
    context = _workspace_root_from(monkeypatch, workspace, "context")
    row = {"id": "greetings", "capability_source": "operator_pack", "pack_source_path": str(root), "version": "0.1.0"}

    registered = _loader_registers(workspace)
    vouched = registration in {"registered", "sibling_other_workflow_bad_projection"}
    assert registered == (["greetings"] if vouched else [])
    if registered:
        [entry] = resolver.verified_selected_pack_sources([row], context_variables=context)
        assert entry["authority"] == "workspace_build_context"
        return
    with pytest.raises(resolver.PackIntegrityError, match="without digest pin") as refused:
        resolver.verified_selected_pack_sources([row], context_variables=context)
    assert "it is directly under it, but" in str(refused.value)
    reason = {
        "other_workflow_only": "registers no pack 'greetings' at this path",
        "no_projection": "registers no pack 'greetings' at this path",
        "inactive": "registers no pack 'greetings' at this path",
        "projection_from_value": "capability_packs must project registered pack declarations",
        "own_undeclared_projection": "Undeclared build-context projection 'bogus_key'",
        "sibling_undeclared_projection": "Undeclared build-context projection 'bogus_key'",
        "sibling_capability_packs_value": "capability_packs must project registered pack declarations",
        "sibling_operator_capabilities_scalar": "Invalid trusted build-context value for 'operator_capabilities'",
        "pack_id_registered_twice": "Conflicting registered capability pack 'greetings'",
    }[registration]
    assert reason in str(refused.value), str(refused.value)
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[row], context_variables=context)
    assert len(errors) == 1 and "An unverified pack is never admitted" in errors[0], errors
    [pinned] = resolver.verified_selected_pack_sources([{**row, **_pinned("greetings", root)}], context_variables=context)
    assert pinned["authority"] == "selected_pins"


def test_a_recorded_build_pins_its_workspace_packs_to_the_identity_acceptance_verified(tmp_path, monkeypatch):
    """The registry vouches at a version; the recorded rows carry the digest acceptance verified, so an in-place edit
    that keeps the version fails every later use instead of being trusted on the registry alone."""
    from mozaiksai.control_plane.implementations.coding_worker import (
        pack_output_owners,
        parent_pack_output_owners,
    )

    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace)
    [row] = _registered_rows(workspace)
    catalog_row = dict(HOSTED_MOZAIKSPAY_ROW)
    _workspace_root_from(monkeypatch, workspace, "environment")
    resolved: list[dict] = []
    scanner.scan_generated_bundle(
        {"app.json": "{}"}, capability_packs=[row, catalog_row], resolved_capability_packs=resolved,
    )
    assert {identity["pack_id"]: identity["authority"] for identity in resolved} == {
        "greetings": "workspace_build_context", "mozaikspay": "factory_build_context",
    }
    fresh = resolver.verify_pack_integrity(root, "greetings")

    recorded_row, recorded_catalog = resolver.pin_workspace_pack_selections([row, catalog_row], resolved)
    assert (recorded_row["version"], recorded_row["digest"]) == (fresh["version"], fresh["digest"])
    assert {key: value for key, value in recorded_row.items() if key != "digest"} == row
    # A shipped catalog row is the installed package: it is recorded as selected, without a host-dependent digest.
    assert recorded_catalog == catalog_row
    owners = pack_output_owners([recorded_row])
    assert parent_pack_output_owners([recorded_row]) == owners
    [entry] = resolver.verified_selected_pack_sources([recorded_row])
    assert entry["authority"] == "workspace_build_context"

    # The same version, edited in place: the recorded digest no longer verifies, for admission and for Studio.
    template = sorted(path for path in (root / "templates").rglob("*") if path.is_file())[0]
    original = template.read_bytes()
    template.write_bytes(original + b"\n# edited in place\n")
    assert resolver.verify_pack_integrity(root, "greetings")["version"] == fresh["version"]
    with pytest.raises(resolver.PackIntegrityError, match="selected digest does not match freshly verified source"):
        resolver.verified_selected_pack_sources([recorded_row])
    with pytest.raises(resolver.PackIntegrityError, match="selected digest does not match"):
        pack_output_owners([recorded_row])
    assert parent_pack_output_owners([recorded_row]) is None
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[recorded_row])
    assert len(errors) == 1 and "does not match freshly verified source" in errors[0], errors

    # Restored, the recorded row stays verifiable even after its registration is withdrawn: its own pins vouch.
    template.write_bytes(original)
    manifest = root / "context.yaml"
    withdrawn = manifest.read_bytes().replace(b"  - AppGenerator", b"  - AgentGenerator")
    manifest.write_bytes(withdrawn)
    retargeted = {**recorded_row, "digest": resolver.verify_pack_integrity(root, "greetings")["digest"]}
    [pinned] = resolver.verified_selected_pack_sources([retargeted])
    assert pinned["authority"] == "selected_pins"


def test_a_recorded_selection_of_a_workspace_pack_must_carry_its_digest(tmp_path, monkeypatch):
    """A build recorded before builds pinned workspace packs (or a refinement that copied its rows) shows no bytes, so
    its rows are refused when read back as a recorded selection, while a launch selection still resolves."""
    from mozaiksai.control_plane.implementations.coding_worker import (
        pack_output_owners,
        pack_template_source,
    )

    workspace = tmp_path / "workspace" / "build_context"
    _workspace_operator_pack(workspace)
    [row] = _registered_rows(workspace)
    _workspace_root_from(monkeypatch, workspace, "environment")
    [entry] = resolver.verified_selected_pack_sources([row])
    assert entry["authority"] == "workspace_build_context"
    for read in (
        lambda: resolver.verified_selected_pack_sources([row], recorded=True),
        lambda: pack_output_owners([row]),
        lambda: pack_template_source([row], "modules/greetings/module.yaml"),
    ):
        with pytest.raises(resolver.PackIntegrityError, match="carries no digest.*Run an AppGenerator revision"):
            read()
    [pinned] = resolver.pin_workspace_pack_selections([row], [resolver.pack_identity(entry)])
    assert resolver.verified_selected_pack_sources([pinned], recorded=True)[0]["digest"] == entry["digest"]


def test_every_recorded_row_under_the_workspace_root_is_pinned_whatever_authority_resolved_it(tmp_path, monkeypatch):
    """A pack installed in place under the workspace root is accepted as `installed` when the caller names the
    build_context_root, but a later reader without it resolves the same directory through the registry: the recorded
    row must still carry the digest acceptance verified."""
    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace)
    [row] = _registered_rows(workspace)
    context = _workspace_root_from(monkeypatch, workspace, "context")
    fresh = resolver.verify_pack_integrity(root, "greetings")
    installed = {**{key: fresh[key] for key in ("pack_id", "version", "digest", "source")},
                 "resolution": "explicit", "authority": "installed"}
    [recorded] = resolver.pin_workspace_pack_selections([row], [installed], context_variables=context)
    assert (recorded["version"], recorded["digest"]) == (fresh["version"], fresh["digest"])


def test_a_workspace_root_that_is_the_shipped_catalog_records_its_packs_as_selected(monkeypatch):
    """Shipped packs follow the installed catalog across upgrades in every workspace, including one rooted at it."""
    shipped = resolver.verify_pack_integrity(MESSAGING, "messaging")
    row = {"id": "messaging", "pack_source_path": str(MESSAGING), "version": shipped["version"]}
    identity = {**{key: shipped[key] for key in ("pack_id", "version", "digest", "source")},
                "resolution": "explicit", "authority": "factory_build_context"}
    context = {"build_context_root": str(BUILD_CONTEXT)}
    assert resolver.pin_workspace_pack_selections([row], [identity], context_variables=context) == [row]


@pytest.mark.parametrize("error", ["context_authority", "value", "validation"])
def test_unloadable_shipped_declarations_vouch_for_no_workspace_pack(tmp_path, monkeypatch, error):
    """Whatever an invalid declaration raises while the policy is built, the workspace authority refuses with a
    diagnostic instead of escaping (Studio would answer 500)."""
    from pydantic import BaseModel

    from mozaiksai.core.workflow.context.authority import ContextAuthorityError

    workspace = tmp_path / "workspace" / "build_context"
    root = _workspace_operator_pack(workspace)
    context = _workspace_root_from(monkeypatch, workspace, "context")

    class _Declaration(BaseModel):
        writer_id: int

    def refuse(**kwargs):
        if error == "context_authority":
            raise ContextAuthorityError("context_authority.invalid_declaration")
        if error == "value":
            raise ValueError("runtime context must be immutable_runtime_authority")
        _Declaration(writer_id="not-a-writer")

    monkeypatch.setattr("mozaiksai.core.workflow.context.authority.build_context_authority_policy", refuse)
    row = {"id": "greetings", "capability_source": "operator_pack", "pack_source_path": str(root), "version": "0.1.0"}
    with pytest.raises(resolver.PackIntegrityError, match="build-context declarations could not be read"):
        resolver.verified_selected_pack_sources([row], context_variables=context)


def _undeclared_contract_pack(workspace: Path) -> Path:
    """The greetings pack, registered under a workspace, whose context does not declare contract.yaml as an asset."""
    root = _workspace_operator_pack(workspace)
    manifest = root / "context.yaml"
    text = manifest.read_text(encoding="utf-8")
    declared = "  - path: contract.yaml\n    kind: contract\n"
    assert declared in text
    manifest.write_text(text.replace(declared, ""), encoding="utf-8")
    return root


def test_the_pack_digest_covers_contract_yaml_even_when_the_context_does_not_declare_it(tmp_path, monkeypatch):
    """contract.yaml decides output ownership whether or not it is a declared asset, so an in-place edit of it that
    keeps the version changes the digest, and a recorded row pinned to the old digest no longer verifies."""
    workspace = tmp_path / "workspace" / "build_context"
    root = _undeclared_contract_pack(workspace)
    [row] = _registered_rows(workspace)
    _workspace_root_from(monkeypatch, workspace, "environment")
    [entry] = resolver.verified_selected_pack_sources([row])
    [recorded] = resolver.pin_workspace_pack_selections([row], [resolver.pack_identity(entry)])
    contract = root / "contract.yaml"
    contract.write_bytes(contract.read_bytes() + b"\n# owner re-declared in place\n")
    after = resolver.verify_pack_integrity(root, "greetings")
    assert after["version"] == entry["version"] and after["digest"] != entry["digest"]
    with pytest.raises(resolver.PackIntegrityError, match="selected digest does not match freshly verified source"):
        resolver.verified_selected_pack_sources([recorded], recorded=True)


def test_every_shipped_pack_declares_its_contract_so_its_digest_is_unchanged_by_the_contract_rule():
    """The digest always covers contract.yaml; every shipped pack already declares it, so no shipped identity moved."""
    import yaml

    undeclared = []
    for context_path in sorted(BUILD_CONTEXT.glob("*/context.yaml")):
        context = yaml.safe_load(context_path.read_text(encoding="utf-8")) or {}
        if not isinstance(context.get("pack"), dict) or not (context_path.parent / "contract.yaml").is_file():
            continue
        assets = {(str(a.get("path")), str(a.get("kind"))) for a in context.get("assets") or [] if isinstance(a, dict)}
        if ("contract.yaml", "contract") not in assets:
            undeclared.append(context_path.parent.name)
    assert undeclared == []


def test_a_contract_asset_that_is_not_a_file_is_an_integrity_diagnostic(tmp_path):
    root = tmp_path / "greetings"
    shutil.copytree(GREETINGS, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    manifest = root / "context.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  - path: contract.yaml\n    kind: contract\n", "  - path: templates/\n    kind: contract\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(resolver.PackIntegrityError, match="Contract asset must be a file"):
        resolver.verify_pack_integrity(root, "greetings")
    row = {"id": "greetings", "pack_source_path": str(root), "version": "0.1.0", "digest": "sha256:" + "0" * 64}
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[row])
    assert len(errors) == 1 and "Contract asset must be a file" in errors[0], errors


def test_a_workspace_pack_without_an_identity_from_acceptance_cannot_be_recorded(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace" / "build_context"
    _workspace_operator_pack(workspace)
    [row] = _registered_rows(workspace)
    context = _workspace_root_from(monkeypatch, workspace, "context")
    for identities in (None, [], [{"pack_id": "other", "authority": "workspace_build_context"}]):
        with pytest.raises(resolver.PackIntegrityError, match="acceptance recorded no verified identity"):
            resolver.pin_workspace_pack_selections([row], identities, context_variables=context)
    [identity] = [
        resolver.pack_identity(entry)
        for entry in resolver.verified_selected_pack_sources([row], context_variables=context)
    ]
    with pytest.raises(resolver.PackIntegrityError, match="differs from the .* acceptance verified"):
        resolver.pin_workspace_pack_selections([{**row, "version": "0.0.9"}], [identity], context_variables=context)
    # Rows that name no workspace pack need no identity: a generated module, or a catalog row.
    generated = {"id": "notes", "capability_source": "generated_module"}
    assert resolver.pin_workspace_pack_selections(
        [generated, dict(HOSTED_MOZAIKSPAY_ROW)], None, context_variables=context,
    ) == [generated, dict(HOSTED_MOZAIKSPAY_ROW)]


# ------------------------------------------------------------------ planning reads only verified packs


def test_planning_reads_a_selected_pack_only_after_the_resolver_verified_it(caplog):
    """Pack-owned outputs, facades and managed contracts come only from a verified selection, never a fallback."""
    from mozaiksai.core.workflow.generator_support import module_action_inventory as inventory

    shipped = resolver.verify_pack_integrity(MOZAIKSPAY, "mozaikspay")
    pinned = {**HOSTED_MOZAIKSPAY_ROW, "version": shipped["version"], "digest": shipped["digest"]}
    [selected] = inventory._selected_packs({"capability_packs": [pinned]})
    assert selected.identity == {
        **{key: shipped[key] for key in ("pack_id", "version", "digest", "source")},
        "resolution": "factory_build_context", "authority": "factory_build_context",
    }
    assert "ui/pages/pricing.yaml" in inventory.pack_owned_output_paths({"capability_packs": [pinned]})
    assert "billing_portal" in inventory.managed_facade_actions({"capability_packs": [pinned]})

    # A selection the resolver refuses (here a digest pin the source does not verify to) yields nothing to plan
    # with: not its unverified source, not the projected operator contracts, not its inline facades.
    caplog.set_level("WARNING", logger=inventory.__name__)
    projected = [{"contract_id": "mozaikspay", "required_outputs": [{"path": "ui/pages/pricing.yaml", "owner": "templates"}]}]
    facades = [{"module_id": "billing_portal", "pages": [{"primary_actions": ["list_plans"]}]}]
    refused = {**pinned, "digest": "sha256:" + "0" * 64, "facades": facades}
    context = {"capability_packs": [refused], "operator_contracts": projected}
    assert inventory._selected_packs(context) == []
    assert inventory.pack_owned_output_paths(context) == frozenset()
    assert inventory.pack_facade_directories(context) == []
    assert any("SELECTED_PACK_UNRESOLVED pack=mozaikspay" in record.getMessage() for record in caplog.records)
    # What a managed pack owns (ownership, managed data, facade actions) is enforced from its contract, so a
    # refused managed selection is never silently omitted there: it raises with the resolver's diagnostic.
    for read in (inventory.managed_pack_contracts, inventory.managed_facade_actions):
        with pytest.raises(ValueError, match="Selected managed pack 'mozaikspay' cannot be verified.*selected digest"):
            read(context)

    # A managed row that resolves nowhere grants no facade from its own inline declaration.
    inline = {"id": "acme_pay", "capability_source": "managed_capability", "facades": facades}
    with pytest.raises(ValueError, match="Selected managed pack 'acme_pay' cannot be verified"):
        inventory.managed_pack_contracts({"capability_packs": [inline]})
    # The id-only managed row's contracts are the shipped catalog's verified contract assets.
    contracts = inventory.managed_pack_contracts({"capability_packs": [dict(HOSTED_MOZAIKSPAY_ROW)]})
    assert "mozaikspay" in {contract["contract_id"] for contract in contracts}


# ------------------------------------------------------------------ malformed rows


@pytest.mark.parametrize("row", [
    {"capability_id": "mozaikspay"},
    {"module_id": "mozaikspay", "capability_source": "managed_capability"},
    {"module_id": "orders", "capability_source": "generated_module"},
], ids=["capability_id", "module_id_managed", "module_id_planned"])
def test_a_row_naming_its_pack_only_under_a_non_canonical_key_is_refused_as_malformed(row, caplog):
    """No producer emits such a row, so it is malformed: refused with a diagnostic, never silently a non-selection."""
    from mozaiksai.core.workflow.generator_support import module_action_inventory as inventory

    message = "Malformed capability_packs row: it names a pack only under"
    with pytest.raises(resolver.PackIntegrityError, match=message):
        resolver.verified_selected_pack_sources([dict(row)])
    with pytest.raises(resolver.PackIntegrityError, match=message):
        resolver.resolve_declared_pack_output_paths([dict(row)])
    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=[dict(row)])
    assert len(errors) == 1 and message in errors[0], errors
    result = _hosted_facade(_hosted_mozaikspay_bundle(), [row])
    assert not result.passed and result.resolved_capability_packs == []
    caplog.set_level("WARNING", logger=inventory.__name__)
    assert inventory.pack_owned_output_paths({"capability_packs": [dict(row)]}) == frozenset()
    assert any(message in record.getMessage() for record in caplog.records)


def test_a_canonical_id_beside_a_non_canonical_key_is_an_ordinary_selection():
    [entry] = resolver.verified_selected_pack_sources([{"id": "mozaikspay", "module_id": "billing_portal"}])
    assert (entry["pack_id"], entry["authority"]) == ("mozaikspay", "factory_build_context")



# ------------------------------------------------------------------ owner composition (a)


def test_a_verified_selection_materializes_and_passes_canonical_validation_with_its_identities(tmp_path, monkeypatch):
    """Selection -> materialization -> canonical validation, across authorities, as one flow.

    The hosted id-only MozaiksPay row resolves to the shipped catalog, and a
    Messaging copy registered as an operator pack in the workspace build
    context resolves to the workspace registry. Assembly materializes and
    composes both; the validation facade passes the bundle and returns both
    identities with their authorities. A tampered template-owned output fails
    the same facade, and so does the same selection once its workspace
    authority cannot be resolved.
    """
    import yaml

    from mozaiksai.core.workflow.generator_support.subscription_data_contract import (
        ensure_subscription_assignment_stores,
    )

    workspace = tmp_path / "workspace" / "build_context"
    _workspace_operator_pack(workspace, source=MESSAGING)
    [messaging] = _registered_rows(workspace)
    assert (messaging["capability_source"], "digest" in messaging) == ("operator_pack", False)
    _workspace_root_from(monkeypatch, workspace, "environment")
    rows = [dict(HOSTED_MOZAIKSPAY_ROW), messaging]

    contributions: list[dict] = []
    emitted = resolver.resolve_managed_capability_templates(
        rows, context_variables={"build_timestamp": "2026-10-18T00:00:00Z"}, ui_contributions=contributions,
    )
    shared = compose_ui_registry(contributions, {item["filename"]: item["content"] for item in emitted})
    files = {item["filename"]: item["content"] for item in _merge_code_files(
        [{"code_files": emitted}, {"code_files": [{"filename": path, "content": content} for path, content in sorted(shared.items())]}],
        app_build_plan={"capability_packs": [{
            "capability_pack_id": "messages", "capability_source": "generated_module",
            "primary_entities": ["Thread", "Message", "ThreadRead"],
        }]},
        data_contract=dict(EMPTY_CONTRACT), context_variables={"capability_packs": rows},
    )}
    files["app.json"] = json.dumps({"appId": "billing", "appName": "Billing"})
    files["config/subscriptions.yaml"] = HOSTED_SUBSCRIPTIONS
    files[DATA_CONTRACT] = json.dumps(ensure_subscription_assignment_stores(
        json.loads(files[DATA_CONTRACT]),
        {"contract_required": True, "subscription_config_file": yaml.safe_load(HOSTED_SUBSCRIPTIONS)},
    ))
    assert {pack["pack_id"] for pack in json.loads(files[PROVENANCE])["packs"]} == {"mozaikspay", "messaging"}

    result = _hosted_facade(files, rows)
    assert result.passed, _errors(result)
    assert [(item["pack_id"], item["resolution"], item["authority"]) for item in result.resolved_capability_packs] == [
        ("mozaikspay", "factory_build_context", "factory_build_context"),
        ("messaging", "explicit", "workspace_build_context"),
    ]

    policy = "modules/messages/backend/policy.py"
    tampered = _hosted_facade({**files, policy: files[policy] + "\nimport os  # tampered\n"}, rows)
    assert not tampered.passed
    assert any(error.startswith(f"{policy}: differs from the verified template of the selected pack 'messaging'")
               for error in _errors(tampered)), _errors(tampered)

    monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH")
    unresolved = _hosted_facade(files, rows)
    assert not unresolved.passed and unresolved.resolved_capability_packs == []
    assert any("not in a workspace build context" in error for error in _errors(unresolved)), _errors(unresolved)
