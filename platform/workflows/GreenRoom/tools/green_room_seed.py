from typing import Any, Dict, Optional, Literal


def _normalize_tone(value: Optional[str]) -> str:
    raw = str(value or "warm").strip().lower()
    if raw in {"roast", "warm", "chaotic", "clean"}:
        return raw
    return "warm"


async def capture_set_seed(
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

    if context_variables is not None:
        try:
            context_variables.set("set_title", title)
            context_variables.set("set_brief", brief)
            context_variables.set("audience", target_audience)
            context_variables.set("tone", normalized_tone)
            context_variables.set("set_seed_captured", True)
        except Exception:
            pass

    return {
        "status": "ok",
        "set_title": title,
        "set_brief": brief,
        "audience": target_audience,
        "tone": normalized_tone,
        "set_seed_captured": True,
    }


async def capture_set_brief(
    *,
    canonical_description: str,
    opening_angle: str,
    best_target: str,
    boundary_rule: str,
    closer_energy: str,
    tone: Optional[Literal["roast", "warm", "chaotic", "clean"]] = None,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    normalized_tone = _normalize_tone(tone)

    title = ""
    brief = ""
    audience = ""
    if context_variables is not None:
        try:
            title = str(context_variables.get("set_title") or "").strip()
            brief = str(context_variables.get("set_brief") or "").strip()
            audience = str(context_variables.get("audience") or "").strip()
        except Exception:
            title = ""
            brief = ""
            audience = ""

    set_brief_packet = {
        "set_title": title,
        "set_brief": brief,
        "canonical_description": str(canonical_description or "").strip(),
        "audience": audience,
        "tone": normalized_tone,
        "opening_angle": str(opening_angle or "").strip(),
        "best_target": str(best_target or "").strip(),
        "boundary_rule": str(boundary_rule or "").strip(),
        "closer_energy": str(closer_energy or "").strip(),
    }

    if context_variables is not None:
        try:
            context_variables.set("tone", normalized_tone)
            context_variables.set("set_brief_packet", set_brief_packet)
            context_variables.set("set_brief_ready", True)
        except Exception:
            pass

    return {
        "status": "ok",
        "set_title": title,
        "set_brief_packet": set_brief_packet,
        "set_brief_ready": True,
    }
