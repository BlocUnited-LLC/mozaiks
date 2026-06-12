"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_language_profile_context.py

Covers:

  _fmt_list:
    - empty items → []
    - single item → [indented_label, indented_dash_item]
    - multiple items → all included
    - default indent=2 adds 2-space prefix to label and 4-space prefix to items
    - custom indent respected
    - title passed through as-is

  _build_backend_block:
    - header line includes language and framework
    - stub_files rendered via _fmt_list when non-empty
    - stub_files omitted when empty
    - persistence_api rendered when non-empty
    - persistence_api omitted when absent
    - event_api rendered when present
    - entrypoint_format rendered when present
    - hard_constraints capped at 6
    - whitespace-only constraint items filtered
    - empty backend dict → minimal header only

  _build_database_block:
    - header includes db_type and schema_format
    - collection_api rendered when present
    - index_format description rendered when present
    - hard_constraints capped at 4
    - empty database dict → minimal header only

  _build_frontend_block:
    - header includes language and framework
    - component_extension rendered when present
    - custom_route path_template rendered
    - api_client_import rendered
    - api_client_module rendered
    - hard_constraints capped at 4
    - empty frontend dict → minimal header only

  _build_services_block:
    - empty dict → empty string (no "Services:" line alone)
    - dict with string values → "Services:\n  key: value"
    - non-string values skipped
    - multiple string values → all rendered

  _build_body:
    - always starts with "pack: {pack_id} — {label}"
    - missing pack_id uses default "webapp_builder"
    - agent in _NEEDS_BACKEND gets backend block
    - agent NOT in _NEEDS_BACKEND does not get backend block
    - agent in _NEEDS_DATABASE gets database block
    - agent in _NEEDS_FRONTEND gets frontend block
    - agent in _NEEDS_SERVICES gets services block (when non-empty)
    - DatabaseAgent also gets backend persistence api facts
    - parts joined with double newline
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_language_profile_context import (
    _build_backend_block,
    _build_body,
    _build_database_block,
    _build_frontend_block,
    _build_services_block,
    _fmt_list,
)

# ---------------------------------------------------------------------------
# 1. _fmt_list
# ---------------------------------------------------------------------------

class TestFmtList:
    def test_empty_items_returns_empty(self):
        assert _fmt_list("stub_files:", []) == []

    def test_single_item_returns_two_lines(self):
        result = _fmt_list("stub_files:", ["handler.py"])
        assert len(result) == 2

    def test_label_in_first_line(self):
        result = _fmt_list("stub_files:", ["handler.py"])
        assert "stub_files:" in result[0]

    def test_default_indent_2_on_label(self):
        result = _fmt_list("label:", ["item"])
        assert result[0] == "  label:"

    def test_default_indent_item_prefix(self):
        result = _fmt_list("label:", ["item"])
        # pad=" " * 2 = "  ", item gets "    - item"
        assert result[1] == "    - item"

    def test_custom_indent_applied(self):
        result = _fmt_list("hdr:", ["x"], indent=4)
        assert result[0] == "    hdr:"
        assert result[1] == "      - x"

    def test_multiple_items_all_included(self):
        result = _fmt_list("hdr:", ["a", "b", "c"])
        assert len(result) == 4
        assert any("a" in line for line in result)
        assert any("c" in line for line in result)

    def test_zero_indent(self):
        result = _fmt_list("hdr:", ["item"], indent=0)
        assert result[0] == "hdr:"
        assert result[1] == "  - item"


# ---------------------------------------------------------------------------
# 2. _build_backend_block
# ---------------------------------------------------------------------------

