"""Project reviewed DesignDocs grants into Factory context and prompts."""

from __future__ import annotations

import json
from typing import Any

from factory_app.workflows._shared.hook_utils import update_agent_section
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_design_access_policy,
)


async def hydrate_design_access_policy(context_variables: Any = None) -> dict[str, Any]:
    policy = resolve_design_access_policy(context_variables)
    context_variables.set("access_policy", policy)
    return {"status": "loaded", "role_count": len(policy["roles"]) if policy else 0}


def inject_design_access_policy(agent: Any, messages: list[dict[str, Any]]) -> None:
    policy = resolve_design_access_policy(agent.context_variables)
    update_agent_section(
        agent, "[APP ROLE ACCESS]",
        "The reviewed DesignDocs access_policy below is the only source of generated role grants. "
        "Code derives module permission ids and config/auth.yaml. Preserve these roles in the app plan; "
        "do not invent grants, broaden them, or write auth.yaml. Record ownership still applies.\n"
        + json.dumps(policy, ensure_ascii=False, sort_keys=True),
    )
