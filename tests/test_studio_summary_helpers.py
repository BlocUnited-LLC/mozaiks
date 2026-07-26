"""
Studio summary pure helper unit tests.

Covers:
  _normalize_request_kind:
    - non-string → None
    - unknown string → None
    - known values: "greenfield_app", "brownfield_app", "refinement" → returned
    - whitespace stripped before check

  _normalize_change_class:
    - non-string → None
    - unknown string → None
    - known values: "patch", "design", "feature", "core" → returned

  _normalize_string_list:
    - non-list → []
    - non-string items filtered
    - empty/whitespace items filtered
    - valid strings returned stripped

  _flatten_unique_strings:
    - empty list → []
    - multiple groups flattened
    - duplicates across groups deduplicated, first occurrence kept
    - order preserved

  _normalize_build_tasks:
    - non-list → []
    - non-dict items filtered
    - owned_paths/depends_on/acceptance_criteria normalized to lists
    - string scalar fields coerced to str

  _normalize_current_request:
    - non-dict → empty request
    - text stripped
    - request_kind and change_class normalized
    - updated_at only kept if string
    - missing text → ""

  _normalize_recent_requests:
    - non-list → []
    - non-dict items filtered
    - items with empty text filtered
    - limited to 8 items
    - text stripped

  _resolve_admins:
    - empty admins list → []
    - non-list admins → []
    - non-string items filtered
    - empty/whitespace strings filtered
    - valid admin strings returned stripped

  _runtime_readiness:
    - 0 workflows → "no_workflows"
    - workflows > 0, no entry point → "workflows_present_no_entry_point"
    - workflows > 0, entry point set → "entry_point_configured"

  _summarize_refinement_policy:
    - non-dict input → disabled/empty
    - enabled True → enabled=True
    - classifier enabled → classifier_enabled=True
    - llm_profile_count from profiles dict

  _format_studio_timestamp_label:
    - non-string → fallback
    - empty/whitespace string → fallback
    - valid string → returned

  _recommend_lifecycle_next_step:
    - each known lifecycle state → descriptive string

  build_apps_metrics:
    - empty list → all zeros
    - counts in_progress, active, needs_revision

  _build_connector_summary:
    - empty list → all zero counts
    - active connectors counted
    - health status counted
"""
from __future__ import annotations

from mozaiksai.core.runtime.app.studio_summary import (
    _build_connector_summary,
    _flatten_unique_strings,
    _format_studio_timestamp_label,
    _normalize_build_tasks,
    _normalize_change_class,
    _normalize_current_request,
    _normalize_recent_requests,
    _normalize_request_kind,
    _normalize_string_list,
    _recommend_lifecycle_next_step,
    _resolve_admins,
    _runtime_readiness,
    _summarize_refinement_policy,
    build_app_list_entry,
    build_apps_metrics,
)

# ---------------------------------------------------------------------------
# 1. _normalize_request_kind
# ---------------------------------------------------------------------------

class TestNormalizeRequestKind:
    def test_none_returns_none(self):
        assert _normalize_request_kind(None) is None

    def test_non_string_returns_none(self):
        assert _normalize_request_kind(42) is None

    def test_unknown_string_returns_none(self):
        assert _normalize_request_kind("unknown_type") is None

    def test_greenfield_app_accepted(self):
        assert _normalize_request_kind("greenfield_app") == "greenfield_app"

    def test_brownfield_app_accepted(self):
        assert _normalize_request_kind("brownfield_app") == "brownfield_app"

    def test_refinement_accepted(self):
        assert _normalize_request_kind("refinement") == "refinement"

    def test_whitespace_stripped(self):
        assert _normalize_request_kind("  greenfield_app  ") == "greenfield_app"

    def test_empty_string_returns_none(self):
        assert _normalize_request_kind("") is None


# ---------------------------------------------------------------------------
# 2. _normalize_change_class
# ---------------------------------------------------------------------------

