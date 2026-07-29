from __future__ import annotations

import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any

import yaml

from factory_app.workflows.AppGenerator.tools.default_runtime_configs import (
    load_default_refinement_policy_config,
)

DEFAULT_REFINEMENT_HARNESS_EXTENDS = "mozaiks.default_refinement_harness"
REFINEMENT_HARNESS_SCHEMA_VERSION = "mozaiks.refinement_harness.v1"
REFINEMENT_HARNESS_TOOLS_SCHEMA_VERSION = "mozaiks.refinement_harness.tools.v1"
REFINEMENT_HARNESS_POLICIES_SCHEMA_VERSION = "mozaiks.refinement_harness.policies.v1"
_HARNESS_CONFIG = "refinement_harness/config/harness.yaml"
_REFINEMENT_POLICY = "app/config/refinement_policy.yaml"
_HARNESS_TOOLS = "refinement_harness/config/tools.yaml"
_HARNESS_POLICIES = "refinement_harness/config/policies.yaml"
_OVERLAY_TOP_LEVEL_KEYS = {"schema_version", "extends", "overrides"}
_OVERLAY_OVERRIDE_KEYS = {"routing", "checkpoints", "prompts"}


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


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _validate_refinement_harness_overlay(manifest_dict: dict[str, Any]) -> None:
    unknown_keys = set(manifest_dict) - _OVERLAY_TOP_LEVEL_KEYS
    if unknown_keys:
        raise ValueError(
            "Generated refinement_harness/config/harness.yaml must be an extends overlay; "
            f"unsupported top-level keys: {', '.join(sorted(unknown_keys))}"
        )
    if manifest_dict.get("schema_version") != REFINEMENT_HARNESS_SCHEMA_VERSION:
        raise ValueError(
            "Generated refinement_harness/config/harness.yaml must set "
            f"schema_version to {REFINEMENT_HARNESS_SCHEMA_VERSION!r}"
        )
    if manifest_dict.get("extends") != DEFAULT_REFINEMENT_HARNESS_EXTENDS:
        raise ValueError(
            "Generated refinement_harness/config/harness.yaml must extend "
            f"{DEFAULT_REFINEMENT_HARNESS_EXTENDS!r}"
        )
    overrides = manifest_dict.get("overrides")
    if overrides is None:
        manifest_dict["overrides"] = {}
        return
    if not isinstance(overrides, dict):
        raise ValueError("Generated refinement harness overrides must be an object")
    overrides = _drop_none(overrides)
    manifest_dict["overrides"] = overrides
    unknown_override_keys = set(overrides) - _OVERLAY_OVERRIDE_KEYS
    if unknown_override_keys:
        raise ValueError(
            "Generated refinement harness overrides may only declare "
            f"{', '.join(sorted(_OVERLAY_OVERRIDE_KEYS))}; "
            f"found {', '.join(sorted(unknown_override_keys))}"
        )


def _validate_optional_manifest(
    data: Any,
    *,
    expected_schema: str,
    label: str,
) -> dict[str, Any] | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"refinement_harness.{label} must be an object when provided")
    if data.get("schema_version") != expected_schema:
        raise ValueError(
            f"refinement_harness.{label} must set schema_version to {expected_schema!r}"
        )
    return data


def _normalize_prompt_files(raw_prompts: Any) -> tuple[dict[str, str], dict[str, str]]:
    files: dict[str, str] = {}
    prompt_refs: dict[str, str] = {}
    if raw_prompts is None:
        return files, prompt_refs
    if not isinstance(raw_prompts, list):
        raise ValueError("refinement_harness.prompt_files must be a list when provided")
    for prompt in raw_prompts:
        if not isinstance(prompt, dict):
            continue
        prompt_id = str(prompt.get("id") or "").strip()
        content = str(prompt.get("content") or "").strip()
        if not prompt_id or not content:
            continue
        safe_id = _safe_prompt_id(prompt_id)
        filename = _safe_prompt_path(prompt.get("filename"), safe_id)
        files[filename] = _dump_yaml({"id": safe_id, "content": content})
        prompt_refs[safe_id] = filename
    return files, prompt_refs


def build_refinement_harness_code_files(raw: Any) -> list[dict[str, str]]:
    """Materialize a typed RefinementHarnessBundle into bundle files."""

    if not isinstance(raw, dict):
        return []

    harness_yaml = raw.get("harness_yaml")
    tools_yaml = raw.get("tools_yaml")
    if not isinstance(harness_yaml, dict):
        raise ValueError("refinement_harness.harness_yaml must be an object")

    harness_yaml = deepcopy(harness_yaml)
    _validate_refinement_harness_overlay(harness_yaml)
    prompt_files, prompt_refs = _normalize_prompt_files(raw.get("prompt_files"))
    if prompt_refs:
        overrides = harness_yaml.setdefault("overrides", {})
        prompts = overrides.setdefault("prompts", {})
        if not isinstance(prompts, dict):
            raise ValueError("refinement_harness.harness_yaml.overrides.prompts must be an object")
        prompts.update(prompt_refs)

    files: dict[str, str] = {
        _HARNESS_CONFIG: _dump_yaml(harness_yaml),
        _REFINEMENT_POLICY: _dump_yaml(load_default_refinement_policy_config()),
    }

    tools_yaml = _validate_optional_manifest(
        tools_yaml,
        expected_schema=REFINEMENT_HARNESS_TOOLS_SCHEMA_VERSION,
        label="tools_yaml",
    )
    if tools_yaml:
        files[_HARNESS_TOOLS] = _dump_yaml(tools_yaml)

    policies_yaml = _validate_optional_manifest(
        raw.get("policies_yaml"),
        expected_schema=REFINEMENT_HARNESS_POLICIES_SCHEMA_VERSION,
        label="policies_yaml",
    )
    if policies_yaml:
        files[_HARNESS_POLICIES] = _dump_yaml(policies_yaml)

    files.update(prompt_files)

    return [{"filename": name, "content": content} for name, content in sorted(files.items())]


__all__ = ["build_refinement_harness_code_files"]

