"""Ask context must be generatable and resolve before app acceptance."""

import json

import pytest
import yaml
from jsonschema import Draft202012Validator

from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.runtime.app.page_schema import (
    PageSchemaValidationError,
    build_page_action_index,
    build_page_action_index_from_module_contracts,
    validate_page_schema,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.outputs.structured import get_provider_response_model
from tests.test_continuous_deterministic_materialization import _load_models
from tests.test_page_schema_runtime_validation import _valid_page, _write_app


def _page(module="books", action="read"):
    return _valid_page(meta={"ask_context": [{"module": module, "action": action}]})


def _files(page, *, safe=True, permissions=()):
    return {
        "ui/pages/home.yaml": yaml.safe_dump(page),
        "modules/books/module.yaml": yaml.safe_dump({
            "schema_version": "mozaiks.module.v1",
            "module": {"id": "books", "handler": "backend.handler:BooksModule"},
            "permissions": [{"id": "books.private", "description": "Private books"}],
            "actions": [{"id": "read", "ask_context_safe": safe,
                         "description": "Read books", "handler_method": "read",
                         "permissions": list(permissions)}],
        }),
    }


@pytest.mark.parametrize("module,action", [("missing", "read"), ("books", "missing")])
def test_unknown_ask_reference_fails_runtime_page_closure(module, action):
    with pytest.raises(PageSchemaValidationError) as exc_info:
        validate_page_schema(_page(module, action), action_index={"books": frozenset({"read"})})
    assert exc_info.value.diagnostics[0].location == "$.meta.ask_context[0]"


@pytest.mark.asyncio
@pytest.mark.parametrize("module,action", [("missing", "read"), ("books", "missing")])
async def test_unknown_ask_reference_fails_factory_wiring(module, action):
    result = await validate_wiring({"generated_files": _files(_page(module, action))})
    assert result["passed"] is False
    assert result["failed_tests"][0]["test"] == "wiring_ask_context"


@pytest.mark.parametrize("primitive", ["DataTable", "ResourceTable"])
def test_generated_ask_context_survives_provider_json_and_runtime(primitive):
    models = _load_models()
    page = models["AppPageSchema"].model_validate(_valid_page(
        meta={"ask_context": [{"module": "books", "action": "read",
                               "params": [{"key": "status", "value": "open"}]}]},
        sections=[{"id": "books", "primitive": primitive,
                   "config": {"columns": [{"key": "title"}]}}],
    ))
    dumped = page.model_dump(mode="json")
    provider = get_provider_response_model(models["AppPageSchema"]).model_json_schema()
    Draft202012Validator(provider).validate(dumped)
    runtime = validate_page_schema(dumped)
    assert runtime.meta.ask_context[0].action == "read"
    assert runtime.meta.ask_context[0].params[0].value == "open"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["schema", "custom", "disk"])
@pytest.mark.parametrize("safe,permissions,passed", [
    (True, (), True), (False, (), False), (True, ("books.private",), False),
])
async def test_eligibility_uses_actual_module_contracts(tmp_path, surface, safe, permissions, passed):
    files = _files(_page(), safe=safe, permissions=permissions)
    if surface == "custom":
        files.pop("ui/pages/home.yaml")
        files["ui/route_manifest.json"] = json.dumps({"pages": [{
            "path": "/home", "component": "CustomHome", "meta": _page()["meta"],
        }]})
    if surface == "disk":
        for name, content in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        context = ContextVariablesBridge({"generated_app_dir": str(tmp_path)})
    else:
        context = ContextVariablesBridge({"generated_files": files})
    result = await validate_wiring(context)
    assert result["passed"] is passed
    assert context.get("wiring_validation_passed") is passed


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["schema", "custom"])
@pytest.mark.parametrize("safe,permissions,module,action,passed", [
    (True, (), "books", "read", True),
    (False, (), "books", "read", False),
    (True, ("books.private",), "books", "read", False),
    (True, (), "missing", "read", False),
    (True, (), "books", "missing", False),
])
async def test_app_load_and_fallback_check_enforce_eligibility(
    tmp_path, surface, safe, permissions, module, action, passed,
):
    from mozaiksai.control_plane.app_validation import run_app_validation_fallback_checks
    from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader

    page = _page(module, action)
    _write_app(tmp_path, page)
    for name, content in _files(page, safe=safe, permissions=permissions).items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if surface == "custom":
        (tmp_path / "ui/pages/home.yaml").unlink()
        (tmp_path / "ui/route_manifest.json").write_text(json.dumps({"pages": [{
            "path": "/home", "component": "CustomHome", "meta": page["meta"],
        }]}), encoding="utf-8")
    backend = tmp_path / "modules/books/backend"
    backend.mkdir()
    (backend / "handler.py").write_text(
        "class BooksModule:\n    async def read(self, ctx):\n        return {'rows': []}\n",
        encoding="utf-8",
    )
    loaded_module = ModuleLoader(str(tmp_path)).load("books")
    assert loaded_module.action_ask_context_map["read"] is (safe and not permissions)
    assert build_page_action_index([loaded_module], ask_context_only=True) == (
        build_page_action_index_from_module_contracts(tmp_path, ask_context_only=True)
    )
    check = next(item for item in run_app_validation_fallback_checks(tmp_path)
                 if item.name == "mozaiks_page_contracts")
    assert check.status == ("passed" if passed else "failed")
    if passed:
        result = await AppLoader.load(str(tmp_path))
        if surface == "schema":
            assert result.page_schemas["home"].meta.ask_context[0].action == "read"
        else:
            assert result.page_schemas == {}
    else:
        with pytest.raises(AppLoadError, match="ineligible_ask_context|unknown_module|unknown_action"):
            await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["no_module", "no_opt_in", "malformed", "stale_page"])
async def test_ask_declarations_fail_closed_with_authoritative_snapshot(fault):
    files = _files(_page())
    if fault == "no_module":
        files.pop("modules/books/module.yaml")
    elif fault == "no_opt_in":
        module = yaml.safe_load(files["modules/books/module.yaml"])
        module["actions"][0].pop("ask_context_safe")
        files["modules/books/module.yaml"] = yaml.safe_dump(module)
    elif fault == "malformed":
        files["ui/pages/home.yaml"] = yaml.safe_dump(_valid_page(meta={"ask_context": "invalid"}))
    else:
        files["ui/pages/home.yaml"] = yaml.safe_dump(_page(action="missing"))
    result = await validate_wiring(ContextVariablesBridge({
        "generated_files": files, "app_pages": [_valid_page()],
        "app_build_plan": {"capability_packs": [{"id": "books", "actions": ["read"]}]},
    }))
    assert result["passed"] is False


@pytest.mark.asyncio
async def test_acceptance_validates_explicit_files_without_context():
    from factory_app.workflows.AppGenerator.tools.app_validation import (
        run_app_bundle_acceptance_gate,
    )
    from tests.test_generated_app_functional_acceptance import _basic_crud_files

    files = _basic_crud_files()
    page = yaml.safe_load(files["ui/pages/orders.yaml"])
    page["meta"] = {"ask_context": [{"module": "orders", "action": "missing"}]}
    files["ui/pages/orders.yaml"] = yaml.safe_dump(page)
    result = await run_app_bundle_acceptance_gate(files=files)
    assert result["passed"] is False
    assert any(item["test"] == "wiring_ask_context" for item in result["failed_tests"])
