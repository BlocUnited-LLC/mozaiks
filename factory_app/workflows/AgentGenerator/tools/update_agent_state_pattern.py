import logging
from typing import Any

from factory_app.workflows.AgentGenerator.patternbook import (
    build_pattern_lookup_maps,
    get_pattern_by_name,
    render_pattern_example,
    render_pattern_guidance,
    render_patternbook_summary,
)
from mozaiksai.core.workflow.agents.factory import _compose_prompt_sections

logger = logging.getLogger(__name__)


PATTERN_ID_BY_NAME, PATTERN_NAME_BY_ID, PATTERN_DISPLAY_NAME_BY_ID = build_pattern_lookup_maps()

PATTERN_GUIDANCE_PLACEHOLDER = "{{PATTERN_GUIDANCE_AND_EXAMPLES}}"
PATTERN_GUIDANCE_SECTION_IDS = {"pattern_guidance_and_examples"}
PATTERN_GUIDANCE_SECTION_HEADING = "[PATTERN GUIDANCE AND EXAMPLES]"


def _load_pattern_guidance_text() -> str:
    """Load canonical pattern guidance from the AG2 Network patternbook."""

    return render_patternbook_summary()


def _load_pattern_example_str(pattern_id: int, section_key: str = "WorkflowStrategy") -> str | None:
    """Load a pattern example from the AG2 Network patternbook."""

    return render_pattern_example(pattern_id, section_key)


def _apply_pattern_guidance(agent, guidance: str) -> bool:
    """Insert pattern guidance into the agent's PATTERN GUIDANCE AND EXAMPLES section."""
    try:
        normalized = (guidance or "").strip()
        if not normalized:
            logger.debug(f"No guidance content supplied for {getattr(agent, 'name', 'unknown')}" )
            return False

        sections = getattr(agent, "_mozaiks_prompt_sections", None)
        placeholder = PATTERN_GUIDANCE_PLACEHOLDER
        section_updated = False

        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                section_id = section.get("id")
                heading = section.get("heading")
                if section_id in PATTERN_GUIDANCE_SECTION_IDS or heading == PATTERN_GUIDANCE_SECTION_HEADING:
                    content = section.get("content") or ""
                    if placeholder in content:
                        section["content"] = content.replace(placeholder, normalized)
                    else:
                        section["content"] = normalized
                    section_updated = True

            if section_updated:
                try:
                    recomposed = _compose_prompt_sections(sections)
                    if hasattr(agent, "_system_message"):
                        agent._system_message = recomposed
                    elif hasattr(agent, "update_system_message") and callable(agent.update_system_message):
                        agent.update_system_message(recomposed)
                    agent._mozaiks_prompt_sections = sections
                    agent._mozaiks_base_system_message = recomposed
                    logger.debug(f"Applied pattern guidance via prompt section for {getattr(agent, 'name', 'unknown')}")
                    return True
                except Exception as compose_err:
                    logger.error(
                        f"Failed to recompose prompt sections after inserting pattern guidance for {getattr(agent, 'name', 'unknown')}: {compose_err}",
                        exc_info=True,
                    )

        current_message = getattr(agent, "_system_message", "") or ""
        if placeholder and placeholder in current_message:
            updated = current_message.replace(placeholder, normalized)
            if hasattr(agent, "_system_message"):
                agent._system_message = updated
            elif hasattr(agent, "update_system_message") and callable(agent.update_system_message):
                agent.update_system_message(updated)
            agent._mozaiks_base_system_message = updated
            logger.debug(f"Applied pattern guidance via string replacement for {getattr(agent, 'name', 'unknown')}")
            return True

        if hasattr(agent, "_system_message"):
            separator = "\n\n" if current_message else ""
            agent._system_message = f"{current_message}{separator}{normalized}".strip()
            agent._mozaiks_base_system_message = agent._system_message
            logger.debug(f"Appended pattern guidance to system_message for {getattr(agent, 'name', 'unknown')} (placeholder missing)" )
            return True

        logger.warning(f"Unable to apply pattern guidance for {getattr(agent, 'name', 'unknown')}: no accessible system message")
        return False

    except Exception as err:
        logger.error(f"Unhandled error applying pattern guidance for {getattr(agent, 'name', 'unknown')}: {err}", exc_info=True)
        return False


def inject_pattern_selection_guidance(agent, messages: list[dict[str, Any]]) -> None:
    """Inject the AG2 Network patternbook into PatternAgent."""

    try:
        guidance = render_patternbook_summary()
        if _apply_pattern_guidance(agent, guidance):
            logger.info(f"Injected AG2 Network patternbook into {getattr(agent, 'name', 'unknown')}")
    except Exception as err:
        logger.error(f"Error in inject_pattern_selection_guidance: {err}", exc_info=True)


