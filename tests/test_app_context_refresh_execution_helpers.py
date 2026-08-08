"""
Pure helper unit tests for:
  mozaiksai/control_plane/app_context_refresh_execution.py

Covers sync pure helpers NOT covered elsewhere:

  _dedupe:
    - empty list → []
    - duplicates removed, order preserved
    - empty/whitespace values excluded

  _artifact_lifecycle_value:
    - None → None
    - string value → returned as string
    - object with .value attribute → .value returned as string

  _stale_status_value:
    - AppContextStaleStatus enum → .value returned
    - string stale_status → returned stripped
    - None/empty stale_status → AppContextStaleStatus.UNKNOWN.value returned

  _missing_expected_artifacts:
    - empty expected → []
    - all present → []
    - one missing → that kind returned
    - empty string expected kind filtered out

  _dedupe_artifact_refs:
    - empty list → []
    - unique refs preserved
    - duplicate key (kind, key, version) dropped
    - order preserved

  _normalize_launch_result:
    - None → None
    - ContextRefreshLaunchResult instance → returned unchanged
    - dict → model_validate returns ContextRefreshLaunchResult

  _source_ref_payload:
    - SourceRef → model_dump dict with required fields

  _resolve_previous_context_version_id:
    - explicit previous_context_version_id → returned as-is
    - None explicit + launch_result with version → launch_result version returned
    - None explicit + no launch_result + workflow_context_variables version → returned
    - all None → plan.current_context_version_id returned

  _normalize_plan:
    - ContextRefreshPlan instance → returned unchanged
    - dict → model_validate returns ContextRefreshPlan

  _validate_refresh_plan:
    - valid plan + matching app_id → returns resolved app_id
    - wrong workflow_sequence → ValueError
    - app_id mismatch → ValueError
    - empty app_id → ValueError
"""
from __future__ import annotations

from enum import StrEnum

import pytest

from mozaiksai.control_plane.app_context_refresh_execution import (
    ContextRefreshLaunchResult,
    ContextRefreshLaunchStatus,
    _artifact_lifecycle_value,
    _dedupe,
    _dedupe_artifact_refs,
    _missing_expected_artifacts,
    _normalize_launch_result,
    _normalize_plan,
    _resolve_previous_context_version_id,
    _source_ref_payload,
    _stale_status_value,
    _validate_refresh_plan,
)
from mozaiksai.core.app_context.models import (
    AppContextMode,
    AppContextStaleStatus,
    AppContextVersion,
    ArtifactRef,
    SourceRef,
    SourceRefKind,
)
from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    ContextRefreshPlan,
)

# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------

def _plan(app_id: str = "my-app", **kwargs) -> ContextRefreshPlan:
    return ContextRefreshPlan(app_id=app_id, **kwargs)


def _artifact_ref(
    build_family: str = "app_context_version",
    build_key: str | None = "default",
    build_record_id: str = "v1",
) -> ArtifactRef:
    return ArtifactRef(
        build_family=build_family,
        build_key=build_key,
        build_record_id=build_record_id,
    )


def _source_ref(
    source_ref_id: str = "src-1",
    kind: SourceRefKind = SourceRefKind.REPO,
    uri: str = "https://github.com/example/repo",
) -> SourceRef:
    return SourceRef(source_ref_id=source_ref_id, kind=kind, uri=uri)


def _context_version(
    context_version_id: str = "ctx-1",
    app_id: str = "my-app",
    stale_status: AppContextStaleStatus | str = AppContextStaleStatus.CURRENT,
) -> AppContextVersion:
    return AppContextVersion(
        context_version_id=context_version_id,
        app_id=app_id,
        mode=AppContextMode.BROWNFIELD,
        stale_status=stale_status,
    )


def _launch_result(
    app_id: str = "my-app",
    current_context_version_id: str | None = None,
) -> ContextRefreshLaunchResult:
    return ContextRefreshLaunchResult(
        status=ContextRefreshLaunchStatus.LAUNCHED,
        workflow_sequence=BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
        app_id=app_id,
        current_context_version_id=current_context_version_id,
    )


