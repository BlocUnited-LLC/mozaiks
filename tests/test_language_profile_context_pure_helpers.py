"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_language_profile_context.py

Covers:
  _fmt_list:
    - empty items → []
    - single item → label line + item line
    - multiple items → all included
    - indent applied to label and items
    - zero indent → no padding
    - custom indent

  _build_backend_block:
    - language/framework in header line
    - stub_files rendered when present
    - persistence api rendered when present
    - event_api rendered when present
    - entrypoint_format rendered when present
    - hard_constraints rendered (max 6)
    - empty fields omitted
    - whitespace values treated as empty

  _build_database_block:
    - type/schema_format in header line
    - collection_api rendered when present
    - index_format description rendered when present
    - hard_constraints rendered (max 4)
    - empty fields omitted

  _build_frontend_block:
    - language/framework in header line
    - component_extension rendered when present
    - custom_route path_template rendered
    - api_client_import rendered
    - api_client_module rendered
    - hard_constraints rendered (max 4)
    - empty fields omitted

  _build_services_block:
    - services with string values → rendered
    - non-string values skipped
    - empty services → empty string returned
    - single key/value included

  _resolve_pack_id:
    - agent without context_variables → default pack id
    - agent with context_variables.get() returning pack_id → pack_id returned
    - agent with empty/whitespace pack_id → default returned
    - agent with None context_variables → default returned
