"""Enforce declared platform and selected-pack ownership before design approval."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from factory_app.workflows._shared.hook_utils import workflow_context_path
from mozaiksai.core.session.build_context import load_contract_descriptors
from mozaiksai.core.workflow.context.frozen import detach


class SurfaceOwnershipRule(BaseModel):
    """Exact contract identifiers, never classification of free-form prose."""

    model_config = ConfigDict(extra="forbid", strict=True)

    owner: str = Field(min_length=1)
    facade_module: str | None = None
    surface_ids: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    collection_names: list[str] = Field(default_factory=list)


def _get(context: Any, key: str, default: Any = None) -> Any:
    return detach(context.get(key, default)) if context is not None else default


def default_subscription_contract(context: Any) -> dict[str, Any] | None:
    """The same default pack used to complete greenfield subscription pages."""
    if _get(context, "brownfield_build_path") or _get(context, "monetization_enabled") is not True:
        return None
    binding = _get(context, "run_build_binding", {}) or {}
    if binding.get("phase", "genesis") != "genesis":
        return None
    blueprint = _get(context, "concept_blueprint", {}) or {}
    intent = blueprint.get("monetization_intent") or {}
    if intent.get("monetized") is not True or intent.get("subscription_contract_likely") is not True:
        return None
    return dict(yaml.safe_load(workflow_context_path("mozaikspay", "contract.yaml").read_text(encoding="utf-8")))


def _selected_contracts(context: Any, *, include_default_subscription: bool) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for pack in _get(context, "capability_packs", []) or []:
        if pack.get("capability_source") != "managed_capability" or pack.get("status", "active") != "active":
            continue
        source = pack.get("pack_source_path")
        if source:
            root = Path(source)
            config = yaml.safe_load((root / "context.yaml").read_text(encoding="utf-8"))
            contracts.extend(load_contract_descriptors(root, config))
        else:
            pack_id = pack.get("id") or pack.get("pack_id") or pack.get("capability_pack_id")
            contracts.extend(
                contract for contract in _get(context, "operator_contracts", []) or []
                if contract.get("contract_id") == pack_id
                or (contract.get("canonical_provider") or {}).get("provider_pack_id") == pack_id
            )
    default = default_subscription_contract(context) if include_default_subscription else None
    if default and not any(contract.get("contract_id") == default["contract_id"] for contract in contracts):
        contracts.append(default)
    return contracts


def _identifiers(values: Any) -> set[str]:
    return {str(value).strip().casefold() for value in values or []}


def validate_surface_ownership(
    surface_map: dict[str, Any],
    *,
    context_variables: Any,
    data_contract: dict[str, Any] | None = None,
    include_default_subscription: bool = False,
) -> None:
    """Reject duplicate owners without rewriting approved surfaces or storage."""
    catalog = yaml.safe_load(
        workflow_context_path("AppGenerator", "capability_routing.yaml").read_text(encoding="utf-8"),
    )
    declarations = list(catalog["layers"]["runtime_provided"]["surface_ownership"])
    contracts = _selected_contracts(context_variables, include_default_subscription=include_default_subscription)
    facade_actions: dict[str, set[str]] = {}
    for contract in contracts:
        ownership = contract.get("surface_ownership", [])
        if not isinstance(ownership, list):
            raise ValueError(f"{contract.get('contract_id')}: surface_ownership must be a list.")
        declarations.extend(ownership)
        for facade in contract.get("facades") or []:
            facade_actions.setdefault(facade["module_id"], set()).update(
                action for page in facade.get("pages") or [] for action in page.get("primary_actions") or []
            )
    rules = [SurfaceOwnershipRule.model_validate(rule) for rule in declarations]
    blueprint = _get(context_variables, "concept_blueprint", {}) or {}
    for hint in blueprint.get("surface_candidate_hints") or []:
        if hint.get("owner_hint") == "platform":
            rules.append(SurfaceOwnershipRule(owner="Mozaiks platform", surface_ids=[hint["surface_id"]]))

    surfaces = surface_map.get("surfaces") or []
    data = data_contract or {}
    collections = [
        (str(surface.get("surface_id") or ""), collection)
        for surface in data.get("surfaces") or [] for collection in surface.get("collections") or []
    ]
    collections.extend(("", collection) for collection in data.get("shared_collections") or [])

    for rule in rules:
        if rule.facade_module and rule.facade_module not in facade_actions:
            raise ValueError(f"{rule.owner}: surface_ownership references undeclared facade {rule.facade_module!r}.")
        remedy = (
            f"Bind UI to {rule.facade_module} and its declared actions "
            f"{sorted(facade_actions[rule.facade_module])}; keep provider state out of app data_contract."
            if rule.facade_module else
            "Reference the platform capability with owner='platform'; use config/auth.yaml for auth "
            "and keep platform identity/session state out of app modules and data_contract."
        )
        reserved_entities = _identifiers(rule.entity_names)
        reserved_actions = _identifiers(rule.action_ids)
        reserved_surfaces = _identifiers(rule.surface_ids)
        for surface in surfaces:
            surface_id = str(surface.get("surface_id") or "")
            is_facade = surface_id == rule.facade_module
            if surface.get("owner") != "app" or surface.get("surface_kind") != "module":
                continue
            conflicts = _identifiers(surface.get("primary_entities")) & reserved_entities
            actions = _identifiers(surface.get("owned_mutations"))
            if is_facade:
                conflicts |= _identifiers(surface.get("primary_entities"))
                conflicts |= actions - _identifiers(facade_actions[surface_id])
            else:
                conflicts |= actions & reserved_actions
                conflicts |= _identifiers([surface_id]) & reserved_surfaces
            if conflicts:
                raise ValueError(
                    f"Surface {surface_id!r} claims {sorted(conflicts)}, owned by {rule.owner}. {remedy} "
                    "Revise DesignDocs ownership before app planning; do not generate a replacement module."
                )
        for group_id, collection in collections:
            owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
            name = str(collection.get("name") or "")
            if (
                _identifiers([name]) & _identifiers(rule.collection_names)
                or _identifiers([owner_id, group_id]) & reserved_surfaces
                or rule.facade_module and rule.facade_module in {owner_id, group_id}
            ):
                raise ValueError(
                    f"Collection {name!r} on surface {owner_id!r} duplicates {rule.owner} state. {remedy} "
                    "Remove the app-owned state declaration before saving."
                )
