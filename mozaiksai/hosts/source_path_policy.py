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


def _reject_http_snapshot_inputs(context_variables: Mapping[str, Any]) -> None:
    """A caller-supplied trigger label cannot mint router-owned snapshot authority."""
    if not context_variables:
        return
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    for workflow_name in workflow_manager.list_workflows():
        definitions = (workflow_manager.get_config(workflow_name).get("context_variables") or {}).get("definitions") or {}
        roots = {
            source["snapshot"].get("context_key")
            for definition in definitions.values() if isinstance(definition, Mapping)
            if isinstance(source := definition.get("source"), Mapping) and isinstance(source.get("snapshot"), Mapping)
        }
        if roots.intersection(context_variables):
            raise HTTPException(status_code=400, detail="Saved workflow input snapshots are selected only by the server.")


def authorize_http_workflow_source_paths(
    context_variables: Mapping[str, Any], *, principal: UserPrincipal,
) -> None:
    """Admit caller discovery paths without accepting saved runtime input authority."""
    _reject_http_snapshot_inputs(context_variables)
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
