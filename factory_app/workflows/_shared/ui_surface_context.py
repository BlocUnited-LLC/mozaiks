"""Project canonical UI surface guidance before design and page planning."""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from mozaiksai.core.workflow.ui_surface_taxonomy import format_ui_surface_taxonomy_guidance

_HEADER = "[UI SURFACE TAXONOMY]"


def inject_ui_surface_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Reuse the taxonomy owner without duplicating history or writing workflow state.

    Page implementation already receives this section from its primitive-catalog
    hook. These earlier producers need the same decision rule before they commit
    an ExperienceSpec or AppBuildPlan, not only when custom code is generated.
    """
    if getattr(agent, "name", "") not in {"DesignDocsAgent", "AppPlanAgent"}:
        return
    update_agent_section(agent, _HEADER, format_ui_surface_taxonomy_guidance())


__all__ = ["inject_ui_surface_context"]
