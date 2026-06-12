"""
generated_bundle_scanner private helper unit tests.

Covers pure helpers NOT tested by test_generated_bundle_scanner.py
(which only tests the public scan_generated_bundle() API).

  _is_scannable:
    - .py / .js / .jsx / .ts / .tsx / .yaml / .yml → True
    - .env and .env.example → True
    - .png / .json / .txt → False
    - uppercase suffixes → True (lowercased internally)
    - empty string → False

  _normalized_path:
    - backslashes converted to forward slashes
    - leading/trailing whitespace stripped
    - empty string → empty string
    - already-forward-slash path unchanged

  _normalized_files_map:
    - keys normalized; values unchanged
    - empty-path keys dropped
    - dict with mixed paths

  _iter_module_yaml_paths:
    - modules/{module_id}/module.yaml → returned
    - wrong depth (2 parts, 4 parts) → skipped
    - wrong root (other/x/module.yaml) → skipped
    - wrong filename (modules/x/other.yaml) → skipped
    - module_id extracted correctly
    - empty module_id segment → skipped

  _declared_module_id_from_yaml:
    - valid YAML with module.id → id returned
    - valid YAML without module key → top-level id returned
    - top-level id field → returned
    - malformed YAML → None
    - non-dict parsed → None
    - empty module_id → None

  _load_data_contract:
    - missing key → (None, None)
    - valid JSON object → (dict, None)
    - invalid JSON → (None, error message string)
    - JSON array (not object) → (None, error message string)

  _module_surface_ids:
    - no surfaces key → empty set
    - surfaces not list → empty set
    - surface with kind "module" → id in result
    - surface with kind "page" → id excluded
    - collection ownership module → id in result
    - nested ownership kind derivation from parent surface_kind
    - non-dict surface items skipped

  _find_raw_secret_fields:
    - empty dict → []
    - flat dict with secret key → path reported
    - nested dict → dotted path reported
    - list of dicts → indexed path reported
    - non-secret key → not reported
    - empty string value → not reported
    - non-dict/list value → nothing reported

  _pack_id_from_descriptor:
    - non-dict → ""
    - capability_pack_id field → returned
    - id field fallback → returned
    - pack_id field fallback → returned
    - all three present → capability_pack_id wins
    - all empty → ""

  _selected_hosted_pack_ids:
    - None → empty set
    - empty list → empty set
    - non-hosted_pack capability_source → excluded
    - hosted_pack with id → included
    - non-dict item → skipped
    - multiple hosted packs → all ids

  _iter_api_endpoint_literals:
    - no match → []
    - double-quoted /api/modules/... → found
    - single-quoted /api/modules/... → found
    - partial match (no opening quote) → not found
    - multiple matches in content
"""
from __future__ import annotations

import json

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _declared_module_id_from_yaml,
    _find_raw_secret_fields,
    _is_scannable,
    _iter_api_endpoint_literals,
    _iter_module_yaml_paths,
    _load_data_contract,
    _module_surface_ids,
    _normalized_files_map,
    _normalized_path,
    _pack_id_from_descriptor,
    _selected_hosted_pack_ids,
)
from mozaiksai.core.runtime.app.paths import APP_DATA_CONTRACT_PATH

# ---------------------------------------------------------------------------
# 1. _is_scannable
# ---------------------------------------------------------------------------

class TestIsScannable:
    def test_py_is_scannable(self):
        assert _is_scannable("app/service.py") is True

    def test_js_is_scannable(self):
        assert _is_scannable("src/index.js") is True

    def test_jsx_is_scannable(self):
        assert _is_scannable("components/App.jsx") is True

    def test_ts_is_scannable(self):
        assert _is_scannable("src/types.ts") is True

    def test_tsx_is_scannable(self):
        assert _is_scannable("src/App.tsx") is True

    def test_yaml_is_scannable(self):
        assert _is_scannable("config/module.yaml") is True

    def test_yml_is_scannable(self):
        assert _is_scannable("config/module.yml") is True

    def test_env_is_scannable(self):
        assert _is_scannable(".env") is True

    def test_env_example_is_scannable(self):
        assert _is_scannable(".env.example") is True

    def test_png_not_scannable(self):
        assert _is_scannable("assets/logo.png") is False

    def test_json_not_scannable(self):
        assert _is_scannable("data/contract.json") is False

    def test_txt_not_scannable(self):
        assert _is_scannable("README.txt") is False

    def test_uppercase_suffix_scannable(self):
        # case-insensitive check
        assert _is_scannable("Service.PY") is True

    def test_empty_string_not_scannable(self):
        assert _is_scannable("") is False


