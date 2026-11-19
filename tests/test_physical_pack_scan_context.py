"""Standalone verified scans preserve dictionary template inputs and ownership."""

import json
import shutil
from pathlib import Path

from factory_app.workflows.AppGenerator.tools import generated_bundle_scanner as scanner
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    verify_pack_integrity,
)


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
