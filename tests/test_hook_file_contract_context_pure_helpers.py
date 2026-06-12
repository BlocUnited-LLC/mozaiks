"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_file_contract_context.py

Covers:
  _format_list_block:
    - empty items → []
    - single item → title + "  - item" lines
    - multiple items → all included
    - no indent applied to title (unlike _fmt_list in language profile)
    - items prefixed with "  - "

  _build_contract_block:
    - contract_name used as header line
    - owner_agent rendered when present
    - owner_agent omitted when empty/missing
    - summary rendered when present
    - required_outputs rendered
    - optional_outputs rendered
    - hard_constraints capped at 5
    - whitespace items in lists filtered
    - empty contract → just name: line
    - downstream_python_defaults rendered
    - optional_python_hooks rendered
    - optional_js_stubs rendered

  _build_archetype_block:
    - name as header
    - summary rendered when present
    - select_when capped at 3 items
    - yaml_always all rendered
    - yaml_optional rendered
    - python_stub_defaults rendered
    - hard_constraints capped at 4
    - empty archetype → just name: line
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_archetype_block,
    _build_contract_block,
    _format_list_block,
)

# ---------------------------------------------------------------------------
# 1. _format_list_block
# ---------------------------------------------------------------------------

class TestFormatListBlock:
    def test_empty_items_returns_empty_list(self):
        assert _format_list_block("required_outputs:", []) == []

    def test_single_item_returns_two_lines(self):
        result = _format_list_block("required_outputs:", ["handler.py"])
        assert len(result) == 2

    def test_title_is_first_line(self):
        result = _format_list_block("required_outputs:", ["handler.py"])
        assert result[0] == "required_outputs:"

    def test_item_has_two_space_prefix_and_dash(self):
        result = _format_list_block("required_outputs:", ["handler.py"])
        assert result[1] == "  - handler.py"

    def test_multiple_items_all_included(self):
        result = _format_list_block("files:", ["a.py", "b.py", "c.py"])
        assert len(result) == 4
        assert any("a.py" in line for line in result)
        assert any("b.py" in line for line in result)
        assert any("c.py" in line for line in result)

    def test_title_preserved_exactly(self):
        result = _format_list_block("  required_outputs:", ["file.py"])
        assert result[0] == "  required_outputs:"


# ---------------------------------------------------------------------------
# 2. _build_contract_block
# ---------------------------------------------------------------------------

