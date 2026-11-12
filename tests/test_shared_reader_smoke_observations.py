"""Runtime smoke observes declared owner records when peers have reader grants."""

from __future__ import annotations

from types import SimpleNamespace

from factory_app.workflows.AppGenerator.tools import app_runtime_smoke as smoke
from mozaiksai.core.runtime.persistence.naming import collection_name_for
from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import ModuleSmokeFixture


def observation_run():
    collection = {
        "name": "entries", "entity": "Entry", "scope": "app", "tenancy": "app_wide", "owner_field": None,
        "ownership": {"surface_id": "ledger", "surface_kind": "module"},
        "mongo_collection": "shared_entries", "fields": [{"name": "id", "type": "integer"}],
    }
    contract = {
        "version": "1",
        "surfaces": [{"surface_id": "ledger", "surface_kind": "module", "collections": [collection]}],
        "shared_collections": [{
            "owner_module": "ledger", "name": "entries", "mongo_collection": "shared_entries",
            "primary_alias": "ledger.entries", "read_by": [{"module": "audit", "access": "read_only"}],
        }],
    }
    stored = {"id": 1, "amount": 5}
    observations = []

    class Cursor:
        def sort(self, *_args):
            return self

        async def to_list(self, length=None):
            return [dict(stored)]

    class Rows:
        def find(self, query):
            observations.append(("find", query))
            return Cursor()

        async def find_one(self, query, *_args):
            observations.append(("find_one", query))
            value = query.get("id", 1)
            if isinstance(value, dict):
                return dict(stored) if 1 in value["$in"] else None
            return dict(stored) if value == 1 else None

    name = collection_name_for(app_id="smoke-app", module_id="ledger", entity_name="entries")
    database = {name: Rows()}
    run = SimpleNamespace(
        app_id="smoke-app", database_name="smoke-db", client={"smoke-db": database},
        database=database, load=SimpleNamespace(data_contract=contract), fixtures={},
    )
    entity = SimpleNamespace(module="ledger", collection=collection, id_field="id", actions={"get": "get_entry"})
    return run, entity, name, stored, observations


async def test_role_denial_snapshot_reads_declared_owner_with_shared_reader():
    run, _entity, name, stored, observations = observation_run()
    assert await smoke._SmokeRun._data_snapshot(run) == {name: [stored]}
    assert observations == [("find", {})]


async def test_stored_record_and_fields_are_observed_with_shared_reader():
    run, entity, _name, stored, observations = observation_run()
    assert await smoke._stored(run, entity, 1) == stored
    assert await smoke._stored_fields(run, entity) == ["amount", "id"]
    assert len(observations) == 2


async def test_missing_get_lookup_observes_existing_owner_rows_with_shared_reader():
    run, entity, _name, _stored, observations = observation_run()
    run.fixtures[("ledger", "get_entry")] = ModuleSmokeFixture.model_validate({
        "action": "get_entry", "roles": [], "setup": [],
        "params": [{"field": "lookup", "source": "record", "record_field": "id"}],
    })
    module = SimpleNamespace(name="ledger", action_schemas_map={
        "get_entry": {"input": {"type": "object", "properties": {"lookup": {"type": "integer", "enum": [1, 2]}}}},
    })
    assert await smoke._missing_get_lookup(run, module, entity) == 2
    assert observations == [("find_one", {"id": 1}), ("find_one", {"id": 2})]
