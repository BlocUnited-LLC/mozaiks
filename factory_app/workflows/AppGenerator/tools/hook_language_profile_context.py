"""
Hook: Inject Language Profile Context

Fires as prompt middleware on AppGenerator execution agents that generate
language-specific code:

  ConfigMiddlewareAgent  — backend language, module stub paths, persistence API
  ModelAgent             — backend language, schema/DTO conventions
  ServiceAgent           — backend language, stub paths, persistence and event APIs
  FrontendStubAgent      — frontend language, component extension, API client import
  ControllerAgent        — backend language, service structure
  DatabaseAgent          — database type, schema format, collection API

Reads the active dev pack's language_profile.yaml from build_context/{pack_id}/.
Defaults to webapp_builder. The pack_id is resolved from the context variable
`dev_pack_id`; if absent, webapp_builder is used.

This hook is the mechanism that makes the execution layer domain-agnostic:
agents receive language/framework facts as injected context rather than
hardcoding Python/React/MongoDB assumptions in their prompts.
"""

from __future__ import annotations

import logging
from typing import Any

from factory_app.workflows._shared.hook_utils import workflow_context_path

logger = logging.getLogger(__name__)

_DEFAULT_PACK_ID = "webapp_builder"
_LANGUAGE_PROFILE_HEADER = "[LANGUAGE PROFILE CONTEXT]"

