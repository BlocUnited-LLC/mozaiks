"""Generic build-context prompt projection.

Build context files own the projection policy. This module owns only the
mechanics: load active context files, find projections for the current agent,
render declared catalog slices, and inject them into the agent prompt.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from mozaiksai.core.session.build_context import (
    BuildContextError,
    discover_build_context_files,
    iter_context_assets,
    load_build_context,
    resolve_context_asset_path,
)

logger = logging.getLogger(__name__)


def _compose_prompt_sections(sections: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        content = str(section.get("content") or "").strip()
        rendered.append("\n".join(part for part in (heading, content) if part))
    return "\n\n".join(part for part in rendered if part)


def _context_data(agent: Any) -> dict[str, Any]:
    context = getattr(agent, "context_variables", None) or getattr(agent, "_context_variables", None)
    if context is None:
        return {}
    if hasattr(context, "data") and isinstance(context.data, dict):
        return context.data
    if isinstance(context, dict):
        return context
    return {}


def _candidate_build_context_roots(agent: Any) -> list[Path]:
    data = _context_data(agent)
    candidates: list[Path] = []
    for key in ("build_context_root", "build_context_path"):
        value = data.get(key)
        if value:
            candidates.append(Path(str(value)).expanduser())
    env_value = os.getenv("MOZAIKS_BUILD_CONTEXT_PATH")
    if env_value:
        candidates.append(Path(env_value).expanduser())

    cwd = Path.cwd()
    candidates.extend(
        [
            cwd / "build_context",
            cwd / "factory_app" / "build_context",
        ]
    )

    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved)
        if key in seen or not resolved.exists() or not resolved.is_dir():
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise BuildContextError(f"Build context projection catalog must be a mapping: {path}")
    return data


def _read_dotted(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").split("."):
        key = part.strip()
        if not key:
            return None
        if isinstance(current, Mapping):
            current = current.get(key)
            continue
        return None
    return current


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _record_label(record: Mapping[str, Any]) -> str:
    record_id = record.get("id")
    label = record.get("label") or record.get("name") or record.get("key") or record_id
    key = record.get("key")
    parts = []
    if record_id is not None:
        parts.append(str(record_id))
    if label:
        parts.append(str(label))
    heading = ". ".join(parts) if parts else "record"
    if key and str(key) not in heading:
        heading = f"{heading} ({key})"
    return heading


def _render_record(record: Mapping[str, Any], *, detail: bool) -> list[str]:
    lines = [f"- {_record_label(record)}"]
    preferred = (
        "capability_kind",
        "recommendation_rank",
        "domains",
        "graph_strategy",
        "routing_idiom",
        "mozaiks_support",
        "intent_signals",
        "selection_aliases",
        "agent_archetypes",
        "transition_generation",
        "required_context",
        "required_tools",
        "alternatives",
        "avoid_when",
        "generator_notes",
    )
    for key in preferred:
        value = record.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list) and any(isinstance(item, Mapping) for item in value):
            lines.append(f"  {key}:")
            dumped = yaml.safe_dump(value, sort_keys=False).strip()
            lines.extend(f"    {line}" for line in dumped.splitlines())
        elif isinstance(value, list):
            lines.append(f"  {key}: {', '.join(str(item) for item in value)}")
        elif isinstance(value, Mapping):
            lines.append(f"  {key}: {yaml.safe_dump(dict(value), sort_keys=False).strip()}")
        else:
            lines.append(f"  {key}: {value}")
    if detail:
        remaining = {
            str(key): value
            for key, value in record.items()
            if key not in {"id", "label", "name", "key", *preferred}
        }
        if remaining:
            lines.append("  details:")
            dumped = yaml.safe_dump(remaining, sort_keys=False).strip()
            lines.extend(f"    {line}" for line in dumped.splitlines())
    return lines


def _render_summary(catalog: Mapping[str, Any], projection: Mapping[str, Any]) -> str:
    records_path = str(projection.get("records") or "patterns").strip()
    records = _read_dotted(catalog, records_path)
    header = str(projection.get("heading") or catalog.get("description") or catalog.get("catalog_id") or "BUILD CONTEXT").strip()
    lines = [f"[{header.upper()}]"]
    for rule in _string_list(catalog.get("global_rules")):
        lines.append(f"- {rule}")
    if isinstance(records, list):
        for record in records:
            if isinstance(record, Mapping):
                lines.extend(_render_record(record, detail=False))
    return "\n".join(lines)


def _selected_value(agent: Any, projection: Mapping[str, Any]) -> Any:
    selected_by = str(projection.get("selected_by") or "").strip()
    if not selected_by:
        return None
    return _read_dotted(_context_data(agent), selected_by)


def _render_selected_item(catalog: Mapping[str, Any], projection: Mapping[str, Any], agent: Any) -> str:
    records_path = str(projection.get("records") or "patterns").strip()
    record_id_field = str(projection.get("record_id_field") or "id").strip()
    selected = _selected_value(agent, projection)
    records = _read_dotted(catalog, records_path)
    header = str(projection.get("heading") or "BUILD CONTEXT DETAIL").strip()
    lines = [f"[{header.upper()}]"]
    if isinstance(records, list):
        for record in records:
            if isinstance(record, Mapping) and str(record.get(record_id_field)) == str(selected):
                lines.extend(_render_record(record, detail=True))
                return "\n".join(lines)
    lines.append(f"No selected catalog record for {record_id_field}={selected!r}.")
    return "\n".join(lines)


def _render_projection(catalog: Mapping[str, Any], projection: Mapping[str, Any], agent: Any) -> str:
    render_mode = str(projection.get("render") or "summary").strip()
    if render_mode == "selected_record":
        return _render_selected_item(catalog, projection, agent)
    return _render_summary(catalog, projection)


def _projection_targets_agent(projection: Mapping[str, Any], agent_name: str) -> bool:
    targets = projection.get("recipients") or []
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, list):
        return False
    normalized = {str(target).strip() for target in targets if str(target).strip()}
    return "*" in normalized or "all" in normalized or agent_name in normalized


def _asset_projections(asset: Mapping[str, Any]) -> list[dict[str, Any]]:
    projections = asset.get("projections")
    if projections is None and isinstance(asset.get("projection"), Mapping):
        projections = [asset.get("projection")]
    if not isinstance(projections, list):
        return []
    selected: list[dict[str, Any]] = []
    for projection in projections:
        if isinstance(projection, Mapping):
            selected.append(dict(projection))
    return selected


def _matching_projections(agent: Any) -> list[tuple[Path, dict[str, Any]]]:
    agent_name = str(getattr(agent, "name", "") or "").strip()
    if not agent_name:
        return []
    matches: list[tuple[Path, dict[str, Any]]] = []
    for root in _candidate_build_context_roots(agent):
        for context_path in discover_build_context_files(root):
            config = load_build_context(context_path)
            for asset in iter_context_assets(config, kind="catalog"):
                catalog_path = resolve_context_asset_path(context_path.parent, asset)
                for projection in _asset_projections(asset):
                    if _projection_targets_agent(projection, agent_name):
                        matches.append((catalog_path, projection))
    return matches


def _apply_text(agent: Any, marker: str, text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False

    placeholder = f"{{{{{marker}}}}}" if marker and not marker.startswith("{{") else marker
    sections = getattr(agent, "_mozaiks_prompt_sections", None)
    if isinstance(sections, list):
        updated = False
        for section in sections:
            if not isinstance(section, dict):
                continue
            content = str(section.get("content") or "")
            if placeholder and placeholder in content:
                section["content"] = content.replace(placeholder, normalized)
                updated = True
        if updated:
            recomposed = _compose_prompt_sections(sections)
            if hasattr(agent, "update_system_message"):
                agent.update_system_message(recomposed)
            elif hasattr(agent, "_system_message"):
                agent._system_message = recomposed
            agent._mozaiks_prompt_sections = sections
            agent._mozaiks_base_system_message = recomposed
            return True

    current = getattr(agent, "_system_message", None) or getattr(agent, "system_message", "") or ""
    if placeholder and placeholder in current:
        updated_message = current.replace(placeholder, normalized)
    else:
        updated_message = f"{current}\n\n{normalized}".strip()
    if hasattr(agent, "update_system_message"):
        agent.update_system_message(updated_message)
    elif hasattr(agent, "_system_message"):
        agent._system_message = updated_message
    agent._mozaiks_base_system_message = updated_message
    return True


def inject_build_context_projections(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inject context-declared build-context projections for the current agent."""

    try:
        rendered: dict[str, list[str]] = {}
        for catalog_path, projection in _matching_projections(agent):
            marker = str(projection.get("marker") or "").strip()
            if not marker:
                continue
            catalog = _read_yaml_mapping(catalog_path)
            rendered.setdefault(marker, []).append(_render_projection(catalog, projection, agent))

        for marker, blocks in rendered.items():
            _apply_text(agent, marker, "\n\n".join(blocks))
    except Exception as exc:
        logger.debug("Build-context projection failed for %s: %s", getattr(agent, "name", "unknown"), exc)


__all__ = ["inject_build_context_projections"]
