"""Enforce declared platform and selected-pack ownership before design approval."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from factory_app.workflows._shared.hook_utils import workflow_context_path
from mozaiksai.core.runtime.app.auth_contract import validate_app_auth_contract
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import managed_pack_contracts

_MODULE_ACTION_ENDPOINT = re.compile(r"^/api/modules/([A-Za-z0-9_.-]+)/[A-Za-z0-9_.-]+$")


class SurfaceOwnershipRule(BaseModel):
    """Exact contract identifiers, never classification of free-form prose."""

    model_config = ConfigDict(extra="forbid", strict=True)

    owner: str = Field(min_length=1)
    facade_module: str | None = None
    # The platform capability whose own pages a matching surface duplicates.
    # `authentication` pages are served at the generated auth contract's routes.
    platform_capability: Literal["authentication"] | None = None
    surface_ids: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    collection_names: list[str] = Field(default_factory=list)
    surface_collection_names: list[str] = Field(default_factory=list)
    surface_entity_names: list[str] = Field(default_factory=list)
    surface_action_ids: list[str] = Field(default_factory=list)
    state_field_names: list[str] = Field(default_factory=list)
    identity_evidence_fields: list[str] = Field(default_factory=list)
    structured_state_field_types: dict[str, list[Literal["object", "array"]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def declared_structured_fields(self) -> Self:
        if _identifiers(self.structured_state_field_types) - _identifiers(self.state_field_names):
            raise ValueError("structured_state_field_types must reference declared state_field_names")
        if _identifiers(self.identity_evidence_fields) - _identifiers(self.state_field_names):
            raise ValueError("identity_evidence_fields must reference declared state_field_names")
        if self.platform_capability and self.facade_module:
            raise ValueError("platform_capability describes platform ownership, not a managed facade")
        return self


def auth_contract_login_route() -> str:
    """The route where the generated auth contract serves sign-in.

    Generated apps receive this contract from the same template
    (render_auth_scaffold), so a design page duplicating sign-in is replaced by a
    reference to this route rather than by an app-built page.
    """
    template = workflow_context_path("webapp_builder", "templates", "config", "auth.yaml")
    config = yaml.safe_load(template.read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
    return validate_app_auth_contract(config).routes.login


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


def _identifiers(values: Any) -> set[str]:
    return {str(value).strip().casefold() for value in values or []}


def _ownership_rules(
    context_variables: Any, *, include_default_subscription: bool,
) -> tuple[list[SurfaceOwnershipRule], dict[str, dict[str, Any]]]:
    catalog = yaml.safe_load(
        workflow_context_path("AppGenerator", "capability_routing.yaml").read_text(encoding="utf-8"),
    )
    declarations = list(catalog["layers"]["runtime_provided"]["surface_ownership"])
    contracts = managed_pack_contracts(context_variables)
    default = default_subscription_contract(context_variables) if include_default_subscription else None
    if default and not any(contract.get("contract_id") == default["contract_id"] for contract in contracts):
        contracts.append(default)
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
        or _identifiers([
            *(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or []),
        ]) & _identifiers(rule.action_ids)
    )


def _matches_collection(collection: dict[str, Any], group_id: str, rule: SurfaceOwnershipRule) -> bool:
    owner_id = str((collection.get("ownership") or {}).get("surface_id") or group_id)
    return bool(
        _identifiers([collection.get("name")]) & _identifiers(rule.collection_names)
        or _identifiers([owner_id, group_id]) & _identifiers(rule.surface_ids)
        or rule.facade_module and rule.facade_module in {owner_id, group_id}
        or (
            _identifiers([collection.get("name")]) & _identifiers(rule.surface_collection_names)
            and _identifiers(field.get("name") for field in collection.get("fields") or [])
            & _identifiers(rule.identity_evidence_fields)
        )
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
            actions = _identifiers([
                *(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or []),
            ])
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


def _typed_section_bindings(config_hint: Any) -> list[str]:
    """Module ids a section binds through typed references in its config hint.

    Only the two typed shapes downstream planning understands count:
    ``data_source: {module_id, action_id}`` and a canonical module action URL.
    Column names or labels are prose and never decide ownership.
    """
    if not isinstance(config_hint, str) or not config_hint.strip():
        return []
    try:
        config = json.loads(config_hint)
    except ValueError:
        return []
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            source = node.get("data_source")
            if isinstance(source, dict) and source.get("module_id"):
                found.append(str(source["module_id"]))
            endpoint = node.get("api_endpoint")
            match = _MODULE_ACTION_ENDPOINT.match(endpoint) if isinstance(endpoint, str) else None
            if match:
                found.append(match.group(1))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    return list(dict.fromkeys(found))


def _remove_platform_pages(
    surface: dict[str, Any],
    surfaces: list[dict[str, Any]],
    spec: dict[str, Any],
    *,
    owner: str,
) -> list[dict[str, Any]]:
    """Drop the pages a platform surface owns; the platform already serves them.

    A page is only removable when nothing app-owned depends on it: no other
    surface lists it, and no section binds an app module through a typed
    reference. Either one is a design decision, named for the retry.
    """
    surface_id = str(surface["surface_id"])
    names = _identifiers(surface.get("owned_pages"))
    if not names:
        return []
    app_modules = {
        str(other["surface_id"]) for other in surfaces
        if other is not surface and other.get("owner") == "app" and other.get("surface_kind") == "module"
    }
    removed: list[dict[str, Any]] = []
    for page in list(spec.get("pages") or []):
        name = str(page.get("name") or "")
        if not _identifiers([name]) & names:
            continue
        co_owners = [
            str(other["surface_id"]) for other in surfaces
            if other is not surface and _identifiers([name]) & _identifiers(other.get("owned_pages"))
        ]
        if co_owners:
            raise ValueError(
                f"Page {name!r} ({page.get('route')}) is owned by {surface_id!r}, which normalizes to "
                f"{owner}, and also by {co_owners}. The platform serves sign-in itself, so the app builds "
                f"no page for {surface_id!r}: remove {name!r} from owned_pages of {co_owners} and drop the "
                f"page, or move its app-owned sections to a page owned only by {co_owners[0]!r} and remove "
                f"{name!r} from {surface_id!r}.owned_pages."
            )
        bound = [
            (str(section.get("id")), module)
            for section in page.get("sections") or []
            for module in _typed_section_bindings(section.get("config_hint")) if module in app_modules
        ]
        if bound:
            raise ValueError(
                f"Page {name!r} ({page.get('route')}) is owned by {surface_id!r}, which normalizes to "
                f"{owner}, but sections {sorted({section for section, _ in bound})} bind app-owned modules "
                f"{sorted({module for _, module in bound})}. The platform serves sign-in itself: move those "
                f"sections to a page owned by the app module they bind, or drop the binding, then remove "
                f"{name!r} from {surface_id!r}.owned_pages."
            )
        spec["pages"].remove(page)
        removed.append({"name": name, "route": page.get("route")})
    if removed and not spec.get("pages"):
        raise ValueError(
            f"Removing the platform sign-in page(s) {[page['name'] for page in removed]} owned by "
            f"{surface_id!r} leaves no approved pages. Design the app's own pages under app-owned surfaces."
        )
    return removed


def _rewrite_routes(node: Any, routes: dict[str, str], login_route: str, hits: list[str]) -> Any:
    if isinstance(node, str) and node in routes:
        hits.append(node)
        return login_route
    if isinstance(node, dict):
        return {key: _rewrite_routes(value, routes, login_route, hits) for key, value in node.items()}
    if isinstance(node, list):
        return [_rewrite_routes(value, routes, login_route, hits) for value in node]
    return node


def _redirect_navigation(spec: dict[str, Any], routes: dict[str, str], login_route: str) -> dict[str, list[dict[str, Any]]]:
    """Point typed references to a removed page's route at the auth contract login route."""
    redirected: dict[str, list[dict[str, Any]]] = {}
    for page in spec.get("pages") or []:
        for section in page.get("sections") or []:
            hint = section.get("config_hint")
            if not isinstance(hint, str) or not hint.strip():
                continue
            try:
                config = json.loads(hint)
            except ValueError:
                continue
            hits: list[str] = []
            rewritten = _rewrite_routes(config, routes, login_route, hits)
            if hits:
                section["config_hint"] = json.dumps(rewritten)
                for route in dict.fromkeys(hits):
                    redirected.setdefault(routes[route], []).append({
                        "page": page.get("name"), "section": section.get("id"), "from": route, "to": login_route,
                    })
    return redirected


