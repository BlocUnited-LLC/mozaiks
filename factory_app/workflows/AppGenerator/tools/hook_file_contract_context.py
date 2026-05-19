"""Inject AppGenerator file contracts and module archetypes into agent prompts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).parent
_FILE_CONTRACTS_PATH = _TOOLS_DIR / "file_contracts.yaml"
_MODULE_ARCHETYPES_PATH = _TOOLS_DIR / "module_archetypes.yaml"

_FILE_CONTRACTS_HEADER = "[FILE CONTRACTS CONTEXT]"
_MODULE_ARCHETYPES_HEADER = "[MODULE ARCHETYPES CONTEXT]"

_PLANNING_CONTRACT_ORDER = (
    "page_bundle",
    "module_contract",
    "persistence_contract",
    "backend_foundation",
    "control_plane_pack",
    "api_surface",
    "agent_backend_integration",
)

_AGENT_DEFAULT_CONTRACTS = {
    "AppSchemaAgent": ["page_bundle"],
    "ControlPlaneAgent": ["control_plane_pack"],
    "ServiceAgent": ["module_contract"],
    "FrontendStubAgent": ["module_contract"],
    "ControllerAgent": ["api_surface"],
}


def _load_yaml(path: Path) -> Dict[str, Any] | None:
    try:
        import yaml  # type: ignore

        with open(path, "r", encoding="utf-8") as fh:
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
        setattr(agent, "_system_message", new_message)


def _format_list_block(title: str, items: List[str]) -> List[str]:
    if not items:
        return []
    lines = [title]
    lines.extend(f"  - {item}" for item in items)
    return lines


def _build_contract_block(contract_name: str, contract: Dict[str, Any]) -> str:
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


def _build_file_contracts_body(agent: Any, file_contracts: Dict[str, Any]) -> str:
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
            contract = task_contracts.get(contract_name)
            if isinstance(contract, dict):
                lines.append(_build_contract_block(contract_name, contract))
        return "\n\n".join(lines)

    target_contract_names = _AGENT_DEFAULT_CONTRACTS.get(agent_name, [])
    task_type = _current_task_type(agent)

    if agent_name == "ConfigMiddlewareAgent":
        if task_type in {"module_contract", "backend_foundation"}:
            target_contract_names = [task_type]
        else:
            target_contract_names = ["module_contract", "backend_foundation"]
    elif agent_name == "ControlPlaneAgent":
        target_contract_names = ["control_plane_pack"]
    elif agent_name == "ControllerAgent" and task_type == "module_contract":
        target_contract_names = ["api_surface", "module_contract"]

    for contract_name in target_contract_names:
        contract = task_contracts.get(contract_name)
        if isinstance(contract, dict):
            lines.append("")
            lines.append(_build_contract_block(contract_name, contract))

    return "\n".join(lines) if len(lines) > 3 else ""


def _selected_module_type(agent: Any) -> str:
    context_variables = getattr(agent, "context_variables", None)
    module_contract = _context_get(context_variables, "module_contract", {}) or {}
    if isinstance(module_contract, dict):
        module_yaml = module_contract.get("module_yaml") or {}
        if isinstance(module_yaml, dict):
            module_block = module_yaml.get("module") or {}
            if isinstance(module_block, dict):
                module_type = str(module_block.get("type") or "").strip()
                if module_type:
                    return module_type
    return ""


def _build_archetype_block(name: str, archetype: Dict[str, Any]) -> str:
    lines = [f"{name}:"]
    summary = str(archetype.get("summary") or "").strip()
    if summary:
        lines.append(f"  summary: {summary}")

    select_when = [str(item) for item in archetype.get("select_when") or [] if str(item).strip()]
    yaml_family = archetype.get("canonical_yaml_family") if isinstance(archetype.get("canonical_yaml_family"), dict) else {}
    always = [str(item) for item in yaml_family.get("always") or [] if str(item).strip()]
    optional = [str(item) for item in yaml_family.get("optional") or [] if str(item).strip()]
    python_defaults = [str(item) for item in archetype.get("python_stub_defaults") or [] if str(item).strip()]
    hard_constraints = [str(item) for item in archetype.get("hard_constraints") or [] if str(item).strip()]

    lines.extend(_format_list_block("  select_when:", select_when[:3]))
    lines.extend(_format_list_block("  yaml_always:", always))
    lines.extend(_format_list_block("  yaml_optional:", optional))
    lines.extend(_format_list_block("  python_stub_defaults:", python_defaults))
    lines.extend(_format_list_block("  hard_constraints:", hard_constraints[:4]))
    return "\n".join(lines)


def _build_module_archetypes_body(agent: Any, module_archetypes: Dict[str, Any]) -> str:
    agent_name = getattr(agent, "name", "")
    if agent_name not in {"ConfigMiddlewareAgent", "ServiceAgent"}:
        return ""

    archetypes = module_archetypes.get("archetypes") if isinstance(module_archetypes.get("archetypes"), dict) else {}
    if not archetypes:
        return ""

    selected_type = _selected_module_type(agent)
    lines = [
        "Module archetypes keep module.type selection and backend layering cookie-cutter across domains.",
        "They guide conventions; they do not replace the canonical structured outputs or owned_paths.",
    ]

    if selected_type and selected_type in archetypes:
        lines.append("")
        lines.append(f"Selected module type: {selected_type}")
        lines.append(_build_archetype_block(selected_type, archetypes[selected_type]))
        other_names = [name for name in archetypes.keys() if name != selected_type]
        if other_names:
            lines.append("")
            lines.append("Other available module types: " + ", ".join(other_names))
        return "\n\n".join(lines)

    for name, archetype in archetypes.items():
        if not isinstance(archetype, dict):
            continue
        lines.append("")
        lines.append(_build_archetype_block(name, archetype))

    return "\n\n".join(lines)


def inject_cookie_cutter_contracts_context(agent: Any, messages: List[Dict[str, Any]]) -> None:
    """Inject file contracts and module archetypes into relevant AppGenerator agents."""
    del messages

    agent_name = getattr(agent, "name", "")
    if agent_name not in {
        "AppPlanAgent",
        "AppSchemaAgent",
        "ControlPlaneAgent",
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

    except Exception as exc:
        logger.error("[%s] Failed to inject cookie-cutter contracts context: %s", agent_name, exc)


__all__ = ["inject_cookie_cutter_contracts_context"]
