from __future__ import annotations

from typing import Any, Dict

from mozaiksai.core.workflow.ui_tools import emit_ui_surface


async def show_acceptance_diagram(context_variables: Any = None) -> Dict[str, Any]:
    chat_id = None
    workflow_name = None

    if context_variables is not None and hasattr(context_variables, "get"):
        try:
            chat_id = context_variables.get("chat_id")
            workflow_name = context_variables.get("workflow_name")
        except Exception:
            chat_id = None
            workflow_name = None

    if not workflow_name:
        return {
            "status": "error",
            "artifact_emitted": False,
            "surface_component": "DiagramViewer",
        }

    payload = {
        "component_type": "DiagramViewer",
        "interaction_type": "ui_surface",
        "title": "Workflow primitive acceptance sequence",
        "summary": "The artifact viewer renders the canonical three-lane acceptance path.",
        "diagram": "\n".join(
            [
                "sequenceDiagram",
                "    participant User",
                "    participant IntakeAgent",
                "    participant ReviewAgent",
                "    participant UI as Workflow UI",
                "",
                "    IntakeAgent->>User: Ask one discovery question",
                "    User->>IntakeAgent: Reply through composer",
                "    ReviewAgent->>UI: Emit ApprovalCard",
                "    UI->>ReviewAgent: Return structured approval",
                "    ReviewAgent->>UI: Emit DiagramViewer artifact",
            ]
        ),
        "checkpoints": [
            {"label": "Composer", "status": "validated"},
            {"label": "Approval Card", "status": "validated"},
            {"label": "Artifact Surface", "status": "emitted"},
        ],
    }

    event_id = await emit_ui_surface(
        "DiagramViewer",
        payload,
        chat_id=chat_id,
        workflow_name=str(workflow_name),
    )

    return {
        "status": "emitted",
        "artifact_emitted": True,
        "surface_component": "DiagramViewer",
        "surface_event_id": event_id,
    }
