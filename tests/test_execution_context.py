from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.control_plane import (
    ApprovedExecutionContext,
    build_coding_request_from_execution_context,
)


def _context(**overrides):
    value = {
        "handoff_id": "handoff-1",
        "request_id": "request-1",
        "plan_id": "plan-1",
        "app_id": "app-zero",
        "build_registry_id": "registry-1",
        "repository_full_name": "BlocUnited-LLC/mozaiks-app",
        "baseline_commit_sha": "a" * 40,
        "raw_request": "Add a support panel",
        "request_type": "feature",
        "approved_plan_digest": "b" * 64,
        "snapshot_digest": "snapshot-1",
        "graph_identity_digest": "graph-1",
        "impact_report_digest": "impact-1",
        "execution_strategy_id": "standard_coding_refinement",
        "rationale": "A narrow app surface change.",
        "allowed_paths": ["app/modules/support/"],
        "prohibited_paths": ["app/security/"],
    }
    value.update(overrides)
    return value


def test_build_request_scopes_baseline_files_and_preserves_context():
    request = build_coding_request_from_execution_context(
        _context(),
        baseline_files={
            "app/modules/support/service.py": "before",
            "app/security/secrets.yaml": "secret policy",
            "README.md": "readme",
        },
    )

    assert request.files == {"app/modules/support/service.py": "before"}
    assert request.baseline_files["app/security/secrets.yaml"] == "secret policy"
    assert request.raw_user_request == "Add a support panel"
    assert request.metadata["approved_plan_digest"] == "b" * 64


def test_prohibited_path_wins_over_allowed_scope():
    with pytest.raises(ValueError, match="no scoped baseline files"):
        build_coding_request_from_execution_context(
            _context(allowed_paths=["app/"]),
            baseline_files={"app/security/secrets.yaml": "secret policy"},
        )


def test_rejects_unsafe_context_path():
    with pytest.raises(ValidationError):
        ApprovedExecutionContext.model_validate(_context(allowed_paths=["../app"]))


def test_empty_scoped_baseline_fails_closed():
    with pytest.raises(ValueError, match="no scoped baseline files"):
        build_coding_request_from_execution_context(
            _context(), baseline_files={"README.md": "readme"}
        )
