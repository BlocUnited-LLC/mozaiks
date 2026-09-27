"""Render generated module ownership policies from the canonical data contract."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from mozaiksai.core.workflow.context.frozen import detach

_SCOPE_ATTRIBUTES = {
    "app": "app_id",
    "user": "user_id",
    "tenant": "tenant_id",
    "workspace": "workspace_id",
}
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def render_module_policy(module_id: str, collections: list[dict[str, Any]]) -> str:
    """Compile explicit collection scopes into a complete, fail-closed policy."""
    scopes: dict[str, tuple[str, str]] = {}
    for collection in collections:
        name = str(collection.get("name") or "")
        scope = collection.get("scope")
        field = collection.get("scope_field")
        location = f"data_contract module {module_id!r} collection {name!r}"
        if not name or name in scopes:
            raise ValueError(f"{location}: collection names must be nonempty and unique")
        if scope not in _SCOPE_ATTRIBUTES:
            raise ValueError(f"{location}: scope must be app, user, tenant, or workspace")
        if not isinstance(field, str) or not _FIELD.fullmatch(field):
            raise ValueError(f"{location}: scope_field must be an explicit document field name")
        if field not in {item.get("name") for item in collection.get("fields") or [] if isinstance(item, dict)}:
            raise ValueError(f"{location}: scope_field {field!r} must be declared in fields")
        if field in _SCOPE_ATTRIBUTES.values() and field != _SCOPE_ATTRIBUTES[scope]:
            raise ValueError(f"{location}: reserved scope_field {field!r} must match scope {scope!r}")
        ownership = collection.get("ownership") or {}
        if ownership.get("surface_id") != module_id or ownership.get("surface_kind") != "module":
            raise ValueError(f"{location}: ownership must match its module surface")
        scopes[name] = (field, _SCOPE_ATTRIBUTES[scope])
    if not scopes:
        raise ValueError(f"data_contract module {module_id!r} requires at least one collection")

    return (
        '"""Ownership policy compiled from data/contract.json."""\n\n'
        f"_SCOPES = {dict(sorted(scopes.items()))!r}\n\n\n"
        "def _scope(context, entity_name):\n"
        "    if entity_name is None:\n"
        "        if len(_SCOPES) != 1:\n"
        "            raise ValueError('entity_name is required for a module with multiple collections')\n"
        "        entity_name = next(iter(_SCOPES))\n"
        "    if entity_name not in _SCOPES:\n"
        "        raise ValueError(f'Undeclared policy collection: {entity_name!r}')\n"
        "    field, attribute = _SCOPES[entity_name]\n"
        "    identity = getattr(context, attribute, None)\n"
        "    if not isinstance(identity, str) or not identity.strip():\n"
        "        raise PermissionError(f'Missing required scope identity: {attribute}')\n"
        "    return field, identity\n\n\n"
        "def scoped_query(context, filters=None, *, entity_name=None):\n"
        "    field, identity = _scope(context, entity_name)\n"
        "    query = dict(filters or {})\n"
        "    if field == 'app_id':\n"
        "        query.pop('app_id', None)\n"
        "    else:\n"
        "        query[field] = identity\n"
        "    return query\n\n\n"
        "def scope_record(context, record, *, entity_name=None):\n"
        "    field, identity = _scope(context, entity_name)\n"
        "    return {**dict(record), field: identity}\n"
    )


def materialize_module_policies(
    files: Mapping[str, str],
    data_contract: dict[str, Any] | None = None,
    *,
    module_ids: set[str] | None = None,
) -> dict[str, str]:
    """Return policies for persistent modules present in a bundle or owned task.

    The serialized data contract wins when available. A task can supply its
    approved contract before the DatabaseAgent's artifact has been assembled.
    """
    existing_policy_modules = {
        parts[1] for path in files
        if len(parts := path.split("/")) == 4
        and parts[0] == "modules" and parts[2:] == ["backend", "policy.py"]
    }
    if "data/contract.json" in files:
        data_contract = json.loads(files["data/contract.json"])
    if not isinstance(data_contract, dict):
        if module_ids or existing_policy_modules:
            raise ValueError("Persistent module policies require data_contract")
        return {}
    selected = module_ids
    if selected is None:
        selected = {
            parts[1]
            for path in files
            if len(parts := path.split("/")) == 4
            and parts[0] == "modules" and parts[2] == "backend"
        }
    result: dict[str, str] = {}
    for surface in data_contract.get("surfaces") or []:
        if not isinstance(surface, dict) or surface.get("surface_kind") != "module":
            continue
        module_id = str(surface.get("surface_id") or "")
        if module_id not in selected:
            continue
        path = f"modules/{module_id}/backend/policy.py"
        if path in result:
            raise ValueError(f"data_contract has duplicate module surface {module_id!r}")
        result[path] = render_module_policy(module_id, surface.get("collections") or [])
        if path in files and files[path] != result[path]:
            raise ValueError(f"{path}: policy.py is rendered from data_contract; omit model-authored policy source")
    missing = sorted((module_ids or existing_policy_modules) - {path.split("/")[1] for path in result})
    if missing:
        raise ValueError(f"Persistent module policy has no data_contract surface: {missing}")
    return result


def materialize_task_module_policies(
    files: Mapping[str, str], *, task: Mapping[str, Any], data_contract: Any = None,
) -> dict[str, str]:
    """Render owned policy artifacts before batch output ownership validation."""
    selected = {
        parts[1]
        for path in task.get("owned_paths") or []
        if len(parts := str(path).split("/")) == 4
        and parts[0] == "modules" and parts[2:] == ["backend", "policy.py"]
    }
    emitted = {
        path
        for path in files
        if len(parts := path.split("/")) == 4
        and parts[0] == "modules" and parts[2:] == ["backend", "policy.py"]
    }
    selected.update(path.split("/")[1] for path in emitted)
    if not selected:
        return {}
    return materialize_module_policies(files, detach(data_contract), module_ids=selected)
