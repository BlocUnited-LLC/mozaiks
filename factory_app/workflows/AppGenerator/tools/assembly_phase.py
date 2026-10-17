"""
Assembly Phase Tool

Merges outputs from multiple feature-scoped AppGenerator runs into a single
workflow bundle. The bundle is a collection of workflow YAMLs, tools, and
config_impacts artifacts consumed by mozaiks-core.

Responsibilities:
1. Collect code_files from all feature generations
2. De-duplicate by filename (last writer wins)
3. Return merged code_files list for DownloadAgent
"""
from __future__ import annotations

import json
import logging
from typing import Any

from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    admitted_app_file_map,
    extract_code_file_entries_from_payload,
)
from factory_app.workflows.AppGenerator.tools.module_api_template import get_module_api_template
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    verified_pack_data_contracts,
    verified_pack_template_inventory,
    verified_template_module_ids,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    compile_data_contract,
    materialize_collection_auth,
    materialize_data_contract,
)
from mozaiksai.core.workflow.generator_support.module_account_data import (
    materialize_module_account_handlers,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import pack_owned_outputs
from mozaiksai.core.workflow.generator_support.module_policy import materialize_module_policies
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    materialize_module_read_implementations,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    auth_contract_scopes,
    materialize_module_actions,
    materialize_module_schemas,
    materialize_module_write_implementations,
)

logger = logging.getLogger(__name__)


def _storage_name(collection: dict[str, Any]) -> Any:
    """The physical collection a contract row names, as the assignment store compiler resolves it."""
    return collection.get("mongo_collection") or collection.get("collection") or collection.get("name")


def _alias_identity(alias: Any) -> tuple[str, str] | None:
    """An alias row's name and target collection, or None when either is not a string."""
    if not isinstance(alias, dict):
        return None
    name = alias.get("alias") or alias.get("data_alias")
    target = alias.get("collection") or alias.get("mongo_collection")
    return (name, target) if isinstance(name, str) and isinstance(target, str) else None


#: The surface the subscription assignment store compiler declares its stores under.
_ASSIGNMENT_STORE_SURFACE = {"surface_id": "subscription_assignments", "surface_kind": "app_policy"}

_CANNOT_REPLACE = (
    "data/contract.json requires the approved DesignDocs data_contract; without it the selected "
    "packs' data contributions cannot replace a carried contract that "
)


def _is_assignment_surface(surface: dict[str, Any]) -> bool:
    return {key: surface.get(key) for key in _ASSIGNMENT_STORE_SURFACE} == _ASSIGNMENT_STORE_SURFACE


def _is_assignment_store(surface: dict[str, Any], collection: dict[str, Any]) -> bool:
    """Whether a carried collection is one the subscription assignment store compiler writes."""
    lifecycle = collection.get("lifecycle")
    return (
        _is_assignment_surface(surface) and collection.get("ownership") == _ASSIGNMENT_STORE_SURFACE
        and isinstance(lifecycle, dict) and lifecycle.get("write_mode") == "platform_sync"
        and isinstance(collection.get("data_alias"), str)
    )


