from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from .config import load_control_plane_config
from .schema import (
    ControlPlanePromptDefinition,
    ControlPlaneManifest,
    ControlPlanePoliciesManifest,
    ControlPlanePromptsManifest,
    ControlPlaneToolsManifest,
    LoadedControlPlanePack,
)


class ControlPlanePackLoadError(Exception):
    """Raised when a control-plane pack cannot be found or validated."""


def resolve_factory_control_plane_root() -> Path:
    return (Path(__file__).resolve().parents[2] / "factory_app" / "control_plane").resolve()


def resolve_app_control_plane_root(app_root: Optional[Path] = None) -> Path:
    from mozaiksai.core.workflow.paths import resolve_active_app_root

    root = app_root or resolve_active_app_root()
    return (root.parent / "control_plane").resolve()


def resolve_control_plane_pack_path(
    *,
    app_root: Optional[Path] = None,
    factory_root: Optional[Path] = None,
) -> Path:
    app_candidate = resolve_app_control_plane_root(app_root)
    if (app_candidate / "config" / "control_plane.yaml").exists():
        return app_candidate

    factory_candidate = factory_root or resolve_factory_control_plane_root()
    if (factory_candidate / "config" / "control_plane.yaml").exists():
        return factory_candidate.resolve()

    raise ControlPlanePackLoadError(
        "Control-plane pack was not found in control_plane/config or factory_app/control_plane/config."
    )


def load_selected_control_plane_pack(
    *,
    app_root: Optional[Path] = None,
    factory_root: Optional[Path] = None,
) -> LoadedControlPlanePack:
    _ = load_control_plane_config(app_root)
    return load_control_plane_pack(app_root=app_root, factory_root=factory_root)


def load_control_plane_pack(
    *,
    app_root: Optional[Path] = None,
    factory_root: Optional[Path] = None,
) -> LoadedControlPlanePack:
    pack_path = resolve_control_plane_pack_path(app_root=app_root, factory_root=factory_root)
    manifest = ControlPlaneManifest.model_validate(_load_yaml_file(pack_path / "config" / "control_plane.yaml"))
    prompts = _load_prompt_manifest(pack_path / "prompts")
    tools = ControlPlaneToolsManifest.model_validate(_load_yaml_file(pack_path / "config" / "tools.yaml"))
    policies = ControlPlanePoliciesManifest.model_validate(
        _load_yaml_file(pack_path / "config" / "policies.yaml", required=False)
        or {"schema_version": "mozaiks.control_plane.policies"}
    )
    _validate_pack(manifest=manifest, prompts=prompts, tools=tools, pack_path=pack_path)
    return LoadedControlPlanePack(path=pack_path, manifest=manifest, prompts=prompts, tools=tools, policies=policies)


def _load_yaml_file(path: Path, *, required: bool = True) -> dict:
    if not path.exists():
        if required:
            raise ControlPlanePackLoadError(f"Missing control-plane manifest: {path}")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ControlPlanePackLoadError(f"Failed to parse YAML at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ControlPlanePackLoadError(f"Control-plane manifest must be a YAML object: {path}")
    return data


def _load_prompt_manifest(prompts_root: Path) -> ControlPlanePromptsManifest:
    if not prompts_root.exists() or not prompts_root.is_dir():
        raise ControlPlanePackLoadError(f"Missing control-plane prompts directory: {prompts_root}")

    prompts: list[ControlPlanePromptDefinition] = []
    for prompt_path in sorted(prompts_root.glob("*.yaml")):
        data = _load_yaml_file(prompt_path)
        prompt_id = str(data.get("id") or prompt_path.stem).strip()
        content = str(data.get("content") or "").strip()
        prompts.append(ControlPlanePromptDefinition(id=prompt_id, content=content))

    return ControlPlanePromptsManifest(
        schema_version="mozaiks.control_plane.prompts",
        prompts=prompts,
    )


def _validate_pack(
    *,
    manifest: ControlPlaneManifest,
    prompts: ControlPlanePromptsManifest,
    tools: ControlPlaneToolsManifest,
    pack_path: Path,
) -> None:
    prompt_ids = {prompt.id for prompt in prompts.prompts}
    tool_ids = {tool.id for tool in tools.tools}
    checkpoint_targets = {"harness"} | {checkpoint.event for checkpoint in manifest.checkpoints}

    for tool in tools.tools:
        invalid_targets = [target for target in tool.available_to if target not in checkpoint_targets]
        if invalid_targets:
            raise ControlPlanePackLoadError(
                f"tools.yaml tool '{tool.id}' contains invalid available_to target(s): {', '.join(sorted(invalid_targets))}"
            )

    for checkpoint in manifest.checkpoints:
        if checkpoint.prompt_id and checkpoint.prompt_id not in prompt_ids:
            raise ControlPlanePackLoadError(
                f"control_plane.yaml checkpoint '{checkpoint.id}' prompt_id '{checkpoint.prompt_id}' was not found in prompts.yaml"
            )
        for tool_id in checkpoint.tool_ids:
            if tool_id not in tool_ids:
                raise ControlPlanePackLoadError(
                    f"control_plane.yaml checkpoint '{checkpoint.id}' tool_ids references unknown tool '{tool_id}'"
                )
            tool = next(tool for tool in tools.tools if tool.id == tool_id)
            if checkpoint.event not in tool.available_to:
                raise ControlPlanePackLoadError(
                    f"Tool '{tool_id}' is not available to '{checkpoint.event}' in {pack_path / 'config' / 'tools.yaml'}"
                )
