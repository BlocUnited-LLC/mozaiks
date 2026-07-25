from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

import yaml
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.default_runtime_configs import (
    load_default_refinement_policy_config,
)

_HARNESS_CONFIG = "refinement_harness/config/harness.yaml"
_REFINEMENT_POLICY = "app/config/refinement_policy.yaml"
_HARNESS_TOOLS = "refinement_harness/config/tools.yaml"
_HARNESS_POLICIES = "refinement_harness/config/policies.yaml"


def _dump_yaml(data: dict[str, Any]) -> str:
    return str(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _safe_prompt_id(prompt_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", prompt_id.strip()).strip("_")
    if not cleaned:
        raise ValueError("refinement harness prompt id must contain at least one safe character")
    return cleaned


def _safe_prompt_path(raw: Any, prompt_id: str) -> str:
    safe_id = _safe_prompt_id(prompt_id)
    candidate = str(raw or "").replace("\\", "/").strip()
    if not candidate:
        candidate = f"refinement_harness/prompts/{safe_id}.yaml"
    path = PurePosixPath(candidate)
    if (
        path.is_absolute()
        or any(part == ".." for part in path.parts)
        or not candidate.startswith("refinement_harness/prompts/")
        or path.suffix != ".yaml"
    ):
        raise ValueError(
            "refinement harness prompt files must live under refinement_harness/prompts/*.yaml"
        )
    return str(path)


def _validate_refinement_harness_manifest(manifest_dict: dict[str, Any]) -> None:
    """Parse the generated harness.yaml through the runtime schema.

    This catches structural errors (extra fields, wrong field names, missing
    required fields) at generation time rather than when the app loads the pack.
    Sequence cross-checking against extension_registry.json is intentionally
    deferred to the loader — the generator may run before the registry is stable.

    The import is deferred to avoid a circular import:
    refinement_harness_codegen → mozaiksai.control_plane → coding_worker →
    app_validation → code_file_utils → refinement_harness_codegen.
    """
    from mozaiksai.control_plane.schema import ControlPlaneManifest  # noqa: PLC0415

    try:
        ControlPlaneManifest.model_validate(manifest_dict)
    except ValidationError as exc:
        raise ValueError(
            f"Generated refinement_harness/config/harness.yaml failed schema validation: {exc}"
        ) from exc


def build_refinement_harness_code_files(raw: Any) -> list[dict[str, str]]:
    """Materialize a typed RefinementHarnessBundle into bundle files."""

    if not isinstance(raw, dict):
        return []

    harness_yaml = raw.get("harness_yaml")
    tools_yaml = raw.get("tools_yaml")
    if not isinstance(harness_yaml, dict):
        raise ValueError("refinement_harness.harness_yaml must be an object")
    if not isinstance(tools_yaml, dict):
        raise ValueError("refinement_harness.tools_yaml must be an object")

    _validate_refinement_harness_manifest(harness_yaml)

    files: dict[str, str] = {
        _HARNESS_CONFIG: _dump_yaml(harness_yaml),
        _REFINEMENT_POLICY: _dump_yaml(load_default_refinement_policy_config()),
        _HARNESS_TOOLS: _dump_yaml(tools_yaml),
    }

    policies_yaml = raw.get("policies_yaml")
    if isinstance(policies_yaml, dict) and policies_yaml:
        files[_HARNESS_POLICIES] = _dump_yaml(policies_yaml)

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


__all__ = ["build_refinement_harness_code_files"]

