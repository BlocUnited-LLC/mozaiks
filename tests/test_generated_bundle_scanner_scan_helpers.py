"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/generated_bundle_scanner.py

Covers helpers NOT already tested in test_generated_bundle_scanner_helpers.py:

  _scan_canonical_app_paths:
    - empty files_map → []
    - canonical paths only → []
    - disallowed path (config/data.json) → error returned
    - noncanonical config path → error returned
    - noncanonical root path → error returned

  _scan_security_secret_contract:
    - no secrets.yaml → []
    - valid YAML no raw fields → []
    - invalid YAML → error returned
    - raw api_key field with value → error returned
    - raw field with empty value → not flagged
    - nested raw secret field → error returned

  _scan_data_contract_module_alignment:
    - no data contract → []
    - invalid JSON data contract → error returned
    - valid contract no module surfaces → []
    - module surface without matching module.yaml → error returned
    - module surface with matching module.yaml → []
    - mismatched declared module id vs folder id → error returned
"""
from __future__ import annotations

import json

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_canonical_app_paths,
    _scan_data_contract_module_alignment,
    _scan_security_secret_contract,
)

# ---------------------------------------------------------------------------
# 1. _scan_canonical_app_paths
# ---------------------------------------------------------------------------

class TestScanCanonicalAppPaths:
    def test_empty_files_map_returns_empty(self):
        assert _scan_canonical_app_paths({}) == []

    def test_canonical_paths_only_returns_empty(self):
        files_map = {
            "modules/orders/module.yaml": "id: orders",
            "config/ai.json": "{}",
        }
        assert _scan_canonical_app_paths(files_map) == []

    def test_disallowed_path_returns_error(self):
        # config/data.json is a removed/disallowed app path
        files_map = {"config/data.json": "{}"}
        errors = _scan_canonical_app_paths(files_map)
        assert len(errors) >= 1
        assert any("removed app paths" in e or "removed" in e.lower() for e in errors)

    def test_noncanonical_config_path_returns_error(self):
        # config/secret.json is noncanonical
        files_map = {"config/secret.json": "{}"}
        errors = _scan_canonical_app_paths(files_map)
        assert any("noncanonical" in e for e in errors)

    def test_noncanonical_root_path_returns_error(self):
        # notaplane/file.py has no canonical root plane
        files_map = {"notaplane/file.py": "x"}
        errors = _scan_canonical_app_paths(files_map)
        assert any("outside the canonical app planes" in e for e in errors)

    def test_multiple_issues_can_appear(self):
        files_map = {
            "config/data.json": "{}",
            "notaplane/file.py": "x",
        }
        errors = _scan_canonical_app_paths(files_map)
        # May be 1 or 2 errors depending on how issues are combined
        assert len(errors) >= 1


# ---------------------------------------------------------------------------
# 2. _scan_security_secret_contract
# ---------------------------------------------------------------------------

class TestScanSecuritySecretContract:
    def test_no_secrets_file_returns_empty(self):
        assert _scan_security_secret_contract({"modules/orders/module.yaml": "id: orders"}) == []

    def test_empty_secrets_file_returns_empty(self):
        assert _scan_security_secret_contract({"security/secrets.yaml": ""}) == []

    def test_valid_names_only_returns_empty(self):
        content = "secrets:\n  - name: STRIPE_KEY\n    backend: env\n"
        assert _scan_security_secret_contract({"security/secrets.yaml": content}) == []

    def test_invalid_yaml_returns_error(self):
        content = "secrets: [invalid: yaml: ::::"
        errors = _scan_security_secret_contract({"security/secrets.yaml": content})
        assert len(errors) == 1
        assert "valid YAML" in errors[0]

    def test_raw_api_key_with_value_returns_error(self):
        content = "api_key: sk_live_abc123\n"
        errors = _scan_security_secret_contract({"security/secrets.yaml": content})
        assert len(errors) == 1
        assert "raw credential" in errors[0] or "names-only" in errors[0]

    def test_raw_field_with_empty_value_not_flagged(self):
        content = "api_key: \"\"\n"
        errors = _scan_security_secret_contract({"security/secrets.yaml": content})
        assert errors == []

    def test_nested_raw_secret_field_returns_error(self):
        content = "config:\n  password: supersecret\n"
        errors = _scan_security_secret_contract({"security/secrets.yaml": content})
        assert len(errors) == 1
        assert "config.password" in errors[0]

    def test_raw_password_with_value_flagged(self):
        content = "secrets:\n  - name: db\n    password: hunter2\n"
        errors = _scan_security_secret_contract({"security/secrets.yaml": content})
        assert len(errors) == 1

    def test_secrets_yaml_path_case_insensitive(self):
        # Files map uses normalized paths; security/secrets.yaml is canonical
        content = "name: STRIPE_KEY\n"
        result = _scan_security_secret_contract({"security/secrets.yaml": content})
        assert result == []


# ---------------------------------------------------------------------------
# 3. _scan_data_contract_module_alignment
# ---------------------------------------------------------------------------

class TestScanDataContractModuleAlignment:
    def test_no_data_contract_returns_empty(self):
        files_map = {"modules/orders/module.yaml": "id: orders"}
        assert _scan_data_contract_module_alignment(files_map) == []

    def test_invalid_json_returns_error(self):
        files_map = {"data/contract.json": "not-json{{}"}
        errors = _scan_data_contract_module_alignment(files_map)
        assert len(errors) >= 1

    def test_valid_contract_no_surfaces_returns_empty(self):
        contract = {"schema_version": "1", "collections": []}
        files_map = {"data/contract.json": json.dumps(contract)}
        assert _scan_data_contract_module_alignment(files_map) == []

    def test_module_surface_without_module_yaml_returns_error(self):
        contract = {
            "surfaces": [
                {"surface_kind": "module", "surface_id": "orders"}
            ]
        }
        files_map = {"data/contract.json": json.dumps(contract)}
        errors = _scan_data_contract_module_alignment(files_map)
        assert len(errors) >= 1
        assert "orders" in errors[0]

    def test_module_surface_with_matching_module_yaml_returns_empty(self):
        contract = {
            "surfaces": [
                {"surface_kind": "module", "surface_id": "orders"}
            ]
        }
        files_map = {
            "data/contract.json": json.dumps(contract),
            "modules/orders/module.yaml": "id: orders\nactions: []\n",
        }
        errors = _scan_data_contract_module_alignment(files_map)
        assert errors == []

    def test_mismatched_module_id_in_yaml_returns_error(self):
        contract = {
            "surfaces": [
                {"surface_kind": "module", "surface_id": "orders"}
            ]
        }
        # module.yaml declares id as "wrong_id" instead of "orders"
        files_map = {
            "data/contract.json": json.dumps(contract),
            "modules/orders/module.yaml": "id: wrong_id\nactions: []\n",
        }
        errors = _scan_data_contract_module_alignment(files_map)
        assert len(errors) >= 1
        assert "wrong_id" in errors[0] or "orders" in errors[0]

    def test_empty_files_map_returns_empty(self):
        assert _scan_data_contract_module_alignment({}) == []
