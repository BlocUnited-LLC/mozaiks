from __future__ import annotations

import logging
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import update_agent_section
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index_from_context,
    workflow_names_from_context,
)
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
            "\n- Table actions with requires_selection need selection: single or multi; the default none makes those actions unreachable."
            "\n- Do not render editable identifier, ownership, or server timestamp fields. For update/delete payloads bind identifiers with {selected_row.<id_field>}."
            "\n- An explicit submit payload must include each editable field using {form.<field_name>}; null payload sends the form values."
            "\n- submit/delete actions require their own data_source: {module_id, action_id} selected from accepted module contracts. Code renders href; never emit endpoint URLs."
            "\n- Use separate create/edit forms or modals when the module declares different create/update actions; each has its own data_source. Create forms omit selected-row prefill; edit forms use initial_values_key: selected_row."
            "\n- A module delete action uses the runtime POST command endpoint, not a REST DELETE route."
            "\n- Choose data_key and metric value_key/detail_key/trend_key from the bound action's output_schema. Table columns use the returned array's declared item property keys."
            "\n- Code fills items/total only for a declared canonical list envelope; never invent or rename KPI fields based on their labels."
            "\n- Code writes and logs the bindings the contracts determine (cleared undeclared detail/trend keys, a metric id that names a returned field, a create/edit modal replacing a workflow button with no generated workflow, an Edit modal for a gated update action on a listed collection). Open choices fail with the valid options for every page at once."
            "\n- Every user-facing action with an entitlement_gate must be reachable from a page action or data binding. Give paid create/edit actions working forms and submit bindings."
            "\n- workflow actions may reference only workflows present in the supplied bundle artifacts. Do not invent a workflow for CRUD; use the declared module action."
            "\n- Custom React pages use these same shipped component APIs. DataTable columns do not execute render/cell callbacks. Use actions with selection and onAction(actionId, selectedRows), or compose a semantic list with shared Buttons for per-item controls. Never hide required actions inside unsupported column props."
            "\n- React state updater functions must stay pure: do not call moduleAction, start/stop timers, or set other state inside setState(previous => ...). React may call an updater more than once. Own asynchronous work in explicit event handlers or effects with cleanup."
            "\n- If an interaction must persist a result exactly once, carry a stable operation identity through its declared action and require backend idempotency; component state alone cannot guarantee this across retries/reloads. Surface a failed save with a retry action instead of swallowing it. If the approved action contract cannot express the requirement, request repair of its owning contract."
        )
        update_agent_section(agent, _SURFACE_HEADER, format_ui_surface_taxonomy_guidance())
        update_agent_section(agent, _HEADER, body)
        context = getattr(agent, "context_variables", None)
        if context is not None:
            update_agent_section(agent, "[APPROVED PAGE ACTION INVENTORY]", (
                "These are the accepted prerequisite module contracts, restricted to approved HTTP actions. "
                "Choose data_source module_id/action_id from this inventory. Each output_schema names "
                "the exact response fields, including array item properties for table columns. "
                "A label is presentation only: never transform a returned field name to match it. "
                "Custom summaries are declared custom_reads; a list response cannot supply KPI fields. "
                "If an action or output field is missing, repair its owning contract instead of inventing it.\n"
                + yaml.safe_dump(module_action_index_from_context(context), sort_keys=True)
            ))
            update_agent_section(agent, "[AVAILABLE PAGE WORKFLOWS]", (
                "Only these artifact-backed workflows may be launched by page actions. "
                "An empty list means every create/edit interaction must use a typed module action.\n"
                + yaml.safe_dump(sorted(workflow_names_from_context(context)))
            ))
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

