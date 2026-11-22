"""Read reviewed role grants from the bound persisted DesignDocs summary."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, cast

import yaml

from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_role_grants import (
    DesignAccessPolicy,
    GrantCollectionIndex,
    PackAuthorization,
    ReviewedPackIdentity,
    compiled_role_grants,
    validate_access_policy,
    verified_pack_authorization,
)
from mozaiksai.core.workflow.generator_support.page_action_bindings import design_page_write_actions


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


def validate_design_page_sign_in(
    experience_spec: Any, policy: dict[str, Any], *, require_explicit: bool = False,
) -> dict[str, bool]:
    """Bind explicit login choices as a set. Older approvals contain only role gates."""
    spec = detach(experience_spec)
    if spec is None and not require_explicit:
        return {}
    roles = validate_design_page_roles(spec, policy)
    if spec is None:
        raise ValueError("Page sign-in choices require an ExperienceSpec page inventory")
    pages = spec["pages"]
    if not require_explicit and not any("requiresAuth" in page for page in pages):
        return {}
    sign_in = {}
    for page in pages:
        route = page["route"]
        gate = page.get("requiresAuth")
        if type(gate) is not bool:
            raise ValueError(f"Page {route!r} must declare an explicit boolean requiresAuth")
        if roles[route] and not gate:
            raise ValueError(f"Page {route!r} has role restrictions and requiresAuth must be true")
        sign_in[route] = gate
    return sign_in


def access_policy_sha256(
    policy: dict[str, Any], experience_spec: Any = None, *, pack_identities: Any = None,
) -> str:
    reviewed: dict[str, Any] = {"access_policy": policy, "page_roles": validate_design_page_roles(experience_spec, policy)}
    sign_in = validate_design_page_sign_in(experience_spec, policy)
    if sign_in:
        reviewed["page_requires_auth"] = sign_in
    if pack_identities:
        identities = [ReviewedPackIdentity.model_validate(row).model_dump() for row in pack_identities]
        if len({row["pack_id"] for row in identities}) != len(identities):
            raise ValueError("access_policy review repeats a pack identity")
        reviewed["pack_identities"] = sorted(identities, key=lambda row: row["pack_id"])
    encoded = json.dumps(reviewed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resolve_reviewed_design(context: Any) -> tuple[
    dict[str, Any] | None, dict[str, Any] | None, PackAuthorization | None,
]:
    """Ignore candidate state; only the immutable data-reference may grant roles.

    A CURRENT summary is not proof of owner review. Genesis summaries become
    CURRENT on save, so the review receipt and its exact policy digest are also
    required. A reviewed refinement draft belongs only to its bound build.
    """
    if context is None:
        return None, None, None
    build_record = detach(context.get("design_docs_build_artifact"))
    record = build_record if build_record is not None else detach(context.get("design_docs_artifact"))
    candidate = detach(context.get("access_policy"))
    if record is None:
        if isinstance(candidate, dict) and candidate.get("grants"):
            raise ValueError("access_policy grants have no reviewed DesignDocs artifact")
        spec = detach(context.get("experience_spec"))
        if isinstance(spec, dict) and any(page.get("roles") for page in spec.get("pages", []) if isinstance(page, dict)):
            raise ValueError("Page role restrictions have no reviewed DesignDocs artifact")
        if isinstance(spec, dict) and any("requiresAuth" in page for page in spec.get("pages", []) if isinstance(page, dict)):
            raise ValueError("Page sign-in choices have no reviewed DesignDocs artifact")
        return None, None, None
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
        if validate_design_page_sign_in(summary.get("experience_spec"), {"roles": [], "grants": []}):
            raise ValueError("Page sign-in choices have no reviewed DesignDocs access policy")
        return None, None, None
    review = summary.get("access_policy_review")
    reviewed_packs = review.get("pack_identities") if isinstance(review, dict) else None
    if reviewed_packs is not None and not isinstance(reviewed_packs, list):
        raise ValueError("access_policy review pack identities must be an explicit list")
    raw_policy = DesignAccessPolicy.model_validate(
        summary["access_policy"] if summary["access_policy"] is not None else {"roles": [], "grants": []},
    ).model_dump()
    if not isinstance(review, dict) or (
        review.get("policy_sha256") != access_policy_sha256(
            raw_policy, summary.get("experience_spec"), pack_identities=reviewed_packs,
        )
        or review.get("target_app_id") != binding.target_app_id
        or not review.get("build_id")
    ):
        raise ValueError("access_policy review does not match the persisted policy, page access or pack identities; review DesignDocs again")
    # Only the reviewed choices select sources. An expanded workflow registry
    # describes availability and cannot add packs to an existing approval.
    pack_authorization = verified_pack_authorization(context, selection=reviewed_packs or [])
    current_packs = [identity.model_dump() for identity in pack_authorization.identities]
    recorded = [ReviewedPackIdentity.model_validate(row).model_dump() for row in (reviewed_packs or [])]
    if sorted(recorded, key=lambda row: row["pack_id"]) != current_packs:
        raise ValueError("access_policy review does not match the selected pack identities; review DesignDocs again")
    policy = validate_access_policy(
        raw_policy, data_contract=summary.get("data_contract"),
        design_surface_map=summary.get("surface_map"), pack_authorization=pack_authorization,
    )
    page_roles = validate_design_page_roles(summary.get("experience_spec"), policy)
    if build_record is not None and review["build_id"] != binding.build_id:
        raise ValueError("access_policy projection belongs to a different build")
    if binding.phase == "genesis" and review["build_id"] != binding.build_id:
        raise ValueError("access_policy genesis review belongs to a different build")
    if record.get("lifecycle_status") == "draft":
        if review["build_id"] != binding.build_id:
            raise ValueError("access_policy draft belongs to a different build")
    elif record.get("lifecycle_status") != "current":
        raise ValueError("access_policy artifact is not current or this build's reviewed draft")
    if current_packs or policy["grants"] or any(page_roles.values()) or validate_design_page_sign_in(summary.get("experience_spec"), policy):
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
    return policy, summary, pack_authorization


def resolve_design_access_policy(context: Any) -> dict[str, Any] | None:
    return _resolve_reviewed_design(context)[0]


def resolve_scaffold_access(context: Any) -> tuple[dict[str, Any] | None, bool]:
    """Resolve role policy and app-wide sign-in needs from a single bound approval."""
    policy, summary, _ = _resolve_reviewed_design(context)
    gates = validate_design_page_sign_in(summary.get("experience_spec"), policy) if policy and summary else {}
    return policy, any(gates.values())


def resolve_design_role_projection(context: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Compile the displayed role permissions from the same review as their grant anchors."""
    policy, summary, authorization = _resolve_reviewed_design(context)
    if policy is None or summary is None:
        return policy, []
    roles = compiled_role_grants(policy, data_contract=summary.get("data_contract"), pack_authorization=authorization)
    return policy, roles


