"""Project verified selected-pack design constraints into the DesignDocs prompt."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import update_agent_section
from mozaiksai.core.runtime.app.module_loader import ModuleDefinition
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    _selected_packs,
    pack_template_module_contracts,
)

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


def inject_selected_pack_design_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Expose verified selections and design boundaries, never template implementations."""
    if getattr(agent, "name", "") != "DesignDocsAgent":
        return
    context = getattr(agent, "context_variables", None)
    # DesignDocs uses the current trusted launch selection, not a stale
    # AppBuildPlan fallback that the downstream assembly helper also supports.
    if context is None or not context.get("capability_packs"):
        return

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
