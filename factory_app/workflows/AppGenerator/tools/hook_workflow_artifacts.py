import logging
from typing import Any, Dict, List

from .code_context.tools import index_workflow_artifacts

logger = logging.getLogger(__name__)


def sync_workflow_artifacts(agent, messages: List[Dict[str, Any]]) -> None:
    """
    Update agent state hook to persist AgentGenerator workflow artifacts into
    the code context system (no direct system-message injection).
    """
    try:
        context_variables = getattr(agent, "context_variables", {})
        if not context_variables:
            return

        app_id = context_variables.get("app_id")
        workspace_id = context_variables.get("workspace_id", app_id)
        workflow_name = context_variables.get("workflow_name") or "Workflow"
        workflow_config = context_variables.get("agent_workflow_config")
        if isinstance(workflow_config, dict):
            wf_from_config = workflow_config.get("workflow_name")
            if isinstance(wf_from_config, str) and wf_from_config.strip():
                workflow_name = wf_from_config.strip()

        if not app_id:
            return

        already_indexed = False
        if hasattr(context_variables, "get"):
            try:
                already_indexed = bool(context_variables.get("workflow_artifacts_indexed"))
            except Exception:
                already_indexed = False
        elif isinstance(context_variables, dict):
            already_indexed = bool(context_variables.get("workflow_artifacts_indexed"))

        if already_indexed:
            return

        artifacts = {
            "workflow_config": workflow_config,
            "workflow_tools": context_variables.get("agent_workflow_tools"),
            "workflow_structured_outputs": context_variables.get("agent_workflow_structured_outputs"),
            "workflow_context_variables": context_variables.get("agent_workflow_context_variables"),
            "workflow_ui_components": context_variables.get("agent_workflow_ui_components"),
            "workflow_db_intent": context_variables.get("agent_workflow_db_intent"),
        }

        if not any(artifacts.values()):
            return

        result = index_workflow_artifacts(
            app_id=str(app_id),
            workspace_id=str(workspace_id),
            workflow_name=str(workflow_name),
            artifacts=artifacts,
            mode="incremental",
        )

        if result.get("success") and hasattr(context_variables, "set"):
            try:
                context_variables.set("workflow_artifacts_indexed", True)
            except Exception:
                pass

        if result.get("success"):
            logger.info(f"[{agent.name}] Indexed workflow artifacts into code context")
        else:
            logger.debug(f"[{agent.name}] Workflow artifact indexing skipped: {result.get('message')}")

    except Exception as exc:
        logger.error(f"[{agent.name}] Failed to sync workflow artifacts: {exc}")