def resolve_reviewed_pack_selection(context: Any) -> list[dict[str, Any]]:
    """Resolve choices through the same bound artifact and review as its grants.

    Missing reviewed identities authorize no reusable packs. A later plan that
    wants to materialize any must obtain a new explicit design review.
    """
    _, summary, _ = _resolve_reviewed_design(context)
    if summary is None:
        return []
    return cast(list[dict[str, Any]], detach(summary["access_policy_review"].get("pack_identities") or []))


def resolve_authored_module_access_policy(context: Any) -> dict[str, Any] | None:
    """Project the reviewed policy to generated modules; pack manifests remain pack-owned."""
    policy, summary, authorization = _resolve_reviewed_design(context)
    if policy is None or summary is None:
        return policy
    pack_modules = set(authorization.actions) if authorization is not None else set()
    if not pack_modules or not policy["grants"]:
        return policy
    collections = GrantCollectionIndex(summary["data_contract"], authorization)
    return {
        **policy,
        "grants": [
            grant for grant in policy["grants"]
            if collections.resolve(grant["collection"])[0] not in pack_modules
        ],
    }


def resolve_design_page_roles(context: Any) -> dict[str, list[str] | None] | None:
    policy, summary, _ = _resolve_reviewed_design(context)
    if policy is None or summary is None or summary.get("experience_spec") is None:
        return None
    return validate_design_page_roles(summary.get("experience_spec"), policy)


