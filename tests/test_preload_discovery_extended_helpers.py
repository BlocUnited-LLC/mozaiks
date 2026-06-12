"""
Pure helper unit tests for:
  factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py

Covers helpers NOT tested in test_preload_discovery_helpers.py
or test_preload_discovery_pure_helpers.py:

  _summarize_theme_evidence:
    - empty dict → None
    - None/falsy → None
    - appearance only → single-part summary
    - colors included → comma-joined up to 4
    - fonts included → comma-joined up to 4
    - layout_hints included → comma-joined up to 4
    - all fields populated → multi-part joined with semicolons
    - extra colors beyond 4 → capped at 4
    - empty colors list → no colors part
    - dict with only empty lists → None

  _summarise_openapi_spec:
    - empty spec → path_count 0, empty methods, sample_paths []
    - non-dict spec → path_count 0
    - spec with paths → path_count correct
    - methods extracted from path items
    - methods uppercased and sorted
    - sample_paths capped at 15
    - title and version extracted from info
    - source preserved in result
    - security_schemes extracted from components

  _context_graph_scan_policy_inputs:
    - both absent → {}
    - from context_variables → returned as dict
    - from discovery_inputs → returned as dict
    - context_variables takes priority over discovery_inputs
    - JSON string value coerced to dict

  _context_graph_request_text:
    - all sources empty → ""
    - raw_user_request from discovery_inputs → included
    - description from discovery_inputs → included
    - app_name from context_variables → included
    - multiple sources → first 4 joined by newline
    - empty string values excluded
    - whitespace-only values excluded

  _detect_storage_pattern:
    - no signals → "unknown"
    - mongodb package signal → "mongodb"
    - sql package signal → "sql"
    - redis package signal → "redis"
    - mongodb source pattern → "mongodb"
    - file_store source pattern → "file_store"
    - package signal takes priority over source
    - empty lists → "unknown"
"""
from __future__ import annotations

from factory_app.workflows.ExistingAppDiscovery.tools.preload_discovery_context import (
    _context_graph_request_text,
    _context_graph_scan_policy_inputs,
    _detect_storage_pattern,
    _summarise_openapi_spec,
    _summarize_theme_evidence,
)

# ---------------------------------------------------------------------------
# 1. _summarize_theme_evidence
# ---------------------------------------------------------------------------

class TestSummarizeThemeEvidence:
    def test_empty_dict_returns_none(self):
        assert _summarize_theme_evidence({}) is None

    def test_none_input_returns_none(self):
        assert _summarize_theme_evidence(None) is None  # type: ignore

    def test_appearance_only(self):
        result = _summarize_theme_evidence({"appearance": "dark"})
        assert result is not None
        assert "dark appearance" in result
        assert result.endswith(".")

    def test_colors_included(self):
        result = _summarize_theme_evidence({"colors": ["#000", "#fff"]})
        assert "colors #000, #fff" in result

    def test_fonts_included(self):
        result = _summarize_theme_evidence({"fonts": ["Inter", "Roboto"]})
        assert "fonts Inter, Roboto" in result

    def test_layout_hints_included(self):
        result = _summarize_theme_evidence({"layout_hints": ["sidebar", "grid"]})
        assert "layout hints sidebar, grid" in result

    def test_all_fields_multi_part_joined_with_semicolons(self):
        evidence = {
            "appearance": "light",
            "colors": ["#fff"],
            "fonts": ["Inter"],
            "layout_hints": ["grid"],
        }
        result = _summarize_theme_evidence(evidence)
        assert ";" in result
        assert "light appearance" in result
        assert "colors" in result
        assert "fonts" in result
        assert "layout hints" in result

    def test_colors_capped_at_four(self):
        colors = ["#111", "#222", "#333", "#444", "#555", "#666"]
        result = _summarize_theme_evidence({"colors": colors})
        # Only first 4 should appear
        assert "#555" not in result
        assert "#666" not in result
        assert "#111" in result

    def test_empty_colors_list_not_included(self):
        result = _summarize_theme_evidence({"appearance": "dark", "colors": []})
        assert "colors" not in result

    def test_dict_with_only_empty_lists_returns_none(self):
        evidence = {"colors": [], "fonts": [], "layout_hints": []}
        assert _summarize_theme_evidence(evidence) is None

    def test_result_starts_with_brand_evidence(self):
        result = _summarize_theme_evidence({"appearance": "dark"})
        assert result is not None
        assert result.startswith("Host brand evidence suggests")


