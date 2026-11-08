"""Read reviewed role grants from the bound persisted DesignDocs summary."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_role_grants import validate_access_policy


def validate_design_page_roles(experience_spec: Any, policy: dict[str, Any]) -> dict[str, list[str] | None]:
    """Close the existing page.roles references against the proposed role inventory."""
    if experience_spec is None:
        return {}
    spec = detach(experience_spec)
    if not isinstance(spec, dict) or not isinstance(spec.get("pages"), list):
        raise ValueError("Page role restrictions require an ExperienceSpec page inventory")
    known = {role["role_id"] for role in policy["roles"]}
    result: dict[str, list[str] | None] = {}
    for page in spec["pages"]:
        route = page.get("route") if isinstance(page, dict) else None
        if not isinstance(route, str) or not route.startswith("/") or route in result:
            raise ValueError("Page role restrictions require unique canonical page routes")
        roles = page.get("roles")
        if roles is not None:
            if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
                raise ValueError(f"Page {route!r} roles must be a string list or null")
            unknown = sorted(set(roles) - known)
            if unknown:
                raise ValueError(f"Page {route!r} names roles absent from access_policy: {unknown}")
            if page.get("ui_surface") == "custom_react_page" and len(set(roles)) > 1:
                raise ValueError(f"Custom page {route!r} cannot project multiple roles through requiresRole")
            roles = sorted(roles)
        result[route] = roles
    return dict(sorted(result.items()))


def access_policy_sha256(policy: dict[str, Any], experience_spec: Any = None) -> str:
    reviewed = {"access_policy": policy, "page_roles": validate_design_page_roles(experience_spec, policy)}
    encoded = json.dumps(reviewed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_reviewed_design(context: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Ignore candidate state; only the immutable data-reference may grant roles.

    A CURRENT summary is not proof of owner review. Genesis summaries become
    CURRENT on save, so the review receipt and its exact policy digest are also
    required. A reviewed refinement draft belongs only to its bound build.
    """
    if context is None:
        return None, None
    build_record = detach(context.get("design_docs_build_artifact"))
    record = build_record if build_record is not None else detach(context.get("design_docs_artifact"))
    candidate = detach(context.get("access_policy"))
    if record is None:
        if isinstance(candidate, dict) and candidate.get("grants"):
            raise ValueError("access_policy grants have no reviewed DesignDocs artifact")
        spec = detach(context.get("experience_spec"))
        if isinstance(spec, dict) and any(page.get("roles") for page in spec.get("pages", []) if isinstance(page, dict)):
            raise ValueError("Page role restrictions have no reviewed DesignDocs artifact")
        return None, None
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
        validate_design_page_roles(summary.get("experience_spec"), {"roles": [], "grants": []})
        return None, None
    policy = validate_access_policy(
        summary["access_policy"], data_contract=summary.get("data_contract"),
        design_surface_map=summary.get("surface_map"),
    )
    page_roles = validate_design_page_roles(summary.get("experience_spec"), policy)
    review = summary.get("access_policy_review")
    if not isinstance(review, dict) or (
        review.get("policy_sha256") != access_policy_sha256(policy, summary.get("experience_spec"))
        or review.get("target_app_id") != binding.target_app_id
        or not review.get("build_id")
    ):
        raise ValueError("access_policy review does not match the persisted policy and page roles; review DesignDocs again")
    if build_record is not None and review["build_id"] != binding.build_id:
        raise ValueError("access_policy projection belongs to a different build")
    if record.get("lifecycle_status") == "draft":
        if review["build_id"] != binding.build_id:
            raise ValueError("access_policy draft belongs to a different build")
    elif record.get("lifecycle_status") != "current":
        raise ValueError("access_policy artifact is not current or this build's reviewed draft")
    if policy["grants"] or any(page_roles.values()):
        if (
            review.get("status") != "approved" or not review.get("review_id")
            or not review.get("reviewed_by")
            or review.get("reviewed_by") != commit.get("author_user_id")
        ):
            raise ValueError("access_policy grants and page restrictions require explicit owner approval")
    elif review.get("status") not in {"approved", "not_required"}:
        raise ValueError("access_policy review is not complete")
    # Downstream code must compile the same collection and action identities
    # that were reviewed, rather than borrowing grants for a different draft.
    for key, field in (("data_contract", "data_contract"), ("design_surface_map", "surface_map"),
                       ("experience_spec", "experience_spec")):
        supplied = detach(context.get(key))
        if supplied is not None and supplied != summary.get(field):
            raise ValueError(f"access_policy approved design does not match {key}")
    return policy, summary


def resolve_design_access_policy(context: Any) -> dict[str, Any] | None:
    return _resolve_reviewed_design(context)[0]


def resolve_design_page_roles(context: Any) -> dict[str, list[str] | None] | None:
    policy, summary = _resolve_reviewed_design(context)
    if policy is None or summary is None or summary.get("experience_spec") is None:
        return None
    return validate_design_page_roles(summary.get("experience_spec"), policy)


def _page_role_meta(page: dict[str, Any], *, declarative: bool) -> dict[str, Any]:
    meta = page.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("Page role metadata must be an object")
    aliases = {"roles", "requiredRole"} & meta.keys()
    aliases |= ({"requiredRole", "requiresRole"} if declarative else {"roles", "requiredRole", "requiresRole"}) & page.keys()
    if aliases:
        raise ValueError(f"Page role metadata must use canonical meta.requiresRole, not {sorted(aliases)}")
    return meta


def validate_page_role_preservation(
    pages: list[dict[str, Any]], context: Any, *,
    custom_routes: list[dict[str, Any]] | None = None, require_complete: bool = False,
) -> dict[str, list[str] | None] | None:
    """Compare generated page gates and return their exact reviewed authority."""
    approved = resolve_design_page_roles(context)
    if approved is None:
        return None  # Standalone authored apps do not claim Factory design approval.
    seen: set[str] = set()
    for page in pages:
        route, roles = page.get("route"), page.get("roles")
        meta = _page_role_meta(page, declarative=True)
        override = meta.get("requiresRole")
        if route not in approved:
            raise ValueError(f"Page {route!r} has no reviewed page in ExperienceSpec")
        seen.add(route)
        candidate = sorted(roles) if isinstance(roles, list) and all(isinstance(role, str) for role in roles) else roles
        if candidate != approved[route]:
            raise ValueError(f"Page {route!r} must preserve reviewed roles {approved[route]!r}; received {roles!r}")
        if override is not None and [override] != approved[route]:
            raise ValueError(f"Page {route!r} requiresRole overrides its reviewed roles")
    for entry in custom_routes or []:
        route = entry.get("path")
        role = _page_role_meta(entry, declarative=False).get("requiresRole")
        if route not in approved:
            raise ValueError(f"Custom page {route!r} has no reviewed page in ExperienceSpec")
        seen.add(route)
        roles = approved[route]
        if roles and len(set(roles)) > 1:
            raise ValueError(f"Custom page {route!r} cannot project multiple roles through requiresRole")
        expected = roles[0] if roles else None
        if role != expected:
            raise ValueError(f"Custom page {route!r} must preserve reviewed requiresRole {expected!r}; received {role!r}")
    if require_complete and set(approved) - seen:
        raise ValueError(f"Reviewed page role restrictions are missing routes {sorted(set(approved) - seen)}")
    return approved