def resolve_reviewed_page_writes(context: Any) -> dict[str, list[dict[str, str]]]:
    """Explicit submit/delete identities from the bound design, independent of roles and layout."""
    _, summary, _ = _resolve_reviewed_design(context)
    experience = (summary or {}).get("experience_spec") or {}
    required: dict[str, list[dict[str, str]]] = {}
    for page in experience.get("pages") or []:
        if page.get("ui_surface") != "declarative_page":
            continue
        route = page["route"]
        writes = design_page_write_actions(page)
        if writes:
            required[route] = writes
    return required


def resolve_design_page_sign_in(context: Any) -> dict[str, bool]:
    policy, summary, _ = _resolve_reviewed_design(context)
    return validate_design_page_sign_in(summary.get("experience_spec"), policy) if policy and summary else {}


def _page_role_meta(page: dict[str, Any], *, declarative: bool) -> dict[str, Any]:
    meta = page.get("meta") or {}
    if not isinstance(meta, dict):
        raise ValueError("Page role metadata must be an object")
    aliases = {"roles", "requiredRole"} & meta.keys()
    aliases |= ({"requiredRole", "requiresRole"} if declarative else {"roles", "requiredRole", "requiresRole"}) & page.keys()
    if aliases:
        raise ValueError(f"Page role metadata must use canonical meta.requiresRole, not {sorted(aliases)}")
    return meta


def _require_legacy_sign_in(target: dict[str, Any], route: str | None) -> None:
    gate = target.get("requiresAuth")
    if gate is not None and type(gate) is not bool:
        raise ValueError(f"Page {route!r} requiresAuth must be a boolean")
    if gate is False:
        raise ValueError(f"Page {route!r} needs a new DesignDocs sign-in review before authoring a public route")


