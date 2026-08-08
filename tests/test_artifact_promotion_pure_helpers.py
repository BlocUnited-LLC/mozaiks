"""
Pure helper unit tests for:
  mozaiksai/control_plane/artifact_promotion.py

Covers sync pure helpers (no IO/filesystem):

  _is_relative_to:
    - child inside parent → True
    - child outside parent → False
    - same path → True
    - prefix-match-but-not-child rejected

  _normalize_relative_path:
    - valid relative path → (normalized, None)
    - empty string → (None, error)
    - null byte → (None, error)
    - absolute Unix path → (None, error)
    - path traversal (..) → (None, error)
    - leading ./ stripped
    - backslash normalized to forward slash
    - secret path terms → (path, warning)
    - glob chars → (path, warning)

  _validation_status_from_evidence:
    - failed non-empty → FAILED
    - completed non-empty, no failed → PASSED
    - both empty → PENDING
    - failed takes priority over completed

  _normalized_policy_decisions:
    - None → []
    - plain Mapping list → dict list
    - PromotionPolicyDecision list → serialized
    - empty list → []

  _commit_metadata_document:
    - Pydantic model → model_dump dict
    - plain Mapping → dict
    - None → {}
    - other types → {}

  _build_review_snapshot:
    - all fields extracted from RefinementReviewRecord
    - affected_bundle_paths is a list

  _build_scoped_execution_snapshot:
    - all fields extracted from ScopedRefinementResult
    - changed_files serialized

  _commit_metadata_payload:
    - doc with "metadata" dict → that dict
    - doc without "metadata" → {}
    - None doc → {}
    - metadata non-dict → {}

  _build_acceptance_commit_metadata:
    - acceptance key added to metadata
    - accepted_by/accepted_at/request_id/review_status set
    - notes included when provided
    - notes None → not in acceptance
    - existing metadata keys preserved
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from mozaiksai.control_plane.artifact_promotion import (
    _build_acceptance_commit_metadata,
    _build_review_snapshot,
    _build_scoped_execution_snapshot,
    _commit_metadata_document,
    _commit_metadata_payload,
    _is_relative_to,
    _normalize_relative_path,
    _normalized_policy_decisions,
    _validation_status_from_evidence,
)
from mozaiksai.control_plane.promotion_policy import PromotionPolicyDecision
from mozaiksai.control_plane.review import RefinementReviewRecord
from mozaiksai.control_plane.scoped_execution import (
    ScopedRefinementChangedFile,
    ScopedRefinementResult,
)
from mozaiksai.control_plane.validation_evidence import ValidationEvidence
from mozaiksai.core.artifacts import BuildRecordValidationStatus

# ---------------------------------------------------------------------------
# 1. _is_relative_to
# ---------------------------------------------------------------------------

class TestIsRelativeTo:
    def test_child_inside_parent_true(self):
        parent = Path("/workspace/app")
        child = Path("/workspace/app/modules/wallet")
        assert _is_relative_to(child, parent) is True

    def test_child_outside_parent_false(self):
        parent = Path("/workspace/app")
        child = Path("/workspace/other/file.py")
        assert _is_relative_to(child, parent) is False

    def test_same_path_true(self):
        p = Path("/workspace/app")
        assert _is_relative_to(p, p) is True

    def test_deep_nested_child_true(self):
        parent = Path("/a")
        child = Path("/a/b/c/d/e")
        assert _is_relative_to(child, parent) is True

    def test_sibling_with_shared_prefix_false(self):
        parent = Path("/workspace/app")
        child = Path("/workspace/app2/file.py")
        assert _is_relative_to(child, parent) is False


# ---------------------------------------------------------------------------
# 2. _normalize_relative_path
# ---------------------------------------------------------------------------

class TestNormalizeRelativePath:
    def test_valid_path_normalized(self):
        normalized, error = _normalize_relative_path("app/modules/wallet.py")
        assert error is None
        assert normalized == "app/modules/wallet.py"

    def test_empty_string_error(self):
        normalized, error = _normalize_relative_path("")
        assert normalized is None
        assert error is not None

    def test_null_byte_error(self):
        normalized, error = _normalize_relative_path("app/file\x00.py")
        assert normalized is None
        assert error is not None

    def test_absolute_unix_path_rejected(self):
        normalized, error = _normalize_relative_path("/etc/passwd")
        assert normalized is None
        assert error is not None

    def test_path_traversal_rejected(self):
        normalized, error = _normalize_relative_path("../../etc/passwd")
        assert normalized is None
        assert error is not None

    def test_leading_dot_slash_stripped(self):
        normalized, error = _normalize_relative_path("./app/modules/wallet.py")
        assert error is None
        assert normalized == "app/modules/wallet.py"

    def test_backslash_normalized_to_forward(self):
        normalized, error = _normalize_relative_path("app\\modules\\wallet.py")
        assert error is None
        assert normalized == "app/modules/wallet.py"

    def test_secret_path_term_returns_path_with_warning(self):
        # path containing "secret" → path returned but with warning message
        normalized, error = _normalize_relative_path("config/secrets.yaml")
        assert normalized is not None
        assert error is not None

    def test_glob_char_returns_path_with_warning(self):
        normalized, error = _normalize_relative_path("app/*.py")
        assert normalized is not None
        assert error is not None

    def test_multiple_leading_dot_slashes(self):
        normalized, error = _normalize_relative_path("././app/wallet.py")
        assert error is None
        assert normalized == "app/wallet.py"


# ---------------------------------------------------------------------------
# 3. _validation_status_from_evidence
# ---------------------------------------------------------------------------

class TestValidationStatusFromEvidence:
    def test_failed_non_empty_returns_failed(self):
        evidence = ValidationEvidence(failed=["check_a"])
        assert _validation_status_from_evidence(evidence) == BuildRecordValidationStatus.FAILED

    def test_completed_no_failed_returns_passed(self):
        evidence = ValidationEvidence(completed=["check_a"], failed=[])
        assert _validation_status_from_evidence(evidence) == BuildRecordValidationStatus.PASSED

    def test_both_empty_returns_pending(self):
        evidence = ValidationEvidence()
        assert _validation_status_from_evidence(evidence) == BuildRecordValidationStatus.PENDING

    def test_failed_takes_priority_over_completed(self):
        evidence = ValidationEvidence(completed=["check_a"], failed=["check_b"])
        assert _validation_status_from_evidence(evidence) == BuildRecordValidationStatus.FAILED


# ---------------------------------------------------------------------------
# 4. _normalized_policy_decisions
# ---------------------------------------------------------------------------

class TestNormalizedPolicyDecisions:
    def test_no_args_returns_empty(self):
        assert _normalized_policy_decisions() == []

    def test_empty_list_returns_empty(self):
        assert _normalized_policy_decisions(policy_decisions=[]) == []

    def test_plain_mapping_converted_to_dict(self):
        decisions = [{"path": "app/wallet.py", "allowed": True, "mode": "copy", "reason": "ok"}]
        result = _normalized_policy_decisions(policy_decisions=decisions)
        assert len(result) == 1
        assert result[0]["path"] == "app/wallet.py"

    def test_promotion_policy_decision_serialized(self):
        decision = PromotionPolicyDecision(
            path="app/wallet.py",
            allowed=True,
            mode="direct_leaf_patch",
            reason="Allowed by policy",
        )
        result = _normalized_policy_decisions(policy_decisions=[decision])
        assert len(result) == 1
        assert result[0]["path"] == "app/wallet.py"
        assert result[0]["allowed"] is True


# ---------------------------------------------------------------------------
# 5. _commit_metadata_document
# ---------------------------------------------------------------------------

class TestCommitMetadataDocument:
    def test_pydantic_model_serialized(self):
        class MyModel(BaseModel):
            key: str = "value"

        result = _commit_metadata_document(MyModel())
        assert isinstance(result, dict)
        assert result["key"] == "value"

    def test_plain_mapping_converted(self):
        result = _commit_metadata_document({"key": "value"})
        assert result["key"] == "value"

    def test_none_returns_empty(self):
        assert _commit_metadata_document(None) == {}

    def test_string_returns_empty(self):
        assert _commit_metadata_document("not-a-mapping") == {}

    def test_int_returns_empty(self):
        assert _commit_metadata_document(42) == {}


# ---------------------------------------------------------------------------
# 6. _build_review_snapshot
# ---------------------------------------------------------------------------

class TestBuildReviewSnapshot:
    def test_all_fields_extracted(self):
        record = RefinementReviewRecord(
            request_id="req-test",
            status="approved",
            reviewer="alice",
            decision="approved",
            notes=None,
            promotion_allowed=True,
            source_bundle_path="generated/apps/myapp/bundle.zip",
            staging_area="staging/abc",
            affected_bundle_paths=["app/wallet.py"],
        )
        snapshot = _build_review_snapshot(record)
        assert snapshot["status"] == "approved"
        assert snapshot["reviewer"] == "alice"
        assert snapshot["promotion_allowed"] is True
        assert snapshot["source_bundle_path"] == "generated/apps/myapp/bundle.zip"
        assert snapshot["staging_area"] == "staging/abc"
        assert "app/wallet.py" in snapshot["affected_bundle_paths"]
        assert snapshot["write_back_mode"] == "generated_artifact"
        assert snapshot["write_back_target"] is None

    def test_affected_bundle_paths_is_list(self):
        record = RefinementReviewRecord(request_id="req-test", staging_area="staging/abc")
        snapshot = _build_review_snapshot(record)
        assert isinstance(snapshot["affected_bundle_paths"], list)
        assert snapshot["write_back_mode"] == "generated_artifact"


# ---------------------------------------------------------------------------
# 7. _build_scoped_execution_snapshot
# ---------------------------------------------------------------------------

class TestBuildScopedExecutionSnapshot:
    def test_all_fields_extracted(self):
        result = ScopedRefinementResult(request_id="req-test", staging_area="staging/abc", result_path="staging/abc/execution_result.json")
        snapshot = _build_scoped_execution_snapshot(result)
        assert snapshot["execution_mode"] == "scoped_staging"
        assert snapshot["mutation_scope"] == "staging_only"
        assert snapshot["source_mutated"] is False
        assert snapshot["mutation_allowed"] is False
        assert snapshot["result_path"] == "staging/abc/execution_result.json"
        assert isinstance(snapshot["changed_files"], list)

    def test_changed_files_serialized(self):
        changed = ScopedRefinementChangedFile(path="app/wallet.py", status="updated", reason="modified")
        result = ScopedRefinementResult(
            request_id="req-test",
            staging_area="staging/abc",
            result_path="staging/abc/execution_result.json",
            changed_files=[changed],
        )
        snapshot = _build_scoped_execution_snapshot(result)
        assert len(snapshot["changed_files"]) == 1
        assert snapshot["changed_files"][0]["path"] == "app/wallet.py"


# ---------------------------------------------------------------------------
# 8. _commit_metadata_payload
# ---------------------------------------------------------------------------

class TestCommitMetadataPayload:
    def test_metadata_dict_returned(self):
        doc = {"metadata": {"key": "val", "version": "1.0"}}
        result = _commit_metadata_payload(doc)
        assert result == {"key": "val", "version": "1.0"}

    def test_no_metadata_key_returns_empty(self):
        result = _commit_metadata_payload({"other": "stuff"})
        assert result == {}

    def test_none_returns_empty(self):
        assert _commit_metadata_payload(None) == {}

    def test_metadata_non_dict_returns_empty(self):
        result = _commit_metadata_payload({"metadata": "not-a-dict"})
        assert result == {}


# ---------------------------------------------------------------------------
# 9. _build_acceptance_commit_metadata
# ---------------------------------------------------------------------------

class TestBuildAcceptanceCommitMetadata:
    def test_acceptance_key_added(self):
        result = _build_acceptance_commit_metadata(
            commit_metadata={},
            accepted_by="alice",
            accepted_at="2024-06-01T00:00:00Z",
            request_id="req-123",
            review_status="approved",
        )
        assert "acceptance" in result.get("metadata", {})
        acc = result["metadata"]["acceptance"]
        assert acc["accepted_by"] == "alice"
        assert acc["accepted_at"] == "2024-06-01T00:00:00Z"
        assert acc["request_id"] == "req-123"
        assert acc["refinement_review_status"] == "approved"

    def test_notes_included_when_provided(self):
        result = _build_acceptance_commit_metadata(
            commit_metadata={},
            accepted_by="bob",
            accepted_at="2024-06-01T00:00:00Z",
            request_id="req-456",
            review_status="approved",
            notes="Looks good",
        )
        assert "notes" in result["metadata"]["acceptance"]

    def test_notes_none_not_in_acceptance(self):
        result = _build_acceptance_commit_metadata(
            commit_metadata={},
            accepted_by="bob",
            accepted_at="2024-06-01T00:00:00Z",
            request_id="req-456",
            review_status="approved",
            notes=None,
        )
        assert "notes" not in result["metadata"]["acceptance"]

    def test_existing_metadata_preserved(self):
        doc = {"metadata": {"existing_key": "existing_val"}}
        result = _build_acceptance_commit_metadata(
            commit_metadata=doc,
            accepted_by="alice",
            accepted_at="2024-06-01T00:00:00Z",
            request_id="req-789",
            review_status="approved",
        )
        assert result["metadata"]["existing_key"] == "existing_val"
        assert "acceptance" in result["metadata"]

