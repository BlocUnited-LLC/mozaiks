"""
Persistence manager pure helper unit tests.

Covers:
  _has_index_with_keys:
    - empty indexes → False
    - index with non-dict key_spec → skipped
    - index matching expected keys → True
    - index with different keys → False
    - multiple indexes, one matches → True
    - order matters (list comparison)

  extract_workflow_ui_state:
    - non-dict chat_doc → default state
    - chat_doc with no workflow_ui_state → default state
    - workflow_ui_state not dict → default state
    - known field overridden → reflected in output
    - unknown field not in WorkflowUIState → not included
    - tool_calls dict with valid entries → copied
    - tool_calls dict with invalid entries → filtered
    - None value sets field to None

  extract_last_artifact:
    - no artifact in doc → None
    - last_artifact is not dict → None
    - last_artifact dict returned

  extract_pending_input_request:
    - no pending in doc → None
    - pending_input_request not dict → None
    - pending_input_request dict returned
"""
from __future__ import annotations

from mozaiksai.core.data.persistence.persistence_manager import (
    _has_index_with_keys,
    extract_last_artifact,
    extract_pending_input_request,
    extract_workflow_ui_state,
)

# ---------------------------------------------------------------------------
# 1. _has_index_with_keys
# ---------------------------------------------------------------------------

class TestHasIndexWithKeys:
    def test_empty_indexes_returns_false(self):
        assert _has_index_with_keys([], [("app_id", 1)]) is False

    def test_matching_single_key(self):
        indexes = [{"key": {"app_id": 1}}]
        assert _has_index_with_keys(indexes, [("app_id", 1)]) is True

    def test_matching_compound_key(self):
        indexes = [{"key": {"app_id": 1, "session_id": 1}}]
        assert _has_index_with_keys(indexes, [("app_id", 1), ("session_id", 1)]) is True

    def test_different_key_returns_false(self):
        indexes = [{"key": {"user_id": 1}}]
        assert _has_index_with_keys(indexes, [("app_id", 1)]) is False

    def test_multiple_indexes_one_matches(self):
        indexes = [
            {"key": {"user_id": 1}},
            {"key": {"app_id": 1}},
        ]
        assert _has_index_with_keys(indexes, [("app_id", 1)]) is True

    def test_key_order_matters(self):
        # ("session_id", "app_id") ≠ ("app_id", "session_id")
        indexes = [{"key": {"app_id": 1, "session_id": 1}}]
        assert _has_index_with_keys(indexes, [("session_id", 1), ("app_id", 1)]) is False

    def test_index_without_key_field_skipped(self):
        indexes = [{"name": "no_key_field"}]
        assert _has_index_with_keys(indexes, [("app_id", 1)]) is False

    def test_key_spec_not_dict_like_skipped(self):
        # key_spec must have .items(); a plain list does not
        indexes = [{"key": [("app_id", 1)]}]
        # list has no .items() → skipped → False
        assert _has_index_with_keys(indexes, [("app_id", 1)]) is False

    def test_different_direction_returns_false(self):
        indexes = [{"key": {"app_id": -1}}]
        assert _has_index_with_keys(indexes, [("app_id", 1)]) is False


# ---------------------------------------------------------------------------
# 2. extract_workflow_ui_state
# ---------------------------------------------------------------------------

