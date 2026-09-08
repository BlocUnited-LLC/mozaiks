"""Canonical workflow identity-name comparison policy.

One workflow has three spellings of the same identity: the workflow instance
(directory) name, the ``workflow_name`` its exact ``orchestrator.yaml``
declares, and — for graph-v2 applications — the semantic
``WorkflowPayload.workflow_id``.  The runtime workflow loader accepts these as
one identity under a case-insensitive comparison; this module is the single
shared policy for that comparison so the loader and semantics cold validation
can never drift.

Dependency-light on purpose: stdlib only, no filesystem, no runtime manager
imports.  Normalization exists ONLY for equality comparison — original
document spelling is never rewritten or stored in normalized form.
"""

from __future__ import annotations


def normalize_workflow_identity_name(value: str) -> str:
    """The comparison form of one workflow identity name.

    Matches the runtime loader's accepted contract exactly: surrounding
    whitespace is stripped, then the name is lowercased.  Never store or emit
    this form — it is comparison-only.
    """
    return str(value or "").strip().lower()


def workflow_identity_names_equal(left: str, right: str) -> bool:
    """Case-insensitive workflow identity equality; empty names never match."""
    left_normalized = normalize_workflow_identity_name(left)
    right_normalized = normalize_workflow_identity_name(right)
    return bool(left_normalized) and left_normalized == right_normalized


__all__ = [
    "normalize_workflow_identity_name",
    "workflow_identity_names_equal",
]
