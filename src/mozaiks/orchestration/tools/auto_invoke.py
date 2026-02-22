"""Auto-invoke parser for tool calls embedded in model text."""

from __future__ import annotations

import re

from ..domain.tool_spec import ToolCall
from .structured_output import parse_json_object

_AUTO_INVOKE_PATTERN = re.compile(
    r"tool:(?P<tool_name>[a-zA-Z0-9_.-]+)\s+(?P<args>\{.*\})",
    re.DOTALL,
)


def parse_auto_tool_call(text: str, *, task_id: str | None = None) -> ToolCall | None:
    """Parse `tool:<name> {json}` from message text."""
    match = _AUTO_INVOKE_PATTERN.search(text)
    if match is None:
        return None

    arguments = parse_json_object(match.group("args"))
    if arguments is None:
        return None

    return ToolCall(
        tool_name=match.group("tool_name"),
        arguments=arguments,
        task_id=task_id,
    )
