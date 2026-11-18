"""Resolve an explicit design candidate without approving it."""
from typing import Annotated, Any

from factory_app.workflows._shared.pack_selection import pin_pack_choices
from factory_app.workflows._shared.platform.build_target import require_build_binding
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    ManagedCapabilityTemplateError,
)


def select_capability_packs(
    pack_ids: Annotated[list[str], "Canonical IDs from the available pack catalog; [] explicitly selects none"],
    context_variables: Any = None,
) -> dict[str, Any]:
    require_build_binding(context_variables)
    if context_variables.get("workflow_name") != "DesignDocs":
        raise ValueError("Pack selection belongs to the DesignDocs workflow")
    # Clear a prior candidate first: a refused replacement cannot retain old pins.
    context_variables.set("pack_selection", None)
    try:
        choices = pin_pack_choices(context_variables, pack_ids)
    except (ValueError, ManagedCapabilityTemplateError) as exc:
        return {"status": "revise", "error": str(exc)}
    context_variables.set("pack_selection", choices)
    return {"status": "selected", "pack_selection": choices,
        "review_required": bool(choices), "approved": False}