def validate_page_role_preservation(
    pages: list[dict[str, Any]], context: Any, *,
    custom_routes: list[dict[str, Any]] | None = None, require_complete: bool = False,
    planned: bool = False, fill_missing: bool = False,
) -> dict[str, list[str] | None] | None:
    """Check runtime access, optionally projecting omitted fields during authoring."""
    policy, summary, _ = _resolve_reviewed_design(context)
    if policy is None or summary is None or summary.get("experience_spec") is None:
        if fill_missing and context is not None and (context.get("design_docs_build_artifact") or context.get("design_docs_artifact")):
            # Summaries predating access_policy have no sign-in authority either.
            for page in pages:
                _require_legacy_sign_in(page if planned else _page_role_meta(page, declarative=True), page.get("route"))
            for entry in custom_routes or []:
                _require_legacy_sign_in({**_page_role_meta(entry, declarative=False), **entry}, entry.get("path"))
        return None  # Standalone authored apps do not claim Factory design approval.
    approved = validate_design_page_roles(summary["experience_spec"], policy)
    sign_in = validate_design_page_sign_in(summary["experience_spec"], policy)
    seen: set[str] = set()

    def preserve_login(target: dict[str, Any], route: str) -> None:
        if route not in sign_in:
            if fill_missing:
                _require_legacy_sign_in(target, route)
            return
        expected = sign_in[route]
        if fill_missing and target.get("requiresAuth") is None:
            target["requiresAuth"] = expected
        # The platform defaults absent route gates to signed in.
        if target.get("requiresAuth", True) is not expected:
            raise ValueError(f"Page {route!r} must preserve reviewed requiresAuth={str(expected).lower()}")

    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("Page must be an object")
        route, roles = page.get("route"), page.get("roles")
        meta = _page_role_meta(page, declarative=True)
        override = meta.get("requiresRole")
        if route not in approved:
            raise ValueError(f"Page {route!r} has no reviewed page in ExperienceSpec")
        seen.add(route)
        if (route in sign_in or fill_missing) and "requiresAuth" in (meta if planned else page):
            raise ValueError(f"Page {route!r} has requiresAuth outside its canonical location")
        preserve_login(page if planned else meta, route)
        if fill_missing and route in sign_in:
            if not planned:
                page["meta"] = meta
            if roles is None and approved[route] is not None:
                page["roles"] = roles = approved[route]
        candidate = sorted(roles) if isinstance(roles, list) and all(isinstance(role, str) for role in roles) else roles
        if candidate != approved[route]:
            raise ValueError(f"Page {route!r} must preserve reviewed roles {approved[route]!r}; received {roles!r}")
        if override is not None and [override] != approved[route]:
            raise ValueError(f"Page {route!r} requiresRole overrides its reviewed roles")
    for entry in custom_routes or []:
        if not isinstance(entry, dict):
            raise ValueError("Custom route entry must be an object")
        route = entry.get("path")
        meta = _page_role_meta(entry, declarative=False)
        role = meta.get("requiresRole")
        if route not in approved:
            raise ValueError(f"Custom page {route!r} has no reviewed page in ExperienceSpec")
        seen.add(route)
        roles = approved[route]
        if roles and len(set(roles)) > 1:
            raise ValueError(f"Custom page {route!r} cannot project multiple roles through requiresRole")
        expected = roles[0] if roles else None
        if (route in sign_in or fill_missing) and "requiresAuth" in meta:
            raise ValueError(f"Custom page {route!r} must declare requiresAuth on the route entry")
        preserve_login(entry, route)
        if fill_missing and route in sign_in and role is None and expected:
            meta["requiresRole"] = role = expected
            entry["meta"] = meta
        if role != expected:
            raise ValueError(f"Custom page {route!r} must preserve reviewed requiresRole {expected!r}; received {role!r}")
    if require_complete and set(approved) - seen:
        raise ValueError(f"Reviewed page role restrictions are missing routes {sorted(set(approved) - seen)}")
    return approved


def project_reviewed_page_access(
    files: dict[str, str], context: Any, *, owned_paths: set[str],
    owned_routes: set[str] | None = None,
) -> dict[str, str]:
    """Materialize approved access on the editable page files of this task."""
    projected = dict(files)
    for path in sorted(owned_paths & files.keys()):
        if path == "ui/route_manifest.json":
            try:
                manifest = json.loads(files[path])
            except ValueError as exc:
                raise ValueError(f"{path}: invalid JSON") from exc
            routes = manifest.get("pages") if isinstance(manifest, dict) else None
            if not isinstance(routes, list) or any(not isinstance(entry, dict) for entry in routes):
                raise ValueError(f"{path}: pages must be a list of route objects")
            editable = [entry for entry in routes if entry.get("path") in (owned_routes or set())]
            original_routes = deepcopy(editable)
            validate_page_role_preservation([], context, custom_routes=editable, fill_missing=True)
            if editable != original_routes:
                projected[path] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        elif path.startswith("ui/pages/") and path.endswith(".yaml") and path.count("/") == 2:
            try:
                page = yaml.safe_load(files[path])
            except yaml.YAMLError as exc:
                raise ValueError(f"{path}: invalid YAML") from exc
            if not isinstance(page, dict):
                raise ValueError(f"{path}: page must be an object")
            original_page = deepcopy(page)
            validate_page_role_preservation([page], context, fill_missing=True)
            if page != original_page:
                projected[path] = yaml.safe_dump(page, sort_keys=False, allow_unicode=True)
    return projected
