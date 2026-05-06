"""
mermaid_sequence_diagram tool - renders the workflow sequence diagram artifact.

Reads MermaidSequenceDiagram structured output and emits a UI artifact
via the shipped DiagramViewer component for user review.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Optional

from mozaiksai.core.workflow.ui_tools import emit_ui_surface

_logger = logging.getLogger("tools.mermaid_sequence_diagram")


async def mermaid_sequence_diagram(
    *,
    MermaidSequenceDiagram: Annotated[
        Optional[Dict[str, Any]],
        "MermaidSequenceDiagram payload from the diagram agent",
    ] = None,
    context_variables: Annotated[Optional[Any], "Context variables provided by AG2"] = None,
) -> str:
    """Emit the sequence diagram as a UI artifact for user review."""

    # Fall back to structured_output injected by the auto-invoke runtime
    if not MermaidSequenceDiagram or not isinstance(MermaidSequenceDiagram, dict):
        structured: Any = None
        if context_variables:
            try:
                getter = getattr(context_variables, "get", None)
                if callable(getter):
                    structured = getter("structured_output")
                else:
                    data = getattr(context_variables, "data", None)
                    if isinstance(data, dict):
                        structured = data.get("structured_output")
            except Exception:
                pass
        if isinstance(structured, dict):
            MermaidSequenceDiagram = structured.get("MermaidSequenceDiagram") or structured

    if not MermaidSequenceDiagram or not isinstance(MermaidSequenceDiagram, dict):
        _logger.warning("mermaid_sequence_diagram: no diagram data available")
        return "No diagram data provided"

    workflow_name: str = str(MermaidSequenceDiagram.get("workflow_name") or "").strip()
    diagram_text: str = str(MermaidSequenceDiagram.get("diagram") or "").strip()
    legend: List[str] = MermaidSequenceDiagram.get("legend") or []
    notes: Optional[str] = MermaidSequenceDiagram.get("notes") or None
    agent_message: str = str(
        MermaidSequenceDiagram.get("agent_message") or "Review the sequence diagram below."
    ).strip()

    if not isinstance(legend, list):
        legend = []

    chat_id = None
    runtime_workflow_name = "AgentGenerator"
    if context_variables:
        try:
            getter = getattr(context_variables, "get", None)
            if callable(getter):
                chat_id = getter("chat_id")
                runtime_workflow_name = getter("workflow_name") or runtime_workflow_name
            else:
                data = getattr(context_variables, "data", None)
                if isinstance(data, dict):
                    chat_id = data.get("chat_id")
                    runtime_workflow_name = data.get("workflow_name") or runtime_workflow_name
        except Exception:
            pass

    try:
        await emit_ui_surface(
            "DiagramViewer",
            {
                "workflow_name": workflow_name,
                "diagram": diagram_text,
                "diagram_type": "mermaid",
                "legend": legend,
                "notes": notes,
                "agent_message": agent_message,
            },
            chat_id=str(chat_id) if chat_id else None,
            workflow_name=str(runtime_workflow_name or "AgentGenerator"),
        )
        _logger.info("Emitted DiagramViewer artifact for '%s'", workflow_name)
    except Exception as exc:
        _logger.error("Failed to emit DiagramViewer artifact: %s", exc)
        return f"Error rendering diagram: {exc}"

    return f"Sequence diagram rendered for '{workflow_name}'"
