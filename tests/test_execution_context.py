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
            "app/security/secrets.yaml": "FORBIDDEN_SOURCE_CONTENT",
            "README.md": "OUT_OF_SCOPE_SOURCE_CONTENT",
        },
    )

    assert request.files == {"app/modules/support/service.py": "before"}
    assert request.baseline_files == request.files
    assert request.raw_user_request == "Add a support panel"
    assert request.metadata["approved_plan_digest"] == "b" * 64
    assert request.validation_strategy is None
    assert "FORBIDDEN_SOURCE_CONTENT" not in request.model_dump_json()
    assert "OUT_OF_SCOPE_SOURCE_CONTENT" not in request.model_dump_json()


def test_prohibited_path_wins_over_allowed_scope():
    with pytest.raises(ValueError, match="no scoped baseline files"):
        build_coding_request_from_execution_context(
            _context(allowed_paths=["app/"]),
            baseline_files={"app/security/secrets.yaml": "secret policy"},
        )


def test_read_only_path_wins_over_allowed_scope_and_excludes_content():
    request = build_coding_request_from_execution_context(
        _context(
            allowed_paths=["app/modules/"],
            read_only_paths=["app/modules/support/contracts/"],
        ),
        baseline_files={
            "app/modules/support/service.py": "editable",
            "app/modules/support/contracts/module.yaml": "READ_ONLY_SOURCE_CONTENT",
        },
    )

    assert request.files == {"app/modules/support/service.py": "editable"}
    assert request.baseline_files == request.files
    assert "READ_ONLY_SOURCE_CONTENT" not in request.model_dump_json()


def test_read_only_scope_without_editable_files_fails_closed():
    with pytest.raises(ValueError, match="no scoped baseline files"):
        build_coding_request_from_execution_context(
            _context(read_only_paths=["app/modules/support/"]),
            baseline_files={"app/modules/support/service.py": "read only"},
        )


def test_prohibited_scope_is_case_insensitive_for_source_selection():
    with pytest.raises(ValueError, match="no scoped baseline files"):
        build_coding_request_from_execution_context(
            _context(
                allowed_paths=["app/"],
                prohibited_paths=["app/PROTECTED/"],
            ),
            baseline_files={"app/protected/service.py": "should not be exposed"},
        )


def test_rejects_unsafe_context_path():
    with pytest.raises(ValidationError):
        ApprovedExecutionContext.model_validate(_context(allowed_paths=["../app"]))


def test_empty_scoped_baseline_fails_closed():
    with pytest.raises(ValueError, match="no scoped baseline files"):
        build_coding_request_from_execution_context(
            _context(), baseline_files={"README.md": "readme"}
        )


def test_create_only_request_preserves_exact_approved_grant():
    request = build_coding_request_from_execution_context(
        _context(create_paths=["app/modules/support/new.py"]), baseline_files={}
    )
    assert request.files == {}
    assert request.metadata["approved_create_paths"] == ["app/modules/support/new.py"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"create_paths": ["../outside.py"]},
        {"create_paths": ["app/modules/support/new.py", "app/modules/support/NEW.py"]},
        {"create_paths": ["app/modules/support/new", "app/modules/support/new/child.py"]},
        {"create_paths": ["app/modules/support/cafe\u0301.py"]},
        {"create_paths": ["app/modules/support/new.py/active/"]},
        {"create_paths": ["app/modules/support/new.py"], "delete_paths": ["app/modules/support/NEW.py"]},
        {"create_paths": ["README.md"]},
        {"delete_paths": ["app/security/secrets.yaml"]},
    ],
)
def test_exact_operation_grants_reject_unsafe_or_unapproved_paths(overrides):
    with pytest.raises(ValidationError):
        ApprovedExecutionContext.model_validate(_context(**overrides))
