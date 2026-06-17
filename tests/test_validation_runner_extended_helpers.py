"""
Pure helper unit tests for:
  mozaiksai/control_plane/validation_runner.py

Covers helpers NOT tested in test_validation_runner_helpers.py:

  _code_file_list:
    - empty files dict → []
    - all files included when predicate is None
    - files sorted by filename
    - predicate filters entries
    - returns list of {filename, content} dicts

  _page_schema_entries:
    - non-page files → excluded
    - ui/pages/*.yaml → included
    - ui/pages/*.yml → included
    - custom/ sub-folder excluded
    - non-YAML extension excluded
    - invalid YAML → warning, no page entry
    - non-dict YAML → warning, no page entry
    - valid YAML mapping → parsed page included
    - returns (pages, warnings, artifacts) tuple

  _custom_react_entries:
    - ui/pages/custom/*.jsx → included
    - ui/pages/*.jsx → excluded (not in custom/)
    - ui/pages/custom/*.yaml → excluded (wrong extension)

  _module_contract_entries:
    - modules/{id}/module.yaml → included
    - modules/{id}/contracts/events.yaml → included
    - modules/{id}/service.py → excluded (not contract file)
    - non-modules path → excluded

  _module_contract_module_dir:
    - "modules/orders/module.yaml" → "modules/orders"
    - non-modules path → None
    - single segment → None

  _route_bundle_entries:
    - ui/route_manifest.json → included
    - ui/index.js → included
    - admin/admin_registry.yaml → included
    - ui/pages/custom/MyComp.jsx → included
    - ui/pages/dashboard.yaml → included
    - other files → excluded

  _database_surface_entries:
    - data/contract.json → included
    - data/migrations/001.json → included
    - other files → excluded

  _experience_spec_entries:
    - experience_spec.json → included
    - ui_schema.yaml → included
    - other files → excluded

  _is_module_internal_managed_path:
    - modules/managed_payments/... → True
    - modules/payment_provider/... → True
    - modules/orders/... → False
    - non-modules path → False
    - single-segment path → False

  _normalize_selected_targets:
    - None → ([], [])
    - empty list → ([], [])
    - known target → returns mapped names
    - unknown target → in unknown list
    - whitespace/empty entries → skipped
    - known alias → resolved

  _result:
    - name, status, reason preserved
    - artifacts deduplicated
    - no artifacts → empty list
    - status "passed" preserved
    - status "failed" preserved

  _unknown_validation_item:
    - name preserved
    - status is "warning"
    - reason describes unregistered validator
"""
from __future__ import annotations

from mozaiksai.control_plane.validation_runner import (
    _code_file_list,
    _custom_react_entries,
    _database_surface_entries,
    _experience_spec_entries,
    _is_module_internal_managed_path,
    _module_contract_entries,
    _module_contract_module_dir,
    _normalize_selected_targets,
    _page_schema_entries,
    _result,
    _route_bundle_entries,
    _unknown_validation_item,
)

# ---------------------------------------------------------------------------
# 1. _code_file_list
# ---------------------------------------------------------------------------

class TestCodeFileList:
    def test_empty_files_returns_empty(self):
        assert _code_file_list({}) == []

    def test_no_predicate_includes_all(self):
        files = {"b.py": "b", "a.py": "a"}
        result = _code_file_list(files)
        assert len(result) == 2

    def test_files_sorted_by_filename(self):
        files = {"z.py": "z", "a.py": "a", "m.py": "m"}
        result = _code_file_list(files)
        names = [e["filename"] for e in result]
        assert names == sorted(names)

    def test_each_entry_has_filename_and_content(self):
        files = {"foo.py": "bar"}
        result = _code_file_list(files)
        assert result[0]["filename"] == "foo.py"
        assert result[0]["content"] == "bar"

    def test_predicate_filters_entries(self):
        files = {"include_me.py": "x", "skip_me.py": "y"}
        result = _code_file_list(files, predicate=lambda f: f.startswith("include"))
        assert len(result) == 1
        assert result[0]["filename"] == "include_me.py"

    def test_predicate_excludes_all_returns_empty(self):
        files = {"a.py": "x"}
        assert _code_file_list(files, predicate=lambda f: False) == []


