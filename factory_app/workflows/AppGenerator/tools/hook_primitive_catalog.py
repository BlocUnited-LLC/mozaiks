from __future__ import annotations

import logging
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from mozaiksai.core.workflow.ui_primitives import format_generated_page_ui_primitive_guidance
from mozaiksai.core.workflow.ui_surface_taxonomy import format_ui_surface_taxonomy_guidance

logger = logging.getLogger(__name__)

_HEADER = "[SHIPPED PAGE PRIMITIVES]"
_SURFACE_HEADER = "[UI SURFACE TAXONOMY]"


def inject_primitive_catalog(agent: Any, messages: list[dict[str, Any]]) -> None:
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppSchemaAgent":
        return

    try:
        body = (
            f"{format_generated_page_ui_primitive_guidance()}\n\n"
            "Rules:\n"
            "- Do not emit primitive names outside this shipped catalog.\n"
            "- Validate both top-level page sections and nested Grid child primitives against this catalog.\n"
            "- If the page needs a richer UX, compose it from these shipped primitives instead of inventing a new primitive."
            "\n- The current approved app_build_plan overrides older design docs: preserve app name, field optionality, and requested scope."
            "\n- ui.modal.open/close actions set modal_id, never modalId. The id must name a Modal on the same page."
            "\n- Table actions pass the selected record into the modal as selected_row. Edit forms declare initial_values_key: selected_row."
            "\n- Do not render editable identifier, ownership, or server timestamp fields. For update/delete payloads bind identifiers with {selected_row.<id_field>}."
            "\n- An explicit submit payload must include each editable field using {form.<field_name>}; null payload sends the form values."
            "\n- submit actions require href: one fixed /api/modules/{module_id}/{action_id} on the action itself. A payload or table endpoint never supplies a missing href."
            "\n- Use separate create/edit forms or modals when the module declares different create/update actions; never a null or conditional href for combined mode. Create forms omit selected-row prefill; edit forms use initial_values_key: selected_row."
            "\n- A module delete action uses the runtime POST command endpoint, not a REST DELETE route."
            "\n- data_key must exactly match the response array path; metric value_key must match the response field."
        )
        update_agent_section(agent, _SURFACE_HEADER, format_ui_surface_taxonomy_guidance())
        update_agent_section(agent, _HEADER, body)
        logger.info("[%s] Injected shipped page primitive catalog", agent_name)
    except Exception as exc:
        logger.error("[%s] Failed to inject primitive catalog: %s", agent_name, exc)
        update_agent_section(
            agent,
            _HEADER,
            "WARNING: Shipped primitive catalog could not be loaded. "
            "Do NOT emit any primitive names - escalate to the user before generating UI.",
        )


__all__ = ["inject_primitive_catalog"]

