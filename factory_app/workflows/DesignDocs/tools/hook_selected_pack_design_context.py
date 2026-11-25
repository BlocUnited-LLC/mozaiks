"""Project verified selected-pack design constraints into the DesignDocs prompt."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import (
    load_catalog,
    update_agent_section,
    workflow_context_path,
)
from factory_app.workflows._shared.pack_selection import available_pack_rows
from mozaiksai.core.runtime.app.module_loader import ModuleDefinition
from mozaiksai.core.runtime.persistence.intent_loader import iter_data_contract_collections
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import compile_data_contract
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    _selected_packs,
    pack_template_module_contracts,
)
from mozaiksai.core.workflow.generator_support.module_role_grants import verified_pack_authorization

_HEADER = "[SELECTED PACK DESIGN CONTRACTS]"
logger = logging.getLogger(__name__)
_OWNERSHIP_FIELDS = (
    "owner", "facade_module", "surface_ids", "entity_names", "action_ids", "collection_names",
    "action_aliases", "state_field_names", "state_readers", "account_state_field_names", "homonym_entity_names",
)


def _facade_projection(facade: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: facade[key] for key in ("module_id", "provider_module") if key in facade}
    pages = facade.get("pages") or []
    result["pages"] = [
        {key: page[key] for key in ("name", "route", "primary_actions") if key in page}
        for page in pages if isinstance(page, Mapping)
    ]
    return result


def inject_pack_selection_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Expose only the verified choice catalog to the small selection stage."""
    if getattr(agent, "name", "") != "DesignPackSelectionAgent":
        return
    context = getattr(agent, "context_variables", None)
    if context is None:
        return
    available = available_pack_rows(context)
    catalog: dict[str, dict[str, Any]] = {pack_id: {
        "pack_id": pack_id, "capability_source": row.get("capability_source"),
        "description": row.get("description") or "",
        "capabilities": [
            {key: capability[key] for key in ("capability_id", "description") if key in capability}
            for capability in row.get("capabilities") or [] if isinstance(capability, Mapping)
        ],
        "providers": [
            {key: integration[key] for key in ("service", "provider") if key in integration}
            for integration in row.get("required_integrations") or [] if isinstance(integration, Mapping)
        ],
    } for pack_id, row in available.items()}
    domains = load_catalog(workflow_context_path("AppGenerator", "domain_catalogs.yaml")).get("domains", {})
    if not isinstance(domains, dict):
        domains = {}
    for domain, data in domains.items():
        recipe = data.get("pack_recipe") if isinstance(data, dict) else None
        if not isinstance(recipe, dict):
            continue
        pack_id, condition = recipe.get("pack_id"), recipe.get("when")
        if not isinstance(pack_id, str) or pack_id not in catalog:
            continue
        if not isinstance(condition, str) or not condition.strip():
            continue
        if recipe.get("requires_monetization") and context.get("monetization_enabled") is not True:
            continue
        catalog[pack_id].setdefault("app_recipes", {})[domain] = condition
    update_agent_section(
        agent, "[AVAILABLE PACK CATALOG]",
        "These verified offerings are available, not selected. Reason from the supplied approved "
        "concept_blueprint, concept_overview and monetization_enabled. Return DesignPackSelection "
        "with the exact reusable pack_ids this app needs; [] explicitly selects none. "
        "Include declared dependencies; repair any missing dependency named in selection feedback. "
        "Use app_recipes when their stated behavior matches the approved requirements. "
        "A domain label alone does not select a pack. Leave missing app-owned pieces to the design. "
        "Capability-family hints are not pack IDs. "
        "Do not select managed billing for a free/non-monetized app or treat an available provider "
        "as owner intent. The tool records a candidate, not approval; an owner separately reviews "
        "the selected sources and permissions. The subsequent design stage receives their exact "
        "contracts and module actions. Unknown IDs, source changes and missing dependencies fail validation.\n"
        + json.dumps(list(catalog.values()), ensure_ascii=False, separators=(",", ":")),
    )