def _carried_contract_conflict(content: str, composed: dict[str, Any]) -> str | None:
    """Why a carried contract is not only what selected packs and subscription stores derive, else None.

    ``composed`` is that derivation: the selected packs' contributed surfaces
    and the compiled subscription assignment store surface, on an empty base.
    Every carried surface must be one of its surfaces, with the same
    surface_kind, and hold only collections that surface declares, by name
    and physical collection. Nothing else inside such a collection is compared
    (fields, indexes, tenancy, owner_field, collection-level aliases), so a
    changed pack contribution recomposes. Every top-level alias must be one
    the derivation declares, so it targets a derived collection. Any other
    surface, collection or alias, any shared collection, and metadata the
    composition would change are the app's own. Order is not compared: a
    carried composition, such as the pack lifecycle's recomposition, may list
    the same surfaces in another order. Unreadable text never qualifies.

    The reason names the app's own data when there is any. Otherwise it names
    the derived data whose identity changed since the carried contract was
    written: a pack collection's physical collection, a surface without its
    surface_kind, or an assignment store or alias the current subscription
    contract no longer compiles. Their stored rows need an approved contract
    or a migration, never a silent re-point, so both fail closed.
    """
    try:
        carried = json.loads(content)
    except ValueError:
        return _CANNOT_REPLACE + "is not valid JSON"
    if not isinstance(carried, dict) or not all(
        isinstance(carried.get(key) or [], list) for key in ("surfaces", "aliases")
    ):
        return _CANNOT_REPLACE + "is not a data contract with surface and alias lists"
    derived = {
        surface["surface_id"]: (surface.get("surface_kind"), {
            collection.get("name"): _storage_name(collection) for collection in surface.get("collections") or []
        })
        for surface in composed.get("surfaces") or []
    }
    stores = {
        _storage_name(collection) for surface in composed.get("surfaces") or []
        for collection in surface.get("collections") or [] if _is_assignment_store(surface, collection)
    }
    owned: list[str] = []
    changed: list[str] = []
    for surface in carried.get("surfaces") or []:
        if not isinstance(surface, dict) or not isinstance(surface.get("surface_id"), str) or not isinstance(
            surface.get("collections") or [], list,
        ):
            return _CANNOT_REPLACE + "declares a malformed surface"
        surface_id, carried_kind = surface["surface_id"], surface.get("surface_kind")
        kind, collections = derived.get(surface_id, (None, None))
        if collections is None and not (_is_assignment_surface(surface) and surface.get("collections")):
            # An assignment store surface the run no longer compiles is judged by its collections.
            owned.append(f"surface {surface_id!r}")
            continue
        if collections is not None and carried_kind != kind:
            if carried_kind is None:
                changed.append(f"surface {surface_id!r} has no surface_kind, composed as {kind!r}")
            else:
                owned.append(f"surface {surface_id!r} of kind {carried_kind!r}, composed as {kind!r}")
        for collection in surface.get("collections") or []:
            if not isinstance(collection, dict) or not isinstance(collection.get("name"), str):
                return _CANNOT_REPLACE + f"declares a malformed collection in surface {surface_id!r}"
            name, storage = collection["name"], _storage_name(collection)
            if collections is not None and name in collections:
                if storage != collections[name]:
                    changed.append(
                        f"derived collection {name!r} in surface {surface_id!r} is stored in {storage!r}, "
                        f"composed as {collections[name]!r}"
                    )
            elif _is_assignment_store(surface, collection):
                stores.add(storage)
                changed.append(
                    f"subscription assignment store {name!r} in surface {surface_id!r} is not one the current "
                    "subscription contract compiles"
                )
            else:
                owned.append(f"collection {name!r} in surface {surface_id!r}")
    if carried.get("shared_collections"):
        owned.append("shared collections")
    aliases = {_alias_identity(alias) for alias in composed.get("aliases") or []} - {None}
    for alias in carried.get("aliases") or []:
        identity = _alias_identity(alias)
        if identity is None:
            return _CANNOT_REPLACE + "declares a malformed alias"
        if identity in aliases:
            continue
        if identity[1] in stores:
            changed.append(
                f"subscription assignment alias {identity[0]!r} -> {identity[1]!r} is not one the current "
                "subscription contract compiles"
            )
        else:
            owned.append(f"alias {identity[0]!r} -> {identity[1]!r}")
    metadata = set(carried) - {"version", "surfaces", "shared_collections", "aliases"}
    owned.extend(f"metadata {key!r}" for key in sorted(metadata) if carried[key] != composed.get(key))
    if owned:
        return _CANNOT_REPLACE + "holds app-owned data: " + "; ".join(owned)
    if changed:
        return (
            "data/contract.json requires the approved DesignDocs data_contract; the carried contract's derived "
            "data no longer matches the current pack/assignment composition: " + "; ".join(changed)
            + ". Reassemble with an approved DesignDocs data_contract or migrate the stored data"
        )
    return None


