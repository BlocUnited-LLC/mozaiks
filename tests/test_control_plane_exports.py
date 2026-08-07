from __future__ import annotations


def test_control_plane_exports_include_staged_build_record_api() -> None:
    from mozaiksai.control_plane import (
        AcceptedStagedAppBundleBuildRecordError,
        accept_staged_refinement_build_record,
    )

    assert AcceptedStagedAppBundleBuildRecordError.__name__ == "AcceptedStagedAppBundleBuildRecordError"
    assert accept_staged_refinement_build_record.__name__ == "accept_staged_refinement_build_record"
