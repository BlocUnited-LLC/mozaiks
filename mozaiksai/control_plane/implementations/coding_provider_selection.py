"""Deterministic coding-provider selection for the refinement coding worker.

Selection is pure policy over the request shape, refinement policy config, and
trusted coding/validation isolation readiness. The classifier and scope checkpoints
upstream decide whether a bounded patch is appropriate. Disabled ACP uses the
structured-output provider. Enabled ACP handles one or more approved app files
within budget only when both the coding provider and candidate validator are
isolated. Unsupported or over-budget work is reported as blocked; the upstream
scope checkpoint owns the normal workflow route for broader changes.
An ACP attempt never silently switches provider after an operational failure.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from mozaiksai.control_plane.config import ControlPlaneConfig
from mozaiksai.control_plane.contracts import CodingWorkerRequest

CodingProviderChoice = Literal["structured_output", "acp"]

_ACP_ELIGIBLE_ARTIFACT_KINDS = {"app_bundle", "theme_capture"}

class CodingProviderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: CodingProviderChoice | None
    reason: str


def select_coding_provider(
    request: CodingWorkerRequest,
    config: ControlPlaneConfig,
    *,
    acp_worker_ready: bool,
    acp_validation_ready: bool,
) -> CodingProviderSelection:
    """Pick one provider or block an ineligible ACP dispatch. Pure and total."""
    acp = config.coding.providers.acp
    file_count = len(request.files)

    if not acp.enabled:
        return CodingProviderSelection(
            provider="structured_output",
            reason="acp_disabled",
        )
    if request.build_family not in _ACP_ELIGIBLE_ARTIFACT_KINDS:
        return CodingProviderSelection(
            provider=None,
            reason=f"artifact_kind_not_acp_eligible:{request.build_family}",
        )
    if file_count == 0:
        return CodingProviderSelection(
            provider=None,
            reason="empty_file_scope",
        )
    if file_count > acp.budget.max_files:
        return CodingProviderSelection(
            provider=None,
            reason=f"scope_exceeds_acp_max_files:{file_count}>{acp.budget.max_files}",
        )
    if not acp_worker_ready:
        return CodingProviderSelection(
            provider=None,
            reason="isolated_acp_worker_unavailable",
        )
    if not acp_validation_ready:
        return CodingProviderSelection(
            provider=None,
            reason="isolated_candidate_validation_unavailable",
        )
    return CodingProviderSelection(
        provider="acp",
        reason=f"bounded_scope_within_budget:{file_count}",
    )


__all__ = [
    "CodingProviderChoice",
    "CodingProviderSelection",
    "select_coding_provider",
]
