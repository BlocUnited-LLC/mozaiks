"""Account-data guidance and example interoperability, not LLM output proof."""

import inspect
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from textwrap import indent
from types import SimpleNamespace

import pytest
import yaml
from bson import ObjectId
from starlette.responses import JSONResponse

from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    inject_cookie_cutter_contracts_context,
)
from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceContext
from mozaiksai.core.runtime.persistence.naming import collection_name_for
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload


def _contract():
    path = Path(__file__).resolve().parents[1] / "factory_app/build_context/AppGenerator/file_contracts.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["backend_helper_files"]["account_data_handler"]


@pytest.mark.parametrize("agent_name", ["ServiceAgent", "ConfigMiddlewareAgent"])
@pytest.mark.parametrize("trigger", ["owned_path", "user_data_scope"])
def test_account_contract_projects_naming_signature_and_storage_boundaries(agent_name, trigger):
    context = ({"current_build_task": {"owned_paths": ["modules/records/backend/account_data_handler.py"]}}
               if trigger == "owned_path" else {"module_contract": {"module_yaml": {"module": {"user_data_scope": True}}}})
    agent = SimpleNamespace(name=agent_name, system_message="Worker instructions",
                            context_variables=ContextVariablesBridge(context))
    agent.update_system_message = lambda message: setattr(agent, "system_message", message)
    inject_cookie_cutter_contracts_context(agent, [])
    message = agent.system_message
    assert "from mozaiksai.core.runtime.persistence.naming import collection_name_for" in message
    assert f"collection_name_for{inspect.signature(collection_name_for)}" in message
    contract = _contract()
    assert contract["generated_by"] == "ServiceAgent"
    for key in ("database_contract", "collection_resolution", "export_example"):
        assert contract[key] in yaml.safe_load(message.split("Required account-data runtime contract:\n")[1]
                                              .split("Account-data collection naming API:")[0]).values()
    assert "alias" in contract["collection_resolution"]
    assert "app_slug" in contract["collection_resolution"]
    assert '"app_id": app_id, "user_id": user_id' in contract["export_example"]


@pytest.mark.asyncio
@pytest.mark.parametrize("has_owned_records", [True, False])
async def test_export_guidance_uses_repo_collection_scopes_and_json_safe_values(has_owned_records):
    example = _contract()["export_example"]
    source = "async def export_data(db, app_id, user_id, module_id, entity_name):\n" + indent(example, "    ")
    namespace = {}
    exec(source, namespace)
    app_id, user_id, module_id, entity_name = "app-a", "alice", "records", "entries"
    persistence = MongoPersistenceContext(app_id=app_id, database_name="unused", client=object())
    name = persistence.collection_name(module_id, entity_name)
    assert name == collection_name_for(app_id=app_id, module_id=module_id, entity_name=entity_name)
    assert name != collection_name_for(app_id="app-b", module_id=module_id, entity_name=entity_name)
    assert name != persistence.collection_name("other_module", entity_name)
    assert name != persistence.collection_name(module_id, "other_entity")
    when = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
    nested_id = ObjectId()
    rows = [
        {"_id": ObjectId(), "app_id": "app-b", "user_id": user_id},
        {"_id": ObjectId(), "app_id": app_id, "user_id": "bob"},
    ]
    if has_owned_records:
        rows.append({"_id": ObjectId(), "app_id": app_id, "user_id": user_id,
                     "created_at": when, "nested": {"reference": nested_id}})
    before = deepcopy(rows)
    queries = []

    class Collection:
        def find(self, query, projection):
            queries.append((query, projection))
            selected = [{key: value for key, value in row.items() if key != "_id"}
                        for row in rows if all(row.get(key) == value for key, value in query.items())]

            async def to_list(length):
                return selected

            return SimpleNamespace(to_list=to_list)

    # No bare-name collection is available: resolving a different collection fails.
    result = await namespace["export_data"]({name: Collection()}, app_id, user_id, module_id, entity_name)
    assert queries == [({"app_id": app_id, "user_id": user_id}, {"_id": 0})]
    serialized = json.loads(JSONResponse(content=result).body)
    expected = {"records_entries": [{"app_id": app_id, "user_id": user_id,
                                   "created_at": when.isoformat(), "nested": {"reference": str(nested_id)}}]}
    assert serialized == (expected if has_owned_records else {})
    assert rows == before


def test_account_python_materialization_preserves_service_owned_content():
    content = "async def export_data(db, app_id, user_id, module_id, entity_name):\n" + indent(
        _contract()["export_example"], "    ")
    path = "modules/records/backend/account_data_handler.py"
    files = extract_code_file_map_from_payload({
        "python_files": [{"path": path, "kind": "helper", "content": content}],
        "code_files": [{"filename": path, "content": "stale mirror"}],
    })
    assert files == {path: content}
