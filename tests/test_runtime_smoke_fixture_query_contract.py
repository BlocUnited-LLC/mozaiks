"""The recorded runtime-smoke fixture retains runnable current page contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.workflow.generator_support.page_data_bindings import page_query_binding_errors

FIXTURE = Path(__file__).parent / "fixtures/runtime_smoke_good_bundle_fdfa818e.json"


@pytest.mark.parametrize("path", ["ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"])
def test_recorded_smoke_tables_bind_the_declared_bounded_query(path):
    files = json.loads(FIXTURE.read_text(encoding="utf-8"))["files"]
    module = yaml.safe_load(files["modules/task_management/module.yaml"])
    contracts = {f"task_management/{action['id']}": action for action in module["actions"]}
    page = yaml.safe_load(files[path])
    validate_page_schema(page)
    assert page_query_binding_errors(page, contracts) == []

    table = next(section for section in page["sections"] if section["primitive"] == "DataTable")
    assert table["config"]["pagination_mode"] == "server"
    assert table["config"]["total_key"] == "total"
    # The historical client controls cannot reach later pages of this endpoint.
    table["config"].update(pagination_mode="client", total_key=None)
    errors = page_query_binding_errors(page, contracts)
    assert len(errors) == 1
    assert "client controls cannot reach later backend pages" in errors[0]["error"]


def test_recorded_smoke_fixture_distinguishes_historical_and_current_digests():
    record = json.loads(FIXTURE.read_text(encoding="utf-8"))
    provenance = record["provenance"]
    current = hashlib.sha256(json.dumps(record["files"], sort_keys=True).encode("utf-8")).hexdigest()
    assert provenance["files_sha256"] == current
    assert provenance["recorded_files_sha256"] != current
    assert provenance["current_contract_migrations"]
    assert "page_bundle attempt 2 nulls total_key" in provenance["replay"]
