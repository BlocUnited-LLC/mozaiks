"""Standalone verified scans preserve dictionary template inputs and ownership."""

import json
import shutil
from pathlib import Path
from types import MappingProxyType

import yaml

from factory_app.workflows._shared.pack_selection import PhysicalPackContext, available_pack_rows
from factory_app.workflows.AppGenerator.tools import generated_bundle_scanner as scanner
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    verify_pack_integrity,
)
from tests.reviewed_pack_fixture import registered_pack_fixture, reviewed_pack_fixture


def test_physical_dictionary_projection_detaches_frozen_nested_values():
    base = {"custom": MappingProxyType({"labels": ("original",)}), "capability_packs": []}
    view = scanner._resolved_pack_context(base, [])
    data = view.data
    data["custom"]["labels"].append("mutated")
    assert view.data["custom"] == {"labels": ["original"]}
    assert base["custom"]["labels"] == ("original",)


def test_planned_data_scan_preserves_custom_template_input_and_defensive_rows(tmp_path):
    root = tmp_path / "greetings"
    shutil.copytree(Path(__file__).parent / "fixtures/community_packs/greetings", root)
    contract = root / "contract.yaml"
    contract.write_text(contract.read_text(encoding="utf-8").replace(
        "required_outputs:\n", "required_outputs:\n  - path: data/contract.json\n    owner: templates\n",
    ), encoding="utf-8")
    template = root / "templates/data/contract.json.j2"
    template.parent.mkdir()
    planned = {"version": "1", "surfaces": [{"surface_id": "greetings", "surface_kind": "module",
               "collections": [{"name": "greetings", "scope": "app", "entity": "Greeting",
                                "tenancy": "app_wide", "owner_field": None,
                                "fields": [{"name": "custom_greeting", "type": "string", "required": True}],
                                "indexes": []}]}]}
    template.write_text(
        json.dumps(planned).replace("custom_greeting", "{{ custom_field }}"),
        encoding="utf-8",
    )
    pin = verify_pack_integrity(root, "greetings")
    row = {"id": "greetings", "pack_source_path": str(root),
           "version": pin["version"], "digest": pin["digest"]}
    base = {"custom_field": "custom_greeting", "capability_packs": []}
    entries = scanner._resolve_scan_packs([row], base)
    view = scanner._resolved_pack_context(base, scanner._resolved_pack_rows(entries))
    projected = view.data
    projected["custom_field"] = "caller-mutation"
    projected["capability_packs"].clear()
    assert view.data["custom_field"] == "custom_greeting"
    assert len(view.data["capability_packs"]) == 1
    assert base == {"custom_field": "custom_greeting", "capability_packs": []}

    files = {"app.json": '{"appId":"physical-scan-app"}', "data/contract.json": json.dumps(planned)}
    assert scanner._scan_planned_data_fields(files, planned, context_variables=view) == []
    errors = scanner.scan_generated_bundle(files, capability_packs=[row],
                                           planned_data_contract=planned, context_variables=base)
    assert not any(error.startswith("data/contract.json:") for error in errors), errors


def test_bound_bundle_scan_preserves_approved_plan_template_inputs(tmp_path, monkeypatch):
    pack_id = "planned_notes"
    registered = registered_pack_fixture(tmp_path, monkeypatch, pack_id, "config_file")
    root = Path(registered["pack_source_path"])
    context_file = root / "context.yaml"
    descriptor = yaml.safe_load(context_file.read_text(encoding="utf-8"))
    descriptor["assets"].append({"path": "templates", "kind": "templates"})
    context_file.write_text(yaml.safe_dump(descriptor), encoding="utf-8")
    path = "docs/planned-notes.md"
    template = root / "templates" / (path + ".j2")
    template.parent.mkdir(parents=True)
    template.write_text("# {{ app_build_plan.description }}\n", encoding="utf-8")
    contract_file = root / "contract.yaml"
    contract = yaml.safe_load(contract_file.read_text(encoding="utf-8"))
    contract["required_outputs"] = [{"path": path, "owner": "templates"}]
    contract_file.write_text(yaml.safe_dump(contract), encoding="utf-8")

    pack = available_pack_rows({}, pack_ids={pack_id})[pack_id]
    data_contract = {"version": "1", "surfaces": [], "shared_collections": []}
    approved = reviewed_pack_fixture({
        "app_id": "reviewed-scan-app",
        "app_build_plan": {"description": "Reviewed plan", "capability_packs": [pack]},
        "data_contract": data_contract,
        "capability_packs": [],
    }, [pack_id])
    bound_context = PhysicalPackContext(approved)
    files = {
        "app.json": '{"appId":"reviewed-scan-app"}',
        "data/contract.json": json.dumps(data_contract),
        path: "# Reviewed plan\n",
    }

    errors = scanner.scan_generated_bundle(
        files, capability_packs=[pack], planned_data_contract=data_contract, context_variables=bound_context,
    )

    assert not any(error.startswith("data/contract.json:") for error in errors), errors
    scan_context = scanner._resolved_pack_context(
        bound_context, scanner._resolved_pack_rows(scanner._resolve_scan_packs([pack], bound_context)),
    )
    projected = scan_context.data
    assert projected["data_contract"] == data_contract
    assert len(projected["capability_packs"]) == 1
    projected["app_build_plan"]["description"] = "caller mutation"
    projected["capability_packs"].clear()
    assert scan_context.data["app_build_plan"]["description"] == "Reviewed plan"
    assert len(scan_context.data["capability_packs"]) == 1
    assert approved["capability_packs"] == []
