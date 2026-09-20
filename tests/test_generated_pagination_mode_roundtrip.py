"""Generated table modes must survive ordinary JSON dumps without normalization."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from mozaiksai.core.runtime.app.page_schema import validate_page_schema
from mozaiksai.core.workflow.outputs.structured import get_provider_response_model
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.mark.parametrize("primitive", ["DataTable", "ResourceTable"])
def test_generated_table_default_survives_json_and_runtime_roundtrip(primitive):
    models = _load_models()
    model = models[f"App{primitive}Config"]
    config = model.model_validate({"columns": [{"key": "title"}]})
    dumped = config.model_dump(mode="json")
    assert dumped["pagination_mode"] == "client"

    schema = deepcopy(model.model_json_schema())
    assert schema["properties"]["pagination_mode"]["default"] == "client"
    assert "pagination_mode" not in schema["required"]
    provider = get_provider_response_model(model).model_json_schema()
    assert "pagination_mode" in provider["required"]
    Draft202012Validator(provider).validate(dumped)
    assert not Draft202012Validator(provider).is_valid({**dumped, "pagination_mode": None})
    assert model.model_json_schema() == schema

    page = models["AppPageSchema"].model_validate({
        "schema_version": "mozaiks.app_page.v1", "name": "reports", "route": "/reports",
        "title": "Reports", "page_type": "record_list", "layout": "full-width",
        "sections": [{"id": "reports", "primitive": primitive, "config": dumped}],
    })
    runtime = validate_page_schema(page.model_dump(mode="json"))
    assert runtime.sections[0].config["pagination_mode"] == "client"


def test_generated_server_mode_survives_json_and_runtime_roundtrip():
    models = _load_models()
    page = models["AppPageSchema"].model_validate({
        "schema_version": "mozaiks.app_page.v1", "name": "reports", "route": "/reports",
        "title": "Reports", "page_type": "record_list", "layout": "full-width",
        "sections": [{"id": "reports", "primitive": "DataTable", "config": {
            "columns": [{"key": "title"}], "api_endpoint": "/api/modules/reports/list_reports",
            "pagination_mode": "server", "pagination": True, "page_size": 20,
            "data_key": "reports", "total_key": "total",
        }}],
    })
    runtime = validate_page_schema(page.model_dump(mode="json"))
    assert runtime.sections[0].config["pagination_mode"] == "server"
    assert runtime.sections[0].config["total_key"] == "total"


@pytest.mark.parametrize("primitive", ["DataTable", "ResourceTable"])
@pytest.mark.parametrize("mode", [None, "remote"])
def test_generated_table_rejects_null_and_unknown_modes(primitive, mode):
    model = _load_models()[f"App{primitive}Config"]
    with pytest.raises(ValidationError, match="pagination_mode"):
        model.model_validate({"columns": [{"key": "title"}], "pagination_mode": mode})
