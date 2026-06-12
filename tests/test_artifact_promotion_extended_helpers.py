"""
Pure helper unit tests for:
  mozaiksai/control_plane/artifact_promotion.py

Covers helpers NOT tested in test_artifact_promotion_pure_helpers.py:

  _commit_metadata_document:
    - dict input → returned as dict
    - Mapping input → converted to dict
    - object with model_dump() → called and dict returned
    - non-dict model_dump() result → {}
    - non-Mapping non-model → {}
    - None → {}

  _commit_metadata_payload:
    - no "metadata" key → {}
    - metadata is not a dict → {}
    - metadata is a dict → returned as dict

  _normalized_policy_decisions:
    - None promotion_result, None policy_decisions → []
    - list of Mapping decisions → list of dicts
    - PromotionPolicyDecision instance → model_dump() dict
    - mixed list → both normalized
    - non-Mapping non-model entry → skipped

  _build_acceptance_commit_metadata:
    - accepted_by, accepted_at, request_id in acceptance block
    - review_status in acceptance block
    - notes=None → "notes" key absent
    - notes present → included (without credentials)
    - base commit_metadata dict merged
    - existing metadata preserved and acceptance block added
"""
from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.artifact_promotion import (
    _build_acceptance_commit_metadata,
    _commit_metadata_document,
    _commit_metadata_payload,
    _normalized_policy_decisions,
)
from mozaiksai.control_plane.promotion_policy import PromotionPolicyDecision

# ---------------------------------------------------------------------------
# 1. _commit_metadata_document
# ---------------------------------------------------------------------------

class TestCommitMetadataDocument:
    def test_plain_dict_returned(self):
        doc = {"app_id": "my-app", "version": "1"}
        assert _commit_metadata_document(doc) == {"app_id": "my-app", "version": "1"}

    def test_mapping_subclass_converted(self):
        from collections import OrderedDict
        mapping = OrderedDict([("key", "value")])
        result = _commit_metadata_document(mapping)
        assert result == {"key": "value"}

    def test_object_with_model_dump_called(self):
        class FakeModel:
            def model_dump(self, **kwargs):
                return {"from_model": True}

        result = _commit_metadata_document(FakeModel())
        assert result == {"from_model": True}

    def test_model_dump_non_dict_result_returns_empty(self):
        class FakeModel:
            def model_dump(self, **kwargs):
                return ["not", "a", "dict"]

        result = _commit_metadata_document(FakeModel())
        assert result == {}

    def test_none_returns_empty(self):
        assert _commit_metadata_document(None) == {}

    def test_integer_returns_empty(self):
        assert _commit_metadata_document(42) == {}

    def test_string_returns_empty(self):
        assert _commit_metadata_document("not-a-dict") == {}


# ---------------------------------------------------------------------------
# 2. _commit_metadata_payload
# ---------------------------------------------------------------------------

class TestCommitMetadataPayload:
    def test_no_metadata_key_returns_empty(self):
        assert _commit_metadata_payload({"other_key": "value"}) == {}

    def test_metadata_not_dict_returns_empty(self):
        assert _commit_metadata_payload({"metadata": "not-a-dict"}) == {}

    def test_metadata_dict_returned(self):
        doc = {"metadata": {"app_id": "my-app", "build": "123"}}
        result = _commit_metadata_payload(doc)
        assert result == {"app_id": "my-app", "build": "123"}

    def test_empty_input_returns_empty(self):
        assert _commit_metadata_payload({}) == {}

    def test_none_input_returns_empty(self):
        assert _commit_metadata_payload(None) == {}


# ---------------------------------------------------------------------------
# 3. _normalized_policy_decisions
# ---------------------------------------------------------------------------

class TestNormalizedPolicyDecisions:
    def test_none_both_returns_empty(self):
        assert _normalized_policy_decisions() == []

    def test_empty_policy_decisions_returns_empty(self):
        assert _normalized_policy_decisions(policy_decisions=[]) == []

    def test_mapping_decisions_converted_to_dicts(self):
        decisions = [{"path": "ui/pages/home.yaml", "allowed": True}]
        result = _normalized_policy_decisions(policy_decisions=decisions)
        assert len(result) == 1
        assert result[0]["path"] == "ui/pages/home.yaml"
        assert result[0]["allowed"] is True

    def test_promotion_policy_decision_model_serialized(self):
        decision = PromotionPolicyDecision(
            path="ui/pages/home.yaml",
            allowed=True,
            mode="direct_leaf_patch",
            reason="UI leaf patch",
        )
        result = _normalized_policy_decisions(policy_decisions=[decision])
        assert len(result) == 1
        assert result[0]["path"] == "ui/pages/home.yaml"
        assert result[0]["allowed"] is True

    def test_mixed_list_both_normalized(self):
        decision = PromotionPolicyDecision(
            path="ui/pages/a.yaml",
            allowed=True,
            mode="direct_leaf_patch",
            reason="ok",
        )
        decisions = [decision, {"path": "modules/orders/module.yaml", "allowed": False}]
        result = _normalized_policy_decisions(policy_decisions=decisions)
        assert len(result) == 2

    def test_non_mapping_non_model_skipped(self):
        decisions = ["not-a-dict", 42, None]
        result = _normalized_policy_decisions(policy_decisions=decisions)  # type: ignore
        assert result == []


# ---------------------------------------------------------------------------
# 4. _build_acceptance_commit_metadata
# ---------------------------------------------------------------------------

class TestBuildAcceptanceCommitMetadata:
    def _call(self, **overrides) -> dict[str, Any]:
        base: dict[str, Any] = {
            "commit_metadata": {},
            "accepted_by": "mbari",
            "accepted_at": "2026-06-12T10:00:00Z",
            "request_id": "req-abc123",
            "review_status": "approved",
            "notes": None,
        }
        base.update(overrides)
        return _build_acceptance_commit_metadata(**base)

    def test_accepted_by_in_acceptance_block(self):
        result = self._call()
        assert result["metadata"]["acceptance"]["accepted_by"] == "mbari"

    def test_accepted_at_in_acceptance_block(self):
        result = self._call()
        assert result["metadata"]["acceptance"]["accepted_at"] == "2026-06-12T10:00:00Z"

    def test_request_id_in_acceptance_block(self):
        result = self._call()
        assert result["metadata"]["acceptance"]["request_id"] == "req-abc123"

    def test_review_status_in_acceptance_block(self):
        result = self._call()
        assert result["metadata"]["acceptance"]["refinement_review_status"] == "approved"

    def test_notes_none_not_in_acceptance_block(self):
        result = self._call(notes=None)
        assert "notes" not in result["metadata"]["acceptance"]

    def test_notes_present_included(self):
        result = self._call(notes="Looks good to ship")
        assert result["metadata"]["acceptance"]["notes"] == "Looks good to ship"

    def test_existing_metadata_keys_preserved(self):
        base_meta = {"metadata": {"app_id": "my-app"}}
        result = _build_acceptance_commit_metadata(
            commit_metadata=base_meta,
            accepted_by="user",
            accepted_at="2026-06-12T00:00:00Z",
            request_id="req-1",
            review_status="approved",
        )
        assert result["metadata"]["app_id"] == "my-app"
        assert "acceptance" in result["metadata"]

    def test_none_commit_metadata_still_builds_acceptance(self):
        result = self._call(commit_metadata=None)
        assert "acceptance" in result["metadata"]