# ---------------------------------------------------------------------------
# 2. _normalized_path
# ---------------------------------------------------------------------------

class TestNormalizedPath:
    def test_backslash_to_forward_slash(self):
        assert _normalized_path("modules\\tickets\\module.yaml") == "modules/tickets/module.yaml"

    def test_forward_slash_unchanged(self):
        assert _normalized_path("modules/tickets/module.yaml") == "modules/tickets/module.yaml"

    def test_whitespace_stripped(self):
        assert _normalized_path("  app.json  ") == "app.json"

    def test_empty_string(self):
        assert _normalized_path("") == ""

    def test_mixed_slashes(self):
        assert _normalized_path("a\\b/c\\d") == "a/b/c/d"


# ---------------------------------------------------------------------------
# 3. _normalized_files_map
# ---------------------------------------------------------------------------

class TestNormalizedFilesMap:
    def test_backslash_keys_normalized(self):
        result = _normalized_files_map({"modules\\tickets\\module.yaml": "content"})
        assert "modules/tickets/module.yaml" in result
        assert result["modules/tickets/module.yaml"] == "content"

    def test_empty_path_key_dropped(self):
        result = _normalized_files_map({"": "orphan"})
        assert "" not in result

    def test_values_unchanged(self):
        result = _normalized_files_map({"app.json": '{"name": "Test"}'})
        assert result["app.json"] == '{"name": "Test"}'

    def test_empty_dict(self):
        assert _normalized_files_map({}) == {}

    def test_multiple_keys_all_normalized(self):
        result = _normalized_files_map({"a\\b.py": "x", "c/d.yaml": "y"})
        assert set(result.keys()) == {"a/b.py", "c/d.yaml"}


# ---------------------------------------------------------------------------
# 4. _iter_module_yaml_paths
# ---------------------------------------------------------------------------

class TestIterModuleYamlPaths:
    def test_canonical_path_returned(self):
        result = _iter_module_yaml_paths({"modules/tickets/module.yaml": ""})
        assert result == {"tickets": "modules/tickets/module.yaml"}

    def test_wrong_depth_two_parts_skipped(self):
        result = _iter_module_yaml_paths({"modules/module.yaml": ""})
        assert result == {}

    def test_wrong_depth_four_parts_skipped(self):
        result = _iter_module_yaml_paths({"modules/tickets/backend/module.yaml": ""})
        assert result == {}

    def test_wrong_root_skipped(self):
        result = _iter_module_yaml_paths({"other/tickets/module.yaml": ""})
        assert result == {}

    def test_wrong_filename_skipped(self):
        result = _iter_module_yaml_paths({"modules/tickets/handler.py": ""})
        assert result == {}

    def test_multiple_modules(self):
        files = {
            "modules/tickets/module.yaml": "",
            "modules/users/module.yaml": "",
        }
        result = _iter_module_yaml_paths(files)
        assert result == {
            "tickets": "modules/tickets/module.yaml",
            "users": "modules/users/module.yaml",
        }

    def test_non_module_paths_ignored(self):
        files = {
            "modules/tickets/module.yaml": "",
            "app.json": "",
            "data/contract.json": "",
        }
        result = _iter_module_yaml_paths(files)
        assert len(result) == 1
        assert "tickets" in result


# ---------------------------------------------------------------------------
# 5. _declared_module_id_from_yaml
# ---------------------------------------------------------------------------

class TestDeclaredModuleIdFromYaml:
    def test_module_id_under_module_key(self):
        yaml_content = "module:\n  id: tickets\n  handler: backend.handler:Module\n"
        assert _declared_module_id_from_yaml("modules/tickets/module.yaml", yaml_content) == "tickets"

    def test_top_level_id_field(self):
        yaml_content = "id: users\n"
        assert _declared_module_id_from_yaml("modules/users/module.yaml", yaml_content) == "users"

    def test_malformed_yaml_returns_none(self):
        assert _declared_module_id_from_yaml("path", "{ bad: yaml:: }}}") is None

    def test_non_dict_yaml_returns_none(self):
        assert _declared_module_id_from_yaml("path", "- item1\n- item2\n") is None

    def test_empty_module_id_returns_none(self):
        yaml_content = "module:\n  id: ''\n"
        assert _declared_module_id_from_yaml("path", yaml_content) is None

    def test_whitespace_only_module_id_returns_none(self):
        yaml_content = "module:\n  id: '   '\n"
        assert _declared_module_id_from_yaml("path", yaml_content) is None


