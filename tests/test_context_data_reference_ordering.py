"""Authorization reads select the newest stored policy or stop on lookup failure."""

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from mozaiksai.core import core_config
from mozaiksai.core.workflow.context import variables
from mozaiksai.core.workflow.context.schema import ContextVariableDefinition, ContextVariableSource
from mozaiksai.core.workflow.declarative.contracts import ContextVariableSourceSpec
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    access_policy_sha256,
    resolve_design_access_policy,
)
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs
from tests.test_generated_role_grants import _policy, _reviewed_context


class _Cursor:
    def __init__(self, rows, projection):
        self.rows = deepcopy(rows)
        self.projection = projection

    def sort(self, fields):
        for name, direction in reversed(fields):
            self.rows.sort(key=lambda row: row[name], reverse=direction == -1)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length):
        return [
            {key: value for key, value in row.items() if not self.projection or key in self.projection or key == "_id"}
            for row in self.rows[:length]
        ]


class _Collection:
    def __init__(self, rows=(), error=None):
        self.rows, self.error = rows, error
        self.query = None

    def find(self, query, projection):
        self.query = query
        if self.error:
            raise self.error
        return _Cursor(self.rows, projection)


def _definition():
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AppGenerator/context_variables.yaml"
    return ContextVariableDefinition.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))["definitions"]["design_docs_build_artifact"]
    )


def _install_collection(monkeypatch, collection):
    monkeypatch.setattr(core_config, "get_mongo_client", lambda: {"mozaiksai": {"ArtifactVersions": collection}})


@pytest.mark.asyncio
@pytest.mark.parametrize(("fields", "value_type", "expected"), [
    (None, "object", {"title": "Reviewed design"}),
    (["title"], "string", "Reviewed design"),
    (["title", "missing"], "object", {"title": "Reviewed design"}),
    (["_id"], "string", "design-used"),
    (["_id", "title"], "object", {"_id": "design-used", "title": "Reviewed design"}),
])
async def test_data_reference_identity_requires_explicit_projection(monkeypatch, fields, value_type, expected):
    collection = _Collection([{"_id": "design-used", "title": "Reviewed design"}])
    _install_collection(monkeypatch, collection)
    definition = ContextVariableDefinition.model_validate({
        "type": value_type,
        "source": {"type": "data_reference", "database_name": "mozaiksai",
                   "collection": "ArtifactVersions", "fields": fields},
    })
    value = await variables._load_data_reference_value(
        "selected_design", definition, default_database_name=None,
        app_id="target", context={},
    )
    assert value == expected


@pytest.mark.asyncio
async def test_newest_build_draft_revokes_grants_despite_reverse_random_ids(monkeypatch):
    context = _reviewed_context(_policy())
    older = deepcopy(context["design_docs_artifact"])
    older.update(_id="av_" + "f" * 24, version_number=3, lifecycle_status="draft", created_at=datetime(2026, 11, 7, tzinfo=UTC))
    latest = deepcopy(older)
    latest.update(_id="av_" + "0" * 24, version_number=4, created_at=datetime(2026, 11, 6, tzinfo=UTC))
    empty = {"roles": [], "grants": []}
    summary = latest["commit_metadata"]["metadata"]["summary_payload"]
    summary["access_policy"] = empty
    summary["access_policy_review"].update(status="not_required", policy_sha256=access_policy_sha256(empty))
    collection = _Collection([older, latest])
    _install_collection(monkeypatch, collection)
    selected = await variables._load_data_reference_value(
        "design_docs_build_artifact", _definition(), default_database_name=None,
        app_id="test-app", context=context,
    )
    assert selected["commit_metadata"]["metadata"]["summary_payload"]["access_policy"] == empty
    assert collection.query["commit_metadata.metadata.summary_payload.access_policy_review.build_id"] == "build-test"
    context["design_docs_build_artifact"] = selected
    assert resolve_design_access_policy(context) == empty


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [False, True])
async def test_lookup_error_cannot_restore_older_authorization(monkeypatch, authorization):
    _install_collection(monkeypatch, _Collection(error=RuntimeError("lookup failed")))
    definition = _definition().model_copy(update={"authorization": authorization})
    call = variables._load_data_reference_value(
        "design_docs_build_artifact", definition, default_database_name=None,
        app_id="test-app", context=_reviewed_context(_policy()),
    )
    if authorization:
        with pytest.raises(RuntimeError, match="lookup failed"):
            await call
    else:
        assert await call is None


@pytest.mark.asyncio
async def test_authorization_reference_requires_supported_adapter(monkeypatch):
    monkeypatch.setattr(variables, "get_db_adapter", lambda source: None)
    with pytest.raises(ValueError, match="no database adapter"):
        await variables._load_data_reference_value(
            "design_docs_build_artifact", _definition(), default_database_name=None,
            app_id="test-app", context=_reviewed_context(_policy()),
        )


@pytest.mark.parametrize("model", [ContextVariableSource, ContextVariableSourceSpec])
@pytest.mark.parametrize("sort", [[], [{"field": "$where", "direction": "desc"}],
                                    [{"field": "version_number", "direction": -1}],
                                    [{"field": "version_number", "direction": "newest"}]])
def test_sort_contract_rejects_unknown_fields_and_direction_aliases(model, sort):
    with pytest.raises(ValueError):
        model.model_validate({"type": "data_reference", "sort": sort})


def test_agentgenerator_source_model_carries_typed_ordering():
    models, _ = load_workflow_structured_outputs("AgentGenerator")
    adapter = TypeAdapter(models["ContextVariableSource"].model_fields["sort"].annotation)
    sort = adapter.dump_python(adapter.validate_python([{"field": "version_number", "direction": "desc"}]), mode="json")
    parsed = ContextVariableSourceSpec.model_validate({"type": "data_reference", "sort": sort})
    assert parsed.sort[0].direction == "desc"
