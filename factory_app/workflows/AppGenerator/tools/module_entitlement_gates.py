"""Compile approved subscription gates on initial assembly and repaired manifests."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any

from factory_app.workflows._shared.subscription_contract_context import (
    validate_module_contract_updates,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload,
    safe_relpath,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import all_module_actions
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    compile_module_entitlement_gates,
    resolve_subscription_contract,
)

logger = logging.getLogger(__name__)


def apply_entitlement_gates(
    code_files: list[dict[str, str]],
    *,
    context_variables: Any,
    require_all_modules: bool = True,
    failed_task_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Compile the approved gate decisions after all module files are merged."""
    if context_variables is None:
        return code_files
    contract = resolve_subscription_contract({
        key: detach(context_variables.get(key))
        for key in ("subscription_contract", "subscription_contract_artifact")
    })
    gates_by_module = validate_module_contract_updates(contract, context_variables) if contract is not None else None
    approved_modules = all_module_actions(context_variables)

    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
    existing = dict(detach(context_variables.get("generated_files")) or {})
    existing.update(extract_code_file_map_from_payload({"code_files": detach(context_variables.get("code_files"))}))
    file_map = compile_module_entitlement_gates(
        file_map, gates_by_module=gates_by_module, approved_actions=approved_modules,
        existing_files=existing,
    )
    present = {
        pure.parts[1] for path in file_map
        if len((pure := PurePosixPath(path)).parts) == 3
        and pure.parts[0] == "modules" and pure.parts[2] == "module.yaml"
    }
    remaining = set(gates_by_module or {}) - present if require_all_modules else set()
    if remaining:
        failed_tasks = failed_task_ids or set()
        plan = detach(context_variables.get("app_build_plan")) or {}
        for module_id in sorted(remaining.copy()):
            path = f"modules/{module_id}/module.yaml"
            owners = {
                str(task.get("task_id") or "")
                for task in plan.get("build_tasks") or []
                if path in {safe_relpath(str(owned)) for owned in task.get("owned_paths") or []}
            }
            if owners and owners <= failed_tasks:
                # Partial batches must reach acceptance and bounded recovery.
                # Only recorded failure of every exact path owner excuses absence.
                remaining.remove(module_id)
                logger.info(
                    "[AppGenerator] entitlement module %s not assembled: owning tasks %s failed",
                    path, sorted(owners),
                )
    if remaining:
        raise ValueError(
            f"Missing module.yaml for approved entitlement module ids {sorted(remaining)}. "
            f"Valid approved module ids: {sorted(approved_modules)}."
        )
    return [{"filename": k, "content": v} for k, v in sorted(file_map.items())]
