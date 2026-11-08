"""Read reviewed role grants from the bound persisted DesignDocs summary."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_role_grants import validate_access_policy


def access_policy_sha256(policy: dict[str, Any]) -> str:
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_design_access_policy(context: Any) -> dict[str, Any] | None:
    """Ignore candidate state; only the immutable data-reference may grant roles.

    A CURRENT summary is not proof of owner review. Genesis summaries become
    CURRENT on save, so the review receipt and its exact policy digest are also
    required. A reviewed refinement draft belongs only to its bound build.
    """
    if context is None:
        return None
    build_record = detach(context.get("design_docs_build_artifact"))
    record = build_record if build_record is not None else detach(context.get("design_docs_artifact"))
    candidate = detach(context.get("access_policy"))
    if record is None:
        if isinstance(candidate, dict) and candidate.get("grants"):
            raise ValueError("access_policy grants have no reviewed DesignDocs artifact")
        return None
    binding = RunBuildBinding.model_validate(detach(context.get("run_build_binding")))
    if not isinstance(record, dict) or (
        record.get("app_id") != binding.target_app_id
        or record.get("build_family") != "design_docs"
        or record.get("build_key") != "design_docs"
        or record.get("source_workflow") != "DesignDocs"
    ):
        raise ValueError("access_policy artifact does not belong to this app's DesignDocs")
    commit = record.get("commit_metadata") or {}
    summary = (commit.get("metadata") or {}).get("summary_payload")
    if not isinstance(summary, dict):
        raise ValueError("access_policy artifact has no design summary")
    if "access_policy" not in summary:
        if isinstance(candidate, dict) and candidate.get("grants"):
            raise ValueError("access_policy grants are absent from the reviewed design")
        return None
    policy = validate_access_policy(
        summary["access_policy"], data_contract=summary.get("data_contract"),
        design_surface_map=summary.get("surface_map"),
    )
    review = summary.get("access_policy_review")
    if not isinstance(review, dict) or (
        review.get("policy_sha256") != access_policy_sha256(policy)
        or review.get("target_app_id") != binding.target_app_id
        or not review.get("build_id")
    ):
        raise ValueError("access_policy review does not match the persisted policy")
    if build_record is not None and review["build_id"] != binding.build_id:
        raise ValueError("access_policy projection belongs to a different build")
    if record.get("lifecycle_status") == "draft":
        if review["build_id"] != binding.build_id:
            raise ValueError("access_policy draft belongs to a different build")
    elif record.get("lifecycle_status") != "current":
        raise ValueError("access_policy artifact is not current or this build's reviewed draft")
    if policy["grants"]:
        if (
            review.get("status") != "approved" or not review.get("review_id")
            or not review.get("reviewed_by")
            or review.get("reviewed_by") != commit.get("author_user_id")
        ):
            raise ValueError("access_policy grants require explicit owner approval")
    elif review.get("status") not in {"approved", "not_required"}:
        raise ValueError("access_policy review is not complete")
    # Downstream code must compile the same collection and action identities
    # that were reviewed, rather than borrowing grants for a different draft.
    for key, field in (("data_contract", "data_contract"), ("design_surface_map", "surface_map")):
        supplied = detach(context.get(key))
        if supplied is not None and supplied != summary.get(field):
            raise ValueError(f"access_policy approved design does not match {key}")
    return policy