# ---------------------------------------------------------------------------
# 2. _page_schema_entries
# ---------------------------------------------------------------------------

class TestPageSchemaEntries:
    def test_non_page_files_excluded(self):
        files = {"modules/orders/module.yaml": "id: orders"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert pages == []
        assert artifacts == []

    def test_ui_pages_yaml_included(self):
        files = {"ui/pages/dashboard.yaml": "id: dashboard\ntitle: Dashboard\n"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert len(artifacts) == 1
        assert len(pages) == 1

    def test_ui_pages_yml_included(self):
        files = {"ui/pages/settings.yml": "id: settings\n"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert len(artifacts) == 1

    def test_custom_subfolder_excluded(self):
        files = {"ui/pages/custom/MyComp.yaml": "x: 1"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert artifacts == []

    def test_non_yaml_extension_excluded(self):
        files = {"ui/pages/dashboard.jsx": "x"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert artifacts == []

    def test_invalid_yaml_produces_warning(self):
        files = {"ui/pages/bad.yaml": "key: [broken: yaml"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert len(warnings) == 1
        assert "ui/pages/bad.yaml" in warnings[0]
        assert len(pages) == 0

    def test_non_dict_yaml_produces_warning(self):
        files = {"ui/pages/list_page.yaml": "- item1\n- item2\n"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert len(warnings) == 1
        assert len(pages) == 0

    def test_valid_yaml_dict_parsed_into_pages(self):
        files = {"ui/pages/home.yaml": "id: home\ntitle: Home\n"}
        pages, warnings, artifacts = _page_schema_entries(files)
        assert len(pages) == 1
        assert pages[0]["id"] == "home"
        assert warnings == []


# ---------------------------------------------------------------------------
# 3. _custom_react_entries
# ---------------------------------------------------------------------------

class TestCustomReactEntries:
    def test_custom_jsx_included(self):
        files = {"ui/pages/custom/MyPanel.jsx": "export default function MyPanel() {}"}
        result = _custom_react_entries(files)
        assert len(result) == 1
        assert result[0]["filename"] == "ui/pages/custom/MyPanel.jsx"

    def test_non_custom_jsx_excluded(self):
        files = {"ui/pages/MyPanel.jsx": "x"}
        assert _custom_react_entries(files) == []

    def test_custom_yaml_excluded(self):
        files = {"ui/pages/custom/MyPage.yaml": "x: 1"}
        assert _custom_react_entries(files) == []

    def test_empty_files_returns_empty(self):
        assert _custom_react_entries({}) == []


# ---------------------------------------------------------------------------
# 4. _module_contract_entries
# ---------------------------------------------------------------------------

class TestModuleContractEntries:
    def test_module_yaml_included(self):
        files = {"modules/orders/module.yaml": "id: orders"}
        result = _module_contract_entries(files)
        assert any(e["filename"] == "modules/orders/module.yaml" for e in result)

    def test_contracts_yaml_included(self):
        files = {"modules/orders/contracts/events.yaml": "events: []"}
        result = _module_contract_entries(files)
        assert len(result) == 1

    def test_backend_service_excluded(self):
        files = {"modules/orders/backend/service.py": "x = 1"}
        assert _module_contract_entries(files) == []

    def test_non_modules_path_excluded(self):
        files = {"config/settings.yaml": "x: 1"}
        assert _module_contract_entries(files) == []

    def test_empty_files_returns_empty(self):
        assert _module_contract_entries({}) == []


# ---------------------------------------------------------------------------
# 5. _module_contract_module_dir
# ---------------------------------------------------------------------------

class TestModuleContractModuleDir:
    def test_module_yaml_returns_dir(self):
        assert _module_contract_module_dir("modules/orders/module.yaml") == "modules/orders"

    def test_nested_contract_returns_dir(self):
        assert _module_contract_module_dir("modules/orders/contracts/events.yaml") == "modules/orders"

    def test_non_modules_returns_none(self):
        assert _module_contract_module_dir("config/settings.yaml") is None

    def test_single_segment_returns_none(self):
        assert _module_contract_module_dir("modules") is None

    def test_modules_with_module_id_only_returns_dir(self):
        assert _module_contract_module_dir("modules/products/module.yaml") == "modules/products"


# ---------------------------------------------------------------------------
# 6. _route_bundle_entries
# ---------------------------------------------------------------------------

class TestRouteBundleEntries:
    def test_route_manifest_included(self):
        files = {"ui/route_manifest.json": "{}"}
        result = _route_bundle_entries(files)
        assert any(e["filename"] == "ui/route_manifest.json" for e in result)

    def test_ui_index_included(self):
        files = {"ui/index.js": "export {}"}
        result = _route_bundle_entries(files)
        assert any(e["filename"] == "ui/index.js" for e in result)

    def test_admin_registry_included(self):
        files = {"admin/admin_registry.yaml": "pages: []"}
        result = _route_bundle_entries(files)
        assert any(e["filename"] == "admin/admin_registry.yaml" for e in result)

    def test_custom_jsx_page_included(self):
        files = {"ui/pages/custom/DashPage.jsx": "x"}
        result = _route_bundle_entries(files)
        assert len(result) == 1

    def test_yaml_page_included(self):
        files = {"ui/pages/home.yaml": "id: home"}
        result = _route_bundle_entries(files)
        assert len(result) == 1

    def test_unrelated_file_excluded(self):
        files = {"modules/orders/module.yaml": "id: orders"}
        assert _route_bundle_entries(files) == []


# ---------------------------------------------------------------------------
# 7. _database_surface_entries
# ---------------------------------------------------------------------------

class TestDatabaseSurfaceEntries:
    def test_data_contract_included(self):
        files = {"data/contract.json": "{}"}
        result = _database_surface_entries(files)
        assert any(e["filename"] == "data/contract.json" for e in result)

    def test_migration_json_included(self):
        files = {"data/migrations/001_init.json": "{}"}
        result = _database_surface_entries(files)
        assert len(result) == 1

    def test_unrelated_file_excluded(self):
        files = {"config/settings.json": "{}"}
        assert _database_surface_entries(files) == []

    def test_empty_files_returns_empty(self):
        assert _database_surface_entries({}) == []


# ---------------------------------------------------------------------------
# 8. _experience_spec_entries
# ---------------------------------------------------------------------------

class TestExperienceSpecEntries:
    def test_experience_spec_json_included(self):
        files = {"experience_spec.json": "{}"}
        result = _experience_spec_entries(files)
        assert any(e["filename"] == "experience_spec.json" for e in result)

    def test_ui_schema_yaml_included(self):
        files = {"ui_schema.yaml": "title: Test"}
        result = _experience_spec_entries(files)
        assert len(result) == 1

    def test_experience_spec_yaml_included(self):
        files = {"experience_spec.yaml": "navigation_model: sidebar"}
        result = _experience_spec_entries(files)
        assert len(result) == 1

    def test_non_experience_spec_excluded(self):
        files = {"data/contract.json": "{}"}
        assert _experience_spec_entries(files) == []

    def test_nested_path_but_matching_name_included(self):
        files = {"modules/orders/experience_spec.json": "{}"}
        result = _experience_spec_entries(files)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 9. _is_module_internal_managed_path
# ---------------------------------------------------------------------------

class TestIsModuleInternalManagedPath:
    def test_managed_prefix_returns_true(self):
        assert _is_module_internal_managed_path("modules/managed_payments/module.yaml") is True

    def test_provider_term_returns_true(self):
        assert _is_module_internal_managed_path("modules/payment_provider/module.yaml") is True

    def test_standard_module_returns_false(self):
        assert _is_module_internal_managed_path("modules/orders/module.yaml") is False

    def test_non_modules_path_returns_false(self):
        assert _is_module_internal_managed_path("config/settings.yaml") is False

    def test_single_segment_returns_false(self):
        assert _is_module_internal_managed_path("modules") is False

    def test_empty_string_returns_false(self):
        assert _is_module_internal_managed_path("") is False


# ---------------------------------------------------------------------------
# 10. _normalize_selected_targets
# ---------------------------------------------------------------------------

class TestNormalizeSelectedTargets:
    def test_none_returns_empty_tuples(self):
        normalized, unknown = _normalize_selected_targets(None)
        assert normalized == []
        assert unknown == []

    def test_empty_list_returns_empty_tuples(self):
        normalized, unknown = _normalize_selected_targets([])
        assert normalized == []
        assert unknown == []

    def test_known_target_resolved(self):
        normalized, unknown = _normalize_selected_targets(["route_component_validation"])
        assert "route_component_validation" in normalized
        assert unknown == []

    def test_unknown_target_in_unknown_list(self):
        normalized, unknown = _normalize_selected_targets(["nonexistent_validator"])
        assert "nonexistent_validator" in unknown
        assert normalized == []

    def test_whitespace_stripped_from_entry(self):
        normalized, unknown = _normalize_selected_targets(["  route_component_validation  "])
        assert "route_component_validation" in normalized

    def test_empty_string_entries_skipped(self):
        normalized, unknown = _normalize_selected_targets(["", "route_component_validation"])
        assert len(normalized) >= 1
        assert "" not in unknown

    def test_alias_database_migration_review_resolved(self):
        normalized, unknown = _normalize_selected_targets(["database_migration_review"])
        assert "data_contract_validation" in normalized
        assert "migration_plan_validation" in normalized
        assert unknown == []

    def test_duplicate_targets_deduped(self):
        normalized, _ = _normalize_selected_targets([
            "route_component_validation",
            "route_component_validation",
        ])
        assert normalized.count("route_component_validation") == 1


# ---------------------------------------------------------------------------
# 11. _result
# ---------------------------------------------------------------------------

class TestResult:
    def test_name_preserved(self):
        r = _result(name="my_validation", status="passed", reason="All good")
        assert r.name == "my_validation"

    def test_status_passed_preserved(self):
        r = _result(name="v", status="passed", reason="ok")
        assert r.status == "passed"

    def test_status_failed_preserved(self):
        r = _result(name="v", status="failed", reason="bad")
        assert r.status == "failed"

    def test_reason_preserved(self):
        r = _result(name="v", status="skipped", reason="Not applicable")
        assert r.reason == "Not applicable"

    def test_no_artifacts_returns_empty_list(self):
        r = _result(name="v", status="passed", reason="ok")
        assert r.artifacts == []

    def test_artifacts_included(self):
        r = _result(name="v", status="passed", reason="ok", artifacts=["a.yaml"])
        assert "a.yaml" in r.artifacts

    def test_artifacts_deduplicated(self):
        r = _result(name="v", status="passed", reason="ok", artifacts=["a.yaml", "a.yaml"])
        assert r.artifacts.count("a.yaml") == 1


# ---------------------------------------------------------------------------
# 12. _unknown_validation_item
# ---------------------------------------------------------------------------

class TestUnknownValidationItem:
    def test_name_preserved(self):
        r = _unknown_validation_item("my_unknown_check")
        assert r.name == "my_unknown_check"

    def test_status_is_warning(self):
        r = _unknown_validation_item("something")
        assert r.status == "warning"

    def test_reason_mentions_no_validator(self):
        r = _unknown_validation_item("something")
        assert "No deterministic validator" in r.reason

    def test_artifacts_empty(self):
        r = _unknown_validation_item("something")
        assert r.artifacts == []
