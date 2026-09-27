from __future__ import annotations

import json
from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assembly_phase import _merge_code_files
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_planned_data_fields,
)
from mozaiksai.core.runtime.persistence.indexes import (
    DatabaseIndexApplyError,
    _normalize_index_spec,
)
from mozaiksai.core.runtime.persistence.migrations import load_data_migrations
from mozaiksai.core.workflow.generator_support.code_files import (
    compile_data_contract,
    materialize_data_contract,
)
from mozaiksai.core.workflow.generator_support.persistence_artifacts import (
    materialize_data_migrations,
    normalize_data_contract_indexes,
)


def _contract():
    return {"version": "1", "surfaces": [{
        "surface_id": "tasks", "surface_kind": "module", "collections": [{
            "name": "tasks", "scope": "app", "entity": "Task", "tenancy": "per_user",
            "owner_field": "user_id", "ownership": {"surface_id": "tasks", "surface_kind": "module"},
            "fields": [{"name": "user_id", "type": "string", "required": True}],
            "indexes": [{"name": None, "keys": [{"field": "user_id", "order": 1}], "unique": True}],
        }],
    }], "shared_collections": []}


def _managed_context():
    return {"capability_packs": [{
        "id": "managed_payments", "capability_source": "managed_capability",
        "facades": [{"module_id": "billing_portal", "pages": []}],
    }]}


def test_null_index_name_compiles_before_runtime_and_preserves_approved_input():
    approved = _contract()
    original = deepcopy(approved)
    index = approved["surfaces"][0]["collections"][0]["indexes"][0]
    with pytest.raises(DatabaseIndexApplyError, match="name is required"):
        _normalize_index_spec(index, "index")
    compiled = normalize_data_contract_indexes(approved)
    named = compiled["surfaces"][0]["collections"][0]["indexes"][0]
    assert _normalize_index_spec(named, "index").name.startswith("idx_")
    assert approved == original
    assert normalize_data_contract_indexes(compiled) == compiled
    files = materialize_data_contract({}, data_contract=approved)
    assert json.loads(files["data/contract.json"]) == compiled
    assert _scan_planned_data_fields(files, approved) == []


def test_index_name_identity_includes_key_order_and_options_and_preserves_explicit_names():
    source = _contract()
    collection = source["surfaces"][0]["collections"][0]
    collection["indexes"] = [
        {"name": None, "keys": [["user_id", 1], ["updated_at", -1]]},
        {"name": None, "keys": [["updated_at", -1], ["user_id", 1]]},
        {"name": None, "keys": [["user_id", 1], ["updated_at", -1]], "unique": True},
        {"name": "chosen_name", "keys": [["user_id", 1]]},
    ]
    names = [item["name"] for item in normalize_data_contract_indexes(source)["surfaces"][0]["collections"][0]["indexes"]]
    assert len(set(names)) == 4
    assert names[-1] == "chosen_name"


def test_assembly_stamps_real_shape_of_managed_facade_migration(tmp_path):
    path = "data/migrations/001_billing_portal_collections.json"
    migration = {"migration_id": "billing_portal_001_collections", "surfaces": [{
        "surface_id": "billing_portal", "collections": [{
            "mongo_collection": "billing_plans", "data_alias": "billing.plans",
            "indexes": [{"name": "billing_plan_id", "keys": [{"field": "plan_id", "order": 1}], "unique": True}],
        }],
    }]}
    outputs = [{"database_files": [{"path": path, "content": json.dumps(migration)}]}]
    merged = _merge_code_files(outputs, context_variables=_managed_context())
    compiled = json.loads(merged[0]["content"])
    assert compiled == {
        "migration_id": "billing_portal_001_collections", "version": "1",
        "schema_version": "mozaiks.data_migration.v1", "operations": [],
    }
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(compiled), encoding="utf-8")
    assert load_data_migrations(tmp_path) == [compiled]
    assert "billing.plans" not in merged[0]["content"]