"""
from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.hook_language_profile_context import (
    _DEFAULT_PACK_ID,
    _build_backend_block,
    _build_database_block,
    _build_frontend_block,
    _build_services_block,
    _fmt_list,
    _resolve_pack_id,
)

# ---------------------------------------------------------------------------
# 1. _fmt_list
# ---------------------------------------------------------------------------

class TestFmtList:
    def test_empty_items_returns_empty_list(self):
        assert _fmt_list("stub_files:", []) == []

    def test_single_item_returns_two_lines(self):
        result = _fmt_list("stub_files:", ["handler.py"])
        assert len(result) == 2

    def test_label_as_first_line(self):
        result = _fmt_list("stub_files:", ["handler.py"])
        assert result[0].endswith("stub_files:")

    def test_item_prefixed_with_dash(self):
        result = _fmt_list("stub_files:", ["handler.py"])
        assert "- handler.py" in result[1]

    def test_default_indent_is_two_spaces(self):
        result = _fmt_list("stub_files:", ["handler.py"])
        assert result[0].startswith("  stub_files:")

    def test_custom_indent_applied(self):
        result = _fmt_list("stub_files:", ["handler.py"], indent=4)
        assert result[0].startswith("    stub_files:")
        assert result[1].startswith("      - handler.py")  # 4 + 2

    def test_zero_indent(self):
        result = _fmt_list("stub_files:", ["handler.py"], indent=0)
        assert result[0] == "stub_files:"

    def test_multiple_items_all_included(self):
        result = _fmt_list("files:", ["a.py", "b.py", "c.py"])
        assert len(result) == 4  # label + 3 items
        assert any("a.py" in line for line in result)
        assert any("b.py" in line for line in result)
        assert any("c.py" in line for line in result)


# ---------------------------------------------------------------------------
# 2. _build_backend_block
# ---------------------------------------------------------------------------

class TestBuildBackendBlock:
    def _minimal(self) -> dict[str, Any]:
        return {"language": "Python", "framework": "FastAPI"}

    def test_header_contains_language_and_framework(self):
        result = _build_backend_block(self._minimal())
        assert "Python" in result
        assert "FastAPI" in result

    def test_stub_files_rendered_when_present(self):
        backend = {**self._minimal(), "stub_files": ["backend/handler.py"]}
        result = _build_backend_block(backend)
        assert "handler.py" in result

    def test_stub_files_omitted_when_empty(self):
        result = _build_backend_block(self._minimal())
        assert "stub_files" not in result

    def test_persistence_api_rendered(self):
        backend = {**self._minimal(), "persistence": {"api": "ctx.persistence.collection"}}
        result = _build_backend_block(backend)
        assert "ctx.persistence.collection" in result

    def test_event_api_rendered(self):
        backend = {**self._minimal(), "event_api": "ctx.events.emit"}
        result = _build_backend_block(backend)
        assert "ctx.events.emit" in result

    def test_entrypoint_format_rendered(self):
        backend = {**self._minimal(), "entrypoint_format": "async def {action}(ctx)"}
        result = _build_backend_block(backend)
        assert "entrypoint_format" in result

    def test_hard_constraints_rendered(self):
        backend = {**self._minimal(), "hard_constraints": ["Use type hints", "No globals"]}
        result = _build_backend_block(backend)
        assert "Use type hints" in result
        assert "No globals" in result

    def test_hard_constraints_capped_at_six(self):
        constraints = [f"constraint_{i}" for i in range(10)]
        backend = {**self._minimal(), "hard_constraints": constraints}
        result = _build_backend_block(backend)
        # Only first 6 should appear
        for i in range(6):
            assert f"constraint_{i}" in result
        assert "constraint_6" not in result

    def test_whitespace_language_in_header(self):
        backend = {"language": "  Python  ", "framework": "FastAPI"}
        result = _build_backend_block(backend)
        assert "Python" in result

    def test_empty_optional_fields_omitted(self):
        result = _build_backend_block({"language": "Python", "framework": "FastAPI"})
        assert "event_api" not in result
        assert "persistence" not in result


# ---------------------------------------------------------------------------
# 3. _build_database_block
# ---------------------------------------------------------------------------

class TestBuildDatabaseBlock:
    def _minimal(self) -> dict[str, Any]:
        return {"type": "MongoDB", "schema_format": "document"}

    def test_header_contains_type_and_schema_format(self):
        result = _build_database_block(self._minimal())
        assert "MongoDB" in result
        assert "document" in result

    def test_collection_api_rendered(self):
        db = {**self._minimal(), "collection_api": "ctx.persistence.collection(module_id, entity)"}
        result = _build_database_block(db)
        assert "ctx.persistence.collection" in result

    def test_collection_api_omitted_when_empty(self):
        result = _build_database_block(self._minimal())
        assert "collection_api" not in result

    def test_index_format_description_rendered(self):
        db = {**self._minimal(), "index_format": {"description": "JSON array of index specs"}}
        result = _build_database_block(db)
        assert "JSON array of index specs" in result

    def test_hard_constraints_rendered(self):
        db = {**self._minimal(), "hard_constraints": ["No raw queries"]}
        result = _build_database_block(db)
        assert "No raw queries" in result

    def test_hard_constraints_capped_at_four(self):
        constraints = [f"c_{i}" for i in range(8)]
        db = {**self._minimal(), "hard_constraints": constraints}
        result = _build_database_block(db)
        for i in range(4):
            assert f"c_{i}" in result
        assert "c_4" not in result

    def test_empty_type_in_header(self):
        result = _build_database_block({"type": "", "schema_format": ""})
        assert result.startswith("Database (")


# ---------------------------------------------------------------------------
# 4. _build_frontend_block
# ---------------------------------------------------------------------------

class TestBuildFrontendBlock:
    def _minimal(self) -> dict[str, Any]:
        return {"language": "TypeScript", "framework": "React"}

    def test_header_contains_language_and_framework(self):
        result = _build_frontend_block(self._minimal())
        assert "TypeScript" in result
        assert "React" in result

    def test_component_extension_rendered(self):
        frontend = {**self._minimal(), "component_extension": ".tsx"}
        result = _build_frontend_block(frontend)
        assert ".tsx" in result

    def test_component_extension_omitted_when_empty(self):
        result = _build_frontend_block(self._minimal())
        assert "component_extension" not in result

    def test_custom_route_path_template_rendered(self):
        frontend = {**self._minimal(), "custom_route": {"path_template": "/apps/:id"}}
        result = _build_frontend_block(frontend)
        assert "/apps/:id" in result

    def test_api_client_import_rendered(self):
        frontend = {**self._minimal(), "custom_route": {"api_client_import": "import api from 'api'"}}
        result = _build_frontend_block(frontend)
        assert "import api from 'api'" in result

    def test_api_client_module_rendered(self):
        frontend = {**self._minimal(), "custom_route": {"api_client_module": "src/api/client.js"}}
        result = _build_frontend_block(frontend)
        assert "src/api/client.js" in result

    def test_hard_constraints_rendered(self):
        frontend = {**self._minimal(), "hard_constraints": ["Use hooks only"]}
        result = _build_frontend_block(frontend)
        assert "Use hooks only" in result

    def test_hard_constraints_capped_at_four(self):
        constraints = [f"c_{i}" for i in range(8)]
        frontend = {**self._minimal(), "hard_constraints": constraints}
        result = _build_frontend_block(frontend)
        for i in range(4):
            assert f"c_{i}" in result
        assert "c_4" not in result

    def test_empty_optional_fields_omitted(self):
        result = _build_frontend_block(self._minimal())
        assert "component_extension" not in result
        assert "custom_route" not in result


# ---------------------------------------------------------------------------
# 5. _build_services_block
# ---------------------------------------------------------------------------

class TestBuildServicesBlock:
    def test_empty_services_returns_empty_string(self):
        assert _build_services_block({}) == ""

    def test_string_values_rendered(self):
        services = {"stripe": "stripe-python>=5"}
        result = _build_services_block(services)
        assert "stripe" in result
        assert "stripe-python>=5" in result

    def test_non_string_values_skipped(self):
        services = {"config": {"key": "value"}, "name": "my_service"}
        result = _build_services_block(services)
        assert "my_service" in result
        # dict value "config" shouldn't appear as a key with its value
        assert '{"key": "value"}' not in result

    def test_multiple_string_values_all_rendered(self):
        services = {"stripe": "stripe-python", "sendgrid": "sendgrid"}
        result = _build_services_block(services)
        assert "stripe" in result
        assert "sendgrid" in result

    def test_result_starts_with_services_header(self):
        services = {"stripe": "stripe-python"}
        result = _build_services_block(services)
        assert result.startswith("Services:")


# ---------------------------------------------------------------------------
# 6. _resolve_pack_id
# ---------------------------------------------------------------------------

class TestResolvePackId:
    def test_agent_without_context_variables_returns_default(self):
        class FakeAgent:
            pass

        assert _resolve_pack_id(FakeAgent()) == _DEFAULT_PACK_ID

    def test_agent_with_none_context_variables_returns_default(self):
        class FakeAgent:
            context_variables = None

        assert _resolve_pack_id(FakeAgent()) == _DEFAULT_PACK_ID

    def test_agent_with_valid_pack_id_returns_it(self):
        class FakeCtxVars:
            def get(self, key: str, default: Any = None) -> Any:
                if key == "dev_pack_id":
                    return "enterprise_builder"
                return default

        class FakeAgent:
            context_variables = FakeCtxVars()

        assert _resolve_pack_id(FakeAgent()) == "enterprise_builder"

    def test_agent_with_empty_pack_id_returns_default(self):
        class FakeCtxVars:
            def get(self, key: str, default: Any = None) -> Any:
                if key == "dev_pack_id":
                    return ""
                return default

        class FakeAgent:
            context_variables = FakeCtxVars()

        assert _resolve_pack_id(FakeAgent()) == _DEFAULT_PACK_ID

    def test_agent_with_whitespace_pack_id_returns_default(self):
        class FakeCtxVars:
            def get(self, key: str, default: Any = None) -> Any:
                if key == "dev_pack_id":
                    return "   "
                return default

        class FakeAgent:
            context_variables = FakeCtxVars()

        assert _resolve_pack_id(FakeAgent()) == _DEFAULT_PACK_ID

    def test_agent_with_none_pack_id_returns_default(self):
        class FakeCtxVars:
            def get(self, key: str, default: Any = None) -> Any:
                return None

        class FakeAgent:
            context_variables = FakeCtxVars()

        assert _resolve_pack_id(FakeAgent()) == _DEFAULT_PACK_ID

    def test_pack_id_stripped(self):
        class FakeCtxVars:
            def get(self, key: str, default: Any = None) -> Any:
                if key == "dev_pack_id":
                    return "  enterprise_builder  "
                return default

        class FakeAgent:
            context_variables = FakeCtxVars()

        assert _resolve_pack_id(FakeAgent()) == "enterprise_builder"
