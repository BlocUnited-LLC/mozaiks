"""Shared AG2 1.0 Network patternbook loader for factory workflows."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import yaml

from factory_app.workflows._shared.hook_utils import workflow_context_path

PATTERNBOOK_PATH = workflow_context_path("AgentGenerator", "ag2_network_patterns.yaml")


def normalize_pattern_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


@lru_cache(maxsize=1)
def load_patternbook() -> dict[str, Any]:
    data = yaml.safe_load(PATTERNBOOK_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Patternbook must be a mapping: {PATTERNBOOK_PATH}")
    patterns = data.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError(f"Patternbook must declare non-empty patterns[]: {PATTERNBOOK_PATH}")
    return data


def list_patterns() -> list[dict[str, Any]]:
    patterns = load_patternbook().get("patterns", [])
    return [dict(pattern) for pattern in patterns if isinstance(pattern, dict)]


def get_pattern_by_id(pattern_id: int | str | None) -> dict[str, Any] | None:
    try:
        normalized_id = int(pattern_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    for pattern in list_patterns():
        if pattern.get("id") == normalized_id:
            return pattern
    return None


def get_pattern_by_name(name: Any) -> dict[str, Any] | None:
    normalized = normalize_pattern_name(name)
    if not normalized:
        return None
    for pattern in list_patterns():
        candidates = [
            pattern.get("key"),
            pattern.get("label"),
            *(pattern.get("selection_aliases") or []),
        ]
        if normalized in {normalize_pattern_name(candidate) for candidate in candidates}:
            return pattern
    return None


def build_pattern_lookup_maps() -> tuple[dict[str, int], dict[int, str], dict[int, str]]:
    id_by_name: dict[str, int] = {}
    name_by_id: dict[int, str] = {}
    display_name_by_id: dict[int, str] = {}

    for pattern in list_patterns():
        pattern_id = int(pattern["id"])
        key = str(pattern["key"])
        label = str(pattern["label"])
        name_by_id[pattern_id] = key
        display_name_by_id[pattern_id] = label
        for candidate in [key, label, *(pattern.get("selection_aliases") or [])]:
            normalized = normalize_pattern_name(candidate)
            if normalized:
                id_by_name[normalized] = pattern_id

    return id_by_name, name_by_id, display_name_by_id


def render_patternbook_summary() -> str:
    data = load_patternbook()
    lines = [
        "[AG2 NETWORK PATTERNBOOK]",
        "Use this as the canonical workflow pattern catalog for AgentGenerator.",
        "",
        "Global rules:",
    ]
    for rule in data.get("global_rules") or []:
        lines.append(f"- {rule}")

    lines.extend(["", "Patterns:"])
    for pattern in list_patterns():
        lines.append(
            f"- {pattern['id']}. {pattern['label']} "
            f"({pattern['key']}): {pattern['graph_strategy']} / {pattern['routing_idiom']} "
            f"[support: {pattern['mozaiks_support']}]"
        )
        signals = ", ".join(pattern.get("intent_signals") or [])
        if signals:
            lines.append(f"  Intent signals: {signals}.")
        context = ", ".join(pattern.get("required_context") or [])
        tools = ", ".join(pattern.get("required_tools") or [])
        if context or tools:
            lines.append(f"  Requires: context [{context or 'none'}], tools [{tools or 'none'}].")
        notes = pattern.get("generator_notes") or []
        if notes:
            lines.append(f"  Generator rule: {notes[0]}")
    return "\n".join(lines)


def render_pattern_guidance(pattern_id: int | str | None) -> str:
    pattern = get_pattern_by_id(pattern_id)
    if not pattern:
        return render_patternbook_summary()

    lines = [
        f"[AG2 NETWORK PATTERN - {pattern['label']}]",
        f"Pattern key: {pattern['key']}",
        f"Mozaiks support: {pattern['mozaiks_support']}",
        f"Graph strategy: {pattern['graph_strategy']}",
        f"Routing idiom: {pattern['routing_idiom']}",
        "",
        "Use when:",
    ]
    for signal in pattern.get("intent_signals") or []:
        lines.append(f"- {signal}")

    lines.append("")
    lines.append("Avoid when:")
    for avoid in pattern.get("avoid_when") or []:
        lines.append(f"- {avoid}")

    lines.append("")
    lines.append("AG2 1.0 primitives:")
    for primitive in pattern.get("beta_primitives") or []:
        lines.append(f"- {primitive}")

    context = pattern.get("required_context") or []
    tools = pattern.get("required_tools") or []
    lines.extend(
        [
            "",
            f"Required context variables: {', '.join(context) if context else 'none'}",
            f"Required tools: {', '.join(tools) if tools else 'none'}",
            "",
            "Transition generation:",
            f"- Strategy: {pattern.get('transition_generation', {}).get('strategy')}",
        ]
    )
    for item in pattern.get("transition_generation", {}).get("ordering") or []:
        lines.append(f"- Ordering: {item}")
    terminal_rule = pattern.get("transition_generation", {}).get("terminal_rule")
    if terminal_rule:
        lines.append(f"- Terminal rule: {terminal_rule}")

    notes = pattern.get("generator_notes") or []
    if notes:
        lines.extend(["", "Generator notes:"])
        for note in notes:
            lines.append(f"- {note}")

    return "\n".join(lines)


def render_pattern_example(pattern_id: int | str | None, section_key: str = "WorkflowStrategy") -> str | None:
    pattern = get_pattern_by_id(pattern_id)
    if not pattern:
        return None
    examples = pattern.get("examples")
    if not isinstance(examples, Mapping):
        return None
    example = examples.get(section_key)
    if example is None:
        return None
    return json.dumps(example, indent=2)

