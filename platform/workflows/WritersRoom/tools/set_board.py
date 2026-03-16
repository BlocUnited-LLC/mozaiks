import uuid
from typing import Any, Dict, List, Optional

from mozaiksai.core.workflow.pack.resume_contract import mark_resume_consumed


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _pick_first_model(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    nested = row.get("mfj_child_outputs")
    if isinstance(nested, dict):
        for value in nested.values():
            if isinstance(value, dict):
                return value
    return row


async def present_set_board(
    *,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    chat_id: Optional[str] = None
    workflow_name: str = "WritersRoom"
    agent_name: str = "HostAgent"
    results: Dict[str, Any] = {}
    set_title: str = ""
    set_brief: str = ""
    brief_packet: Dict[str, Any] = {}

    if context_variables is not None:
        try:
            chat_id = context_variables.get("chat_id")
            workflow_name = context_variables.get("workflow_name") or workflow_name
            set_title = str(context_variables.get("set_title") or "")
            set_brief = str(context_variables.get("set_brief") or "")
            current_packet = context_variables.get("set_brief_packet")
            if isinstance(current_packet, dict):
                brief_packet = dict(current_packet)
            current_results = context_variables.get("mfj_writers_room_results")
            if isinstance(current_results, dict):
                results = dict(current_results)
        except Exception:
            pass

    cards: List[Dict[str, Any]] = []
    risk_notes: List[str] = []
    total_score = 0

    for task_key, value in results.items():
        row = _pick_first_model(value if isinstance(value, dict) else {})
        if not isinstance(row, dict):
            continue
        score = _safe_int(row.get("score"), default=0)
        risk_note = str(row.get("risk_note") or "").strip()
        if risk_note:
            risk_notes.append(risk_note)
        card = {
            "task_key": str(task_key),
            "lane": str(row.get("lane_name") or task_key),
            "headline": str(row.get("bit_headline") or ""),
            "bit": str(row.get("strongest_bit") or ""),
            "tag": str(row.get("supporting_tag") or ""),
            "crowd_hook": str(row.get("crowd_hook") or ""),
            "risk_note": risk_note,
            "closer_idea": str(row.get("closer_idea") or ""),
            "score": score,
        }
        cards.append(card)
        total_score += score

    cards.sort(key=lambda card: int(card.get("score") or 0), reverse=True)
    average_score = int(round(total_score / len(cards))) if cards else 0
    recommended_direction = cards[0]["lane"] if cards else "Observational"
    top_bits = [card["bit"] for card in cards if card.get("bit")][:3]

    summary = {
        "recommended_direction": recommended_direction,
        "average_score": average_score,
        "top_bits": top_bits,
        "risk_notes": risk_notes[:3],
    }
    if context_variables is not None:
        try:
            context_variables.set("writers_summary", summary)
            context_variables.set("set_direction", recommended_direction)
            mark_resume_consumed(context_variables)
        except Exception:
            pass

    if not chat_id:
        return {"status": "error", "message": "chat_id missing"}

    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = await SimpleTransport.get_instance()
    base_payload: Dict[str, Any] = {
        "component_type": "SetBoardCard",
        "set_title": set_title,
        "set_brief": set_brief,
        "brief_packet": brief_packet,
        "cards": cards,
        "recommended_direction": recommended_direction,
        "average_score": average_score,
        "top_bits": top_bits,
        "risk_notes": risk_notes[:3],
    }

    inline_event_id = f"SetPulse_{uuid.uuid4().hex[:8]}"
    await transport.send_ui_tool_event(
        event_id=inline_event_id,
        chat_id=chat_id,
        tool_name="SetBoardCard",
        component_name="SetBoardCard",
        display_type="inline",
        payload={
            **base_payload,
            "title": "Writers Room Pulse",
            "presentation_mode": "inline",
            "workflow_name": workflow_name,
            "agent_name": agent_name,
        },
        awaiting_response=False,
        agent_name=agent_name,
    )

    artifact_event_id = f"SetBoard_{uuid.uuid4().hex[:8]}"
    await transport.send_ui_tool_event(
        event_id=artifact_event_id,
        chat_id=chat_id,
        tool_name="SetBoardCard",
        component_name="SetBoardCard",
        display_type="artifact",
        payload={
            **base_payload,
            "title": "Backstage SetBoard",
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
        "recommended_direction": recommended_direction,
        "average_score": average_score,
    }