class TestBuildContractBlock:
    def test_contract_name_is_header(self):
        result = _build_contract_block("module_contract", {})
        assert result.startswith("module_contract:")

    def test_empty_contract_single_line(self):
        result = _build_contract_block("module_contract", {})
        lines = result.splitlines()
        assert len(lines) == 1
        assert lines[0] == "module_contract:"

    def test_owner_agent_rendered(self):
        contract = {"owner_agent": "ConfigMiddlewareAgent"}
        result = _build_contract_block("module_contract", contract)
        assert "owner_agent: ConfigMiddlewareAgent" in result

    def test_owner_agent_omitted_when_empty(self):
        contract = {"owner_agent": ""}
        result = _build_contract_block("module_contract", contract)
        assert "owner_agent" not in result

    def test_owner_agent_omitted_when_none(self):
        contract = {"owner_agent": None}
        result = _build_contract_block("module_contract", contract)
        assert "owner_agent" not in result

    def test_summary_rendered(self):
        contract = {"summary": "Generates module YAML and Python stubs."}
        result = _build_contract_block("module_contract", contract)
        assert "Generates module YAML and Python stubs." in result

    def test_required_outputs_rendered(self):
        contract = {"required_outputs": ["module.yaml", "handler.py"]}
        result = _build_contract_block("module_contract", contract)
        assert "module.yaml" in result
        assert "handler.py" in result

    def test_optional_outputs_rendered(self):
        contract = {"optional_outputs": ["schemas.py"]}
        result = _build_contract_block("module_contract", contract)
        assert "schemas.py" in result

    def test_hard_constraints_capped_at_five(self):
        constraints = [f"constraint_{i}" for i in range(8)]
        contract = {"hard_constraints": constraints}
        result = _build_contract_block("module_contract", contract)
        for i in range(5):
            assert f"constraint_{i}" in result
        assert "constraint_5" not in result

    def test_whitespace_items_filtered(self):
        contract = {"required_outputs": ["  ", "handler.py", ""]}
        result = _build_contract_block("module_contract", contract)
        assert "handler.py" in result
        # Whitespace entry should not appear in output
        assert "\n  -   \n" not in result

    def test_downstream_python_defaults_rendered(self):
        contract = {"downstream_python_defaults": ["backend/handler.py"]}
        result = _build_contract_block("module_contract", contract)
        assert "backend/handler.py" in result

    def test_optional_python_hooks_rendered(self):
        contract = {"optional_python_hooks": ["backend/settings.py"]}
        result = _build_contract_block("module_contract", contract)
        assert "backend/settings.py" in result

    def test_optional_js_stubs_rendered(self):
        contract = {"optional_js_stubs": ["ui/MyComponent.jsx"]}
        result = _build_contract_block("module_contract", contract)
        assert "ui/MyComponent.jsx" in result

    def test_all_fields_in_one_call(self):
        contract = {
            "owner_agent": "ConfigMiddlewareAgent",
            "summary": "Generates module contracts.",
            "required_outputs": ["module.yaml"],
            "hard_constraints": ["Must use canonical paths"],
        }
        result = _build_contract_block("module_contract", contract)
        assert "ConfigMiddlewareAgent" in result
        assert "Generates module contracts." in result
        assert "module.yaml" in result
        assert "Must use canonical paths" in result


# ---------------------------------------------------------------------------
# 3. _build_archetype_block
# ---------------------------------------------------------------------------

class TestBuildArchetypeBlock:
    def test_name_is_header(self):
        result = _build_archetype_block("crud_module", {})
        assert result.startswith("crud_module:")

    def test_empty_archetype_single_line(self):
        result = _build_archetype_block("crud_module", {})
        lines = result.splitlines()
        assert len(lines) == 1

    def test_summary_rendered(self):
        archetype = {"summary": "Standard CRUD module with repo and service layers."}
        result = _build_archetype_block("crud_module", archetype)
        assert "Standard CRUD module" in result

    def test_select_when_capped_at_three(self):
        archetype = {"select_when": [f"when_{i}" for i in range(6)]}
        result = _build_archetype_block("crud_module", archetype)
        for i in range(3):
            assert f"when_{i}" in result
        assert "when_3" not in result

    def test_yaml_always_all_rendered(self):
        archetype = {"canonical_yaml_family": {"always": ["module.yaml", "events.yaml"]}}
        result = _build_archetype_block("crud_module", archetype)
        assert "module.yaml" in result
        assert "events.yaml" in result

    def test_yaml_optional_rendered(self):
        archetype = {"canonical_yaml_family": {"optional": ["settings.yaml"]}}
        result = _build_archetype_block("crud_module", archetype)
        assert "settings.yaml" in result

    def test_python_stub_defaults_rendered(self):
        archetype = {"python_stub_defaults": ["backend/handler.py"]}
        result = _build_archetype_block("crud_module", archetype)
        assert "backend/handler.py" in result

    def test_hard_constraints_capped_at_four(self):
        archetype = {"hard_constraints": [f"c_{i}" for i in range(7)]}
        result = _build_archetype_block("crud_module", archetype)
        for i in range(4):
            assert f"c_{i}" in result
        assert "c_4" not in result

    def test_whitespace_items_filtered(self):
        archetype = {"select_when": ["  ", "valid_case", ""]}
        result = _build_archetype_block("crud_module", archetype)
        assert "valid_case" in result
        # Whitespace-only entries should be filtered before rendering
        assert "  -   \n" not in result