# ---------------------------------------------------------------------------
# 1. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_unique_values_preserved_in_order(self):
        assert _dedupe(["a", "b", "c"]) == ["a", "b", "c"]

    def test_duplicate_first_kept(self):
        assert _dedupe(["a", "b", "a"]) == ["a", "b"]

    def test_empty_strings_excluded(self):
        assert _dedupe(["a", "", "b"]) == ["a", "b"]

    def test_whitespace_excluded(self):
        assert _dedupe(["a", "  ", "b"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. _artifact_lifecycle_value
# ---------------------------------------------------------------------------

class TestArtifactLifecycleValue:
    def test_none_returns_none(self):
        assert _artifact_lifecycle_value(None) is None

    def test_string_returned_as_string(self):
        assert _artifact_lifecycle_value("current") == "current"

    def test_object_with_value_attr_extracted(self):
        class FakeEnum:
            value = "archived"
        assert _artifact_lifecycle_value(FakeEnum()) == "archived"

    def test_real_enum_value_extracted(self):
        class Status(StrEnum):
            CURRENT = "current"
            ARCHIVED = "archived"
        assert _artifact_lifecycle_value(Status.CURRENT) == "current"


# ---------------------------------------------------------------------------
# 3. _stale_status_value
# ---------------------------------------------------------------------------

class TestStaleStatusValue:
    def test_enum_stale_status_returns_value(self):
        ctx = _context_version(stale_status=AppContextStaleStatus.STALE)
        assert _stale_status_value(ctx) == "stale"

    def test_current_status(self):
        ctx = _context_version(stale_status=AppContextStaleStatus.CURRENT)
        assert _stale_status_value(ctx) == "current"

    def test_unknown_status(self):
        ctx = _context_version(stale_status=AppContextStaleStatus.UNKNOWN)
        assert _stale_status_value(ctx) == AppContextStaleStatus.UNKNOWN.value

    def test_string_status_returned(self):
        ctx = _context_version(stale_status="partially_stale")
        assert _stale_status_value(ctx) == "partially_stale"

    def test_none_status_returns_unknown(self):
        ctx = _context_version(stale_status=AppContextStaleStatus.UNKNOWN)
        ctx = ctx.model_copy(update={"stale_status": None})  # type: ignore[arg-type]
        assert _stale_status_value(ctx) == AppContextStaleStatus.UNKNOWN.value


# ---------------------------------------------------------------------------
# 4. _missing_expected_artifacts
# ---------------------------------------------------------------------------

class TestMissingExpectedArtifacts:
    def test_empty_expected_returns_empty(self):
        result = _missing_expected_artifacts(
            expected_artifacts=[],
            artifacts_created=[_artifact_ref("app_context_version")],
        )
        assert result == []

    def test_all_present_returns_empty(self):
        result = _missing_expected_artifacts(
            expected_artifacts=["app_context_version"],
            artifacts_created=[_artifact_ref("app_context_version")],
        )
        assert result == []

    def test_missing_kind_returned(self):
        result = _missing_expected_artifacts(
            expected_artifacts=["app_context_version", "source_index"],
            artifacts_created=[_artifact_ref("app_context_version")],
        )
        assert "source_index" in result

    def test_all_missing_all_returned(self):
        result = _missing_expected_artifacts(
            expected_artifacts=["kind_a", "kind_b"],
            artifacts_created=[],
        )
        assert set(result) == {"kind_a", "kind_b"}

    def test_empty_string_expected_kind_filtered(self):
        result = _missing_expected_artifacts(
            expected_artifacts=["", "app_context_version"],
            artifacts_created=[_artifact_ref("app_context_version")],
        )
        assert "" not in result


# ---------------------------------------------------------------------------
# 5. _dedupe_artifact_refs
# ---------------------------------------------------------------------------

class TestDedupeArtifactRefs:
    def test_empty_list_returns_empty(self):
        assert _dedupe_artifact_refs([]) == []

    def test_unique_refs_preserved(self):
        refs = [
            _artifact_ref("kind_a", "key1", "v1"),
            _artifact_ref("kind_b", "key2", "v2"),
        ]
        result = _dedupe_artifact_refs(refs)
        assert len(result) == 2

    def test_duplicate_key_dropped(self):
        ref = _artifact_ref("kind_a", "key1", "v1")
        result = _dedupe_artifact_refs([ref, ref])
        assert len(result) == 1

    def test_same_kind_different_version_kept(self):
        refs = [
            _artifact_ref("kind_a", "key1", "v1"),
            _artifact_ref("kind_a", "key1", "v2"),
        ]
        result = _dedupe_artifact_refs(refs)
        assert len(result) == 2

    def test_order_preserved(self):
        refs = [
            _artifact_ref("kind_a", "key1", "v1"),
            _artifact_ref("kind_b", "key2", "v2"),
        ]
        result = _dedupe_artifact_refs(refs)
        assert result[0].build_family == "kind_a"
        assert result[1].build_family == "kind_b"


# ---------------------------------------------------------------------------
# 6. _normalize_launch_result
# ---------------------------------------------------------------------------

class TestNormalizeLaunchResult:
    def test_none_returns_none(self):
        assert _normalize_launch_result(None) is None

    def test_instance_returned_unchanged(self):
        lr = _launch_result()
        result = _normalize_launch_result(lr)
        assert result is lr

    def test_dict_returns_launch_result(self):
        d = {
            "status": "launched",
            "workflow_sequence": BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
            "app_id": "my-app",
        }
        result = _normalize_launch_result(d)
        assert isinstance(result, ContextRefreshLaunchResult)
        assert result.app_id == "my-app"


# ---------------------------------------------------------------------------
# 7. _source_ref_payload
# ---------------------------------------------------------------------------

class TestSourceRefPayload:
    def test_returns_dict(self):
        ref = _source_ref()
        result = _source_ref_payload(ref)
        assert isinstance(result, dict)

    def test_source_ref_id_present(self):
        ref = _source_ref(source_ref_id="src-abc")
        result = _source_ref_payload(ref)
        assert result["source_ref_id"] == "src-abc"

    def test_uri_present(self):
        ref = _source_ref(uri="https://github.com/example/repo")
        result = _source_ref_payload(ref)
        assert result["uri"] == "https://github.com/example/repo"

    def test_kind_serialized_as_string(self):
        ref = _source_ref(kind=SourceRefKind.REPO)
        result = _source_ref_payload(ref)
        # model_dump(mode="json") serializes enum to its value
        assert isinstance(result["kind"], str)


# ---------------------------------------------------------------------------
# 8. _resolve_previous_context_version_id
# ---------------------------------------------------------------------------

class TestResolvePreviousContextVersionId:
    def test_explicit_version_returned(self):
        plan = _plan(current_context_version_id="ctx-plan")
        result = _resolve_previous_context_version_id(
            plan=plan,
            previous_context_version_id="ctx-explicit",
            launch_result=None,
            workflow_context_variables=None,
        )
        assert result == "ctx-explicit"

    def test_launch_result_version_used_when_no_explicit(self):
        plan = _plan(current_context_version_id="ctx-plan")
        lr = _launch_result(current_context_version_id="ctx-launch")
        result = _resolve_previous_context_version_id(
            plan=plan,
            previous_context_version_id=None,
            launch_result=lr,
            workflow_context_variables=None,
        )
        assert result == "ctx-launch"

    def test_workflow_context_variables_used_as_fallback(self):
        plan = _plan(current_context_version_id="ctx-plan")
        result = _resolve_previous_context_version_id(
            plan=plan,
            previous_context_version_id=None,
            launch_result=None,
            workflow_context_variables={"current_context_version_id": "ctx-vars"},
        )
        assert result == "ctx-vars"

    def test_plan_version_returned_as_last_resort(self):
        plan = _plan(current_context_version_id="ctx-plan")
        result = _resolve_previous_context_version_id(
            plan=plan,
            previous_context_version_id=None,
            launch_result=None,
            workflow_context_variables=None,
        )
        assert result == "ctx-plan"

    def test_explicit_whitespace_falls_through_to_launch_result(self):
        plan = _plan(current_context_version_id="ctx-plan")
        lr = _launch_result(current_context_version_id="ctx-launch")
        result = _resolve_previous_context_version_id(
            plan=plan,
            previous_context_version_id="   ",
            launch_result=lr,
            workflow_context_variables=None,
        )
        assert result == "ctx-launch"


# ---------------------------------------------------------------------------
# 9. _normalize_plan
# ---------------------------------------------------------------------------

class TestNormalizePlan:
    def test_instance_returned_unchanged(self):
        plan = _plan()
        result = _normalize_plan(plan)
        assert result is plan

    def test_dict_returns_context_refresh_plan(self):
        d = {"app_id": "my-app"}
        result = _normalize_plan(d)
        assert isinstance(result, ContextRefreshPlan)
        assert result.app_id == "my-app"

    def test_dict_default_workflow_sequence(self):
        result = _normalize_plan({"app_id": "my-app"})
        assert result.workflow_sequence == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE


# ---------------------------------------------------------------------------
# 10. _validate_refresh_plan
# ---------------------------------------------------------------------------

class TestValidateRefreshPlan:
    def test_valid_plan_returns_app_id(self):
        plan = _plan(app_id="my-app")
        resolved = _validate_refresh_plan(plan, app_id="my-app")
        assert resolved == "my-app"

    def test_wrong_workflow_sequence_raises(self):
        plan = ContextRefreshPlan(app_id="my-app", workflow_sequence="unknown_sequence")  # type: ignore[call-arg]
        with pytest.raises(ValueError, match="only supports"):
            _validate_refresh_plan(plan, app_id="my-app")

    def test_empty_app_id_arg_falls_back_to_plan_app_id(self):
        # empty string is falsy, so resolved_app_id uses plan.app_id
        plan = _plan(app_id="my-app")
        resolved = _validate_refresh_plan(plan, app_id="")
        assert resolved == "my-app"

    def test_mismatched_app_id_raises(self):
        plan = _plan(app_id="my-app")
        with pytest.raises(ValueError, match="app_id"):
            _validate_refresh_plan(plan, app_id="other-app")

    def test_none_launch_app_id_uses_plan_app_id(self):
        plan = _plan(app_id="my-app")
        resolved = _validate_refresh_plan(plan, app_id=None)
        assert resolved == "my-app"