def _get_pattern_from_context(agent) -> dict[str, Any]:
    """Extract the active pattern from cached PatternSelection.

    PatternSelection is produced by PatternAgent and cached via the `pattern_selection`
    tool in `context_variables` under the key `PatternSelection`.

    Returns the pattern info for the first primary workflow (or first workflow in the list).

    Returns a minimal dict: {"id": int, "name": str, "display_name": str}
    """
    try:
        if not hasattr(agent, "_context_variables") and not hasattr(agent, "context_variables"):
            logger.debug("Agent has no context_variables attribute")
            return {}

        context = getattr(agent, "context_variables", None) or getattr(agent, "_context_variables", None)
        if context is None:
            logger.debug("Agent context_variables is None")
            return {}

        if hasattr(context, "data"):
            data = context.data
        elif isinstance(context, dict):
            data = context
        else:
            logger.debug("Unexpected context type: %s", type(context))
            return {}

        pattern_selection = data.get("PatternSelection", {})
        if not isinstance(pattern_selection, dict) or not pattern_selection:
            logger.debug("No PatternSelection found in context")
            return {}

        workflows = pattern_selection.get("workflows")
        if not isinstance(workflows, list) or not workflows:
            logger.debug("PatternSelection missing workflows list")
            return {}

        selected_workflow: dict[str, Any] | None = None
        for wf in workflows:
            if isinstance(wf, dict) and wf.get("role") == "primary":
                selected_workflow = wf
                break
        if selected_workflow is None and workflows and isinstance(workflows[0], dict):
            selected_workflow = workflows[0]
        if selected_workflow is None:
            return {}

        raw_id = selected_workflow.get("pattern_id")
        raw_name = selected_workflow.get("pattern_name")

        pattern_id: int | None = raw_id if isinstance(raw_id, int) else None
        if pattern_id is None and isinstance(raw_name, str):
            pattern_record = get_pattern_by_name(raw_name)
            pattern_id = int(pattern_record["id"]) if pattern_record else None

        if pattern_id is None or pattern_id not in PATTERN_NAME_BY_ID:
            logger.warning(
                "Unknown pattern selection provided: id=%r name=%r (workflow=%r)",
                raw_id,
                raw_name,
                selected_workflow.get("name"),
            )
            return {}

        pattern_name = PATTERN_NAME_BY_ID[pattern_id]
        display_name = PATTERN_DISPLAY_NAME_BY_ID.get(pattern_id) or (
            raw_name if isinstance(raw_name, str) and raw_name.strip() else pattern_name.replace("_", " ").title()
        )

        result = {"id": pattern_id, "name": pattern_name, "display_name": display_name}
        logger.info(f"✓ Pattern resolved for {agent.name}: id={pattern_id}, name={pattern_name}")
        return result

    except Exception as e:
        logger.error(f"Error extracting pattern from context: {e}", exc_info=True)
        return {}


def inject_workflow_bundle_builder_guidance(agent, messages: list[dict[str, Any]]) -> None:
    """
    AG2 update_agent_state hook for WorkflowBundleBuilderAgent task batch workers.

    Reads the worker's assigned WorkflowInPack from `current_task` (seeded into
    context_variables by the task batch executor) and injects the full
    pattern-specific generation guidance into the PATTERN_GUIDANCE_AND_EXAMPLES section.

    Falls back to PatternSelection context (single-workflow case) when current_task
    is unavailable.
    """
    try:
        context = getattr(agent, "context_variables", None) or getattr(agent, "_context_variables", None)
        data: dict[str, Any] = {}
        if context is not None:
            data = context.data if hasattr(context, "data") else (context if isinstance(context, dict) else {})

        current_task = data.get("current_task") or {}
        pattern_id: int | None = current_task.get("pattern_id")
        if not isinstance(pattern_id, int):
            pattern_id = None

        if pattern_id is None:
            # Single-workflow fallback: read from PatternSelection context
            pattern = _get_pattern_from_context(agent)
            pattern_id = pattern.get("id") if pattern else None

        if pattern_id is None or pattern_id not in PATTERN_NAME_BY_ID:
            logger.warning(
                f"No resolvable pattern_id for {getattr(agent, 'name', 'unknown')}, skipping guidance injection"
            )
            return

        guidance = render_pattern_guidance(pattern_id)
        if not guidance:
            logger.warning(f"render_pattern_guidance returned empty for pattern_id={pattern_id}")
            return

        pattern_display_name = PATTERN_DISPLAY_NAME_BY_ID.get(pattern_id, PATTERN_NAME_BY_ID[pattern_id])
        workflow_name = current_task.get("name", "")
        header = f"[PATTERN GUIDANCE: {pattern_display_name}]"
        if workflow_name:
            header += f" — Workflow: {workflow_name}"

        full_guidance = f"{header}\n\n{guidance}"

        if _apply_pattern_guidance(agent, full_guidance):
            logger.info(
                f"✓ Injected {pattern_display_name} bundle builder guidance into "
                f"{getattr(agent, 'name', 'unknown')} (workflow={workflow_name!r})"
            )
        else:
            logger.warning(f"Pattern guidance injection failed for {getattr(agent, 'name', 'unknown')}")

    except Exception as err:
        logger.error(f"Error in inject_workflow_bundle_builder_guidance: {err}", exc_info=True)
