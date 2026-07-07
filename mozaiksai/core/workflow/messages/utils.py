"""
Message normalization utilities for AG2 orchestration.

Purpose:
- Normalize AG2 messages to strict format
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    'normalize_to_strict_ag2',
]


def normalize_to_strict_ag2(
    raw_msgs: list[Any] | None,
    *,
    default_user_name: str = "user",
) -> list[dict[str, Any]]:
    """
    Ensure every message is in strict AG2 shape:
      {"role": "user"|"assistant", "name": "<exact agent name>", "content": <str|dict|list>}
    Assumes persisted messages already follow this; mainly fixes locally-seeded items.
    """
    if not raw_msgs:
        return []
    out: list[dict[str, Any]] = []
    for m in raw_msgs:
        if not isinstance(m, dict):
            # ignore non-dicts
            continue

        role = m.get("role")
        name = m.get("name") or m.get("agent_name")
        content = m.get("content")

        # Accept strict messages as-is
        if role in ("user", "assistant") and isinstance(name, str) and name and content is not None:
            out.append({"role": role, "name": name, "content": content})
            continue

        # Try minimal fix-up for messages missing name/role (only for new seeds we add)
        # - If role == "user" and name missing -> set name to "user"
        # - If role missing but name == "user" -> set role to "user"
        # - Otherwise, if assistant-like seed comes through without name, we skip (cannot guess agent)
        if role == "user" and not name:
            name = default_user_name
        if not role and name == default_user_name:
            role = "user"

        if role in ("user", "assistant") and name and content is not None:
            out.append({"role": role, "name": name, "content": content})
        # else drop silently; strictness prevents bad resume
    return out




