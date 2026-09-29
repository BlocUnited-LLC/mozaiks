"""Render the account-data handler a module's per-user collections determine.

A collection with ``tenancy: per_user`` stores rows that belong to one account,
named by its ``owner_field``. The data contract therefore decides what account
export and deletion cover, so code renders ``backend/account_data_handler.py``
for every module owning such a collection and sets ``module.user_data_scope``.
The handler receives the account's runtime persistence from the account routes:
every query is scoped to the app and the authenticated owner by the runtime, and
the handler also filters by the owner field itself.

``per_workspace`` rows belong to a workspace, not to one member, so account
deletion never removes them; a module owning only such collections gets no
rendered handler.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from mozaiksai.core.runtime.persistence.intent_loader import (
    DataContractLoadError,
    iter_data_contract_collections,
)
from mozaiksai.core.workflow.context.frozen import detach

from .module_write_actions import per_user_collections

logger = logging.getLogger(__name__)

ACCOUNT_DATA_HANDLER = "backend/account_data_handler.py"


def account_data_handler_path(module_id: str) -> str:
    return f"modules/{module_id}/{ACCOUNT_DATA_HANDLER}"


def owns_per_user_collections(module_id: str, data_contract: Any) -> bool:
    """Whether the approved data contract gives the module rows that belong to one account."""
    contract = detach(data_contract)
    if not isinstance(contract, dict):
        return False
    try:
        return any(
            owner == module_id and kind == "module" and collection.get("tenancy") == "per_user"
            and collection.get("owner_field")
            for owner, kind, collection in iter_data_contract_collections(contract)
        )
    except DataContractLoadError:
        return False


def render_account_data_handler(module_id: str, collections: list[dict[str, Any]]) -> str:
    """Compile export and deletion of the owner's rows in each per-user collection."""
    scopes: dict[str, tuple[str, tuple[str, ...]]] = {}
    for collection in collections:
        name, owner_field = str(collection.get("name") or ""), collection.get("owner_field")
        if collection.get("tenancy") != "per_user" or not isinstance(owner_field, str) or not owner_field:
            raise ValueError(f"data_contract module {module_id!r} collection {name!r} is not a per_user collection")
        fields = tuple(
            str(field["name"]) for field in collection.get("fields") or []
            if isinstance(field, Mapping) and field.get("name")
        )
        scopes[name] = (owner_field, fields)
    if not scopes:
        raise ValueError(f"data_contract module {module_id!r} owns no per_user collection")
    return (
        '"""Account-data export and deletion compiled from data/contract.json.\n\n'
        "The account routes construct this handler with the authenticated account's\n"
        "runtime persistence, so every query is scoped to the app and the owner the\n"
        "runtime authenticated. Code renders this file from the per_user collections\n"
        "the module owns.\n"
        '"""\n'
        "from __future__ import annotations\n\n"
        "from datetime import date, datetime\n"
        "from typing import Any\n\n"
        f"_MODULE_ID = {module_id!r}\n"
        "# per_user collection -> (owner field, declared fields)\n"
        f"_COLLECTIONS: dict[str, tuple[str, tuple[str, ...]]] = {dict(sorted(scopes.items()))!r}\n"
        "_PAGE_SIZE = 100\n\n\n"
        "def _portable(value: Any) -> Any:\n"
        "    if isinstance(value, (datetime, date)):\n"
        "        return value.isoformat()\n"
        "    if isinstance(value, dict):\n"
        "        return {str(key): _portable(item) for key, item in value.items()}\n"
        "    if isinstance(value, (list, tuple)):\n"
        "        return [_portable(item) for item in value]\n"
        "    if value is None or isinstance(value, (str, int, float, bool)):\n"
        "        return value\n"
        "    return str(value)\n\n\n"
        "class AccountDataHandler:\n"
        f'    """Export and delete the account owner\'s {module_id} records."""\n\n'
        "    def __init__(self, persistence: Any) -> None:\n"
        "        self.persistence = persistence\n\n"
        "    def _collection(self, app_id: str, name: str) -> Any:\n"
        "        if self.persistence.app_id != app_id:\n"
        "            raise PermissionError('account persistence is bound to another app')\n"
        "        return self.persistence.collection(_MODULE_ID, name)\n\n"
        "    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:\n"
        "        deleted = 0\n"
        "        for name, (owner_field, _fields) in _COLLECTIONS.items():\n"
        "            result = await self._collection(app_id, name).delete_many({owner_field: user_id})\n"
        "            deleted += int(getattr(result, 'deleted_count', 0) or 0)\n"
        "        return {'deleted_count': deleted}\n\n"
        "    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:\n"
        "        export: dict[str, Any] = {}\n"
        "        for name, (owner_field, fields) in _COLLECTIONS.items():\n"
        "            collection = self._collection(app_id, name)\n"
        "            records: list[dict[str, Any]] = []\n"
        "            query: dict[str, Any] = {owner_field: user_id}\n"
        "            while True:\n"
        "                page = await collection.find_many(query, limit=_PAGE_SIZE, sort=[('_id', 1)])\n"
        "                records.extend(\n"
        "                    {field: _portable(record[field]) for field in fields if field in record} for record in page\n"
        "                )\n"
        "                if len(page) < _PAGE_SIZE:\n"
        "                    break\n"
        "                query = {owner_field: user_id, '_id': {'$gt': page[-1]['_id']}}\n"
        "            export[f'{_MODULE_ID}_{name}'] = records\n"
        "        return export\n"
    )


def materialize_module_account_handlers(
    files: Mapping[str, str], *, app_build_plan: Any, data_contract: Any = None,
    module_ids: set[str] | None = None,
) -> dict[str, str]:
    """Render the account-data handler of every module owning per_user collections.

    The file is code-owned: a model-authored copy is replaced and the
    replacement is logged, never rejected.
    """
    plan = detach(app_build_plan)
    contract = detach(data_contract)
    if not isinstance(plan, dict) or not isinstance(contract, dict):
        return {}
    selected = module_ids
    if selected is None:
        selected = {parts[1] for path in files if len(parts := path.split("/")) >= 3 and parts[0] == "modules"}
    result: dict[str, str] = {}
    for module_id in sorted(selected):
        collections = per_user_collections(module_id, plan, contract)
        if not collections:
            continue
        path = account_data_handler_path(module_id)
        result[path] = render_account_data_handler(module_id, collections)
        if path in files and files[path] != result[path]:
            logger.warning(
                "ACCOUNT_DATA_HANDLER_OVERWRITTEN: %s is rendered from the per_user collections in "
                "data_contract; the authored copy was replaced.",
                path,
            )
    return result


def materialize_task_module_account_handlers(
    files: Mapping[str, str], *, task: Mapping[str, Any], app_build_plan: Any, data_contract: Any = None,
) -> dict[str, str]:
    """Render owned account-data handlers before batch output ownership validation."""
    selected = {
        parts[1] for path in [*(task.get("owned_paths") or []), *files]
        if len(parts := str(path).split("/")) == 4
        and parts[0] == "modules" and "/".join(parts[2:]) == ACCOUNT_DATA_HANDLER
    }
    if not selected:
        return {}
    return materialize_module_account_handlers(
        files, app_build_plan=app_build_plan, data_contract=data_contract, module_ids=selected,
    )


__all__ = [
    "ACCOUNT_DATA_HANDLER",
    "account_data_handler_path",
    "materialize_module_account_handlers",
    "materialize_task_module_account_handlers",
    "owns_per_user_collections",
    "render_account_data_handler",
]