class TestNormalizeChangeClass:
    def test_none_returns_none(self):
        assert _normalize_change_class(None) is None

    def test_unknown_returns_none(self):
        assert _normalize_change_class("hotfix") is None

    def test_patch_accepted(self):
        assert _normalize_change_class("patch") == "patch"

    def test_design_accepted(self):
        assert _normalize_change_class("design") == "design"

    def test_feature_accepted(self):
        assert _normalize_change_class("feature") == "feature"

    def test_core_accepted(self):
        assert _normalize_change_class("core") == "core"

    def test_whitespace_stripped(self):
        assert _normalize_change_class("  patch  ") == "patch"


# ---------------------------------------------------------------------------
# 3. _normalize_string_list
# ---------------------------------------------------------------------------

class TestNormalizeStringList:
    def test_non_list_returns_empty(self):
        assert _normalize_string_list("not-a-list") == []

    def test_none_returns_empty(self):
        assert _normalize_string_list(None) == []

    def test_empty_list_returns_empty(self):
        assert _normalize_string_list([]) == []

    def test_non_string_items_filtered(self):
        assert _normalize_string_list([1, None, "valid"]) == ["valid"]

    def test_whitespace_only_filtered(self):
        assert _normalize_string_list(["  ", "valid", ""]) == ["valid"]

    def test_valid_strings_stripped(self):
        result = _normalize_string_list(["  hello  ", "world"])
        assert result == ["hello", "world"]

    def test_preserves_order(self):
        result = _normalize_string_list(["c", "a", "b"])
        assert result == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# 4. _flatten_unique_strings
# ---------------------------------------------------------------------------

class TestFlattenUniqueStrings:
    def test_empty_list(self):
        assert _flatten_unique_strings([]) == []

    def test_single_group(self):
        assert _flatten_unique_strings([["a", "b", "c"]]) == ["a", "b", "c"]

    def test_multiple_groups_flattened(self):
        result = _flatten_unique_strings([["a", "b"], ["c", "d"]])
        assert result == ["a", "b", "c", "d"]

    def test_duplicates_removed_first_occurrence_kept(self):
        result = _flatten_unique_strings([["a", "b"], ["b", "c"]])
        assert result == ["a", "b", "c"]

    def test_preserves_order(self):
        result = _flatten_unique_strings([["x"], ["y"], ["x", "z"]])
        assert result == ["x", "y", "z"]


# ---------------------------------------------------------------------------
# 5. _normalize_build_tasks
# ---------------------------------------------------------------------------

class TestNormalizeBuildTasks:
    def test_non_list_returns_empty(self):
        assert _normalize_build_tasks("bad") == []

    def test_non_dict_items_filtered(self):
        result = _normalize_build_tasks([{"task_id": "t-1"}, "not-a-dict", None])
        assert len(result) == 1

    def test_owned_paths_normalized(self):
        result = _normalize_build_tasks([{"task_id": "t-1", "owned_paths": ["a/b.py", "  ", None]}])
        assert result[0]["owned_paths"] == ["a/b.py"]

    def test_depends_on_normalized(self):
        result = _normalize_build_tasks([{"task_id": "t-1", "depends_on": ["t-2", "  t-3  "]}])
        assert result[0]["depends_on"] == ["t-2", "t-3"]

    def test_acceptance_criteria_normalized(self):
        result = _normalize_build_tasks([{"task_id": "t-1", "acceptance_criteria": ["criterion 1"]}])
        assert result[0]["acceptance_criteria"] == ["criterion 1"]

    def test_integer_task_field_coerced_to_str(self):
        result = _normalize_build_tasks([{"task_id": 123}])
        assert result[0]["task_id"] == "123"

    def test_missing_owned_paths_defaults_to_empty(self):
        result = _normalize_build_tasks([{"task_id": "t-1"}])
        assert result[0]["owned_paths"] == []


# ---------------------------------------------------------------------------
# 6. _normalize_current_request
# ---------------------------------------------------------------------------

