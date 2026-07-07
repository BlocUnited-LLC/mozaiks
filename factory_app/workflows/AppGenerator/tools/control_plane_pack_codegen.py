from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.default_runtime_configs import (
    load_default_control_plane_runtime_config,
)

_CONTROL_PLANE_CONFIG = "control_plane/config/control_plane.yaml"
_CONTROL_PLANE_RUNTIME = "control_plane/config/runtime.yaml"
_CONTROL_PLANE_TOOLS = "control_plane/config/tools.yaml"
_CONTROL_PLANE_POLICIES = "control_plane/config/policies.yaml"


def _dump_yaml(data: dict[str, Any]) -> str:
    return str(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _safe_prompt_id(prompt_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", prompt_id.strip()).strip("_")
    if not cleaned:
        raise ValueError("control-plane prompt id must contain at least one safe character")
    return cleaned


def _safe_prompt_path(raw: Any, prompt_id: str) -> str:
    safe_id = _safe_prompt_id(prompt_id)
    candidate = str(raw or "").replace("\\", "/").strip()
    if not candidate:
        candidate = f"control_plane/prompts/{safe_id}.yaml"
    path = PurePosixPath(candidate)
    if (
        path.is_absolute()
        or any(part == ".." for part in path.parts)
        or not candidate.startswith("control_plane/prompts/")
        or path.suffix != ".yaml"
    ):
        raise ValueError(
            "control-plane prompt files must live under control_plane/prompts/*.yaml"
        )
    return str(path)


def _validate_control_plane_manifest(manifest_dict: dict[str, Any]) -> None:
    """Parse the generated control_plane.yaml through the runtime schema.

    This catches structural errors (extra fields, wrong field names, missing
    required fields) at generation time rather than when the app loads the pack.
    Sequence cross-checking against extension_registry.json is intentionally
    deferred to the loader — the generator may run before the registry is stable.

    The import is deferred to avoid a circular import:
    control_plane_pack_codegen → mozaiksai.control_plane → coding_worker →
    app_validation → code_file_utils → control_plane_pack_codegen.
    """
    from mozaiksai.control_plane.schema import ControlPlaneManifest  # noqa: PLC0415

    try:
        ControlPlaneManifest.model_validate(manifest_dict)
    except ValidationError as exc:
        raise ValueError(
            f"Generated control_plane.yaml failed schema validation: {exc}"
        ) from exc


def build_control_plane_pack_code_files(raw: Any) -> list[dict[str, str]]:
    """Materialize a typed ControlPlanePackBundle into bundle files."""

    if not isinstance(raw, dict):
        return []

    control_plane_yaml = raw.get("control_plane_yaml")
    tools_yaml = raw.get("tools_yaml")
    if not isinstance(control_plane_yaml, dict):
        raise ValueError("control_plane_pack.control_plane_yaml must be an object")
    if not isinstance(tools_yaml, dict):
        raise ValueError("control_plane_pack.tools_yaml must be an object")

    _validate_control_plane_manifest(control_plane_yaml)

    files: dict[str, str] = {
        _CONTROL_PLANE_CONFIG: _dump_yaml(control_plane_yaml),
        _CONTROL_PLANE_RUNTIME: _dump_yaml(load_default_control_plane_runtime_config()),
        _CONTROL_PLANE_TOOLS: _dump_yaml(tools_yaml),
    }

    policies_yaml = raw.get("policies_yaml")
    if isinstance(policies_yaml, dict) and policies_yaml:
        files[_CONTROL_PLANE_POLICIES] = _dump_yaml(policies_yaml)

    prompt_files = raw.get("prompt_files")
    if isinstance(prompt_files, list):
        for prompt in prompt_files:
            if not isinstance(prompt, dict):
                continue
            prompt_id = str(prompt.get("id") or "").strip()
            content = str(prompt.get("content") or "").strip()
            if not prompt_id or not content:
                continue
            filename = _safe_prompt_path(prompt.get("filename"), prompt_id)
            files[filename] = _dump_yaml({"id": prompt_id, "content": content})

    return [{"filename": name, "content": content} for name, content in sorted(files.items())]


__all__ = ["build_control_plane_pack_code_files"]