def inject_selected_pack_design_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Expose verified selections and design boundaries, never template implementations."""
    if getattr(agent, "name", "") != "DesignDocsAgent":
        return
    context = getattr(agent, "context_variables", None)
    if context is None:
        return
    if _HEADER in (getattr(agent, "system_message", None) or getattr(agent, "_system_message", "") or ""):
        update_agent_section(agent, _HEADER, "No current selected-pack contracts. The candidate must be selected and validated before using reusable packs.")
    blocks: list[str] = []
    for selected in _selected_packs(context):
        # A bare generated_module ID is app-authored work, not a verified pack.
        if selected.identity is None or selected.config is None:
            continue
        config = selected.config
        pack = config.get("pack") or {}
        lines = [f"Pack: {selected.pack_id} (source: {pack.get('capability_source')})"]
        for capability in config.get("capabilities") or []:
            if isinstance(capability, Mapping) and capability.get("capability_id"):
                target = capability.get("facade_recommended")
                lines.append(f"  capability: {capability['capability_id']}" + (f"; facade: {target}" if target else ""))
        for contract in selected.contracts:
            for boundary in contract.get("runtime_boundaries") or []:
                if isinstance(boundary, Mapping) and boundary.get("rule"):
                    lines.append(f"  boundary: {boundary['rule']}")
            for forbidden in contract.get("forbidden_outputs") or []:
                if isinstance(forbidden, Mapping) and forbidden.get("path_prefix"):
                    reason = f": {forbidden['reason']}" if forbidden.get("reason") else ""
                    lines.append(f"  forbidden output: {forbidden['path_prefix']}{reason}")
            for ownership in contract.get("surface_ownership") or []:
                if isinstance(ownership, Mapping):
                    fields = {key: ownership[key] for key in _OWNERSHIP_FIELDS if key in ownership}
                    lines.append("  surface ownership: " + json.dumps(fields, ensure_ascii=False, separators=(",", ":")))
            for facade in contract.get("facades") or []:
                if isinstance(facade, Mapping):
                    lines.append("  facade: " + json.dumps(_facade_projection(facade), ensure_ascii=False, separators=(",", ":")))
        blocks.append("\n".join(lines))

    if blocks:
        blocks.extend(_pack_collection_context(context))
        # Project action identities from the same verified static manifests used
        # by page binding. This is a compact schema view, never source code.
        for path, source in pack_template_module_contracts(context).items():
            try:
                module = ModuleDefinition.model_validate(yaml.safe_load(source))
                if module.module.id != PurePosixPath(path).parts[1]:
                    raise ValueError("module id does not match its declared path")
            except (ValueError, yaml.YAMLError) as exc:
                logger.warning("Selected pack module contract is unavailable for design (%s)", type(exc).__name__)
                blocks.append(
                    f"Pack module contract unavailable ({PurePosixPath(path).parts[1]}): "
                    "do not invent its actions; the unusable contract requires validation."
                )
                continue
            lines = [f"Pack module actions ({module.module.id}):"]
            for action in module.actions:
                details = {
                    "api_surface": action.api_surface,
                    "permissions": action.permissions,
                    "required_inputs": action.input_schema.get("required") or [],
                    "events": action.emits,
                }
                lines.append(f"  {module.module.id}.{action.id}: " + json.dumps(details, separators=(",", ":")))
            blocks.append("\n".join(lines))
        intro = (
            "These are verified selected-pack contracts. For surfaces that use a pack, preserve its exact "
            "id in source_capability_packs and follow its ownership and runtime boundaries. "
            "Pack permissions describe action requirements; do not invent roles or grants. "
            "Design the app's presentation and approved custom behavior around these foundations; "
            "a pack does not prescribe the app's layout or visual identity."
        )
        update_agent_section(agent, _HEADER, intro + "\n\n" + "\n\n".join(blocks))


def _pack_collection_context(context: Any) -> list[str]:
    """Project collection identities from the same pinned data used by design save."""
    try:
        authorization = verified_pack_authorization(context, selection=detach(context.get("pack_selection")))
        contract = compile_data_contract({}, pack_data_contracts=list(authorization.data_contracts))
        rows = [
            {
                "pack_id": authorization.module_packs[owner],
                "module_id": owner,
                "collection": collection["name"],
                "entity": collection["entity"],
                "tenancy": collection["tenancy"],
            }
            for owner, kind, collection in iter_data_contract_collections(contract)
            if kind == "module" and owner in authorization.module_packs
        ]
    except (ValueError, yaml.YAMLError) as exc:
        logger.warning("Selected pack data contract is unavailable for design (%s)", type(exc).__name__)
        return ["Selected pack collection ownership is unavailable. Resolve the pack contract before proposing grants."]
    if not rows:
        return []
    lines = [
        "Design save adds these collections. Bind app pages to their module actions, even when the UI uses a "
        'different name for the records. If packs supply all data with default policies, emit "data_contract": '
        '"selected_pack_data". Otherwise declare only additional app-owned records and app policies. Use the shown owner and '
        "its permission-bearing actions when proposing grants for approved roles."
    ]
    for row in rows:
        lines.append("Pack-owned collection: " + json.dumps(row, separators=(",", ":")))
    return ["\n".join(lines)]
