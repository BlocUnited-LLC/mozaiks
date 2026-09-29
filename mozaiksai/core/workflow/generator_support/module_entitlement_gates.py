"""Compile approved action gates into generated module manifests."""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

import yaml

from mozaiksai.core.taxonomy import SemanticCategory, validate_identifier_grammar
from mozaiksai.core.workflow.context.frozen import detach


def capability_id_for_feature(feature_id: str) -> str:
    """Name a selected pricing feature once for plans and entitlement gates."""
    def component(value: str) -> str:
        snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", value)
        return re.sub(r"[^a-z0-9_]+", "_", snake.lower()).strip("_")

    parts = [component(part) for part in feature_id.split(".")]
    if any(not part for part in parts):
        raise ValueError(f"Feature id {feature_id!r} cannot form a capability id.")
    return validate_identifier_grammar(SemanticCategory.CAPABILITY, "feature." + ".".join(parts))


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


def approved_subscription_gates(
    subscription_contract: Any, *, approved_actions: Mapping[str, list[str]],
    ungated_actions: Mapping[str, list[str]], approved_workflows: list[str],
) -> dict[str, dict[str, str]] | None:
    """Project gates from persisted plan selections for task batch compilation."""
    contract = subscription_contract_payload(subscription_contract)
    if contract is None:
        return None
    if not contract.get("contract_required"):
        return {}
    selections = contract.get("selected_features_by_plan")
    if not isinstance(selections, Mapping):
        raise ValueError("selected_features_by_plan is required on a derived subscription contract.")
    plans = (contract.get("subscription_config_file") or {}).get("plans") or []
    if set(selections) != {plan.get("plan_id") for plan in plans}:
        raise ValueError("selected_features_by_plan must contain exactly the declared plan ids.")
    result: dict[str, dict[str, str]] = {}
    expected_workflows: dict[str, str] = {}
    for plan in plans:
        features = selections[plan["plan_id"]]
        if not isinstance(features, (list, tuple)) or any(not isinstance(feature, str) for feature in features):
            raise ValueError("selected_features_by_plan must map plan ids to feature id lists.")
        if len(features) != len(set(features)):
            raise ValueError(f"Plan {plan['plan_id']!r} repeats a selected feature.")
        expected = sorted({capability_id_for_feature(feature) for feature in features})
        if sorted(plan.get("capabilities") or []) != expected:
            raise ValueError(f"Plan {plan['plan_id']!r} capabilities differ from its selected features.")
        for feature in features:
            kind, separator, reference = feature.partition(".")
            if not separator or kind not in {"module", "workflow"}:
                raise ValueError(f"Invalid selected feature id {feature!r}.")
            if kind == "workflow":
                if reference not in approved_workflows:
                    valid = [f"workflow.{surface_id}" for surface_id in sorted(set(approved_workflows))]
                    raise ValueError(
                        f"Selected workflow feature {feature!r} is not an approved workflow surface. "
                        f"Valid workflow features: {valid}. Remove the feature from the plan or have "
                        "DesignDocs approve an app-owned workflow surface with agentic capabilities."
                    )
                expected_workflows[reference] = capability_id_for_feature(feature)
                continue
            module_id, separator, action_id = reference.partition(".")
            if not separator or not module_id or not action_id:
                raise ValueError(f"Invalid selected module feature id {feature!r}.")
            if action_id not in approved_actions.get(module_id, []) or action_id in ungated_actions.get(module_id, []):
                raise ValueError(
                    f"Selected module feature {feature!r} is not an approved gate target. "
                    f"Valid actions for {module_id!r}: {sorted(set(approved_actions.get(module_id, [])) - set(ungated_actions.get(module_id, [])))}. "
                    "Remove the feature from the plan or approve its action in DesignDocs."
                )
            result.setdefault(module_id, {})[action_id] = capability_id_for_feature(feature)
    supplied: dict[str, dict[str, str]] = {}
    seen_updates: set[tuple[str, str]] = set()
    for update in contract.get("module_contract_updates") or []:
        action = (update["module_id"], update["action_id"])
        if action in seen_updates:
            raise ValueError(f"module_contract_updates repeats derived action {action!r}.")
        seen_updates.add(action)
        gate = update.get("entitlement_gate")
        if gate is not None:
            supplied.setdefault(update["module_id"], {})[update["action_id"]] = gate
    if supplied != result:
        raise ValueError("module_contract_updates differ from gates derived from selected features.")
    workflow_updates = contract.get("workflow_contract_updates") or []
    actual_workflows = {
        update["design_surface_id"]: update["capability_id"] for update in workflow_updates
    }
    if len(actual_workflows) != len(workflow_updates) or actual_workflows != expected_workflows:
        raise ValueError("workflow_contract_updates differ from selected workflow features.")
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
