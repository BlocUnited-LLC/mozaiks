"""Inject factory build-context file, module, and workflow archetypes into prompts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from factory_app.workflows._shared.hook_utils import workflow_context_path

logger = logging.getLogger(__name__)

_FILE_CONTRACTS_PATH = workflow_context_path("AppGenerator", "file_contracts.yaml")
_MODULE_ARCHETYPES_PATH = workflow_context_path("AppGenerator", "module_archetypes.yaml")
_WORKFLOW_ARCHETYPES_PATH = workflow_context_path("AppGenerator", "workflow_archetypes.yaml")

_FILE_CONTRACTS_HEADER = "[FILE CONTRACTS CONTEXT]"
_MODULE_ARCHETYPES_HEADER = "[MODULE ARCHETYPES CONTEXT]"
_WORKFLOW_ARCHETYPES_HEADER = "[WORKFLOW ARCHETYPE CONTEXT]"

_PLANNING_CONTRACT_ORDER = (
    "page_bundle",
    "module_contract",
    "persistence_contract",
    "service_foundation",
    "refinement_harness",
    "api_surface",
)

_AGENT_DEFAULT_CONTRACTS = {
    "AppSchemaAgent": ["page_bundle"],
    "RefinementHarnessAgent": ["refinement_harness"],
    "ServiceAgent": ["module_contract"],
    "FrontendStubAgent": ["module_contract"],
    "ControllerAgent": ["api_surface"],
}


def _load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path.name, exc)
        return None


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _update_section(agent: Any, header: str, body: str) -> None:
    current = (
        getattr(agent, "system_message", None)
        or getattr(agent, "_system_message", "")
        or ""
    )
    section = f"{header}\n{body}"

    if header in current:
        pre, _, rest = current.partition(header)
        next_section_idx = rest.find("\n\n[")
        after = rest[next_section_idx:] if next_section_idx > 0 else ""
        new_message = f"{pre.rstrip()}\n\n{section}{after}"
    else:
        new_message = f"{current}\n\n{section}" if current else section

    if new_message == current:
        return

    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(new_message)
    elif hasattr(agent, "_system_message"):
        agent._system_message = new_message
    else:
        agent._system_message = new_message


def _format_list_block(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    lines = [title]
    lines.extend(f"  - {item}" for item in items)
    return lines


def _build_contract_block(contract_name: str, contract: dict[str, Any]) -> str:
    lines = [f"{contract_name}:"]
    owner_agent = str(contract.get("owner_agent") or "").strip()
    summary = str(contract.get("summary") or "").strip()
    if owner_agent:
        lines.append(f"  owner_agent: {owner_agent}")
    if summary:
        lines.append(f"  summary: {summary}")

    required_outputs = [str(item) for item in contract.get("required_outputs") or [] if str(item).strip()]
    optional_outputs = [str(item) for item in contract.get("optional_outputs") or [] if str(item).strip()]
    downstream_python = [str(item) for item in contract.get("downstream_python_defaults") or [] if str(item).strip()]
    optional_python_hooks = [str(item) for item in contract.get("optional_python_hooks") or [] if str(item).strip()]
    optional_js_stubs = [str(item) for item in contract.get("optional_js_stubs") or [] if str(item).strip()]
    hard_constraints = [str(item) for item in contract.get("hard_constraints") or [] if str(item).strip()]

    lines.extend(_format_list_block("  required_outputs:", required_outputs))
    lines.extend(_format_list_block("  optional_outputs:", optional_outputs))
    lines.extend(_format_list_block("  downstream_python_defaults:", downstream_python))
    lines.extend(_format_list_block("  optional_python_hooks:", optional_python_hooks))
    lines.extend(_format_list_block("  optional_js_stubs:", optional_js_stubs))
    lines.extend(_format_list_block("  hard_constraints:", hard_constraints[:5]))
    return "\n".join(lines)


def _current_task_type(agent: Any) -> str:
    context_variables = getattr(agent, "context_variables", None)
    current_build_task = _context_get(context_variables, "current_build_task", {}) or {}
    if isinstance(current_build_task, dict):
        task_type = str(current_build_task.get("task_type") or current_build_task.get("current_build_task_type") or "").strip()
        if task_type:
            return task_type
    return str(_context_get(context_variables, "current_build_task_type", "") or "").strip()


def _build_file_contracts_body(agent: Any, file_contracts: dict[str, Any]) -> str:
    task_contracts = file_contracts.get("task_contracts") if isinstance(file_contracts.get("task_contracts"), dict) else {}
    agent_name = getattr(agent, "name", "")

    lines = [
        "These task/file contracts are prompt-time guidance aligned to the current structured outputs.",
        "They keep modular YAML and Python/JS outputs cookie-cutter across domains.",
        "Runtime truth remains build_tasks, owned_paths, structured outputs, and runtime loaders.",
    ]

    if agent_name == "AppPlanAgent":
        lines.append("")
        lines.append("Plan only with the active AppGenerator task vocabulary:")
        for contract_name in _PLANNING_CONTRACT_ORDER:
            contract = task_contracts.get(contract_name)  # type: ignore[union-attr]
            if isinstance(contract, dict):
                lines.append(_build_contract_block(contract_name, contract))
        return "\n\n".join(lines)

    target_contract_names = _AGENT_DEFAULT_CONTRACTS.get(agent_name, [])
    task_type = _current_task_type(agent)

    if agent_name == "ConfigMiddlewareAgent":
        if task_type in {"module_contract", "service_foundation"}:
            target_contract_names = [task_type]
        else:
            target_contract_names = ["module_contract", "service_foundation"]
    elif agent_name == "RefinementHarnessAgent":
        target_contract_names = ["refinement_harness"]
    elif agent_name == "ControllerAgent" and task_type == "module_contract":
        target_contract_names = ["api_surface", "module_contract"]

    for contract_name in target_contract_names:
        contract = task_contracts.get(contract_name)  # type: ignore[union-attr]
        if isinstance(contract, dict):
            lines.append("")
            lines.append(_build_contract_block(contract_name, contract))

    return "\n".join(lines) if len(lines) > 3 else ""


def _selected_module_archetype(agent: Any) -> str:
    context_variables = getattr(agent, "context_variables", None)
    current_task = _context_get(context_variables, "current_build_task", {}) or {}
    if isinstance(current_task, dict):
        for key in ("module_archetype", "recommended_module_type"):
            value = str(current_task.get(key) or "").strip()
            if value:
                return value
    # Fall back to module type declared in the module contract
    module_contract = _context_get(context_variables, "module_contract", {}) or {}
    if isinstance(module_contract, dict):
        module_yaml = module_contract.get("module_yaml") or {}
        if isinstance(module_yaml, dict):
            module_meta = module_yaml.get("module") or {}
            if isinstance(module_meta, dict):
                value = str(module_meta.get("type") or "").strip()
                if value:
                    return value
    return ""


def _build_archetype_block(name: str, archetype: dict[str, Any]) -> str:
    lines = [f"{name}:"]
    summary = str(archetype.get("summary") or "").strip()
    if summary:
        lines.append(f"  summary: {summary}")
    canonical_module_type = str(archetype.get("canonical_module_type") or "").strip()
    if canonical_module_type:
        lines.append(f"  canonical_module_type: {canonical_module_type}")
    source_pack = str(archetype.get("source_pack") or "").strip()
    if source_pack:
        lines.append(f"  source_pack: {source_pack}")

    select_when = [str(item) for item in archetype.get("select_when") or [] if str(item).strip()]
    yaml_family = archetype.get("canonical_yaml_family") if isinstance(archetype.get("canonical_yaml_family"), dict) else {}
    always = [str(item) for item in yaml_family.get("always") or [] if str(item).strip()]  # type: ignore[union-attr]
    optional = [str(item) for item in yaml_family.get("optional") or [] if str(item).strip()]  # type: ignore[union-attr]
    python_defaults = [
        str(item)
        for item in (
            archetype.get("python_stub_defaults")
            or archetype.get("backend_stub_defaults")
            or []
        )
        if str(item).strip()
    ]
    hard_constraints = [str(item) for item in archetype.get("hard_constraints") or [] if str(item).strip()]

    lines.extend(_format_list_block("  select_when:", select_when[:3]))
    lines.extend(_format_list_block("  yaml_always:", always))
    lines.extend(_format_list_block("  yaml_optional:", optional))
    lines.extend(_format_list_block("  python_stub_defaults:", python_defaults))
    lines.extend(_format_list_block("  hard_constraints:", hard_constraints[:4]))
    return "\n".join(lines)


def _build_module_archetypes_body(agent: Any, module_archetypes: dict[str, Any]) -> str:
    agent_name = getattr(agent, "name", "")
    if agent_name not in {"ConfigMiddlewareAgent", "ServiceAgent"}:
        return ""

    archetypes = module_archetypes.get("archetypes") if isinstance(module_archetypes.get("archetypes"), dict) else {}
    behavior_patterns = (
        module_archetypes.get("behavior_patterns")
        if isinstance(module_archetypes.get("behavior_patterns"), dict)
        else {}
    )
    if not archetypes:
        return ""

    selected_type = _selected_module_archetype(agent)
    lines = [
        "Module archetypes keep backend layering cookie-cutter across domains.",
        "They guide conventions only; never serialize archetype names into module.yaml or replace owned_paths.",
    ]

    if selected_type and selected_type in archetypes:
        lines.append("")
        lines.append(f"Selected module type: {selected_type}")
        lines.append(_build_archetype_block(selected_type, archetypes[selected_type]))
        other_names = [name for name in archetypes.keys() if name != selected_type]
        if other_names:
            lines.append("")
            lines.append("Other available backend archetypes: " + ", ".join(other_names))
        if behavior_patterns:
            lines.append("")
            lines.append(
                "Available behavior patterns (map to canonical module types; do not serialize pattern names as module.type): "
                + ", ".join(str(name) for name in behavior_patterns)
            )
        return "\n\n".join(lines)

    for name, archetype in archetypes.items():
        if not isinstance(archetype, dict):
            continue
        lines.append("")
        lines.append(_build_archetype_block(name, archetype))

    if behavior_patterns:
        lines.append("")
        lines.append(
            "Behavior patterns map to supported module.type values; never serialize "
            "the behavior pattern key as module.type."
        )
        for name, pattern in behavior_patterns.items():
            if not isinstance(pattern, dict):
                continue
            lines.append("")
            lines.append(_build_archetype_block(str(name), pattern))

    return "\n\n".join(lines)


def _build_workflow_archetypes_body(agent: Any, workflow_archetypes: dict[str, Any]) -> str:
    """Render workflow archetype guidance for injection into AppPlanAgent."""
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppPlanAgent":
        return ""

    archetypes = workflow_archetypes.get("archetypes")
    if not isinstance(archetypes, dict) or not archetypes:
        return ""

    lines = [
        "Workflow archetypes guide orchestrator shape, agent sequence, tool constraints,",
        "and output invariants for workflow-surface capability packs. Apply when planning",
        "any capability pack with surface_kind=workflow.",
    ]

    for name, archetype in archetypes.items():
        if not isinstance(archetype, dict):
            continue

        summary = str(archetype.get("summary", "")).strip()
        select_when = archetype.get("select_when") or []
        hard_constraints = archetype.get("hard_constraints") or []
        blocked_pattern = archetype.get("blocked_phase_pattern")

        lines.append("")
        lines.append(f"archetype: {name}")
        if summary:
            lines.append(f"  summary: {summary[:250]}")
        if select_when:
            lines.append("  select_when:")
            for sw in select_when[:5]:
                lines.append(f"    - {str(sw).strip()[:140]}")
        if hard_constraints:
            lines.append("  hard_constraints:")
            for hc in hard_constraints[:6]:
                lines.append(f"    - {str(hc).strip()[:140]}")
        if blocked_pattern and isinstance(blocked_pattern, dict):
            rule = str(blocked_pattern.get("rule", "")).strip()
            if rule:
                lines.append("  blocked_phase_rule:")
                lines.append(f"    {rule[:250]}")

    return "\n".join(lines)


def inject_cookie_cutter_contracts_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inject file contracts, module archetypes, and workflow archetypes into AppGenerator agents."""
    del messages

    agent_name = getattr(agent, "name", "")
    if agent_name not in {
        "AppPlanAgent",
        "AppSchemaAgent",
        "RefinementHarnessAgent",
        "ConfigMiddlewareAgent",
        "ServiceAgent",
        "FrontendStubAgent",
        "ControllerAgent",
    }:
        return

    try:
        file_contracts = _load_yaml(_FILE_CONTRACTS_PATH)
        if file_contracts:
            file_contracts_body = _build_file_contracts_body(agent, file_contracts)
            if file_contracts_body:
                _update_section(agent, _FILE_CONTRACTS_HEADER, file_contracts_body)

        module_archetypes = _load_yaml(_MODULE_ARCHETYPES_PATH)
        if module_archetypes:
            module_archetypes_body = _build_module_archetypes_body(agent, module_archetypes)
            if module_archetypes_body:
                _update_section(agent, _MODULE_ARCHETYPES_HEADER, module_archetypes_body)

        workflow_archetypes = _load_yaml(_WORKFLOW_ARCHETYPES_PATH)
        if workflow_archetypes:
            workflow_archetypes_body = _build_workflow_archetypes_body(agent, workflow_archetypes)
            if workflow_archetypes_body:
                _update_section(agent, _WORKFLOW_ARCHETYPES_HEADER, workflow_archetypes_body)

    except Exception as exc:
        logger.error("[%s] Failed to inject cookie-cutter contracts context: %s", agent_name, exc)


__all__ = ["inject_cookie_cutter_contracts_context"]