# ---------------------------------------------------------------------------
# 6. _load_data_contract
# ---------------------------------------------------------------------------

class TestLoadDataContract:
    def test_missing_key_returns_none_none(self):
        result, error = _load_data_contract({})
        assert result is None
        assert error is None

    def test_valid_json_object_returned(self):
        data = {"version": "1", "surfaces": []}
        files = {APP_DATA_CONTRACT_PATH: json.dumps(data)}
        result, error = _load_data_contract(files)
        assert result == data
        assert error is None

    def test_invalid_json_returns_error_string(self):
        files = {APP_DATA_CONTRACT_PATH: "not valid json {{{"}
        result, error = _load_data_contract(files)
        assert result is None
        assert isinstance(error, str)
        assert APP_DATA_CONTRACT_PATH in error

    def test_json_array_returns_error_string(self):
        files = {APP_DATA_CONTRACT_PATH: json.dumps([1, 2, 3])}
        result, error = _load_data_contract(files)
        assert result is None
        assert isinstance(error, str)
        assert "JSON object" in error


# ---------------------------------------------------------------------------
# 7. _module_surface_ids
# ---------------------------------------------------------------------------

class TestModuleSurfaceIds:
    def test_no_surfaces_key_returns_empty(self):
        assert _module_surface_ids({}) == set()

    def test_surfaces_not_list_returns_empty(self):
        assert _module_surface_ids({"surfaces": "not_a_list"}) == set()

    def test_surface_with_module_kind_returns_id(self):
        contract = {
            "surfaces": [{"surface_id": "tickets", "surface_kind": "module"}]
        }
        assert _module_surface_ids(contract) == {"tickets"}

    def test_surface_with_page_kind_excluded(self):
        contract = {
            "surfaces": [{"surface_id": "home", "surface_kind": "page"}]
        }
        assert _module_surface_ids(contract) == set()

    def test_collection_ownership_module_included(self):
        contract = {
            "surfaces": [
                {
                    "surface_id": "portal",
                    "surface_kind": "page",
                    "collections": [
                        {
                            "module_id": "tasks",
                            "ownership": {"surface_id": "tasks", "surface_kind": "module"},
                        }
                    ],
                }
            ]
        }
        assert "tasks" in _module_surface_ids(contract)

    def test_non_dict_surface_items_skipped(self):
        contract = {
            "surfaces": ["string_item", {"surface_id": "tickets", "surface_kind": "module"}]
        }
        assert _module_surface_ids(contract) == {"tickets"}


# ---------------------------------------------------------------------------
# 8. _find_raw_secret_fields
# ---------------------------------------------------------------------------

class TestFindRawSecretFields:
    def test_empty_dict_returns_empty(self):
        assert _find_raw_secret_fields({}) == []

    def test_flat_secret_key_reported(self):
        result = _find_raw_secret_fields({"api_key": "sk-abc"})
        assert "api_key" in result

    def test_nested_dotted_path_reported(self):
        result = _find_raw_secret_fields({"outer": {"token": "abc123"}})
        assert "outer.token" in result

    def test_list_of_dicts_indexed_path(self):
        result = _find_raw_secret_fields([{"password": "secret"}])
        assert "0.password" in result

    def test_non_secret_key_not_reported(self):
        result = _find_raw_secret_fields({"username": "alice"})
        assert result == []

    def test_empty_string_value_not_reported(self):
        result = _find_raw_secret_fields({"api_key": ""})
        assert result == []

    def test_none_value_not_reported(self):
        result = _find_raw_secret_fields({"api_key": None})
        assert result == []

    def test_deeply_nested(self):
        result = _find_raw_secret_fields({"a": {"b": {"secret_value": "xyz"}}})
        assert "a.b.secret_value" in result

    def test_webhook_secret_reported(self):
        result = _find_raw_secret_fields({"webhook_secret": "whsec_abc"})
        assert "webhook_secret" in result

    def test_connection_string_reported(self):
        result = _find_raw_secret_fields({"connection_string": "mongodb://user:pass@host"})
        assert "connection_string" in result