class TestNormalizeCurrentRequest:
    def test_non_dict_returns_empty_request(self):
        result = _normalize_current_request(None)
        assert result["text"] == ""
        assert result["request_kind"] is None

    def test_text_stripped(self):
        result = _normalize_current_request({"text": "  Build wallet module  "})
        assert result["text"] == "Build wallet module"

    def test_request_kind_normalized(self):
        result = _normalize_current_request({"text": "x", "request_kind": "refinement"})
        assert result["request_kind"] == "refinement"

    def test_invalid_request_kind_returns_none(self):
        result = _normalize_current_request({"text": "x", "request_kind": "bad"})
        assert result["request_kind"] is None

    def test_change_class_normalized(self):
        result = _normalize_current_request({"text": "x", "change_class": "feature"})
        assert result["change_class"] == "feature"

    def test_updated_at_kept_when_string(self):
        result = _normalize_current_request({"text": "x", "updated_at": "2026-01-01T00:00:00Z"})
        assert result["updated_at"] == "2026-01-01T00:00:00Z"

    def test_non_string_updated_at_dropped(self):
        result = _normalize_current_request({"text": "x", "updated_at": 12345})
        assert result["updated_at"] is None


# ---------------------------------------------------------------------------
# 7. _normalize_recent_requests
# ---------------------------------------------------------------------------

class TestNormalizeRecentRequests:
    def test_non_list_returns_empty(self):
        assert _normalize_recent_requests(None) == []

    def test_non_dict_items_filtered(self):
        result = _normalize_recent_requests([{"text": "hello"}, "bad", None])
        assert len(result) == 1

    def test_items_with_empty_text_filtered(self):
        result = _normalize_recent_requests([{"text": "  "}, {"text": "valid"}])
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_limited_to_8_items(self):
        items = [{"text": f"request {i}"} for i in range(15)]
        result = _normalize_recent_requests(items)
        assert len(result) == 8

    def test_text_stripped(self):
        result = _normalize_recent_requests([{"text": "  a request  "}])
        assert result[0]["text"] == "a request"

    def test_saved_at_kept_when_string(self):
        result = _normalize_recent_requests([{"text": "req", "saved_at": "2026-01-01"}])
        assert result[0]["saved_at"] == "2026-01-01"

    def test_non_string_saved_at_dropped(self):
        result = _normalize_recent_requests([{"text": "req", "saved_at": 0}])
        assert result[0]["saved_at"] is None


# ---------------------------------------------------------------------------
# 8. _resolve_admins
# ---------------------------------------------------------------------------

class TestResolveAdmins:
    def test_empty_list_returns_empty(self):
        assert _resolve_admins({"admins": []}) == []

    def test_non_list_admins_returns_empty(self):
        assert _resolve_admins({"admins": "admin@example.com"}) == []

    def test_none_admins_returns_empty(self):
        assert _resolve_admins({}) == []

    def test_non_string_items_filtered(self):
        assert _resolve_admins({"admins": [None, 42, "admin@example.com"]}) == ["admin@example.com"]

    def test_empty_strings_filtered(self):
        assert _resolve_admins({"admins": ["", "  ", "admin@example.com"]}) == ["admin@example.com"]

    def test_strings_stripped(self):
        result = _resolve_admins({"admins": ["  admin@example.com  "]})
        assert result == ["admin@example.com"]

    def test_valid_admins_returned(self):
        admins = ["a@example.com", "b@example.com"]
        result = _resolve_admins({"admins": admins})
        assert result == admins


# ---------------------------------------------------------------------------
# 9. _runtime_readiness
# ---------------------------------------------------------------------------

class TestRuntimeReadiness:
    def test_zero_workflows_no_workflows(self):
        assert _runtime_readiness(0, None) == "no_workflows"

    def test_zero_workflows_with_entry_point(self):
        assert _runtime_readiness(0, "AppGenerator") == "no_workflows"

    def test_workflows_no_entry_point(self):
        assert _runtime_readiness(2, None) == "workflows_present_no_entry_point"

    def test_workflows_with_entry_point(self):
        assert _runtime_readiness(3, "AppGenerator") == "entry_point_configured"

    def test_one_workflow_no_entry(self):
        assert _runtime_readiness(1, None) == "workflows_present_no_entry_point"

    def test_one_workflow_with_entry(self):
        assert _runtime_readiness(1, "MyWorkflow") == "entry_point_configured"


