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
    if data_contract is None and pack_data_contracts:
        data_contract = {"version": "1", "surfaces": [], "shared_collections": []}
    if isinstance(data_contract, dict):
        data_contract = compile_data_contract(
            data_contract, subscription_contract=subscription_contract, context_variables=context_variables,
            pack_data_contracts=pack_data_contracts,
        )
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

