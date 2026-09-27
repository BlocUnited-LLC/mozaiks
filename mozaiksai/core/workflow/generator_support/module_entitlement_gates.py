"""Compile approved action gates into generated module manifests."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.workflow.context.frozen import detach


def subscription_contract_payload(value: Any) -> dict[str, Any] | None:
    value = detach(value)
    if not isinstance(value, Mapping):
        return None
    if isinstance(value.get("contract_required"), bool):
        return dict(value)

    commit_metadata = value.get("commit_metadata")
    if isinstance(commit_metadata, Mapping):
        metadata = commit_metadata.get("metadata")
        if isinstance(metadata, Mapping):
            payload = metadata.get("summary_payload")
            if isinstance(payload, Mapping) and isinstance(payload.get("contract_required"), bool):
                return dict(payload)

    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        payload = metadata.get("summary_payload")
        if isinstance(payload, Mapping) and isinstance(payload.get("contract_required"), bool):
            return dict(payload)

    return None


def resolve_subscription_contract(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    for key in ("subscription_contract", "subscription_contract_artifact"):
        payload = subscription_contract_payload(data.get(key))
        if payload is not None:
            return payload
    return None


def approved_subscription_gates(subscription_contract: Any) -> dict[str, dict[str, str]] | None:
    """Project already validated subscription decisions supplied by trusted context.

    Factory validates capability grants, action inventory, and facade exclusions
    before this contract crosses the task boundary. This projection never chooses
    a feature or derives gates from generated module source.
    """
    contract = subscription_contract_payload(subscription_contract)
    if contract is None:
        return None
    if not contract.get("contract_required"):
        return {}
    result: dict[str, dict[str, str]] = {}
    for update in contract.get("module_contract_updates") or []:
        gate = update.get("entitlement_gate")
        if gate is not None:
            result.setdefault(update["module_id"], {})[update["action_id"]] = gate
    return result


def compile_module_entitlement_gates(
    files: Mapping[str, str], *, gates_by_module: Mapping[str, Mapping[str, str]] | None,
    approved_actions: Mapping[str, list[str]],
    ungated_actions: Mapping[str, list[str]],
    existing_files: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Apply the single approved gate map and remove every unapproved model gate."""
    file_map = dict(files)
    for path, content in files.items():
        pure = PurePosixPath(path)
        if len(pure.parts) != 3 or pure.parts[0] != "modules" or pure.parts[2] != "module.yaml":
            continue
        module_id = pure.parts[1]
        gates = (gates_by_module or {}).get(module_id, {})
        always_ungated = set(ungated_actions.get(module_id, []))
        if forbidden := set(gates) & always_ungated:
            raise ValueError(
                f"{path}: canonical collection reads and managed facade actions cannot have entitlement gates: "
                f"{sorted(forbidden)}. Declare a custom read for paid view behavior."
            )
        if set(gates) - set(approved_actions.get(module_id, [])):
            raise ValueError(f"{path}: entitlement gates must reference approved module actions")
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError(f"{path}: cannot apply approved entitlement mapping to invalid YAML: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
            if gates:
                raise ValueError(f"{path}: approved entitlement mapping requires an actions list")
            continue  # The module contract validator owns malformed ungated manifests.
        actions = data["actions"]
        if forbidden := {
            action_id for action in actions if isinstance(action, dict)
            and isinstance(action_id := action.get("id"), str)
            and action_id in always_ungated and action.get("entitlement_gate")
        }:
            raise ValueError(
                f"{path}: canonical collection reads and managed facade actions cannot have entitlement gates: "
                f"{sorted(forbidden)}. Declare a custom read for paid view behavior."
            )
        if gates_by_module is None:
            existing = yaml.safe_load((existing_files or {}).get(path, "")) or {}
            previous_actions = existing.get("actions", []) if isinstance(existing, dict) else []
            if any(action.get("entitlement_gate") for action in [*actions, *previous_actions]
                   if isinstance(action, dict)):
                raise ValueError(
                    f"{path}: an approved subscription contract is required before writing a gated "
                    "module manifest; existing entitlement gates cannot be removed without that authority."
                )
        action_ids = [str(action.get("id") or "") for action in actions if isinstance(action, dict)]
        if module_id in approved_actions:
            unapproved = sorted(set(action_ids) - set(approved_actions[module_id]))
            if unapproved:
                raise ValueError(
                    f"{path}: unapproved module actions {unapproved}; every action must be declared "
                    "in DesignDocs owned_mutations/custom_reads or be a canonical collection read. "
                    f"Valid approved action ids: {approved_actions[module_id]}."
                )
        missing = sorted(set(gates) - set(action_ids))
        duplicates = sorted({action_id for action_id in gates if action_ids.count(action_id) > 1})
        if missing or duplicates:
            raise ValueError(
                f"{path}: cannot resolve approved entitlement actions; missing={missing}, duplicate={duplicates}. "
                f"Valid approved action ids: {approved_actions.get(module_id, [])}; generated action ids: {sorted(action_ids)}."
            )
        changed = False
        metadata = data.get("module")
        if module_id in approved_actions and isinstance(metadata, dict) and metadata.get("id") != module_id:
            metadata["id"] = module_id
            changed = True
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            if action_id in gates:
                if action.get("entitlement_gate") != gates[action_id]:
                    action["entitlement_gate"] = gates[action_id]
                    changed = True
            elif "entitlement_gate" in action:
                del action["entitlement_gate"]
                changed = True
        if changed:
            file_map[path] = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return file_map
