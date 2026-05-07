"""
Hook: Inject Domain Catalog Context

Fires as update_agent_state hooks on AppPlanAgent and ConfigMiddlewareAgent.

AppPlanAgent hook  — inject_domain_catalog_context
  Reads domain_catalogs.yaml from the same tools/ directory, scores domains
  against the current app concept, and injects a compact [DOMAIN CATALOG CONTEXT]
  block into the agent system message. This gives AppPlanAgent realistic module
    and domain priors without dumping the entire catalog.

    The catalog now focuses on likely modules and recommended module archetypes.
    File families and implementation defaults live in separate prompt-time
    artifacts (`file_contracts.yaml` and `module_archetypes.yaml`).

  The catalog is advisory. AppPlanAgent must still reason about what the specific
  app actually needs — the catalog provides examples and suggests which files belong
  in which modules, not a mandatory file list.

ConfigMiddlewareAgent hook — inject_module_file_manifest_guard
  Reads current_build_task from context variables. If the task is a module_contract,
  inspects owned_paths to derive which YAML files were declared for this module and
  injects a [MODULE FILE MANIFEST GUARD] block that tells ConfigMiddlewareAgent to
  generate only those files — not the full six-file default set.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CATALOG_PATH = Path(__file__).parent / "domain_catalogs.yaml"

_DOMAIN_CONTEXT_HEADER = "[DOMAIN CATALOG CONTEXT]"
_MANIFEST_GUARD_HEADER = "[MODULE FILE MANIFEST GUARD]"

# YAML files that constitute the full module contract set.
_ALL_MODULE_YAML_FILES = {
    "module.yaml",
    "events.yaml",
    "subscriptions.yaml",
    "notifications.yaml",
    "settings.yaml",
    "admin.yaml",
    "channels.yaml",
}

# Context variable keys AppPlanAgent uses as concept signals.
_CONCEPT_SIGNAL_KEYS = (
    "concept_overview",
    "concept_blueprint",
    "design_surface_map",
    "refinement_request",
)

# Number of top domains to inject into the agent system message.
_TOP_N_DOMAINS = 4

# Maximum number of modules to show per domain in the injected block.
_MAX_MODULES_PER_DOMAIN = 5

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _load_catalog() -> Dict[str, Any] | None:
    """Load domain_catalogs.yaml. Returns None on any failure."""
    try:
        import yaml  # type: ignore
        with open(_CATALOG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except Exception as exc:
        logger.warning("domain_catalogs.yaml could not be loaded: %s", exc)
        return None


def _update_section(agent: Any, header: str, body: str) -> None:
    """Append or replace a named section in the agent system message."""
    try:
        current: str = (
            getattr(agent, "system_message", None)
            or getattr(agent, "_system_message", "")
            or ""
        )
        section = f"{header}\n{body}"

        if header in current:
            # Replace existing section, preserving anything that follows.
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

    except Exception as exc:
        logger.error(
            "[%s] Failed to update system message section %s: %s",
            getattr(agent, "name", "?"),
            header,
            exc,
        )


# ---------------------------------------------------------------------------
# Domain scoring
# ---------------------------------------------------------------------------

def _collect_concept_text(context_variables: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
    """Collect all available concept signal text for domain scoring."""
    parts: list[str] = []

    for key in _CONCEPT_SIGNAL_KEYS:
        val = context_variables.get(key)
        if val:
            if isinstance(val, dict):
                parts.append(str(val))
            elif isinstance(val, str):
                parts.append(val)

    # Include the last few user-role messages as additional signal.
    recent_user_msgs = [
        m.get("content", "") or ""
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user"
    ][-4:]
    parts.extend(recent_user_msgs)

    return " ".join(parts).lower()


def _score_domain(domain_key: str, domain_data: Dict[str, Any], concept_text: str) -> int:
    """Score a domain against concept text. Higher = better match."""
    score = 0
    domain_key_norm = domain_key.replace("_", " ")

    # Domain key itself appears in concept text.
    if domain_key_norm in concept_text:
        score += 10

    # Domain description word overlap.
    description = (domain_data.get("description") or "").lower()
    for word in description.split():
        if len(word) > 4 and word in concept_text:
            score += 1

    # common_app_types matches.
    for app_type in domain_data.get("common_app_types") or []:
        if app_type.lower() in concept_text:
            score += 5

    # Module name hits.
    for module_key in (domain_data.get("modules") or {}).keys():
        module_norm = module_key.replace("_", " ")
        if module_norm in concept_text:
            score += 3

    return score


def _select_top_domains(
    catalog: Dict[str, Any],
    concept_text: str,
) -> List[tuple[str, Dict[str, Any]]]:
    """Return the top N domains sorted by score descending."""
    domains: Dict[str, Any] = catalog.get("domains") or {}
    scored = [
        (key, data, _score_domain(key, data, concept_text))
        for key, data in domains.items()
        if isinstance(data, dict)
    ]
    scored.sort(key=lambda t: t[2], reverse=True)
    return [(key, data) for key, data, _ in scored[:_TOP_N_DOMAINS]]


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------

def _format_global_base(catalog: Dict[str, Any]) -> str:
    """Format the global_base module list as a compact bullet summary."""
    global_base = catalog.get("global_base") or {}
    modules = global_base.get("modules") or {}
    if not modules:
        return ""

    lines = ["Global base modules (common across most apps — include only what this app needs):"]
    for mod_key, mod_data in modules.items():
        desc = (mod_data.get("description") or "").split(".")[0]
        lines.append(f"  - {mod_key}: {desc}")
    return "\n".join(lines)


def _format_domain_excerpt(domain_key: str, domain_data: Dict[str, Any]) -> str:
    """Format a compact planning excerpt for one matched domain."""
    description = domain_data.get("description") or ""
    modules: Dict[str, Any] = domain_data.get("modules") or {}
    common_app_types = [str(item) for item in domain_data.get("common_app_types") or [] if str(item).strip()]

    lines = [f"Domain: {domain_key} — {description}"]
    if common_app_types:
        lines.append(f"  common_app_types: {', '.join(common_app_types[:4])}")
    for i, (mod_key, mod_data) in enumerate(modules.items()):
        if i >= _MAX_MODULES_PER_DOMAIN:
            remaining = len(modules) - _MAX_MODULES_PER_DOMAIN
            lines.append(f"    ... and {remaining} more modules")
            break
        mod_desc = (mod_data.get("description") or "").split(".")[0]
        recommended_type = str(mod_data.get("recommended_module_type") or "standard").strip()
        lines.append(f"  {mod_key} [{recommended_type}]: {mod_desc}")
    return "\n".join(lines)


def _build_app_plan_body(
    catalog: Dict[str, Any],
    top_domains: List[tuple[str, Dict[str, Any]]],
    all_domain_keys: List[str],
) -> str:
    """Build the full body text for the AppPlanAgent system message injection."""
    parts: list[str] = []

    global_base_text = _format_global_base(catalog)
    if global_base_text:
        parts.append(global_base_text)

    parts.append(
        f"Available domain keys ({len(all_domain_keys)} total): "
        + ", ".join(all_domain_keys)
    )

    parts.append(
        f"Top {len(top_domains)} matching domain(s) with module and archetype priors:"
    )
    for domain_key, domain_data in top_domains:
        parts.append(_format_domain_excerpt(domain_key, domain_data))

    parts.append(
        "\n".join([
            "Rules (catalog is advisory — not a mandatory module list):",
            "  - Use these domains as a starting point; adapt to what this specific app needs.",
            "  - Treat recommended_module_type as a planning prior, not runtime truth.",
            "  - Use the injected [FILE CONTRACTS CONTEXT] for task ownership and allowed file families.",
            "  - Use the injected [MODULE ARCHETYPES CONTEXT] for cookie-cutter module.type conventions.",
            "  - Do NOT include all six YAML files by default for every module.",
            "  - Include module.yaml always.",
            "  - Include events.yaml only if the module publishes domain events.",
            "  - Include subscriptions.yaml only if the module reacts to events from other modules.",
            "  - Include notifications.yaml only if module events should trigger user notifications.",
            "  - Include settings.yaml only if the module has configurable behavior or feature flags.",
            "  - Include admin.yaml only if the module needs admin or backoffice panels.",
            "  - Include channels.yaml only for modules requiring real-time WebSocket or push delivery.",
            "  - Omit any file that would contain only an empty array or null object.",
            "  - You may combine modules from multiple domains or global_base as the app requires.",
        ])
    )

    return "\n\n".join(parts)


def _build_manifest_guard_body(
    module_id: str,
    declared_yaml_files: Set[str],
    owned_paths: List[str],
) -> str:
    """Build the ConfigMiddlewareAgent guard block body."""
    declared_sorted = sorted(declared_yaml_files)
    omitted = sorted(_ALL_MODULE_YAML_FILES - declared_yaml_files - {"channels.yaml"})

    lines = [
        f"Module: {module_id}",
        f"Declared YAML files for this module (generate ONLY these):",
    ]
    for f in declared_sorted:
        lines.append(f"  - modules/{module_id}/{f}")

    if omitted:
        lines.append("Files NOT declared for this module (do NOT generate):")
        for f in omitted:
            lines.append(f"  - modules/{module_id}/{f}  ← omit")

    lines += [
        "",
        "HARD CONSTRAINTS:",
        "  1. Generate ONLY the YAML files listed above.",
        "  2. Do NOT emit events.yaml if the module publishes no events.",
        "  3. Do NOT emit subscriptions.yaml if the module reacts to no events.",
        "  4. Do NOT emit notifications.yaml if no events warrant user notifications.",
        "  5. Do NOT emit settings.yaml if the module has no configurable behavior.",
        "  6. Do NOT emit admin.yaml if the module needs no admin panels.",
        "  7. Do NOT emit a file with only an empty array or null object.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public hook functions
# ---------------------------------------------------------------------------

def inject_domain_catalog_context(agent: Any, messages: List[Dict[str, Any]]) -> None:
    """
    update_agent_state hook for AppPlanAgent.

    Reads domain_catalogs.yaml, scores domains against the current concept,
    and injects a compact [DOMAIN CATALOG CONTEXT] block into the agent system
    message. The catalog is advisory context — not a mandatory file contract.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name != "AppPlanAgent":
        return

    try:
        catalog = _load_catalog()
        if not catalog:
            return

        context_variables: Dict[str, Any] = getattr(agent, "context_variables", {}) or {}
        concept_text = _collect_concept_text(context_variables, messages)

        all_domain_keys = sorted((catalog.get("domains") or {}).keys())
        top_domains = _select_top_domains(catalog, concept_text)

        body = _build_app_plan_body(catalog, top_domains, all_domain_keys)
        _update_section(agent, _DOMAIN_CONTEXT_HEADER, body)

        logger.info(
            "[%s] Injected domain catalog context (top domains: %s)",
            agent_name,
            ", ".join(k for k, _ in top_domains),
        )

    except Exception as exc:
        logger.error("[%s] Failed to inject domain catalog context: %s", agent_name, exc)