class TestBuildBackendBlock:
    def test_header_includes_language_and_framework(self):
        block = _build_backend_block({"language": "Python", "framework": "FastAPI"})
        assert block.startswith("Backend (Python/FastAPI):")

    def test_empty_backend_minimal_header(self):
        block = _build_backend_block({})
        assert block.startswith("Backend (/")

    def test_stub_files_rendered_when_present(self):
        block = _build_backend_block({"language": "Python", "stub_files": ["service.py", "handler.py"]})
        assert "stub_files:" in block
        assert "service.py" in block
        assert "handler.py" in block

    def test_stub_files_omitted_when_empty(self):
        block = _build_backend_block({"stub_files": []})
        assert "stub_files:" not in block

    def test_persistence_api_rendered(self):
        block = _build_backend_block({"persistence": {"api": "ctx.persistence.collection(module_id, entity)"}})
        assert "persistence:" in block
        assert "ctx.persistence" in block

    def test_persistence_api_omitted_when_absent(self):
        block = _build_backend_block({})
        assert "persistence:" not in block

    def test_event_api_rendered_when_present(self):
        block = _build_backend_block({"event_api": "emit_event()"})
        assert "event_api: emit_event()" in block

    def test_entrypoint_format_rendered_when_present(self):
        block = _build_backend_block({"entrypoint_format": "async def method(self, ctx)"})
        assert "entrypoint_format:" in block

    def test_hard_constraints_capped_at_6(self):
        constraints = [f"constraint_{i}" for i in range(10)]
        block = _build_backend_block({"hard_constraints": constraints})
        assert "constraint_5" in block
        assert "constraint_6" not in block

    def test_whitespace_constraint_filtered(self):
        block = _build_backend_block({"hard_constraints": ["  ", "real constraint"]})
        assert "real constraint" in block
        lines = block.splitlines()
        assert not any(l.strip() == "-" for l in lines)

    def test_returns_string(self):
        assert isinstance(_build_backend_block({}), str)


# ---------------------------------------------------------------------------
# 3. _build_database_block
# ---------------------------------------------------------------------------

class TestBuildDatabaseBlock:
    def test_header_includes_type_and_format(self):
        block = _build_database_block({"type": "MongoDB", "schema_format": "json"})
        assert block.startswith("Database (MongoDB/json):")

    def test_empty_database_minimal_header(self):
        block = _build_database_block({})
        assert block.startswith("Database (/")

    def test_collection_api_rendered(self):
        block = _build_database_block({"collection_api": "ctx.persistence.collection()"})
        assert "collection_api:" in block

    def test_collection_api_omitted_when_absent(self):
        block = _build_database_block({})
        assert "collection_api" not in block

    def test_index_format_description_rendered(self):
        block = _build_database_block({"index_format": {"description": "Use compound indexes"}})
        assert "index_format: Use compound indexes" in block

    def test_index_format_omitted_when_absent(self):
        block = _build_database_block({})
        assert "index_format" not in block

    def test_hard_constraints_capped_at_4(self):
        constraints = [f"c{i}" for i in range(8)]
        block = _build_database_block({"hard_constraints": constraints})
        assert "c3" in block
        assert "c4" not in block

    def test_returns_string(self):
        assert isinstance(_build_database_block({}), str)


# ---------------------------------------------------------------------------
# 4. _build_frontend_block
# ---------------------------------------------------------------------------

class TestBuildFrontendBlock:
    def test_header_includes_language_and_framework(self):
        block = _build_frontend_block({"language": "JavaScript", "framework": "React"})
        assert block.startswith("Frontend (JavaScript/React):")

    def test_empty_frontend_minimal_header(self):
        block = _build_frontend_block({})
        assert block.startswith("Frontend (/")

    def test_component_extension_rendered(self):
        block = _build_frontend_block({"component_extension": "jsx"})
        assert "component_extension: jsx" in block

    def test_component_extension_omitted_when_absent(self):
        block = _build_frontend_block({})
        assert "component_extension" not in block

    def test_path_template_rendered(self):
        block = _build_frontend_block({
            "custom_route": {"path_template": "/pages/{page_id}"}
        })
        assert "custom_route_path" in block
        assert "/pages/{page_id}" in block

    def test_api_client_import_rendered(self):
        block = _build_frontend_block({
            "custom_route": {"api_client_import": "import apiClient from '@mozaiks/api'"}
        })
        assert "api_client_import:" in block

    def test_api_client_module_rendered(self):
        block = _build_frontend_block({
            "custom_route": {"api_client_module": "@mozaiks/api"}
        })
        assert "api_client_module:" in block

    def test_hard_constraints_capped_at_4(self):
        constraints = [f"fc{i}" for i in range(6)]
        block = _build_frontend_block({"hard_constraints": constraints})
        assert "fc3" in block
        assert "fc4" not in block

    def test_returns_string(self):
        assert isinstance(_build_frontend_block({}), str)


# ---------------------------------------------------------------------------
# 5. _build_services_block
# ---------------------------------------------------------------------------