# ---------------------------------------------------------------------------
# 9. _pack_id_from_descriptor
# ---------------------------------------------------------------------------

class TestPackIdFromDescriptor:
    def test_non_dict_returns_empty(self):
        assert _pack_id_from_descriptor("string") == ""
        assert _pack_id_from_descriptor(None) == ""

    def test_capability_pack_id_field_returned(self):
        assert _pack_id_from_descriptor({"capability_pack_id": "mozaikspay"}) == "mozaikspay"

    def test_id_field_fallback(self):
        assert _pack_id_from_descriptor({"id": "mozaikspay"}) == "mozaikspay"

    def test_pack_id_field_fallback(self):
        assert _pack_id_from_descriptor({"pack_id": "mozaikspay"}) == "mozaikspay"

    def test_capability_pack_id_wins_over_id(self):
        result = _pack_id_from_descriptor({"capability_pack_id": "winner", "id": "loser"})
        assert result == "winner"

    def test_all_empty_returns_empty(self):
        assert _pack_id_from_descriptor({"capability_pack_id": "", "id": "", "pack_id": ""}) == ""

    def test_whitespace_stripped(self):
        assert _pack_id_from_descriptor({"id": "  mozaikspay  "}) == "mozaikspay"


# ---------------------------------------------------------------------------
# 10. _selected_hosted_pack_ids
# ---------------------------------------------------------------------------

class TestSelectedHostedPackIds:
    def test_none_returns_empty_set(self):
        assert _selected_hosted_pack_ids(None) == set()

    def test_empty_list_returns_empty_set(self):
        assert _selected_hosted_pack_ids([]) == set()

    def test_non_hosted_pack_excluded(self):
        packs = [{"id": "wallet", "capability_source": "oss_pack"}]
        assert _selected_hosted_pack_ids(packs) == set()

    def test_hosted_pack_included(self):
        packs = [{"id": "mozaikspay", "capability_source": "hosted_pack"}]
        assert _selected_hosted_pack_ids(packs) == {"mozaikspay"}

    def test_non_dict_item_skipped(self):
        packs = ["string_item", {"id": "mozaikspay", "capability_source": "hosted_pack"}]
        assert _selected_hosted_pack_ids(packs) == {"mozaikspay"}

    def test_multiple_hosted_packs(self):
        packs = [
            {"id": "mozaikspay", "capability_source": "hosted_pack"},
            {"id": "mozaiksmail", "capability_source": "hosted_pack"},
        ]
        assert _selected_hosted_pack_ids(packs) == {"mozaikspay", "mozaiksmail"}

    def test_empty_pack_id_excluded(self):
        packs = [{"id": "", "capability_source": "hosted_pack"}]
        assert _selected_hosted_pack_ids(packs) == set()


# ---------------------------------------------------------------------------
# 11. _iter_api_endpoint_literals
# ---------------------------------------------------------------------------

class TestIterApiEndpointLiterals:
    def test_no_match_returns_empty(self):
        assert _iter_api_endpoint_literals("no endpoints here") == []

    def test_double_quoted_endpoint_found(self):
        result = _iter_api_endpoint_literals('endpoint: "/api/modules/payments/create"')
        assert "/api/modules/payments/create" in result

    def test_single_quoted_endpoint_found(self):
        result = _iter_api_endpoint_literals("url: '/api/modules/tickets/list'")
        assert "/api/modules/tickets/list" in result

    def test_no_opening_quote_not_found(self):
        result = _iter_api_endpoint_literals("/api/modules/tickets/list")
        assert result == []

    def test_multiple_matches_returned(self):
        content = (
            'url1 = "/api/modules/a/action1"\n'
            'url2 = "/api/modules/b/action2"\n'
        )
        result = _iter_api_endpoint_literals(content)
        assert "/api/modules/a/action1" in result
        assert "/api/modules/b/action2" in result
        assert len(result) == 2

    def test_non_api_modules_path_not_found(self):
        result = _iter_api_endpoint_literals('url = "/api/other/endpoint"')
        assert result == []
