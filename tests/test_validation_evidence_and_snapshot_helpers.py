"""
Pure helper unit tests for:
  mozaiksai/control_plane/validation_evidence.py
  mozaiksai/control_plane/workspace_snapshot.py (pure helpers only)

Covers:

  _normalize_name (validation_evidence):
    - lowercases
    - strips whitespace
    - None → ""
    - empty string → ""

  _normalize_artifact_name (validation_evidence):
    - lowercases
    - strips whitespace
    - backslashes → forward slashes
    - None → ""

  ValidationEvidence.completed_names():
    - returns normalized set
    - empty/whitespace entries excluded

  ValidationEvidence.failed_names():
    - returns normalized set

  ValidationEvidence.artifact_names():
    - backslashes normalized in artifact names

  normalize_validation_evidence:
    - None → empty ValidationEvidence
    - list of strings → ValidationEvidence with completed
    - dict → ValidationEvidence via model_validate
    - ValidationEvidence passthrough
    - other type → TypeError

  _safe_segment (workspace_snapshot):
    - alphanumeric/dot/underscore/hyphen preserved
    - special chars replaced with hyphen
    - leading/trailing ".-" stripped
    - empty after strip → "app"

  _context_version_id (workspace_snapshot):
    - starts with "ctx_"
    - contains normalized app_id and artifact_version_id
    - special chars replaced
    - lowercased
    - max 80 chars after "ctx_" prefix

  _file_map_checksum (workspace_snapshot):
    - returns "sha256:" prefixed string
    - deterministic for same input
    - different file maps → different checksums
    - key order doesn't affect result (sorted internally)

  _ownership_boundaries (workspace_snapshot):
    - each boundary has path ending with "/"
    - top-level dirs extracted correctly
    - deduplicates roots
    - caps at 24 boundaries
"""
from __future__ import annotations

import pytest

from mozaiksai.control_plane.validation_evidence import (
    ValidationEvidence,
    _normalize_artifact_name,
    _normalize_name,
    normalize_validation_evidence,
)
from mozaiksai.control_plane.workspace_snapshot import (
    _context_version_id,
    _file_map_checksum,
    _ownership_boundaries,
    _safe_segment,
)

# ---------------------------------------------------------------------------
# 1. _normalize_name
# ---------------------------------------------------------------------------

class TestNormalizeName:
    def test_lowercase(self):
        assert _normalize_name("ROUTE_COMPONENT") == "route_component"

    def test_strips_whitespace(self):
        assert _normalize_name("  route_component  ") == "route_component"

    def test_none_returns_empty(self):
        assert _normalize_name(None) == ""  # type: ignore[arg-type]

    def test_empty_string_returns_empty(self):
        assert _normalize_name("") == ""

    def test_preserves_underscores(self):
        assert _normalize_name("route_component_validation") == "route_component_validation"


# ---------------------------------------------------------------------------
# 2. _normalize_artifact_name
# ---------------------------------------------------------------------------

class TestNormalizeArtifactName:
    def test_lowercase(self):
        assert _normalize_artifact_name("DATA/Contract.JSON") == "data/contract.json"

    def test_strips_whitespace(self):
        assert _normalize_artifact_name("  data/contract.json  ") == "data/contract.json"

    def test_backslashes_converted(self):
        assert _normalize_artifact_name("data\\contract.json") == "data/contract.json"

    def test_none_returns_empty(self):
        assert _normalize_artifact_name(None) == ""  # type: ignore[arg-type]

    def test_empty_returns_empty(self):
        assert _normalize_artifact_name("") == ""


# ---------------------------------------------------------------------------
# 3. ValidationEvidence methods
# ---------------------------------------------------------------------------

class TestValidationEvidenceMethods:
    def test_completed_names_normalized(self):
        ev = ValidationEvidence(completed=["Route_Component_Validation", "  APP_BUNDLE  "])
        names = ev.completed_names()
        assert "route_component_validation" in names
        assert "app_bundle" in names

    def test_completed_names_excludes_empty(self):
        ev = ValidationEvidence(completed=["valid", "", "  "])
        names = ev.completed_names()
        assert "" not in names
        assert len(names) == 1

    def test_failed_names_normalized(self):
        ev = ValidationEvidence(failed=["DATA_CONTRACT"])
        names = ev.failed_names()
        assert "data_contract" in names

    def test_artifact_names_backslashes_normalized(self):
        ev = ValidationEvidence(artifacts=["data\\contract.json"])
        names = ev.artifact_names()
        assert "data/contract.json" in names

    def test_artifact_names_empty_excluded(self):
        ev = ValidationEvidence(artifacts=["valid.json", ""])
        names = ev.artifact_names()
        assert "" not in names

    def test_all_fields_empty_by_default(self):
        ev = ValidationEvidence()
        assert ev.completed_names() == set()
        assert ev.failed_names() == set()
        assert ev.artifact_names() == set()


# ---------------------------------------------------------------------------
# 4. normalize_validation_evidence
# ---------------------------------------------------------------------------

