from __future__ import annotations

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


def _compute_overall_verdict(cards: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for card in cards:
        verdict = str(card.get("verdict") or "conditional").strip().lower()
        if verdict not in {"accept", "reject", "conditional"}:
            verdict = "conditional"
        counts[verdict] = counts.get(verdict, 0) + 1
    if not counts:
        return "conditional"
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


async def present_roast_stage(
    *,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    chat_id: Optional[str] = None
    workflow_name: str = "RoastChat"
    agent_name: str = "HostAgent"
    results: Dict[str, Any] = {}
    profile_line: str = ""
    roast_style: str = "medium"

    if context_variables is not None:
        try:
            chat_id = context_variables.get("chat_id")
            workflow_name = context_variables.get("workflow_name") or workflow_name
            profile_line = str(context_variables.get("profile_line") or "")
            roast_style = str(context_variables.get("roast_style") or "medium")
            current_results = context_variables.get("mfj_roast_results")
            if isinstance(current_results, dict):
                results = dict(current_results)
        except Exception:
            pass

    cards: List[Dict[str, Any]] = []
    objections: List[str] = []
    total_score = 0

    for task_key, value in results.items():
        row = _pick_first_model(value if isinstance(value, dict) else {})
        if not isinstance(row, dict):
            continue
        score = _safe_int(row.get("score"), default=0)
        objection = str(row.get("objection") or "").strip()
        if objection:
            objections.append(objection)
        card = {
            "task_key": str(task_key),
            "judge": str(row.get("judge_name") or task_key),
            "trait": str(row.get("trait_focus") or "Unspecified"),
            "roast_line": str(row.get("roast_line") or ""),
            "verdict": str(row.get("verdict") or "conditional"),
            "score": score,
            "positive_spin": str(row.get("positive_spin") or ""),
            "objection": objection,
        }
        cards.append(card)
        total_score += score

    average_score = int(round(total_score / len(cards))) if cards else 0
    verdict = _compute_overall_verdict(cards)
    top_objections = objections[:3]

    base_payload: Dict[str, Any] = {
        "component_type": "RoastStageCard",
        "profile_line": profile_line,
        "roast_style": roast_style,
        "cards": cards,
        "overall_verdict": verdict,
        "average_score": average_score,
        "top_objections": top_objections,
    }

    if context_variables is not None:
        try:
            context_variables.set(
                "host_summary",
                {
                    "overall_verdict": verdict,
                    "average_score": average_score,
                    "top_objections": top_objections,
                },
            )
            context_variables.set("roast_verdict", verdict)
            mark_resume_consumed(context_variables)
        except Exception:
            pass

    if not chat_id:
        return {"status": "error", "message": "chat_id missing"}

    from mozaiksai.core.transport.simple_transport import SimpleTransport

    transport = await SimpleTransport.get_instance()

    inline_event_id = f"RoastStageInline_{uuid.uuid4().hex[:8]}"
    inline_payload = {
        **base_payload,
        "title": "Roast Jury Pulse",
        "presentation_mode": "inline",
        "agent_message": "Quick pulse check from the jury. Full board is opening now.",
        "cards": cards[:2],
        "top_objections": top_objections[:2],
    }
    await transport.send_ui_tool_event(
        event_id=inline_event_id,
        chat_id=chat_id,
        tool_name="RoastStageCard",
        component_name="RoastStageCard",
        display_type="inline",
        payload={**inline_payload, "workflow_name": workflow_name, "agent_name": agent_name},
        awaiting_response=False,
        agent_name=agent_name,
    )

    artifact_event_id = f"RoastStageCard_{uuid.uuid4().hex[:8]}"
    artifact_payload = {
        **base_payload,
        "title": "Roast Jury Verdict",
        "presentation_mode": "artifact",
    }
    await transport.send_ui_tool_event(
        event_id=artifact_event_id,
        chat_id=chat_id,
        tool_name="RoastStageCard",
        component_name="RoastStageCard",
        display_type="artifact",
        payload={**artifact_payload, "workflow_name": workflow_name, "agent_name": agent_name},
        awaiting_response=False,
        agent_name=agent_name,
    )

    return {
        "status": "emitted",
        "inline_event_id": inline_event_id,
        "artifact_event_id": artifact_event_id,
        "overall_verdict": verdict,
        "average_score": average_score,
        "top_objections": top_objections,
    }