# ---------------------------------------------------------------------------
# 2. _summarise_openapi_spec
# ---------------------------------------------------------------------------

class TestSummariseOpenApiSpec:
    def test_empty_spec_path_count_zero(self):
        result = _summarise_openapi_spec({}, "test_source")
        assert result["path_count"] == 0
        assert result["sample_paths"] == []

    def test_non_dict_spec(self):
        result = _summarise_openapi_spec("not-a-dict", "test_source")  # type: ignore
        assert result["path_count"] == 0
        assert result["methods"] == []

    def test_spec_with_paths_counted(self):
        spec = {"paths": {"/users": {"get": {}}, "/items": {"post": {}}}}
        result = _summarise_openapi_spec(spec, "test")
        assert result["path_count"] == 2

    def test_methods_extracted(self):
        spec = {"paths": {"/users": {"get": {}, "post": {}}}}
        result = _summarise_openapi_spec(spec, "test")
        assert "GET" in result["methods"]
        assert "POST" in result["methods"]

    def test_methods_uppercased(self):
        spec = {"paths": {"/users": {"get": {}}}}
        result = _summarise_openapi_spec(spec, "test")
        assert "GET" in result["methods"]
        assert "get" not in result["methods"]

    def test_methods_sorted(self):
        spec = {"paths": {"/a": {"post": {}, "get": {}, "delete": {}}}}
        result = _summarise_openapi_spec(spec, "test")
        assert result["methods"] == sorted(result["methods"])

    def test_sample_paths_capped_at_15(self):
        paths = {f"/path{i}": {"get": {}} for i in range(20)}
        spec = {"paths": paths}
        result = _summarise_openapi_spec(spec, "test")
        assert len(result["sample_paths"]) == 15

    def test_title_and_version_from_info(self):
        spec = {"info": {"title": "My API", "version": "1.0"}}
        result = _summarise_openapi_spec(spec, "test")
        assert result["title"] == "My API"
        assert result["version"] == "1.0"

    def test_source_preserved(self):
        result = _summarise_openapi_spec({}, "my_source")
        assert result["source"] == "my_source"

    def test_success_flag_set(self):
        result = _summarise_openapi_spec({}, "test")
        assert result["success"] is True

    def test_security_schemes_extracted(self):
        spec = {
            "components": {
                "securitySchemes": {"bearerAuth": {}, "apiKey": {}}
            }
        }
        result = _summarise_openapi_spec(spec, "test")
        assert "bearerAuth" in result["security_schemes"]
        assert "apiKey" in result["security_schemes"]

    def test_non_dict_path_item_skipped(self):
        spec = {"paths": {"/bad": "not-a-dict", "/good": {"get": {}}}}
        result = _summarise_openapi_spec(spec, "test")
        assert "GET" in result["methods"]


# ---------------------------------------------------------------------------
# 3. _context_graph_scan_policy_inputs
# ---------------------------------------------------------------------------

class TestContextGraphScanPolicyInputs:
    def test_both_absent_returns_empty_dict(self):
        result = _context_graph_scan_policy_inputs({}, {})
        assert result == {}

    def test_from_context_variables(self):
        cv = {"context_graph_scan_policy": {"max_files": 100}}
        result = _context_graph_scan_policy_inputs(cv, {})
        assert result == {"max_files": 100}

    def test_from_discovery_inputs(self):
        di = {"context_graph_scan_policy": {"max_files": 50}}
        result = _context_graph_scan_policy_inputs({}, di)
        assert result == {"max_files": 50}

    def test_context_variables_takes_priority(self):
        cv = {"context_graph_scan_policy": {"max_files": 100}}
        di = {"context_graph_scan_policy": {"max_files": 50}}
        result = _context_graph_scan_policy_inputs(cv, di)
        assert result["max_files"] == 100

    def test_json_string_coerced_to_dict(self):
        import json
        cv = {"context_graph_scan_policy": json.dumps({"max_files": 75})}
        result = _context_graph_scan_policy_inputs(cv, {})
        assert result == {"max_files": 75}

    def test_none_context_variables_falls_back_to_discovery(self):
        di = {"context_graph_scan_policy": {"max_files": 30}}
        result = _context_graph_scan_policy_inputs(None, di)
        assert result == {"max_files": 30}