def test_mixed_migration_keeps_app_operations_and_names_indexes():
    path = "data/migrations/001_tasks.json"
    migration = {"migration_id": "001_tasks", "operations": [
        {"type": "ensure_collection", "module_id": "billing_portal", "entity_name": "plans"},
        {"type": "ensure_collection", "module_id": "tasks", "entity_name": "tasks"},
        {"type": "ensure_index", "module_id": "tasks", "entity_name": "tasks",
         "index": {"name": None, "keys": [["user_id", 1]]}},
    ]}
    files = {path: json.dumps(migration)}
    compiled = materialize_data_migrations(files, managed_owners={"billing_portal"})
    operations = json.loads(compiled[path])["operations"]
    assert [item["module_id"] for item in operations] == ["tasks", "tasks"]
    assert operations[1]["index"]["name"].startswith("idx_")
    assert materialize_data_migrations(compiled, managed_owners={"billing_portal"}) == compiled


@pytest.mark.parametrize("changes", [
    {"surfaces": [{"surface_id": "tasks", "collections": []}]},
    {"aliases": [{"alias": "billing.plans", "collection": "billing_plans"}]},
    {"operations": [{"type": "drop_collection", "module_id": "tasks", "entity_name": "tasks"}]},
])
def test_invalid_migration_shapes_fail_at_generation(changes):
    document = {"migration_id": "invalid", "operations": [], **changes}
    with pytest.raises(ValueError):
        materialize_data_migrations({"data/migrations/invalid.json": json.dumps(document)}, managed_owners=set())


def test_managed_facade_cannot_own_data_contract_collections():
    contract = _contract()
    contract["surfaces"][0]["surface_id"] = "billing_portal"
    with pytest.raises(ValueError, match="Managed facade.*cannot own app collections"):
        compile_data_contract(contract, context_variables=_managed_context())


@pytest.mark.parametrize("data_contract", [None, {"version": "1", "surfaces": []}])
def test_subscription_only_assembly_emits_assignment_data_contract(data_contract):
    from factory_app.workflows.AppGenerator.tools.task_integrity import planned_artifact_diagnostics
    from tests.test_subscription_assignment_data_contract import _subscription_contract

    output = {"code_files": [{"filename": "app.json", "content": "{}"}]}
    files = {item["filename"]: item["content"] for item in _merge_code_files(
        [output], data_contract=data_contract, subscription_contract=_subscription_contract(),
    )}
    compiled = json.loads(files["data/contract.json"])
    assert compiled["aliases"] == [{"alias": "billing.subscriptions", "collection": "billing_subscriptions"}]
    assert compiled["surfaces"][-1]["surface_kind"] == "app_policy"
    context = {
        "app_build_plan": {"build_tasks": [{"task_id": "app", "owned_paths": ["app.json"]}]},
        "app_task_batch_results": {"app": output},
    }
    assert planned_artifact_diagnostics(context, files) == []


def test_migration_structured_output_matches_supported_runtime_operations():
    from pathlib import Path

    source = Path(__file__).parents[1] / "factory_app/workflows/AppGenerator/structured_outputs.yaml"
    fields = yaml.safe_load(source.read_text(encoding="utf-8"))["models"]["DataMigrationOperation"]["fields"]
    assert fields["type"]["values"] == ["ensure_collection", "ensure_index"]
    assert {"field_name", "field_type", "required"}.isdisjoint(fields)
    assert fields["module_id"]["type"] == fields["entity_name"]["type"] == "str"


def test_pending_migration_injection_uses_the_same_compiler():
    from factory_app.workflows.AppGenerator.tools.schema_migration import (
        inject_migration_into_bundle,
    )

    files = {}
    inject_migration_into_bundle(files, {"migration_id": "pending", "operations": []})
    migration = json.loads(files["data/migrations/pending.json"])
    assert migration["version"] == "1"
    assert migration["schema_version"] == "mozaiks.data_migration.v1"
