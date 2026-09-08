"""One shared workflow identity-name comparison policy.

The runtime workflow loader and semantics cold binding validation must accept
and reject exactly the same workflow identity spellings.  Both consume the
single helper in ``mozaiksai.core.workflow.workflow_identity`` — these tests
pin the helper's behavior and the fact that both call sites use the SAME
policy object, so the two layers can never drift independently.
"""

from __future__ import annotations

import pytest

from mozaiksai.core.workflow.workflow_identity import (
    normalize_workflow_identity_name,
    workflow_identity_names_equal,
)


def test_normalization_is_strip_then_lowercase_comparison_only() -> None:
    assert normalize_workflow_identity_name("  MyWorkflow \n") == "myworkflow"
    assert normalize_workflow_identity_name("VERSIONPROBE") == "versionprobe"
    assert normalize_workflow_identity_name("") == ""
    assert normalize_workflow_identity_name(None) == ""  # type: ignore[arg-type]


def test_equality_is_case_insensitive_and_whitespace_tolerant() -> None:
    assert workflow_identity_names_equal("MyWorkflow", "myworkflow")
    assert workflow_identity_names_equal(" MyWorkflow ", "MYWORKFLOW")
    assert workflow_identity_names_equal("versionprobe", "versionprobe")
    assert not workflow_identity_names_equal("MyWorkflow", "OtherWorkflow")
    assert not workflow_identity_names_equal("myworkflow", "myworkflow2")


def test_empty_names_never_match() -> None:
    assert not workflow_identity_names_equal("", "")
    assert not workflow_identity_names_equal("  ", "  ")
    assert not workflow_identity_names_equal("", "myworkflow")
    assert not workflow_identity_names_equal("myworkflow", "")


def test_runtime_loader_consumes_the_shared_policy() -> None:
    """The loader's orchestrator identity check accepts/rejects via the helper."""
    from mozaiksai.core.workflow import workflow_manager as manager_module
    from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager

    assert (
        manager_module.workflow_identity_names_equal is workflow_identity_names_equal
    )
    probe = UnifiedWorkflowManager.__new__(UnifiedWorkflowManager)
    # Exact and case-variant spellings the runtime loader accepts.
    probe._validate_orchestrator_contract(
        "MyWorkflow",
        {"workflow_name": "MyWorkflow", "workflow_startup_mode": "AgentDriven"},
    )
    probe._validate_orchestrator_contract(
        "MyWorkflow",
        {"workflow_name": "myworkflow", "workflow_startup_mode": "AgentDriven"},
    )
    with pytest.raises(ValueError, match="mismatched workflow_name"):
        probe._validate_orchestrator_contract(
            "NotesWorkflow",
            {"workflow_name": "ForeignWorkflow", "workflow_startup_mode": "AgentDriven"},
        )


def test_cold_binding_validation_consumes_the_shared_policy() -> None:
    """Semantics cold validation is pinned to the SAME helper object."""
    from mozaiksai.core.semantics import binding as binding_module

    assert (
        binding_module.workflow_identity_names_equal is workflow_identity_names_equal
    )
