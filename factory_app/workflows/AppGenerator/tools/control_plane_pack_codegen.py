from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Dict, List

import yaml


_CONTROL_PLANE_CONFIG = "control_plane/config/control_plane.yaml"
_CONTROL_PLANE_TOOLS = "control_plane/config/tools.yaml"
_CONTROL_PLANE_POLICIES = "control_plane/config/policies.yaml"


def _dump_yaml(data: Dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


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


def build_control_plane_pack_code_files(raw: Any) -> List[Dict[str, str]]:
    """Materialize a typed ControlPlanePackBundle into bundle files."""

    if not isinstance(raw, dict):
        return []

    control_plane_yaml = raw.get("control_plane_yaml")
    tools_yaml = raw.get("tools_yaml")
    if not isinstance(control_plane_yaml, dict):
        raise ValueError("control_plane_pack.control_plane_yaml must be an object")
    if not isinstance(tools_yaml, dict):
        raise ValueError("control_plane_pack.tools_yaml must be an object")

    files: Dict[str, str] = {
        _CONTROL_PLANE_CONFIG: _dump_yaml(control_plane_yaml),
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
