import uuid
from typing import Any, Dict, List, Optional


def _truncate(items: List[str], count: int) -> List[str]:
    return [str(item).strip() for item in items if str(item).strip()][:count]


async def present_final_set(
    *,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    chat_id: Optional[str] = None
    workflow_name: str = "MainStage"
    agent_name: str = "StageHostAgent"
    set_title: str = ""
    set_brief: str = ""
    tone: str = "warm"
    writers_summary: Dict[str, Any] = {}

    if context_variables is not None:
        try:
            chat_id = context_variables.get("chat_id")
            workflow_name = context_variables.get("workflow_name") or workflow_name
            set_title = str(context_variables.get("set_title") or "")
            set_brief = str(context_variables.get("set_brief") or "")
            tone = str(context_variables.get("tone") or "warm")
            current_summary = context_variables.get("writers_summary")
            if isinstance(current_summary, dict):
                writers_summary = dict(current_summary)
        except Exception:
            pass

    direction = str(writers_summary.get("recommended_direction") or "Observational")
    top_bits = _truncate(list(writers_summary.get("top_bits") or []), 3)
    risk_notes = _truncate(list(writers_summary.get("risk_notes") or []), 2)
    opening_line = top_bits[0] if top_bits else f"Tonight's set is called {set_title}, because subtlety clearly died in the parking lot."
    middle_bits = top_bits[1:] or [
        "Find the second beat hiding inside the premise and make it feel inevitable.",
        "Escalate once, then cash out before the room gets tired.",
    ]
    closer = f"Closer: swing back to the {direction.lower()} lane and land it hard."
    clean_alt = f"Clean alt: keep the same premise, but trade sharpness for charm and pace."

    show_packet = {
        "set_title": set_title,
        "set_brief": set_brief,
        "tone": tone,
        "final_direction": direction,
        "opening_line": opening_line,
        "middle_bits": middle_bits,
        "closer": closer,
        "clean_alt": clean_alt,
        "risk_notes": risk_notes,
    }

    if context_variables is not None:
        try:
            context_variables.set("show_packet", show_packet)
            context_variables.set("final_direction", direction)
        except Exception:
            pass

    if not chat_id:
        return {"status": "error", "message": "chat_id missing"}

    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = await SimpleTransport.get_instance()
    base_payload = {
        "component_type": "FinalSetCard",
        **show_packet,
    }

    inline_event_id = f"FinalSetPulse_{uuid.uuid4().hex[:8]}"
    await transport.send_ui_tool_event(
        event_id=inline_event_id,
        chat_id=chat_id,
        tool_name="FinalSetCard",
        component_name="FinalSetCard",
        display_type="inline",
        payload={
            **base_payload,
            "title": "Main Stage Preview",
            "presentation_mode": "inline",
            "workflow_name": workflow_name,
            "agent_name": agent_name,
        },
        awaiting_response=False,
        agent_name=agent_name,
    )

    artifact_event_id = f"FinalSet_{uuid.uuid4().hex[:8]}"
    await transport.send_ui_tool_event(
        event_id=artifact_event_id,
        chat_id=chat_id,
        tool_name="FinalSetCard",
        component_name="FinalSetCard",
        display_type="artifact",
        payload={
            **base_payload,
            "title": "Main Stage Final Set",
            "presentation_mode": "artifact",
            "workflow_name": workflow_name,
            "agent_name": agent_name,
        },
        awaiting_response=False,
        agent_name=agent_name,
    )

    return {
        "status": "emitted",
        "inline_event_id": inline_event_id,
        "artifact_event_id": artifact_event_id,
        "final_direction": direction,
    }