class TestNormalizeValidationEvidence:
    def test_none_returns_empty_evidence(self):
        ev = normalize_validation_evidence(None)
        assert isinstance(ev, ValidationEvidence)
        assert ev.completed == []
        assert ev.failed == []

    def test_list_creates_completed(self):
        ev = normalize_validation_evidence(["route_validation", "app_bundle"])
        assert "route_validation" in ev.completed
        assert "app_bundle" in ev.completed

    def test_dict_creates_evidence(self):
        ev = normalize_validation_evidence({"completed": ["a", "b"], "failed": ["c"]})
        assert "a" in ev.completed
        assert "c" in ev.failed

    def test_evidence_passthrough(self):
        original = ValidationEvidence(completed=["route_validation"])
        result = normalize_validation_evidence(original)
        assert result is original

    def test_other_type_raises_type_error(self):
        with pytest.raises(TypeError):
            normalize_validation_evidence(42)  # type: ignore[arg-type]

    def test_empty_list_creates_empty_evidence(self):
        ev = normalize_validation_evidence([])
        assert ev.completed == []


# ---------------------------------------------------------------------------
# 5. _safe_segment (workspace_snapshot)
# ---------------------------------------------------------------------------

class TestSafeSegment:
    def test_alphanumeric_preserved(self):
        assert _safe_segment("myapp123") == "myapp123"

    def test_dots_preserved(self):
        assert _safe_segment("my.app") == "my.app"

    def test_underscores_preserved(self):
        assert _safe_segment("my_app") == "my_app"

    def test_hyphens_preserved(self):
        assert _safe_segment("my-app") == "my-app"

    def test_special_chars_replaced_with_hyphen(self):
        result = _safe_segment("my app/name!")
        assert " " not in result
        assert "/" not in result
        assert "!" not in result

    def test_empty_after_strip_returns_app(self):
        # "." → stripped → "" → "app"
        assert _safe_segment(".") == "app"

    def test_empty_string_returns_app(self):
        assert _safe_segment("") == "app"

    def test_leading_trailing_dot_hyphen_stripped(self):
        result = _safe_segment("-myapp-")
        assert not result.startswith("-")


# ---------------------------------------------------------------------------
# 6. _context_version_id (workspace_snapshot)
# ---------------------------------------------------------------------------

class TestContextVersionId:
    def test_starts_with_ctx_prefix(self):
        result = _context_version_id("my-app", "v-001")
        assert result.startswith("ctx_")

    def test_contains_app_id(self):
        result = _context_version_id("my-app", "v-001")
        assert "my" in result  # normalized form

    def test_lowercased(self):
        result = _context_version_id("MyApp", "V001")
        assert result == result.lower()

    def test_special_chars_replaced(self):
        result = _context_version_id("my-app", "build/001")
        assert "/" not in result

    def test_deterministic(self):
        r1 = _context_version_id("app", "v1")
        r2 = _context_version_id("app", "v1")
        assert r1 == r2

    def test_different_inputs_different_outputs(self):
        r1 = _context_version_id("app1", "v1")
        r2 = _context_version_id("app2", "v1")
        assert r1 != r2

    def test_max_total_length(self):
        long_id = "a" * 100
        result = _context_version_id(long_id, long_id)
        # "ctx_" prefix + max 80 chars
        assert len(result) <= len("ctx_") + 80


# ---------------------------------------------------------------------------
# 7. _file_map_checksum (workspace_snapshot)
# ---------------------------------------------------------------------------

class TestFileMapChecksum:
    def test_starts_with_sha256(self):
        result = _file_map_checksum({"a.py": "content"})
        assert result.startswith("sha256:")

    def test_deterministic(self):
        file_map = {"a.py": "content", "b.py": "other"}
        r1 = _file_map_checksum(file_map)
        r2 = _file_map_checksum(file_map)
        assert r1 == r2

    def test_key_order_does_not_affect_result(self):
        r1 = _file_map_checksum({"a.py": "x", "b.py": "y"})
        r2 = _file_map_checksum({"b.py": "y", "a.py": "x"})
        assert r1 == r2

    def test_different_content_different_checksum(self):
        r1 = _file_map_checksum({"a.py": "content1"})
        r2 = _file_map_checksum({"a.py": "content2"})
        assert r1 != r2

    def test_empty_file_map_stable(self):
        result = _file_map_checksum({})
        assert result.startswith("sha256:")


# ---------------------------------------------------------------------------
# 8. _ownership_boundaries (workspace_snapshot)
# ---------------------------------------------------------------------------

class TestOwnershipBoundaries:
    def test_paths_end_with_slash(self):
        file_map = {"app/module.py": "", "ui/page.yaml": ""}
        boundaries = _ownership_boundaries(file_map, "ref-001")
        for b in boundaries:
            assert b.path_or_artifact.endswith("/")

    def test_top_level_dirs_extracted(self):
        file_map = {"app/module.py": "", "modules/billing/handler.py": ""}
        boundaries = _ownership_boundaries(file_map, "ref-001")
        paths = {b.path_or_artifact for b in boundaries}
        assert "app/" in paths
        assert "modules/" in paths

    def test_deduplicates_roots(self):
        file_map = {
            "app/module1.py": "",
            "app/module2.py": "",
        }
        boundaries = _ownership_boundaries(file_map, "ref-001")
        assert len(boundaries) == 1

    def test_caps_at_24_boundaries(self):
        # Create more than 24 top-level directories
        file_map = {f"dir_{i}/file.py": "" for i in range(30)}
        boundaries = _ownership_boundaries(file_map, "ref-001")
        assert len(boundaries) <= 24

    def test_source_ref_preserved(self):
        file_map = {"app/mod.py": ""}
        boundaries = _ownership_boundaries(file_map, "my-ref-id")
        assert all(b.source_ref == "my-ref-id" for b in boundaries)

    def test_empty_file_map_returns_empty(self):
        boundaries = _ownership_boundaries({}, "ref-001")
        assert boundaries == []