class TestBuildServicesBlock:
    def test_empty_dict_returns_empty_string(self):
        # Only "Services:" header — len(lines) == 1 → returns ""
        assert _build_services_block({}) == ""

    def test_string_value_rendered(self):
        result = _build_services_block({"email": "sendgrid"})
        assert "Services:" in result
        assert "email: sendgrid" in result

    def test_non_string_value_skipped(self):
        result = _build_services_block({"config": {"key": "val"}, "name": "myservice"})
        assert "name: myservice" in result
        assert "config:" not in result

    def test_multiple_string_values_all_rendered(self):
        result = _build_services_block({"email": "sendgrid", "sms": "twilio"})
        assert "email: sendgrid" in result
        assert "sms: twilio" in result

    def test_returns_empty_when_all_non_string(self):
        # Only non-string values → lines stays at ["Services:"] → len==1 → ""
        assert _build_services_block({"cfg": {"nested": True}}) == ""

    def test_services_header_present_when_non_empty(self):
        result = _build_services_block({"email": "sendgrid"})
        assert result.startswith("Services:")


# ---------------------------------------------------------------------------
# 6. _build_body
# ---------------------------------------------------------------------------

class TestBuildBody:
    def _minimal_profile(self, pack_id="webapp_builder", label="Web App Builder") -> dict:
        return {"pack_id": pack_id, "label": label}

    def test_starts_with_pack_header(self):
        result = _build_body("ConfigMiddlewareAgent", self._minimal_profile())
        assert result.startswith("pack: webapp_builder")

    def test_pack_header_includes_label(self):
        result = _build_body("ConfigMiddlewareAgent", {"pack_id": "custom_pack", "label": "Custom"})
        assert "custom_pack — Custom" in result

    def test_missing_pack_id_uses_default(self):
        result = _build_body("ServiceAgent", {})
        assert "pack: webapp_builder" in result

    def test_needs_backend_agent_gets_backend_block(self):
        profile = {
            **self._minimal_profile(),
            "backend": {"language": "Python", "framework": "FastAPI"},
        }
        result = _build_body("ServiceAgent", profile)
        assert "Backend (Python/FastAPI):" in result

    def test_agent_not_in_needs_backend_no_backend_block(self):
        profile = {
            **self._minimal_profile(),
            "backend": {"language": "Python", "framework": "FastAPI"},
        }
        # FrontendStubAgent is not in _NEEDS_BACKEND
        result = _build_body("FrontendStubAgent", profile)
        assert "Backend (Python/FastAPI):" not in result

    def test_needs_database_agent_gets_database_block(self):
        profile = {
            **self._minimal_profile(),
            "database": {"type": "MongoDB", "schema_format": "json"},
        }
        result = _build_body("DatabaseAgent", profile)
        assert "Database (MongoDB/json):" in result

    def test_non_database_agent_no_database_block(self):
        profile = {
            **self._minimal_profile(),
            "database": {"type": "MongoDB", "schema_format": "json"},
        }
        # ControllerAgent is not in _NEEDS_DATABASE
        result = _build_body("ControllerAgent", profile)
        assert "Database" not in result

    def test_frontend_agent_gets_frontend_block(self):
        profile = {
            **self._minimal_profile(),
            "frontend": {"language": "JavaScript", "framework": "React"},
        }
        result = _build_body("FrontendStubAgent", profile)
        assert "Frontend (JavaScript/React):" in result

    def test_non_frontend_agent_no_frontend_block(self):
        profile = {
            **self._minimal_profile(),
            "frontend": {"language": "JavaScript", "framework": "React"},
        }
        result = _build_body("ServiceAgent", profile)
        assert "Frontend" not in result

    def test_needs_services_agent_gets_services_block(self):
        profile = {
            **self._minimal_profile(),
            "services": {"email": "sendgrid"},
        }
        result = _build_body("ConfigMiddlewareAgent", profile)
        assert "Services:" in result
        assert "email: sendgrid" in result

    def test_database_agent_gets_backend_persistence_facts(self):
        profile = {
            **self._minimal_profile(),
            "backend": {"persistence": {"api": "ctx.persistence.collection()"}},
        }
        result = _build_body("DatabaseAgent", profile)
        assert "Backend persistence API:" in result

    def test_parts_joined_with_double_newline(self):
        profile = {
            **self._minimal_profile(),
            "backend": {"language": "Python"},
            "database": {"type": "MongoDB"},
        }
        result = _build_body("ServiceAgent", profile)
        assert "\n\n" in result

    def test_empty_backend_dict_not_appended(self):
        # backend is {} (falsy) for _NEEDS_BACKEND agents → no backend block
        profile = {**self._minimal_profile(), "backend": {}}
        result = _build_body("ServiceAgent", profile)
        # Empty dict is falsy → backend block not appended
        assert "Backend" not in result
