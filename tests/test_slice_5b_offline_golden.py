from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.core.runtime.app.page_schema import load_app_page_schemas
from mozaiksai.core.runtime.persistence.app_data import load_app_data_contract
from mozaiksai.core.semantics.composition_ledger import compose_plan_artifacts
from tests.slice_5b_composition_helpers import composition_fixture


def test_offline_executable_artifact_composition_golden(tmp_path: Path) -> None:
    fixture = composition_fixture()
    assert fixture["admission"].logical_participant_key == "DatabaseAgent"
    composed = compose_plan_artifacts(
        plan=fixture["successor"],
        resolver=fixture["resolver"],
        assignments=fixture["assignments"],
        assignment_results=(fixture["result"],),
        materialized_bundle=fixture["materialized"],
        base_revision_digest=fixture["base_revision_digest"],
        base_plan=fixture["base"],
        base_outputs=fixture["base_outputs"],
        regeneration_closure=fixture["closure"],
    )

    app_files = composed.files()
    for relative_path, content in app_files.items():
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    assert load_app_data_contract(tmp_path)["app_id"] == "slice-5b-golden"
    assert set(load_app_page_schemas(tmp_path)) == {"home"}
    assert json.loads(app_files["data/contract.json"])["version"] == "1"
    removed_paths = {
        item.artifact.address.path for item in composed.ledger.removed_base_artifacts
    }
    assert removed_paths.isdisjoint(app_files)
