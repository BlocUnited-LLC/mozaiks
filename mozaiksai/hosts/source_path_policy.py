"""HTTP boundary for caller-selected server filesystem discovery sources."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from mozaiksai.core.auth import UserPrincipal, is_auth_explicitly_disabled

_LOCAL_SOURCE_PATH_KEYS = frozenset({
    "repo_path",
    "frontend_repo_path",
    "backend_repo_path",
    "uploaded_openapi_path",
})


def require_http_local_source_mode(principal: UserPrincipal) -> None:
    """Allow HTTP-selected server paths only for no-auth development callers."""
    if not is_auth_explicitly_disabled() or not principal.has_local_development_access:
        raise HTTPException(
            status_code=403,
            detail="HTTP local source paths require local development access with authentication explicitly disabled.",
        )


def authorize_http_workflow_source_paths(
    context_variables: Mapping[str, Any], *, principal: UserPrincipal,
) -> None:
    """Reject local discovery selectors from callers without development access."""
    discovery_inputs = context_variables.get("discovery_inputs")
    if isinstance(discovery_inputs, str):
        try:
            discovery_inputs = json.loads(discovery_inputs)
        except ValueError:
            discovery_inputs = None

    sources = [context_variables]
    if isinstance(discovery_inputs, Mapping):
        sources.append(discovery_inputs)

    for source in sources:
        if any(_present(source.get(key)) for key in _LOCAL_SOURCE_PATH_KEYS):
            require_http_local_source_mode(principal)
        if str(source.get("host_app_source") or "").strip() == "workspace_app":
            require_http_local_source_mode(principal)


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))