def inject_module_file_manifest_guard(agent: Any, messages: List[Dict[str, Any]]) -> None:
    """
    update_agent_state hook for ConfigMiddlewareAgent.

    Reads current_build_task from context variables. If this is a module_contract
    task, derives the declared YAML files from owned_paths and injects a
    [MODULE FILE MANIFEST GUARD] block instructing the agent to generate only
    those files — not the full six-file default set.

    Exits silently if task_type is not module_contract or context is unavailable.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name != "ConfigMiddlewareAgent":
        return

    try:
        context_variables: Dict[str, Any] = getattr(agent, "context_variables", {}) or {}

        current_build_task: Dict[str, Any] = context_variables.get("current_build_task") or {}
        if not current_build_task:
            return

        task_type: str = (
            current_build_task.get("task_type")
            or current_build_task.get("current_build_task_type")
            or context_variables.get("current_build_task_type")
            or ""
        )
        if task_type != "module_contract":
            return

        module_id: str = current_build_task.get("capability_pack_id") or ""
        owned_paths: List[str] = current_build_task.get("owned_paths") or []

        # Derive which YAML files are declared from owned_paths.
        declared_yaml_files: Set[str] = set()
        for path in owned_paths:
            filename = Path(path).name
            if filename in _ALL_MODULE_YAML_FILES:
                declared_yaml_files.add(filename)

        # module.yaml is always required — ensure it's present.
        declared_yaml_files.add("module.yaml")

        # If file_manifest is explicitly set on the task, honour it instead.
        file_manifest: Dict[str, Any] = current_build_task.get("file_manifest") or {}
        if file_manifest:
            manifest_yaml = set(file_manifest.get("yaml_files") or [])
            if manifest_yaml:
                declared_yaml_files = manifest_yaml | {"module.yaml"}

        body = _build_manifest_guard_body(module_id, declared_yaml_files, owned_paths)
        _update_section(agent, _MANIFEST_GUARD_HEADER, body)

        logger.info(
            "[%s] Injected module file manifest guard (module=%s, declared=%s)",
            agent_name,
            module_id,
            sorted(declared_yaml_files),
        )

    except Exception as exc:
        logger.error("[%s] Failed to inject module file manifest guard: %s", agent_name, exc)


__all__ = [
    "inject_domain_catalog_context",
    "inject_module_file_manifest_guard",
]
