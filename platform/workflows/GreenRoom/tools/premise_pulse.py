import uuid
from typing import Any, Dict, Optional


async def present_premise_pulse(
    *,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    chat_id: Optional[str] = None
    workflow_name: str = "GreenRoom"
    agent_name: str = "PremiseCanonAgent"
    payload: Dict[str, Any] = {}

    if context_variables is not None:
        try:
            chat_id = context_variables.get("chat_id")
            workflow_name = context_variables.get("workflow_name") or workflow_name
            packet = context_variables.get("set_brief_packet")
            if isinstance(packet, dict):
                payload = dict(packet)
        except Exception:
            payload = {}

    if not chat_id:
        return {"status": "error", "message": "chat_id missing"}

    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = await SimpleTransport.get_instance()
    event_id = f"PremisePulse_{uuid.uuid4().hex[:8]}"
    await transport.send_ui_tool_event(
        event_id=event_id,
        chat_id=chat_id,
        tool_name="PremisePulseCard",
        component_name="PremisePulseCard",
        display_type="inline",
        payload={
            "component_type": "PremisePulseCard",
            "title": "GreenRoom Premise Pulse",
            "presentation_mode": "inline",
            "workflow_name": workflow_name,
            "agent_name": agent_name,
            **payload,
        },
        awaiting_response=False,
        agent_name=agent_name,
    )

    return {
        "status": "emitted",
        "event_id": event_id,
        "workflow_name": workflow_name,
    }
