"""Enforce declared platform and selected-pack ownership before design approval."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    surface_collection_names: list[str] = Field(default_factory=list)
    surface_entity_names: list[str] = Field(default_factory=list)
    surface_action_ids: list[str] = Field(default_factory=list)
    state_field_names: list[str] = Field(default_factory=list)
    structured_state_field_types: dict[str, list[Literal["object", "array"]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def declared_structured_fields(self) -> Self:
        if _identifiers(self.structured_state_field_types) - _identifiers(self.state_field_names):
            raise ValueError("structured_state_field_types must reference declared state_field_names")
        return self


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


def _ownership_rules(
    context_variables: Any, *, include_default_subscription: bool,
) -> tuple[list[SurfaceOwnershipRule], dict[str, dict[str, Any]]]:
    catalog = yaml.safe_load(
        workflow_context_path("AppGenerator", "capability_routing.yaml").read_text(encoding="utf-8"),
    )
    declarations = list(catalog["layers"]["runtime_provided"]["surface_ownership"])
    contracts = _selected_contracts(context_variables, include_default_subscription=include_default_subscription)
    facades: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        ownership = contract.get("surface_ownership", [])
        if not isinstance(ownership, list):
            raise ValueError(f"{contract.get('contract_id')}: surface_ownership must be a list.")
        if any(not rule.get("facade_module") for rule in ownership):
            raise ValueError(
                f"{contract.get('contract_id')}: ambiguous managed ownership without a canonical facade_module."
            )
        declarations.extend(ownership)
        for facade in contract.get("facades") or []:
            declaration = {**facade, "pack_id": contract["contract_id"]}
            existing = facades.get(facade["module_id"])
            if existing is not None and existing != declaration:
                raise ValueError(f"Ambiguous ownership: competing declarations for facade {facade['module_id']!r}.")
            facades[facade["module_id"]] = declaration
    rules = [SurfaceOwnershipRule.model_validate(rule) for rule in declarations]
    blueprint = _get(context_variables, "concept_blueprint", {}) or {}
    for hint in blueprint.get("surface_candidate_hints") or []:
        if hint.get("owner_hint") == "platform":
            rules.append(SurfaceOwnershipRule(owner="Mozaiks platform", surface_ids=[hint["surface_id"]]))
    for rule in rules:
        if rule.facade_module and rule.facade_module not in facades:
            raise ValueError(f"{rule.owner}: surface_ownership references undeclared facade {rule.facade_module!r}.")
    return rules, facades


def _facade_actions(facade: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        action for page in facade.get("pages") or [] for action in page.get("primary_actions") or []
    ))


def _matches_surface(surface: dict[str, Any], rule: SurfaceOwnershipRule) -> bool:
    return bool(
        _identifiers([surface.get("surface_id")]) & _identifiers(rule.surface_ids)
        or surface.get("surface_id") == rule.facade_module
        or _identifiers(surface.get("primary_entities")) & _identifiers(rule.entity_names)
        or _identifiers(surface.get("owned_mutations")) & _identifiers(rule.action_ids)
    )


def _matches_collection(collection: dict[str, Any], group_id: str, rule: SurfaceOwnershipRule) -> bool:
    owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
    return bool(
        _identifiers([collection.get("name")]) & _identifiers(rule.collection_names)
        or _identifiers([owner_id, group_id]) & _identifiers(rule.surface_ids)
        or rule.facade_module and rule.facade_module in {owner_id, group_id}
    )


def validate_surface_ownership(
    surface_map: dict[str, Any],
    *,
    context_variables: Any,
    data_contract: dict[str, Any] | None = None,
    include_default_subscription: bool = False,
) -> None:
    """Reject duplicate owners in approved designs; late planning never rewrites them."""
    rules, facades = _ownership_rules(context_variables, include_default_subscription=include_default_subscription)
    facade_actions = {module_id: _facade_actions(facade) for module_id, facade in facades.items()}

    surfaces = surface_map.get("surfaces") or []
    data = data_contract or {}
    collections = [
        (str(surface.get("surface_id") or ""), collection)
        for surface in data.get("surfaces") or [] for collection in surface.get("collections") or []
    ]
    collections.extend(("", collection) for collection in data.get("shared_collections") or [])

    for rule in rules:
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
            if _matches_collection(collection, group_id, rule):
                raise ValueError(
                    f"Collection {name!r} on surface {owner_id!r} duplicates {rule.owner} state. {remedy} "
                    "Remove the app-owned state declaration before saving."
                )


def normalize_surface_ownership(
    surface_map: dict[str, Any],
    *,
    context_variables: Any,
    data_contract: dict[str, Any],
    include_default_subscription: bool = False,
) -> list[dict[str, Any]]:
    """Apply only contract-determined corrections, atomically, before design approval.

    A reserved surface name proves the owner, not the meaning of every field it
    contains. Residual auth data moves only to one determined existing app module;
    unresolved owners or mixed app behavior require a design decision.
    """
    rules, facades = _ownership_rules(context_variables, include_default_subscription=include_default_subscription)
    normalized_map, normalized_data = deepcopy(surface_map), deepcopy(data_contract)
    records: dict[tuple[str, str], dict[str, Any]] = {}

    def record(surface_id: str, rule: SurfaceOwnershipRule, removed: str | None = None) -> dict[str, Any]:
        owner = rule.facade_module or "platform"
        entry = records.setdefault((surface_id, owner), {
            "surface_id": surface_id, "owner": owner, "removed_collections": [],
        })
        if removed is not None:
            entry["removed_collections"].append(removed)
        return entry

    def choose(matches: list[SurfaceOwnershipRule], subject: str) -> SurfaceOwnershipRule:
        owners = {rule.facade_module or "platform" for rule in matches}
        if len(owners) != 1:
            raise ValueError(f"Ambiguous ownership for {subject}: competing owners {sorted(owners)}.")
        # A platform hint supplies identity only; prefer the declared capability
        # rule with its bounded state schema when both describe that identity.
        return max(matches, key=lambda rule: len(rule.state_field_names))

    groups = [(group["surface_id"], group["collections"]) for group in normalized_data.get("surfaces") or []]
    groups.append(("", normalized_data.get("shared_collections") or []))
    # Selected facades cannot own residual app data, even without an ownership rule.
    app_modules = {
        surface["surface_id"] for surface in normalized_map["surfaces"]
        if surface.get("owner") == "app" and surface.get("surface_kind") == "module"
        and surface["surface_id"] not in facades
        and not any(_matches_surface(surface, rule) for rule in rules)
    }
    splits: list[tuple[str, dict[str, Any]]] = []
    for group_id, collections in groups:
        for collection in list(collections):
            matches = [rule for rule in rules if _matches_collection(collection, group_id, rule)]
            if not matches:
                continue
            name = str(collection.get("name") or "")
            owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
            rule = choose(matches, f"collection {name!r}")
            scoped = bool(_identifiers([owner_id, group_id]) & _identifiers(rule.surface_ids))
            if scoped and group_id and owner_id != group_id:
                raise ValueError(
                    f"Ambiguous collection {name!r} conflicts with {rule.owner}: "
                    f"group {group_id!r} disagrees with declared owner {owner_id!r}."
                )
            names = _identifiers(rule.collection_names)
            if scoped:
                names |= _identifiers(rule.surface_collection_names)
            declared_fields = collection.get("fields") or []
            fields = _identifiers(field.get("name") for field in declared_fields)
            identity_fields = _identifiers(rule.state_field_names)
            unknown = fields - identity_fields
            state_fields = fields - {"_id", "id", "app_id", "user_id", "created_at", "updated_at"}
            structured_types = {
                name.casefold(): types for name, types in rule.structured_state_field_types.items()
            }
            unbounded_fields = [
                field.get("name") for field in declared_fields
                if str(field.get("name")).casefold() in identity_fields
                if field.get("type") not in {"string", "boolean", "number", "integer", "datetime"}
                and field.get("type") not in structured_types.get(str(field.get("name")).casefold(), [])
            ]
            if unknown and not rule.facade_module and "user_id" in identity_fields and not unbounded_fields:
                candidates = app_modules & {owner_id, group_id} or app_modules
                if len(candidates) != 1:
                    raise ValueError(
                        f"Ambiguous app ownership for collection {name!r} on surface {owner_id!r} "
                        f"after separating {rule.owner}: expected one app module, "
                        f"candidates={sorted(candidates)}, residual fields={sorted(unknown)}."
                    )
                target = next(iter(candidates))
                residual = deepcopy(collection)
                # Globally reserved names still denote platform state after a move.
                if name.casefold() in _identifiers(r for item in rules for r in item.collection_names):
                    residual["name"] = f"{name}_app_data"
                retained = unknown | {"user_id", "app_id"}
                residual["fields"] = [
                    field for field in residual["fields"]
                    if str(field.get("name")).casefold() in retained - {"user_id"}
                ]
                residual["fields"].insert(0, {
                    "name": "user_id", "type": "string", "required": True,
                    "default": None, "enum": None, "nullable": False,
                })
                keys = ["app_id", "user_id"] if "app_id" in fields else ["user_id"]
                indexes = [
                    index for index in residual.get("indexes") or []
                    if index.get("keys") and all(
                        str(key["field"]).split(".", 1)[0].casefold() in retained for key in index["keys"]
                    )
                    and [key["field"] for key in index["keys"]] != keys
                ]
                index_base = f"{residual['name']}_{'_'.join(keys)}_unique"
                index_name = index_base
                index_names = {index.get("name") for index in indexes}
                suffix = 2
                while index_name in index_names:
                    index_name = f"{index_base}_{suffix}"
                    suffix += 1
                indexes.append({
                    "keys": [{"field": key, "order": 1} for key in keys],
                    "unique": True, "sparse": False, "name": index_name,
                })
                residual.update(
                    ownership={"surface_id": target, "surface_kind": "module"},
                    scope="app", search_by="user_id", indexes=indexes,
                    lifecycle={**(residual.get("lifecycle") or {}), "write_mode": "module_action"},
                )
                collections.remove(collection)
                splits.append((target, residual))
                entry = record(owner_id, rule)
                entry.setdefault("split_collections", []).append({
                    "name": name, "target_surface_id": target, "target_collection": residual["name"],
                    "removed_fields": sorted(fields - retained),
                    "retained_fields": [field["name"] for field in residual["fields"]],
                })
                continue
            if name.casefold() not in names or not state_fields or unknown or unbounded_fields:
                actions = _facade_actions(facades[rule.facade_module]) if rule.facade_module else []
                raise ValueError(
                    f"Ambiguous collection {name!r} on surface {owner_id!r} conflicts with {rule.owner}; "
                    f"unrecognized state name/fields={sorted(unknown)}, unbounded fields={unbounded_fields}, "
                    f"state field evidence={sorted(state_fields)}. Separate app-specific data from "
                    f"provider/platform state before saving; canonical owner={rule.facade_module or 'platform'}, "
                    f"declared facade actions={actions}."
                )
            collections.remove(collection)
            record(owner_id, rule, name)

    for target, residual in splits:
        group = next((g for g in normalized_data["surfaces"] if g["surface_id"] == target), None)
        if group is None:
            group = {"surface_id": target, "surface_kind": "module", "collections": []}
            normalized_data["surfaces"].append(group)
            groups.append((target, group["collections"]))
        if any(
            item["name"].casefold() == residual["name"].casefold()
            for group_id, items in groups for item in items
            if target in {group_id, (item.get("ownership") or {}).get("surface_id")}
        ):
            raise ValueError(f"Ambiguous split destination {target!r}/{residual['name']!r}: collection already exists.")
        group["collections"].append(residual)

    surfaces = normalized_map["surfaces"]
    targets: dict[str, str] = {}
    for surface in surfaces:
        surface_id = surface["surface_id"]
        matches = [rule for rule in rules if _matches_surface(surface, rule)]
        if not matches:
            continue
        rule = choose(matches, f"surface {surface_id!r}")
        facade = facades.get(rule.facade_module or "", {})
        entities = _identifiers(rule.entity_names)
        allowed_actions = _identifiers([*rule.action_ids, *_facade_actions(facade)])
        if _identifiers([surface_id]) & _identifiers(rule.surface_ids):
            entities |= _identifiers(rule.surface_entity_names)
            allowed_actions |= _identifiers(rule.surface_action_ids)
        unknown_entities = _identifiers(surface.get("primary_entities")) - entities
        unknown_actions = _identifiers(surface.get("owned_mutations")) - allowed_actions
        remaining = [
            collection["name"] for group_id, collections in groups for collection in collections
            if surface_id in {group_id, (collection.get("ownership") or {}).get("surface_id")}
        ]
        extra_behavior = [key for key in ("events_emitted", "workflow_triggers") if surface.get(key)]
        if facade and _identifiers(surface.get("integrations")) - _identifiers([facade["provider_module"]]):
            extra_behavior.append("integrations")
        if unknown_entities or unknown_actions or remaining or extra_behavior:
            raise ValueError(
                f"Ambiguous surface {surface_id!r} conflicts with {rule.owner}: "
                f"app/unknown entities={sorted(unknown_entities)}, actions={sorted(unknown_actions)}, "
                f"collections={remaining}, behavior={extra_behavior}. Separate app-owned behavior before "
                f"using canonical owner={rule.facade_module or 'platform'}."
            )
        if (
            surface.get("owner") == ("app" if facade else "platform")
            and (not facade or surface_id == rule.facade_module)
            and not surface.get("primary_entities")
            and not (_identifiers(surface.get("owned_mutations")) - _identifiers(_facade_actions(facade)))
        ):
            continue
        corrected = deepcopy(surface)
        corrected.update(owner="app" if facade else "platform", primary_entities=[], owned_mutations=[])
        if facade:
            corrected.update(
                surface_id=rule.facade_module, surface_kind="module",
                source_capability_packs=[facade["pack_id"]],
                owned_mutations=_facade_actions(facade), integrations=[facade["provider_module"]],
            )
        if corrected != surface:
            record(surface_id, rule)
            targets[surface_id] = corrected["surface_id"]
            surface.update(corrected)

    # Several duplicate provider surfaces may map to one already materialized
    # facade. Its approved pages are a union, never replaced by pack defaults.
    merged: dict[str, dict[str, Any]] = {}
    for surface in surfaces:
        surface_id = surface["surface_id"]
        if surface_id in merged:
            if surface_id not in facades:
                raise ValueError(f"Ambiguous duplicate surface {surface_id!r}.")
            merged[surface_id]["owned_pages"] = list(dict.fromkeys([
                *(merged[surface_id].get("owned_pages") or []), *(surface.get("owned_pages") or []),
            ]))
        else:
            merged[surface_id] = surface
    normalized_map["surfaces"] = list(merged.values())
    data_groups: dict[str, dict[str, Any]] = {}
    for group in normalized_data.get("surfaces") or []:
        group["surface_id"] = targets.get(group["surface_id"], group["surface_id"])
        if group["surface_id"] in facades:
            group["surface_kind"] = "module"
        if group["surface_id"] in data_groups:
            data_groups[group["surface_id"]]["collections"].extend(group["collections"])
        else:
            data_groups[group["surface_id"]] = group
    normalized_data["surfaces"] = list(data_groups.values())
    validate_surface_ownership(
        normalized_map, context_variables=context_variables, data_contract=normalized_data,
        include_default_subscription=include_default_subscription,
    )
    surface_map.update(normalized_map)
    data_contract.update(normalized_data)
    return list(records.values())