# ---------------------------------------------------------------------------
# 10. _summarize_refinement_policy
# ---------------------------------------------------------------------------

class TestSummarizeRefinementPolicy:
    def test_non_dict_returns_disabled(self):
        result = _summarize_refinement_policy(None)
        assert result["enabled"] is False
        assert result["classifier_enabled"] is False
        assert result["llm_profile_count"] == 0

    def test_enabled_true(self):
        result = _summarize_refinement_policy({"enabled": True})
        assert result["enabled"] is True

    def test_classifier_enabled(self):
        result = _summarize_refinement_policy({"classifier": {"enabled": True}})
        assert result["classifier_enabled"] is True

    def test_profile_string_returned(self):
        result = _summarize_refinement_policy({"profile": "standard"})
        assert result["profile"] == "standard"

    def test_profile_non_string_returns_none(self):
        result = _summarize_refinement_policy({"profile": 123})
        assert result["profile"] is None

    def test_llm_profile_count(self):
        result = _summarize_refinement_policy({"llm_profiles": {"a": {}, "b": {}}})
        assert result["llm_profile_count"] == 2

    def test_coding_enabled(self):
        result = _summarize_refinement_policy({"coding": {"enabled": True}})
        assert result["coding_enabled"] is True

    def test_non_dict_classifier_ignored(self):
        result = _summarize_refinement_policy({"classifier": "not-a-dict"})
        assert result["classifier_enabled"] is False


# ---------------------------------------------------------------------------
# 11. _format_studio_timestamp_label
# ---------------------------------------------------------------------------

class TestFormatStudioTimestampLabel:
    def test_valid_string_returned(self):
        assert _format_studio_timestamp_label("2026-01-01", fallback="N/A") == "2026-01-01"

    def test_whitespace_string_returns_fallback(self):
        assert _format_studio_timestamp_label("   ", fallback="N/A") == "N/A"

    def test_empty_string_returns_fallback(self):
        assert _format_studio_timestamp_label("", fallback="N/A") == "N/A"

    def test_none_returns_fallback(self):
        assert _format_studio_timestamp_label(None, fallback="Unknown") == "Unknown"

    def test_int_returns_fallback(self):
        assert _format_studio_timestamp_label(12345, fallback="N/A") == "N/A"

    def test_whitespace_stripped_before_return(self):
        assert _format_studio_timestamp_label("  2026-01-01  ", fallback="N/A") == "2026-01-01"


# ---------------------------------------------------------------------------
# 12. _recommend_lifecycle_next_step
# ---------------------------------------------------------------------------

class TestRecommendLifecycleNextStep:
    def test_draft_message(self):
        result = _recommend_lifecycle_next_step("draft")
        assert "build brief" in result.lower() or "studio" in result.lower()

    def test_building_message(self):
        result = _recommend_lifecycle_next_step("building")
        assert "build" in result.lower() or "artifact" in result.lower()

    def test_review_message(self):
        result = _recommend_lifecycle_next_step("review")
        assert "review" in result.lower()

    def test_active_message(self):
        result = _recommend_lifecycle_next_step("active")
        assert "studio" in result.lower() or "monitor" in result.lower()

    def test_needs_revision_message(self):
        result = _recommend_lifecycle_next_step("needs_revision")
        assert "revision" in result.lower()

    def test_archived_message(self):
        result = _recommend_lifecycle_next_step("archived")
        assert "archived" in result.lower() or "archive" in result.lower()

    def test_unknown_state_returns_generic(self):
        result = _recommend_lifecycle_next_step("some_unknown_state")
        assert isinstance(result, str) and len(result) > 0


