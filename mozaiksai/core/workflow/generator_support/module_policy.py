"""Render generated ownership policies from the approved data contract."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from mozaiksai.core.runtime.persistence.intent_loader import (
    iter_data_contract_collections,
    validate_collection_ownership,
)
from mozaiksai.core.workflow.context.frozen import detach

_TENANCY_ATTRIBUTES = {"per_user": "user_id", "per_workspace": "workspace_id"}


def render_module_policy(module_id: str, collections: list[dict[str, Any]]) -> str:
    """Compile explicit collection tenancy into a complete, fail-closed policy."""
    scopes: dict[str, tuple[str | None, str | None]] = {}
    for collection in collections:
        name = str(collection.get("name") or "")
        field = collection.get("owner_field")
        location = f"data_contract module {module_id!r} collection {name!r}"
        if not name or name in scopes:
            raise ValueError(f"{location}: collection names must be nonempty and unique")
        validate_collection_ownership(collection, location)
        attribute = _TENANCY_ATTRIBUTES.get(collection["tenancy"])
        ownership = collection.get("ownership") or {}
        if ownership.get("surface_id") != module_id or ownership.get("surface_kind") != "module":
            raise ValueError(f"{location}: ownership must match its module surface")
        scopes[name] = (field, attribute)
    if not scopes:
        raise ValueError(f"data_contract module {module_id!r} requires at least one collection")

    return (
        '\"\"\"Ownership policy compiled from data/contract.json.\"\"\"\n\n'
        f"_SCOPES = {dict(sorted(scopes.items()))!r}\n\n\n"
        "def _scope(context, entity_name):\n"
        "    if entity_name is None:\n"
        "        if len(_SCOPES) != 1:\n"
        "            raise ValueError('entity_name is required for a module with multiple collections')\n"
        "        entity_name = next(iter(_SCOPES))\n"
        "    if entity_name not in _SCOPES:\n"
        "        raise ValueError(f'Undeclared policy collection: {entity_name!r}')\n"
        "    app_id = getattr(context, 'app_id', None)\n"
        "    if not isinstance(app_id, str) or not app_id.strip():\n"
        "        raise PermissionError('Missing required scope identity: app_id')\n"
        "    field, attribute = _SCOPES[entity_name]\n"
        "    if attribute is None:\n"
        "        return None, None\n"
        "    user_id = getattr(context, 'user_id', None)\n"
        "    if not isinstance(user_id, str) or not user_id.strip():\n"
        "        raise PermissionError('Missing required scope identity: user_id')\n"
        "    identity = getattr(context, attribute, None)\n"
        "    if not isinstance(identity, str) or not identity.strip():\n"
        "        raise PermissionError(f'Missing required scope identity: {attribute}')\n"
        "    return field, identity\n\n\n"
        "def scoped_query(context, filters=None, *, entity_name=None):\n"
        "    field, identity = _scope(context, entity_name)\n"
        "    query = dict(filters or {})\n"
        "    query.pop('app_id', None)\n"
        "    if field is not None:\n"
        "        query[field] = identity\n"
        "    return query\n\n\n"
        "def scope_record(context, record, *, entity_name=None):\n"
        "    field, identity = _scope(context, entity_name)\n"
        "    scoped = {**dict(record), 'app_id': context.app_id}\n"
        "    if field is not None:\n"
        "        scoped[field] = identity\n"
        "    return scoped\n"
    )


def materialize_module_policies(
    files: Mapping[str, str],
    data_contract: dict[str, Any] | None = None,
    *,
    module_ids: set[str] | None = None,
) -> dict[str, str]:
    """Render policies only for modules owning approved collection surfaces.

    The supplied DesignDocs contract is authoritative; its serialized artifact
    must agree and cannot act as a second input authority.
    """
    existing_policy_modules = {
        parts[1] for path in files
        if len(parts := path.split("/")) == 4
        and parts[0] == "modules" and parts[2:] == ["backend", "policy.py"]
    }
    if "data/contract.json" in files:
        serialized = json.loads(files["data/contract.json"])
        if serialized != data_contract:
            raise ValueError("data/contract.json must match the approved data_contract")
    if not isinstance(data_contract, dict):
        if module_ids or existing_policy_modules:
            raise ValueError("Persistent module policies require data_contract")
        return {}
    selected = module_ids
    if selected is None:
        selected = {
            parts[1] for path in files
            if len(parts := path.split("/")) >= 3 and parts[0] == "modules"
        }
    collections_by_module: dict[str, list[dict[str, Any]]] = {}
    for module_id, surface_kind, collection in iter_data_contract_collections(data_contract):
        if surface_kind == "module" and module_id in selected:
            collections_by_module.setdefault(module_id, []).append(collection)
    result: dict[str, str] = {}
    for module_id, collections in collections_by_module.items():
        path = f"modules/{module_id}/backend/policy.py"
        result[path] = render_module_policy(module_id, collections)
        if path in files and files[path] != result[path]:
            raise ValueError(f"{path}: policy.py is rendered from data_contract; omit model-authored policy source")
    missing = sorted(existing_policy_modules - {path.split("/")[1] for path in result})
    if missing:
        raise ValueError(f"Persistent module policy has no data_contract surface: {missing}")
    return result


def materialize_task_module_policies(
    files: Mapping[str, str], *, task: Mapping[str, Any], data_contract: Any = None,
) -> dict[str, str]:
    """Render owned policy artifacts before batch output ownership validation."""
    selected = {
        parts[1] for path in task.get("owned_paths") or []
        if len(parts := str(path).split("/")) == 4
        and parts[0] == "modules" and parts[2:] == ["backend", "policy.py"]
    }
    selected.update(
        parts[1] for path in files
        if len(parts := path.split("/")) == 4
        and parts[0] == "modules" and parts[2:] == ["backend", "policy.py"]
    )
    if not selected:
        return {}
    return materialize_module_policies(files, detach(data_contract), module_ids=selected)