_EXECUTION_AGENTS = {
    "ConfigMiddlewareAgent",
    "ModelAgent",
    "ServiceAgent",
    "FrontendStubAgent",
    "ControllerAgent",
    "DatabaseAgent",
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _load_language_profile(pack_id: str) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore

        path = workflow_context_path(pack_id, "language_profile.yaml")
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        logger.warning(
            "Language profile not found for dev pack '%s'. "
            "Ensure build_context/%s/language_profile.yaml exists.",
            pack_id,
            pack_id,
        )
        return None
    except Exception as exc:
        logger.warning("Failed to load language profile for pack '%s': %s", pack_id, exc)
        return None


def _resolve_pack_id(agent: Any) -> str:
    context_variables = getattr(agent, "context_variables", None)
    if context_variables is None:
        return _DEFAULT_PACK_ID
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        pack_id = getter("dev_pack_id", None)
        if pack_id and isinstance(pack_id, str) and pack_id.strip():
            return pack_id.strip()  # type: ignore[no-any-return]
    return _DEFAULT_PACK_ID


def _update_section(agent: Any, header: str, body: str) -> None:
    try:
        current: str = (
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

    except Exception as exc:
        logger.error(
            "[%s] Failed to update system message section %s: %s",
            getattr(agent, "name", "?"),
            header,
            exc,
        )


def _fmt_list(label: str, items: list[str], indent: int = 2) -> list[str]:
    if not items:
        return []
    pad = " " * indent
    lines = [f"{pad}{label}"]
    lines.extend(f"{pad}  - {item}" for item in items)
    return lines


# ---------------------------------------------------------------------------
# Content builders — scoped to each agent's needs
# ---------------------------------------------------------------------------

def _build_backend_block(backend: dict[str, Any]) -> str:
    language = str(backend.get("language") or "").strip()
    framework = str(backend.get("framework") or "").strip()
    stub_files = [str(f) for f in (backend.get("stub_files") or []) if str(f).strip()]
    persistence = backend.get("persistence") or {}
    persistence_api = str(persistence.get("api") or "").strip()
    event_api = str(backend.get("event_api") or "").strip()
    entrypoint = str(backend.get("entrypoint_format") or "").strip()
    hard_constraints = [str(c) for c in (backend.get("hard_constraints") or []) if str(c).strip()]

    lines = [f"Backend ({language}/{framework}):"]
    lines.extend(_fmt_list("stub_files:", stub_files))
    if persistence_api:
        lines.append(f"  persistence: {persistence_api}")
    if event_api:
        lines.append(f"  event_api: {event_api}")
    if entrypoint:
        lines.append(f"  entrypoint_format: {entrypoint}")
    lines.extend(_fmt_list("hard_constraints:", hard_constraints[:6]))
    return "\n".join(lines)


def _build_database_block(database: dict[str, Any]) -> str:
    db_type = str(database.get("type") or "").strip()
    schema_format = str(database.get("schema_format") or "").strip()
    collection_api = str(database.get("collection_api") or "").strip()
    index_format = database.get("index_format") or {}
    index_desc = str(index_format.get("description") or "").strip()
    hard_constraints = [str(c) for c in (database.get("hard_constraints") or []) if str(c).strip()]

    lines = [f"Database ({db_type}/{schema_format}):"]
    if collection_api:
        lines.append(f"  collection_api: {collection_api}")
    if index_desc:
        lines.append(f"  index_format: {index_desc}")
    lines.extend(_fmt_list("hard_constraints:", hard_constraints[:4]))
    return "\n".join(lines)


def _build_frontend_block(frontend: dict[str, Any]) -> str:
    language = str(frontend.get("language") or "").strip()
    framework = str(frontend.get("framework") or "").strip()
    ext = str(frontend.get("component_extension") or "").strip()
    custom_route = frontend.get("custom_route") or {}
    path_template = str(custom_route.get("path_template") or "").strip()
    api_import = str(custom_route.get("api_client_import") or "").strip()
    api_module = str(custom_route.get("api_client_module") or "").strip()
    hard_constraints = [str(c) for c in (frontend.get("hard_constraints") or []) if str(c).strip()]

    lines = [f"Frontend ({language}/{framework}):"]
    if ext:
        lines.append(f"  component_extension: {ext}")
    if path_template:
        lines.append(f"  custom_route_path: {path_template}")
    if api_import:
        lines.append(f"  api_client_import: {api_import}")
    if api_module:
        lines.append(f"  api_client_module: {api_module}")
    lines.extend(_fmt_list("hard_constraints:", hard_constraints[:4]))
    return "\n".join(lines)


def _build_services_block(services: dict[str, Any]) -> str:
    lines = ["Services:"]
    for key, val in services.items():
        if isinstance(val, str):
            lines.append(f"  {key}: {val}")
    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Per-agent body builders
# ---------------------------------------------------------------------------

_NEEDS_BACKEND = {"ConfigMiddlewareAgent", "ModelAgent", "ServiceAgent", "ControllerAgent"}
_NEEDS_DATABASE = {"DatabaseAgent", "ServiceAgent"}
_NEEDS_FRONTEND = {"FrontendStubAgent"}
_NEEDS_SERVICES = {"ConfigMiddlewareAgent", "ControllerAgent"}


def _build_body(agent_name: str, profile: dict[str, Any]) -> str:
    pack_id = str(profile.get("pack_id") or _DEFAULT_PACK_ID)
    label = str(profile.get("label") or pack_id)
    parts = [f"pack: {pack_id} — {label}"]

    backend = profile.get("backend") if isinstance(profile.get("backend"), dict) else {}
    database = profile.get("database") if isinstance(profile.get("database"), dict) else {}
    frontend = profile.get("frontend") if isinstance(profile.get("frontend"), dict) else {}
    services = profile.get("services") if isinstance(profile.get("services"), dict) else {}

    if agent_name in _NEEDS_BACKEND and backend:
        parts.append(_build_backend_block(backend))

    if agent_name in _NEEDS_DATABASE and database:
        parts.append(_build_database_block(database))

    if agent_name in _NEEDS_FRONTEND and frontend:
        parts.append(_build_frontend_block(frontend))

    if agent_name in _NEEDS_SERVICES and services:
        services_block = _build_services_block(services)
        if services_block:
            parts.append(services_block)

    # DatabaseAgent also gets backend persistence facts for cross-reference
    if agent_name == "DatabaseAgent" and backend:
        persistence = backend.get("persistence") or {}
        persistence_api = str(persistence.get("api") or "").strip()
        if persistence_api:
            parts.append(f"Backend persistence API: {persistence_api}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Public hook
# ---------------------------------------------------------------------------

def inject_language_profile_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inject the active dev pack's language profile into execution agent prompts.

    Reads build_context/{pack_id}/language_profile.yaml and injects a scoped
    [LANGUAGE PROFILE CONTEXT] block into the agent's system message. Each agent
    receives only the sections relevant to its generation role.
    """
    del messages

    agent_name = getattr(agent, "name", "")
    if agent_name not in _EXECUTION_AGENTS:
        return

    try:
        pack_id = _resolve_pack_id(agent)
        profile = _load_language_profile(pack_id)
        if not profile:
            return

        body = _build_body(agent_name, profile)
        if body:
            _update_section(agent, _LANGUAGE_PROFILE_HEADER, body)
            logger.info(
                "[%s] Injected language profile context (pack=%s)",
                agent_name,
                pack_id,
            )

    except Exception as exc:
        logger.error("[%s] Failed to inject language profile context: %s", agent_name, exc)


__all__ = ["inject_language_profile_context"]