# ---------------------------------------------------------------------------
# 4. _context_graph_request_text
# ---------------------------------------------------------------------------

class TestContextGraphRequestText:
    def test_all_sources_empty_returns_empty_string(self):
        assert _context_graph_request_text({}, {}) == ""

    def test_raw_user_request_from_discovery_inputs(self):
        di = {"raw_user_request": "Build me a CRM"}
        result = _context_graph_request_text({}, di)
        assert "Build me a CRM" in result

    def test_description_from_discovery_inputs(self):
        di = {"description": "A sales tracking app"}
        result = _context_graph_request_text({}, di)
        assert "A sales tracking app" in result

    def test_app_name_from_context_variables(self):
        cv = {"app_name": "MySalesApp"}
        result = _context_graph_request_text(cv, {})
        assert "MySalesApp" in result

    def test_app_description_from_context_variables(self):
        cv = {"app_description": "A comprehensive CRM"}
        result = _context_graph_request_text(cv, {})
        assert "A comprehensive CRM" in result

    def test_multiple_sources_joined_by_newline(self):
        cv = {"app_name": "App", "app_description": "Desc"}
        di = {"raw_user_request": "Request", "description": "InputDesc"}
        result = _context_graph_request_text(cv, di)
        assert "\n" in result

    def test_at_most_four_parts_in_result(self):
        cv = {
            "refresh_reason": "Reason",
            "app_description": "Desc",
            "app_name": "Name",
        }
        di = {
            "raw_user_request": "Request",
            "description": "Description",
        }
        result = _context_graph_request_text(cv, di)
        parts = result.split("\n")
        assert len(parts) <= 4

    def test_empty_string_values_excluded(self):
        di = {"raw_user_request": "", "description": "Valid"}
        result = _context_graph_request_text({}, di)
        assert result == "Valid"

    def test_whitespace_only_excluded(self):
        di = {"raw_user_request": "   ", "description": "Valid"}
        result = _context_graph_request_text({}, di)
        assert result == "Valid"

    def test_context_refresh_request_reason_included(self):
        cv = {"context_refresh_request": {"reason": "Schema changed"}}
        result = _context_graph_request_text(cv, {})
        assert "Schema changed" in result


# ---------------------------------------------------------------------------
# 5. _detect_storage_pattern
# ---------------------------------------------------------------------------

class TestDetectStoragePattern:
    def test_empty_inputs_returns_unknown(self):
        assert _detect_storage_pattern([], "") == "unknown"

    def test_mongoose_package_returns_mongodb(self):
        assert _detect_storage_pattern(["mongoose", "express"], "") == "mongodb"

    def test_pymongo_package_returns_mongodb(self):
        assert _detect_storage_pattern(["pymongo"], "") == "mongodb"

    def test_sqlalchemy_package_returns_sql(self):
        assert _detect_storage_pattern(["sqlalchemy", "fastapi"], "") == "sql"

    def test_prisma_package_returns_sql(self):
        assert _detect_storage_pattern(["prisma"], "") == "sql"

    def test_redis_package_returns_redis(self):
        assert _detect_storage_pattern(["ioredis"], "") == "redis"

    def test_aioredis_package_returns_redis(self):
        assert _detect_storage_pattern(["aioredis"], "") == "redis"

    def test_mongodb_source_pattern_returns_mongodb(self):
        assert _detect_storage_pattern([], "const client = new MongoClient(uri)") == "mongodb"

    def test_file_store_source_pattern_returns_file_store(self):
        assert _detect_storage_pattern([], "const data = fs.readFileSync(path)") == "file_store"

    def test_package_signal_takes_priority_over_source(self):
        # Package signals are checked first in the implementation
        result = _detect_storage_pattern(["pymongo"], "fs.readFileSync(path)")
        assert result == "mongodb"

    def test_redis_source_pattern_detected(self):
        assert _detect_storage_pattern([], "const client = new Redis(url)") == "redis"

    def test_unrecognized_packages_return_unknown(self):
        assert _detect_storage_pattern(["some-unrelated-pkg"], "no signals here") == "unknown"
