from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader
from jsonschema import Draft7Validator


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _load_schema() -> dict[str, Any]:
    schema_path = _TEMPLATE_DIR / "lead_qualification_schema.json"
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _render_summary(payload: dict[str, Any]) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), trim_blocks=True, lstrip_blocks=True)
    template = env.get_template("lead_qualification_summary.jinja")
    return template.render(**payload)


async def save_qualification_result(context_variables: Dict[str, Any] | None = None):
    """Persist the structured output and return an artifact-ready payload."""
    context = context_variables or {}
    payload = context.get("structured_output") or context.get("qualification_result") or {}

    if not isinstance(payload, dict):
        payload = {"summary": str(payload)}

    schema = _load_schema()
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        raise ValueError(f"Lead qualification JSON does not match schema: {errors[0].message}")

    payload = dict(payload)
    payload["summary"] = _render_summary(payload)

    result = {
        "status": "ok",
        "artifact": payload,
        "stored_json": json.dumps(payload, indent=2, sort_keys=True),
    }

    context["qualification_result"] = payload
    context["qualification_complete"] = True
    return result
