from __future__ import annotations

from mozaiksai.control_plane.review_package import (
    REVIEW_PACKAGE_SCHEMA_VERSION,
    build_refinement_review_package,
)


def test_review_package_merges_refinement_metadata_change_request_and_actions() -> None:
    package = build_refinement_review_package(
        app_id="app_1",
        build_record_id="av_child_1",
        build_family="app_bundle",
        build_key="app_bundle",
        parent_version_id="av_parent_1",
        lifecycle_status="draft",
        validation_status="passed",
        review_status="validated",
        changed_files=[
            {
                "path": "src/App.jsx",
                "change_type": "modified",
                "diff_preview": "@@ -1 +1 @@",
                "before_size": 10,
                "after_size": 12,
            }
        ],
        selected_paths=["src/App.jsx"],
        current_skipped_files=[],
        parent_skipped_files=["assets/logo.png"],
        refinement_metadata={
            "request_id": "refine_1",
            "change_class": "patch",
            "refinement_lane": "ui_patch",
            "workflow_sequence": "app_revision",
            "target_workflow": "AppGenerator",
            "affected_bundle_paths": ["src/App.jsx"],
            "review": {
                "write_back_mode": "external_patch",
                "write_back_target": "repo:customer/main",
                "notes": "Manual review recommended.",
            },
        },
        change_request={
            "router_decision": {"execution_mode": "coding_worker"},
            "change_intent": {
                "rationale": "A narrow patch is enough.",
                "confidence": 0.91,
                "signals": ["selected_file"],
            },
            "impact_set": {"scope_summary": "Patch selected dashboard file."},
        },
        latest_session={"provider": "control_plane_coding"},
        coding_summary="Patch the selected file.",
        validation_result={"validation_status": "passed"},
        can_accept=True,
        can_reject=True,
        can_promote=False,
    )

    assert package.schema_version == REVIEW_PACKAGE_SCHEMA_VERSION
    assert package.request_id == "refine_1"
    assert package.change_class == "patch"
    assert package.refinement_lane == "ui_patch"
    assert package.workflow_sequence == "app_revision"
    assert package.write_back_mode == "external_patch"
    assert package.write_back_label == "Create patch proposal"
    assert package.write_back.target == "repo:customer/main"
    assert package.affected_paths == ["src/App.jsx"]
    assert package.changed_file_count == 1
    assert package.route_decision.execution_mode == "coding_worker"
    assert package.route_decision.rationale == "A narrow patch is enough."
    assert package.actions[0].id == "accept"
    assert package.actions[0].enabled is True
    assert any(action.id == "reroute" and action.enabled is False for action in package.actions)
    assert "Manual review recommended." in package.risk_notes
    assert any("External source" in note for note in package.risk_notes)
    assert any("parent bundle" in note for note in package.risk_notes)


def test_review_package_defaults_generated_artifact_write_back() -> None:
    package = build_refinement_review_package(
        app_id="app_1",
        build_record_id="av_child_1",
        build_family="app_bundle",
        build_key="app_bundle",
        parent_version_id=None,
        lifecycle_status="draft",
        validation_status="skipped",
        review_status="validated",
        changed_files=[],
        selected_paths=[],
        current_skipped_files=[],
        parent_skipped_files=[],
        validation_override_required=True,
        validation_blocker="Validation has not passed.",
        can_accept=False,
        can_reject=True,
        can_promote=False,
    )

    assert package.write_back_mode == "generated_artifact"
    assert package.write_back_label == "Update app version"
    assert package.validation_override_required is True
    assert package.actions[0].id == "accept"
    assert package.actions[0].enabled is False
    assert package.actions[0].reason == "Validation has not passed."

