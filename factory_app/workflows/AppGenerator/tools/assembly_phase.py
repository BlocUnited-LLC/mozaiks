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
    extract_code_file_entries_from_payload,
)
from factory_app.workflows.AppGenerator.tools.render_auth_scaffold import materialize_auth_scaffold
from mozaiksai.core.workflow.generator_support.code_files import (
    compile_data_contract,
    materialize_collection_auth,
    materialize_data_contract,
)
from mozaiksai.core.workflow.generator_support.module_policy import materialize_module_policies
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    materialize_module_read_implementations,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import (
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
    if isinstance(data_contract, dict):
        data_contract = compile_data_contract(
            data_contract, subscription_contract=subscription_contract, context_variables=context_variables,
        )
    elif subscription_contract is not None:
        assignment_contract = compile_data_contract(
            {"version": "1", "surfaces": [], "shared_collections": []},
            subscription_contract=subscription_contract, context_variables=context_variables,
        )
        if assignment_contract.get("aliases"):
            data_contract = assignment_contract
    owned_paths = list(file_map)
    if subscription_contract is not None and (data_contract or {}).get("aliases"):
        owned_paths.append("data/contract.json")
    file_map = materialize_data_contract(
        file_map, data_contract=data_contract, owned_paths=owned_paths,
        subscription_contract=subscription_contract, context_variables=context_variables,
    )
    file_map = materialize_collection_auth(file_map, data_contract=data_contract)
    file_map.update(materialize_module_actions(
        file_map, app_build_plan=app_build_plan, data_contract=data_contract,
        design_surface_map=design_surface_map,
        subscription_contract=subscription_contract,
    ))
    file_map.update(materialize_module_policies(file_map, data_contract))
    file_map.update(materialize_module_schemas(file_map, app_build_plan=app_build_plan, data_contract=data_contract))
    service_paths = [
        path for task in (app_build_plan or {}).get("build_tasks") or []
        if task.get("task_type") == "business_services"
        for path in task.get("owned_paths") or []
    ]
    file_map.update(materialize_module_read_implementations(
        file_map, app_build_plan=app_build_plan, data_contract=data_contract,
        subscription_contract=subscription_contract, owned_paths=service_paths,
    ))
    file_map.update(materialize_module_write_implementations(
        file_map, app_build_plan=app_build_plan, data_contract=data_contract, owned_paths=service_paths,
    ))
    if "app.json" in file_map:
        file_map.update(materialize_auth_scaffold(file_map, data_contract=data_contract))
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