class TestExtractWorkflowUiState:
    def test_non_dict_returns_default_state(self):
        result = extract_workflow_ui_state(None)
        assert isinstance(result, dict)

    def test_string_input_returns_default_state(self):
        result = extract_workflow_ui_state("not a doc")
        assert isinstance(result, dict)

    def test_empty_doc_returns_default_state(self):
        result = extract_workflow_ui_state({})
        assert isinstance(result, dict)

    def test_workflow_ui_state_not_dict_returns_default(self):
        result = extract_workflow_ui_state({"workflow_ui_state": "bad"})
        assert isinstance(result, dict)

    def test_valid_field_overridden(self):
        # schema_version is a known scalar field in WorkflowUIState
        doc = {"workflow_ui_state": {"schema_version": 2}}
        result = extract_workflow_ui_state(doc)
        assert result["schema_version"] == 2

    def test_unknown_field_not_included(self):
        doc = {"workflow_ui_state": {"completely_unknown_field_xyz": "value"}}
        result = extract_workflow_ui_state(doc)
        assert "completely_unknown_field_xyz" not in result

    def test_none_value_sets_field_to_none(self):
        doc = {"workflow_ui_state": {"last_artifact": None}}
        result = extract_workflow_ui_state(doc)
        assert result["last_artifact"] is None

    def test_tool_calls_valid_dict_entries_copied(self):
        doc = {"workflow_ui_state": {"tool_calls": {"tool_a": {"result": "ok"}}}}
        result = extract_workflow_ui_state(doc)
        assert result["tool_calls"] == {"tool_a": {"result": "ok"}}

    def test_tool_calls_invalid_values_filtered(self):
        # Only entries where value is a dict are kept
        doc = {"workflow_ui_state": {"tool_calls": {"tool_a": "not-a-dict", "tool_b": {"ok": True}}}}
        result = extract_workflow_ui_state(doc)
        assert "tool_a" not in result["tool_calls"]
        assert result["tool_calls"].get("tool_b") == {"ok": True}

    def test_last_artifact_dict_field_copied(self):
        artifact = {"component": "MyComp", "payload": {"data": 1}}
        doc = {"workflow_ui_state": {"last_artifact": artifact}}
        result = extract_workflow_ui_state(doc)
        assert result["last_artifact"] == artifact

    def test_returns_deepcopy_not_reference(self):
        artifact = {"key": "value"}
        doc = {"workflow_ui_state": {"last_artifact": artifact}}
        result = extract_workflow_ui_state(doc)
        result["last_artifact"]["key"] = "mutated"
        # Original artifact should be unchanged
        assert artifact["key"] == "value"


# ---------------------------------------------------------------------------
# 3. extract_last_artifact
# ---------------------------------------------------------------------------

class TestExtractLastArtifact:
    def test_empty_doc_returns_none(self):
        assert extract_last_artifact({}) is None

    def test_none_doc_returns_none(self):
        assert extract_last_artifact(None) is None

    def test_last_artifact_not_dict_returns_none(self):
        doc = {"workflow_ui_state": {"last_artifact": "not-a-dict"}}
        assert extract_last_artifact(doc) is None

    def test_last_artifact_dict_returned(self):
        artifact = {"component": "ArtifactPanel", "payload": {}}
        doc = {"workflow_ui_state": {"last_artifact": artifact}}
        result = extract_last_artifact(doc)
        assert result == artifact

    def test_none_artifact_returns_none(self):
        doc = {"workflow_ui_state": {"last_artifact": None}}
        assert extract_last_artifact(doc) is None


# ---------------------------------------------------------------------------
# 4. extract_pending_input_request
# ---------------------------------------------------------------------------

class TestExtractPendingInputRequest:
    def test_empty_doc_returns_none(self):
        assert extract_pending_input_request({}) is None

    def test_none_doc_returns_none(self):
        assert extract_pending_input_request(None) is None

    def test_pending_not_dict_returns_none(self):
        doc = {"workflow_ui_state": {"pending_input_request": "bad"}}
        assert extract_pending_input_request(doc) is None

    def test_pending_dict_returned(self):
        pending = {"prompt": "Enter your name", "type": "text"}
        doc = {"workflow_ui_state": {"pending_input_request": pending}}
        result = extract_pending_input_request(doc)
        assert result == pending

    def test_none_pending_returns_none(self):
        doc = {"workflow_ui_state": {"pending_input_request": None}}
        assert extract_pending_input_request(doc) is None
