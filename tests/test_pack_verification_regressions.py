"""Selected-pack verification stays consistent across scans, trust, and upgrades."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools import generated_bundle_scanner as scanner
from factory_app.workflows.AppGenerator.tools import (
    resolve_managed_capability_templates as resolver,
)
from factory_app.workflows.AppGenerator.tools.community_component_lifecycle import (
    CommunityComponentConflictError,
    apply_component_upgrade,
    install_verified_local_pack,
    plan_component_upgrade,
)
from mozaiksai.core.validation import validate_generated_app_candidate
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    materialize_data_migrations,
)

BUILD_CONTEXT = Path(__file__).resolve().parents[1] / "factory_app/build_context"
MESSAGING = BUILD_CONTEXT / "messaging"
DATA_CONTRACT = "data/contract.json"
EMPTY_CONTRACT = {"version": "1", "surfaces": [], "shared_collections": []}
PROJECTS = {"surface_id": "projects", "surface_kind": "module", "collections": [{
    "name": "projects", "entity": "Project", "scope": "app", "tenancy": "per_user",
    "owner_field": "owner", "fields": [{"name": "owner", "type": "string", "required": True}],
}]}


def _factory_pack(pack_id: str, **extra: str) -> dict[str, str]:
    return {"id": pack_id, "pack_source_path": str(BUILD_CONTEXT / pack_id), **extra}


def _messaging_copy(root: Path, *, version: str, extra_field: bool = False, without_data: bool = False) -> Path:
    shutil.copytree(MESSAGING, root, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    context = root / "context.yaml"
    text = context.read_text(encoding="utf-8")
    assert 'version: "0.1.13"' in text
    context.write_text(text.replace('version: "0.1.13"', f'version: "{version}"'), encoding="utf-8")
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
    """Write pack templates and the composed data contract, as assembly does."""
    files = {
        item["filename"]: item["content"]
        for item in resolver.resolve_managed_capability_templates(
            [{"id": "messaging", "pack_source_path": str(pack_root)}]
        )
    }
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
    other = {"pack_id": "other_pack", "version": "1.0.0", "source": "local", "digest": "sha256:" + "a" * 64}
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
    greetings = Path(__file__).resolve().parent / "fixtures/community_packs/greetings"
    build_context_root, app_root, root = tmp_path / "build_context", tmp_path / "app", "ui/route_manifest.json"
    v2 = tmp_path / "greetings_v2"
    shutil.copytree(greetings, v2)
    (v2 / "context.yaml").write_text(
        (v2 / "context.yaml").read_text(encoding="utf-8").replace('version: "0.1.0"', 'version: "0.2.0"'), encoding="utf-8",
    )
    service = v2 / "templates/modules/greetings/backend/service.py"
    service.write_text(service.read_text(encoding="utf-8") + "# v2\n", encoding="utf-8")
    install_verified_local_pack(pack_source_path=greetings, build_context_root=build_context_root)
    for item in resolver.resolve_managed_capability_templates([{"id": "greetings", "pack_source_path": str(greetings)}]):
        (app_root / item["filename"]).parent.mkdir(parents=True, exist_ok=True)
        (app_root / item["filename"]).write_text(item["content"], encoding="utf-8")
    # Assembly composed the route manifest from this pack and another contributor.
    provenance_path = app_root / ".mozaiks/pack_provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    entry = provenance["packs"][0]
    entry["materialized_owned_files"] = [row for row in entry["materialized_owned_files"] if row["path"] != root]
    other = {"pack_id": "other_pack", "version": "1.0.0", "source": "local", "digest": "sha256:" + "a" * 64}
    provenance["packs"].append({**other, "materialized_owned_files": []})
    provenance["composed_outputs"] = {root: [{key: entry[key] for key in other}, other]}
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