def _merge_code_files(
    feature_outputs: list[dict[str, Any]],
    *,
    build_timestamp: str | None = None,
    app_build_plan: dict[str, Any] | None = None,
    data_contract: dict[str, Any] | None = None,
    design_surface_map: dict[str, Any] | None = None,
    subscription_contract: dict[str, Any] | None = None,
    context_variables: Any = None,
) -> list[dict[str, str]]:
    """Merge code_files from feature outputs, deduping by filename."""
    file_map: dict[str, str] = {}
    for output in feature_outputs:
        for item in extract_code_file_entries_from_payload(
            output,
            build_timestamp=build_timestamp,
        ):
            filename = item.get("filename")
            content = item.get("content")
            if not filename or content is None:
                continue
            file_map[str(filename)] = str(content)
    inventory = verified_pack_template_inventory(context_variables=context_variables)
    pack_data_contracts = verified_pack_data_contracts(inventory)
    # Without an approved contract the selected packs' contributions compose onto
    # an empty base. A carried data/contract.json is not an approval, so the base
    # may replace it only when it holds nothing the app owns: only the
    # collections and aliases this composition declares, such as an earlier
    # assembly or pack lifecycle composition. Otherwise the app's own surfaces,
    # collections or aliases would be silently dropped, or a derived
    # collection's stored rows silently re-pointed; the error names which.
    pack_only_base = data_contract is None and bool(pack_data_contracts)
    if pack_only_base:
        data_contract = {"version": "1", "surfaces": [], "shared_collections": []}
    if isinstance(data_contract, dict):
        data_contract = compile_data_contract(
            data_contract, subscription_contract=subscription_contract, context_variables=context_variables,
            pack_data_contracts=pack_data_contracts,
        )
        carried = file_map.get("data/contract.json")
        conflict = _carried_contract_conflict(carried, data_contract) if pack_only_base and carried is not None else None
        if conflict is not None:
            raise ValueError(conflict)
    elif subscription_contract is not None:
        assignment_contract = compile_data_contract(
            {"version": "1", "surfaces": [], "shared_collections": []},
            subscription_contract=subscription_contract, context_variables=context_variables,
        )
        if assignment_contract.get("aliases"):
            data_contract = assignment_contract
    owned_paths = list(file_map)
    if pack_data_contracts:
        owned_paths.append("data/contract.json")
    if subscription_contract is not None and (data_contract or {}).get("aliases"):
        owned_paths.append("data/contract.json")
    file_map = materialize_data_contract(
        file_map, data_contract=data_contract, owned_paths=owned_paths,
        subscription_contract=subscription_contract, context_variables=context_variables,
    )
    file_map = materialize_collection_auth(file_map, data_contract=data_contract)
    # Selected pack manifests come from registered templates, never model tasks.
    pack_paths = pack_owned_outputs(context_variables)
    template_modules = verified_template_module_ids(file_map, inventory)
    # A complete verified template backend owns its participant-aware policy and
    # action implementations. All other modules retain canonical CRUD synthesis.
    synthesis_files = {
        path: content for path, content in file_map.items()
        if not (path.startswith("modules/") and path.split("/")[1] in template_modules)
    }
    # Feature outputs may carry an auth edit alongside a module. Only the
    # pre-merge admitted bundle (or the canonical scaffold) can approve scopes.
    admitted_scopes = (
        auth_contract_scopes(
            admitted_app_file_map(context_variables),
            app_build_plan=app_build_plan,
            data_contract=data_contract,
        ) if app_build_plan is not None else None
    )
    synthesis_files.update(materialize_module_actions(
        synthesis_files, app_build_plan=app_build_plan, data_contract=data_contract,
        design_surface_map=design_surface_map,
        subscription_contract=subscription_contract,
        declared_auth_scopes=admitted_scopes,
        pack_owned_manifest_paths=frozenset(
            path for path in pack_paths if path.startswith("modules/") and path.endswith("/module.yaml")
        ),
    ))
    synthesis_files.update(materialize_module_policies(synthesis_files, data_contract))
    synthesis_files.update(materialize_module_schemas(synthesis_files, app_build_plan=app_build_plan, data_contract=data_contract))
    synthesis_files.update(materialize_module_account_handlers(
        synthesis_files, app_build_plan=app_build_plan, data_contract=data_contract,
    ))
    service_paths = [
        path for task in (app_build_plan or {}).get("build_tasks") or []
        if task.get("task_type") == "business_services"
        for path in task.get("owned_paths") or []
    ]
    # Partially templated modules still receive canonical implementations.
    service_paths.extend(
        path for path in pack_paths
        if path in synthesis_files and len(parts := path.split("/")) == 4
        and parts[0] == "modules" and parts[2] == "backend"
        and parts[3] in {"handler.py", "service.py", "repo.py"}
    )
    synthesis_files.update(materialize_module_read_implementations(
        synthesis_files, app_build_plan=app_build_plan, data_contract=data_contract,
        subscription_contract=subscription_contract, owned_paths=service_paths,
    ))
    synthesis_files.update(materialize_module_write_implementations(
        synthesis_files, app_build_plan=app_build_plan, data_contract=data_contract, owned_paths=service_paths,
        subscription_contract=subscription_contract,
    ))
    file_map.update(synthesis_files)
    if "app.json" in file_map:
        file_map.update(materialize_auth_scaffold(file_map, data_contract=data_contract))
        file_map.setdefault("ui/lib/moduleApi.js", get_module_api_template())
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


async def assemble_features(
    app_id: str,
    feature_outputs: list[dict[str, Any]],
    *,
    build_timestamp: str | None = None,
    app_build_plan: dict[str, Any] | None = None,
    data_contract: dict[str, Any] | None = None,
    design_surface_map: dict[str, Any] | None = None,
    subscription_contract: dict[str, Any] | None = None,
    context_variables: Any = None,
) -> dict[str, Any]:
    """
    Merge feature outputs into a single workflow bundle.

    Args:
        app_id: The parent application ID
        feature_outputs: List of outputs from AppGenerator(feature scope)

    Returns:
        {
            "success": bool,
            "code_files": [...],  # All merged files
            "message": str,
        }
    """
    # The workflow tool owns failure reporting. A failed materializer must not
    # become an empty bundle that later checks mistake for successful assembly.
    merged_files = _merge_code_files(
        feature_outputs or [],
        build_timestamp=build_timestamp,
        app_build_plan=app_build_plan,
        data_contract=data_contract,
        design_surface_map=design_surface_map,
        subscription_contract=subscription_contract,
        context_variables=context_variables,
    )
    logger.info(
        "Assembled %d feature outputs into %d files",
        len(feature_outputs or []),
        len(merged_files),
    )
    return {
        "success": True,
        "code_files": merged_files,
        "message": f"Assembled {len(merged_files)} files",
    }

