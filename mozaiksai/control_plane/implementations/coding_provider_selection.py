"""Deterministic coding-provider selection for the refinement coding worker.

Selection is pure policy over the request shape, refinement policy config, and
provider availability. No model output influences dispatch — the classifier
and scope checkpoints upstream decide *whether* coding happens; this module
only decides *which provider* performs an already-approved bounded patch.

v1 policy:

- The structured-output provider is the default and handles every eligible
  request.
- The ACP provider is selected only when all of these hold: it is enabled in
  ``refinement_policy.yaml``, the ``ag2[acp]`` extra is importable, the
  artifact kind is ``app_bundle`` or ``theme_capture``, and the scope spans
  more than one file while staying inside the ACP file budget. Single-file
  patches stay on the cheaper deterministic provider.

Fallback ladder (applied by the worker): an ACP attempt that ends
``unavailable``, ``failed``, ``empty``, ``timeout``, or ``budget_exceeded``
falls back to the structured provider exactly once. ``rejected_scope`` never
falls back — out-of-scope agent behavior is surfaced, not papered over.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import CodingWorkerRequest

CodingProviderChoice = Literal["structured_output", "acp"]

_ACP_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "theme_capture"}

# ACP proposal statuses that permit one structured-provider fallback attempt.
# "rejected_scope" is deliberately absent: a provider that edited outside its
# scope is a policy event to surface, never something to silently retry around.
ACP_FALLBACK_STATUSES = frozenset({"unavailable", "failed", "empty", "timeout", "budget_exceeded"})


class CodingProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: CodingProviderChoice
    reason: str


def select_coding_provider(
    request: CodingWorkerRequest,
    config: ControlPlaneConfig,
    *,
    acp_importable: bool,
) -> CodingProviderSelection:
    """Pick the provider for one eligible coding request. Pure and total."""
    acp = config.coding.providers.acp
    file_count = len(request.files)

    if not acp.enabled:
        return CodingProviderSelection(
            provider="structured_output",
            reason="acp_disabled",
        )
    if not acp_importable:
        return CodingProviderSelection(
            provider="structured_output",
            reason="acp_extra_not_installed",
        )
    if request.build_family not in _ACP_ELIGIBLE_ARTIFACT_KINDS:
        return CodingProviderSelection(
            provider="structured_output",
            reason=f"artifact_kind_not_acp_eligible:{request.build_family}",
        )
    if file_count <= 1:
        return CodingProviderSelection(
            provider="structured_output",
            reason="single_file_scope",
        )
    if file_count > acp.budget.max_files:
        return CodingProviderSelection(
            provider="structured_output",
            reason=f"scope_exceeds_acp_max_files:{file_count}>{acp.budget.max_files}",
        )
    return CodingProviderSelection(
        provider="acp",
        reason=f"multi_file_scope_within_budget:{file_count}",
    )


__all__ = [
    "ACP_FALLBACK_STATUSES",
    "CodingProviderChoice",
    "CodingProviderSelection",
    "select_coding_provider",
]
