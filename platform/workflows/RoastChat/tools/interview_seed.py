from __future__ import annotations

from typing import Annotated, Any, Dict, Optional


async def capture_roast_seed(
    *,
    profile_line: Annotated[str, "Two-line user profile summary"],
    roast_style: Annotated[Optional[str], "gentle|medium|savage"] = None,
    context_variables: Annotated[Optional[Any], "AG2 context variables"] = None,
) -> Dict[str, Any]:
    style_raw = str(roast_style or "medium").strip().lower()
    normalized_style = style_raw if style_raw in {"gentle", "medium", "savage"} else "medium"

    if context_variables is not None:
        try:
            context_variables.set("profile_line", str(profile_line).strip())
            context_variables.set("roast_style", normalized_style)
            context_variables.set("roast_seed_collected", True)
        except Exception:
            pass

    return {
        "status": "ok",
        "profile_line": str(profile_line).strip(),
        "roast_style": normalized_style,
        "roast_seed_collected": True,
    }