# ---------------------------------------------------------------------------
# 13. build_app_list_entry
# ---------------------------------------------------------------------------

class TestBuildAppListEntry:
    def test_building_entry_routes_to_chat_scope(self):
        result = build_app_list_entry(
            {
                "build_registry_id": "appreg_1",
                "app_id": "draft-build-abc123",
                "chat_app_id": "demo-app",
                "lifecycle_state": "building",
                "active_chat_id": "chat_1",
                "active_workflow_id": "ValueEngine",
            }
        )

        assert result["chat_app_id"] == "demo-app"
        assert result["active_chat_id"] == "chat_1"
        assert result["active_workflow_id"] == "ValueEngine"
        assert result["destination"] == "/chat?workflow=ValueEngine&mode=workflow&chat_id=chat_1&app_id=demo-app"

    def test_building_entry_falls_back_to_app_id_without_chat_scope(self):
        result = build_app_list_entry(
            {
                "build_registry_id": "appreg_1",
                "app_id": "build-app",
                "lifecycle_state": "building",
                "active_chat_id": "chat_1",
                "active_workflow_id": "ValueEngine",
            }
        )

        assert result["chat_app_id"] is None
        assert result["destination"] == "/chat?workflow=ValueEngine&mode=workflow&chat_id=chat_1&app_id=build-app"


# ---------------------------------------------------------------------------
# 14. build_apps_metrics
# ---------------------------------------------------------------------------

class TestBuildAppsMetrics:
    def test_empty_list(self):
        result = build_apps_metrics([])
        assert result["total_apps"] == 0
        assert result["in_progress"] == 0
        assert result["active"] == 0
        assert result["needs_revision"] == 0

    def test_total_apps_count(self):
        apps = [{"status": "building"}, {"status": "active"}, {"status": "draft"}]
        assert build_apps_metrics(apps)["total_apps"] == 3

    def test_active_counted(self):
        apps = [{"status": "active"}, {"status": "active"}, {"status": "building"}]
        result = build_apps_metrics(apps)
        assert result["active"] == 2

    def test_needs_revision_counted(self):
        apps = [{"status": "needs_revision"}, {"status": "active"}]
        result = build_apps_metrics(apps)
        assert result["needs_revision"] == 1

    def test_in_progress_uses_build_continue_states(self):
        # building, review, generating etc. are in APP_BUILD_CONTINUE_STATES
        apps = [{"status": "building"}, {"status": "review"}, {"status": "active"}]
        result = build_apps_metrics(apps)
        assert result["in_progress"] >= 2  # building + review are in continue states


# ---------------------------------------------------------------------------
# 15. _build_connector_summary
# ---------------------------------------------------------------------------

class TestBuildConnectorSummary:
    def test_empty_list_all_zeros(self):
        result = _build_connector_summary([])
        assert result["total"] == 0
        assert result["active"] == 0
        assert result["healthy"] == 0

    def test_total_count(self):
        connectors = [{}, {}, {}]
        assert _build_connector_summary(connectors)["total"] == 3

    def test_active_status_counted(self):
        connectors = [
            {"status": "active"},
            {"status": "active"},
            {"status": "revoked"},
        ]
        result = _build_connector_summary(connectors)
        assert result["active"] == 2
        assert result["revoked"] == 1

    def test_healthy_health_status_counted(self):
        connectors = [
            {"status": "active", "health": {"status": "healthy"}},
            {"status": "active", "health": {"status": "unhealthy"}},
        ]
        result = _build_connector_summary(connectors)
        assert result["healthy"] == 1
        assert result["unhealthy"] == 1

    def test_missing_health_counted_as_unknown(self):
        connectors = [{"status": "active"}]
        result = _build_connector_summary(connectors)
        assert result["unknown_health"] == 1

    def test_non_dict_health_counted_as_unknown(self):
        connectors = [{"status": "active", "health": "bad"}]
        result = _build_connector_summary(connectors)
        assert result["unknown_health"] == 1