def normalize_surface_ownership(
    surface_map: dict[str, Any],
    *,
    context_variables: Any,
    data_contract: dict[str, Any],
    experience_spec: dict[str, Any] | None = None,
    include_default_subscription: bool = False,
) -> list[dict[str, Any]]:
    """Apply only contract-determined corrections, atomically, before design approval.

    A reserved surface name proves the owner, not the meaning of every field it
    contains. Residual auth data moves only to one determined existing app module;
    unresolved owners or mixed app behavior require a design decision.

    Events on a surface that normalizes to platform or provider ownership are that
    owner's lifecycle events, which the app cannot emit: they are removed and
    recorded, unless another surface's workflow_triggers consume one. Pages owned
    by a platform authentication surface duplicate the sign-in the auth contract
    already serves: with ``experience_spec`` supplied they are removed from the
    approved inventory and typed navigation to them points at the login route.
    """
    rules, facades = _ownership_rules(context_variables, include_default_subscription=include_default_subscription)
    normalized_map, normalized_data = deepcopy(surface_map), deepcopy(data_contract)
    normalized_spec = deepcopy(experience_spec) if experience_spec is not None else None
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
            identity_evidence = _identifiers(
                field.get("name") for field in collection.get("fields") or []
            ) & _identifiers(rule.identity_evidence_fields)
            if scoped or identity_evidence:
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
                    tenancy="per_user", owner_field="user_id",
                    entity=(residual["name"] if residual["name"].endswith("_app_data") else f"{residual['name']}_app_data"),
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
        target_surface = next(surface for surface in normalized_map["surfaces"] if surface["surface_id"] == target)
        target_surface["primary_entities"] = list(dict.fromkeys([
            *(target_surface.get("primary_entities") or []), residual["entity"],
        ]))

    surfaces = normalized_map["surfaces"]
    targets: dict[str, str] = {}
    removed_routes: dict[str, str] = {}
    for surface in surfaces:
        surface_id = surface["surface_id"]
        matches = [rule for rule in rules if _matches_surface(surface, rule)]
        if not matches:
            continue
        rule = choose(matches, f"surface {surface_id!r}")
        facade = facades.get(rule.facade_module or "", {})
        owner = rule.facade_module or "platform"
        facade_actions = _facade_actions(facade)
        entities = _identifiers(rule.entity_names)
        allowed_actions = _identifiers([*rule.action_ids, *facade_actions])
        if _identifiers([surface_id]) & _identifiers(rule.surface_ids):
            entities |= _identifiers(rule.surface_entity_names)
            allowed_actions |= _identifiers(rule.surface_action_ids)
        unknown_entities = [
            str(entity) for entity in surface.get("primary_entities") or []
            if str(entity).strip().casefold() not in entities
        ]
        unknown_actions = [
            str(action)
            for action in [*(surface.get("owned_mutations") or []), *(surface.get("custom_reads") or [])]
            if str(action).strip().casefold() not in allowed_actions
        ]
        remaining = [
            collection["name"] for group_id, collections in groups for collection in collections
            if surface_id in {group_id, (collection.get("ownership") or {}).get("surface_id")}
        ]
        # Each item names the app-owned behavior to split out and where it goes.
        split_out: list[str] = []
        if unknown_entities:
            split_out.append(f"move entities {unknown_entities} to an app-owned module surface")
        if unknown_actions:
            split_out.append(f"move actions {unknown_actions} to an app-owned module surface")
        if remaining:
            split_out.append(
                f"declare collections {remaining} under their app-owned module via ownership.surface_id"
            )
        triggers = [str(trigger) for trigger in surface.get("workflow_triggers") or []]
        if triggers:
            split_out.append(
                f"declare workflow_triggers {triggers} on the app-owned surface whose action starts them"
            )
        foreign_integrations = sorted(
            _identifiers(surface.get("integrations")) - _identifiers([facade["provider_module"]])
        ) if facade else []
        if foreign_integrations:
            split_out.append(
                f"move integrations {foreign_integrations} to an app-owned surface; "
                f"{rule.facade_module} integrates only {facade['provider_module']}"
            )
        if split_out:
            keep = (
                f"owner=app, surface_id={rule.facade_module}, owned_mutations within {sorted(facade_actions)}"
                if facade else "owner=platform"
            )
            raise ValueError(
                f"Ambiguous surface {surface_id!r} conflicts with {rule.owner}. Split out the app-owned "
                f"behavior: {'; '.join(split_out)}. Then keep {surface_id!r} as a {owner} reference "
                f"({keep}) with no entities, collections, or triggers."
            )
        # Events declared here are the owner's lifecycle events; the app cannot
        # emit them. Only a declared consumer turns removal into a design decision.
        events = [str(event) for event in surface.get("events_emitted") or []]
        consumers = [
            (str(other["surface_id"]), sorted(_identifiers(other.get("workflow_triggers")) & _identifiers(events)))
            for other in surfaces if other is not surface
        ]
        consumers = [(other_id, shared) for other_id, shared in consumers if shared]
        if consumers:
            raise ValueError(
                f"Events {events} on {surface_id!r} are {rule.owner} lifecycle events the app cannot emit, "
                f"but {'; '.join(f'surface {other!r} lists {shared} in workflow_triggers' for other, shared in consumers)}. "
                f"Start that workflow from an app-owned surface's action or domain event, or remove the "
                f"trigger; then {surface_id!r} normalizes to a {owner} reference without events."
            )
        removes_pages = rule.platform_capability == "authentication" and not facade and normalized_spec is not None
        if (
            surface.get("owner") == ("app" if facade else "platform")
            and (not facade or surface_id == rule.facade_module)
            and not surface.get("primary_entities")
            and not (_identifiers(surface.get("owned_mutations")) - _identifiers(facade_actions))
            and not (_identifiers(surface.get("custom_reads")) - _identifiers(facade_actions))
            and not events
            and not (removes_pages and surface.get("owned_pages"))
        ):
            continue
        removed_pages: list[dict[str, Any]] = []
        if removes_pages and normalized_spec is not None:
            removed_pages = _remove_platform_pages(surface, surfaces, normalized_spec, owner=rule.owner)
        corrected = deepcopy(surface)
        corrected.update(
            owner="app" if facade else "platform", primary_entities=[], owned_mutations=[], custom_reads=[],
            events_emitted=[],
        )
        if removes_pages:
            corrected["owned_pages"] = []
        if facade:
            corrected.update(
                surface_id=rule.facade_module, surface_kind="module",
                source_capability_packs=[facade["pack_id"]],
                owned_mutations=facade_actions, integrations=[facade["provider_module"]],
            )
        if corrected != surface:
            entry = record(surface_id, rule)
            if events:
                entry["removed_events"] = events
            if removed_pages:
                entry["removed_pages"] = removed_pages
                removed_routes.update({str(page["route"]): surface_id for page in removed_pages})
            targets[surface_id] = corrected["surface_id"]
            surface.update(corrected)
    if removed_routes:
        login_route = auth_contract_login_route()
        for surface_id, entries in _redirect_navigation(normalized_spec or {}, removed_routes, login_route).items():
            records[(surface_id, "platform")]["redirected_navigation"] = entries

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
    if experience_spec is not None and normalized_spec is not None:
        experience_spec.update(normalized_spec)
    return list(records.values())
