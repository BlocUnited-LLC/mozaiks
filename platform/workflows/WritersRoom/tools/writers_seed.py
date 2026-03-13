from typing import Any, Dict, Optional, Literal


def _normalize_tone(value: Optional[str]) -> str:
    raw = str(value or "warm").strip().lower()
    if raw in {"roast", "warm", "chaotic", "clean"}:
        return raw
    return "warm"


async def capture_room_seed(
    *,
    premise_title: str,
    concept: str,
    audience: str,
    tone: Optional[Literal["roast", "warm", "chaotic", "clean"]] = None,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    normalized_tone = _normalize_tone(tone)
    title = str(premise_title or "").strip()
    brief = str(concept or "").strip()
    target_audience = str(audience or "").strip()
    packet = {
        "set_title": title,
        "set_brief": brief,
        "canonical_description": brief,
        "audience": target_audience,
        "tone": normalized_tone,
        "opening_angle": "Start with the weirdest honest detail first.",
        "best_target": "The performer's own contradictions.",
        "boundary_rule": "Do not punch down.",
        "closer_energy": "Leave the room with a clean hard pop.",
    }

    if context_variables is not None:
        try:
            context_variables.set("set_title", title)
            context_variables.set("set_brief", brief)
            context_variables.set("audience", target_audience)
            context_variables.set("tone", normalized_tone)
            context_variables.set("set_brief_packet", packet)
            context_variables.set("room_seed_ready", True)
        except Exception:
            pass

    return {
        "status": "ok",
        "set_title": title,
        "set_brief": brief,
        "audience": target_audience,
        "tone": normalized_tone,
        "set_brief_packet": packet,
        "room_seed_ready": True,
    }
