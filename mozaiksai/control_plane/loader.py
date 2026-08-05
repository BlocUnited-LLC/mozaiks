from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mozaiksai.core.workflow.pack.config import get_workflow_sequence, load_global_pack_graph

from .config import load_control_plane_config
from .schema import (
    AG2_CHECKPOINT_OUTPUT_CONTRACTS,
    ControlPlaneCheckpointEvent,
    ControlPlaneManifest,
    ControlPlanePoliciesManifest,
    ControlPlanePromptDefinition,
    ControlPlanePromptsManifest,
    ControlPlaneToolsManifest,
    LoadedControlPlanePack,
)


class ControlPlanePackLoadError(Exception):
    """Raised when a refinement harness cannot be found or validated."""


CONTROL_PLANE_TOOL_TARGETS: set[str] = {"harness", *ControlPlaneCheckpointEvent.__args__}  # type: ignore[attr-defined]
DEFAULT_REFINEMENT_HARNESS_EXTENDS = "mozaiks.default_refinement_harness"
REFINEMENT_HARNESS_SCHEMA_VERSION = "mozaiks.refinement_harness.v1"
REFINEMENT_HARNESS_TOOLS_SCHEMA_VERSION = "mozaiks.refinement_harness.tools.v1"
REFINEMENT_HARNESS_POLICIES_SCHEMA_VERSION = "mozaiks.refinement_harness.policies.v1"
_HARNESS_OVERLAY_TOP_LEVEL_KEYS = {"schema_version", "extends", "overrides"}
_HARNESS_OVERRIDE_KEYS = {"routing", "checkpoints", "prompts"}


def resolve_factory_refinement_harness_root() -> Path:
    return (Path(__file__).resolve().parents[2] / "factory_app" / "refinement_harness").resolve()


def resolve_app_refinement_harness_root(app_root: Path | None = None) -> Path:
    from mozaiksai.core.workflow.paths import resolve_active_app_root

    root = app_root or resolve_active_app_root()
    return (root.parent / "refinement_harness").resolve()


def resolve_refinement_harness_path(
    *,
    app_root: Path | None = None,
    factory_root: Path | None = None,
) -> Path:
    app_candidate = resolve_app_refinement_harness_root(app_root)
    if (app_candidate / "config" / "harness.yaml").exists():
        return app_candidate

    factory_candidate = factory_root or resolve_factory_refinement_harness_root()
    if (factory_candidate / "config" / "harness.yaml").exists():
        return factory_candidate.resolve()

    raise ControlPlanePackLoadError(
        "Refinement harness was not found in refinement_harness/config or factory_app/refinement_harness/config."
    )


def load_selected_refinement_harness(
    *,
    app_root: Path | None = None,
    factory_root: Path | None = None,
) -> LoadedControlPlanePack:
    _ = load_control_plane_config(app_root)
    return load_refinement_harness(app_root=app_root, factory_root=factory_root)


def load_refinement_harness(
    *,
    app_root: Path | None = None,
    factory_root: Path | None = None,
) -> LoadedControlPlanePack:
    app_candidate = resolve_app_refinement_harness_root(app_root)
    factory_candidate = factory_root or resolve_factory_refinement_harness_root()
    app_manifest_path = app_candidate / "config" / "harness.yaml"
    if app_manifest_path.exists():
        app_manifest_data = _load_yaml_file(app_manifest_path)
        if app_manifest_data.get("extends"):
            return _load_extended_refinement_harness(
                overlay_path=app_candidate,
                overlay_manifest=app_manifest_data,
                factory_path=factory_candidate,
            )
        return _load_refinement_harness_pack(app_candidate)

    pack_path = resolve_refinement_harness_path(app_root=app_root, factory_root=factory_root)
    return _load_refinement_harness_pack(pack_path)


def _load_refinement_harness_pack(pack_path: Path) -> LoadedControlPlanePack:
    try:
        manifest = ControlPlaneManifest.model_validate(_load_yaml_file(pack_path / "config" / "harness.yaml"))
    except ValidationError as exc:
        raise ControlPlanePackLoadError(
            f"Invalid refinement harness manifest {pack_path / 'config' / 'harness.yaml'}: {exc}"
        ) from exc
    prompts = _load_prompt_manifest(pack_path / "prompts")
    tools = ControlPlaneToolsManifest.model_validate(_load_yaml_file(pack_path / "config" / "tools.yaml"))
    policies = ControlPlanePoliciesManifest.model_validate(
        _load_yaml_file(pack_path / "config" / "policies.yaml", required=False)
        or {"schema_version": "mozaiks.refinement_harness.policies.v1"}
    )
    _validate_pack(manifest=manifest, prompts=prompts, tools=tools, pack_path=pack_path)
    return LoadedControlPlanePack(path=pack_path, manifest=manifest, prompts=prompts, tools=tools, policies=policies)


def _load_extended_refinement_harness(
    *,
    overlay_path: Path,
    overlay_manifest: dict[str, Any],
    factory_path: Path,
) -> LoadedControlPlanePack:
    if overlay_manifest.get("extends") != DEFAULT_REFINEMENT_HARNESS_EXTENDS:
        raise ControlPlanePackLoadError(
            "refinement_harness/config/harness.yaml extends must be "
            f"'{DEFAULT_REFINEMENT_HARNESS_EXTENDS}'"
        )
    _validate_overlay_manifest_shape(overlay_manifest, overlay_path=overlay_path)

    if not (factory_path / "config" / "harness.yaml").exists():
        raise ControlPlanePackLoadError(
            f"Default refinement harness was not found at {factory_path / 'config' / 'harness.yaml'}"
        )

    base_manifest_data = _load_yaml_file(factory_path / "config" / "harness.yaml")
    manifest_data = _merge_harness_manifest_overlay(
        base_manifest_data,
        overlay_manifest.get("overrides") or {},
    )
    base_tools_data = _load_yaml_file(factory_path / "config" / "tools.yaml")
    tools_data = _merge_tools_overlay(base_tools_data, overlay_path=overlay_path)
    base_policies_data = (
        _load_yaml_file(factory_path / "config" / "policies.yaml", required=False)
        or {"schema_version": REFINEMENT_HARNESS_POLICIES_SCHEMA_VERSION}
    )
    policies_data = _merge_policies_overlay(base_policies_data, overlay_path=overlay_path)

    try:
        manifest = ControlPlaneManifest.model_validate(manifest_data)
        tools = ControlPlaneToolsManifest.model_validate(tools_data)
        policies = ControlPlanePoliciesManifest.model_validate(policies_data)
    except ValidationError as exc:
        raise ControlPlanePackLoadError(
            f"Invalid extended refinement harness manifest {overlay_path / 'config' / 'harness.yaml'}: {exc}"
        ) from exc

    try:
        prompts = _merge_prompt_overlay(
            _load_prompt_manifest(factory_path / "prompts"),
            overlay_manifest.get("overrides") or {},
            overlay_path=overlay_path,
        )
    except ValidationError as exc:
        raise ControlPlanePackLoadError(
            f"Invalid extended refinement harness prompts {overlay_path / 'config' / 'harness.yaml'}: {exc}"
        ) from exc
    _validate_pack(manifest=manifest, prompts=prompts, tools=tools, pack_path=overlay_path)
    return LoadedControlPlanePack(
        path=overlay_path,
        manifest=manifest,
        prompts=prompts,
        tools=tools,
        policies=policies,
    )


def _load_yaml_file(path: Path, *, required: bool = True) -> dict:
    if not path.exists():
        if required:
            raise ControlPlanePackLoadError(f"Missing refinement harness manifest: {path}")
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ControlPlanePackLoadError(f"Failed to parse YAML at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ControlPlanePackLoadError(f"Refinement harness manifest must be a YAML object: {path}")
    return data


def _load_prompt_manifest(prompts_root: Path) -> ControlPlanePromptsManifest:
    if not prompts_root.exists() or not prompts_root.is_dir():
        raise ControlPlanePackLoadError(f"Missing refinement harness prompts directory: {prompts_root}")

    prompts: list[ControlPlanePromptDefinition] = []
    for prompt_path in sorted(prompts_root.glob("*.yaml")):
        data = _load_yaml_file(prompt_path)
        prompt_id = str(data.get("id") or prompt_path.stem).strip()
        content = str(data.get("content") or "").strip()
        prompts.append(ControlPlanePromptDefinition(id=prompt_id, content=content))

    return ControlPlanePromptsManifest(
        schema_version="mozaiks.refinement_harness.v1.prompts",
        prompts=prompts,
    )


def _validate_overlay_manifest_shape(overlay_manifest: dict[str, Any], *, overlay_path: Path) -> None:
    unknown_keys = set(overlay_manifest) - _HARNESS_OVERLAY_TOP_LEVEL_KEYS
    if unknown_keys:
        raise ControlPlanePackLoadError(
            "Extended refinement harness overlays may only declare "
            f"{', '.join(sorted(_HARNESS_OVERLAY_TOP_LEVEL_KEYS))}; "
            f"found {', '.join(sorted(unknown_keys))} in {overlay_path / 'config' / 'harness.yaml'}"
        )
    schema_version = overlay_manifest.get("schema_version")
    if schema_version != REFINEMENT_HARNESS_SCHEMA_VERSION:
        raise ControlPlanePackLoadError(
            f"Extended refinement harness overlay schema_version must be {REFINEMENT_HARNESS_SCHEMA_VERSION!r}"
        )
    overrides = overlay_manifest.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ControlPlanePackLoadError("Extended refinement harness overrides must be a YAML object")
    unknown_overrides = set(overrides) - _HARNESS_OVERRIDE_KEYS
    if unknown_overrides:
        raise ControlPlanePackLoadError(
            "Extended refinement harness overrides may only declare "
            f"{', '.join(sorted(_HARNESS_OVERRIDE_KEYS))}; "
            f"found {', '.join(sorted(unknown_overrides))}"
        )


def _merge_harness_manifest_overlay(
    base_manifest_data: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    merged = deepcopy(base_manifest_data)
    if merged.get("schema_version") != REFINEMENT_HARNESS_SCHEMA_VERSION:
        raise ControlPlanePackLoadError(
            f"Default refinement harness schema_version must be {REFINEMENT_HARNESS_SCHEMA_VERSION!r}"
        )

    if "routing" in overrides:
        routing_override = overrides["routing"]
        if not isinstance(routing_override, dict):
            raise ControlPlanePackLoadError("overrides.routing must be a YAML object")
        merged["routing"] = _merge_routing(merged.get("routing") or {}, routing_override)

    if "checkpoints" in overrides:
        checkpoint_overrides = overrides["checkpoints"]
        if not isinstance(checkpoint_overrides, list):
            raise ControlPlanePackLoadError("overrides.checkpoints must be a YAML list")
        merged["checkpoints"] = _merge_list_by_key(
            merged.get("checkpoints") or [],
            checkpoint_overrides,
            key="event",
            item_label="checkpoint",
            merge_item=_merge_checkpoint,
        )

    return merged


def _merge_routing(base_routing: dict[str, Any], routing_override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base_routing)
    if "default_artifact_kind" in routing_override and "default_build_family" not in routing_override:
        routing_override = dict(routing_override)
        routing_override["default_build_family"] = routing_override["default_artifact_kind"]
    if "default_build_family" in routing_override:
        merged["default_build_family"] = routing_override["default_build_family"]
    if "artifacts" in routing_override:
        artifact_overrides = routing_override["artifacts"]
        if not isinstance(artifact_overrides, list):
            raise ControlPlanePackLoadError("overrides.routing.artifacts must be a YAML list")
        merged["artifacts"] = _merge_list_by_key(
            merged.get("artifacts") or [],
            artifact_overrides,
            key="build_family",
            item_label="artifact route",
            merge_item=_deep_merge_mapping,
        )
    unknown_keys = set(routing_override) - {"default_artifact_kind", "default_build_family", "artifacts"}
    if unknown_keys:
        raise ControlPlanePackLoadError(
            f"overrides.routing contains unsupported key(s): {', '.join(sorted(unknown_keys))}"
        )
    return merged


def _merge_checkpoint(base_item: dict[str, Any], override_item: dict[str, Any]) -> dict[str, Any]:
    append_tool_ids = override_item.get("append_tool_ids")
    clean_override = {key: value for key, value in override_item.items() if key != "append_tool_ids"}
    merged = _deep_merge_mapping(base_item, clean_override)
    if append_tool_ids is not None:
        if not isinstance(append_tool_ids, list):
            raise ControlPlanePackLoadError("checkpoint append_tool_ids must be a YAML list")
        existing_tool_ids = list(merged.get("tool_ids") or [])
        for tool_id in append_tool_ids:
            normalized = str(tool_id or "").strip()
            if normalized and normalized not in existing_tool_ids:
                existing_tool_ids.append(normalized)
        merged["tool_ids"] = existing_tool_ids
    return merged


def _merge_tools_overlay(base_tools_data: dict[str, Any], *, overlay_path: Path) -> dict[str, Any]:
    tools_path = overlay_path / "config" / "tools.yaml"
    if not tools_path.exists():
        return deepcopy(base_tools_data)

    overlay_tools_data = _load_yaml_file(tools_path)
    _validate_schema_lock(
        base_tools_data,
        overlay_tools_data,
        expected=REFINEMENT_HARNESS_TOOLS_SCHEMA_VERSION,
        path=tools_path,
    )
    merged = deepcopy(base_tools_data)
    merged["tools"] = _merge_list_by_key(
        base_tools_data.get("tools") or [],
        overlay_tools_data.get("tools") or [],
        key="id",
        item_label="tool",
        merge_item=_deep_merge_mapping,
    )
    return merged


def _merge_policies_overlay(base_policies_data: dict[str, Any], *, overlay_path: Path) -> dict[str, Any]:
    policies_path = overlay_path / "config" / "policies.yaml"
    if not policies_path.exists():
        return deepcopy(base_policies_data)

    overlay_policies_data = _load_yaml_file(policies_path)
    _validate_schema_lock(
        base_policies_data,
        overlay_policies_data,
        expected=REFINEMENT_HARNESS_POLICIES_SCHEMA_VERSION,
        path=policies_path,
    )
    return _deep_merge_mapping(base_policies_data, overlay_policies_data)


def _merge_prompt_overlay(
    base_prompts: ControlPlanePromptsManifest,
    overrides: dict[str, Any],
    *,
    overlay_path: Path,
) -> ControlPlanePromptsManifest:
    prompt_overrides = overrides.get("prompts") or {}
    if not isinstance(prompt_overrides, dict):
        raise ControlPlanePackLoadError("overrides.prompts must be a YAML object")

    prompts = [prompt.model_dump() for prompt in base_prompts.prompts]
    for prompt_id, prompt_ref in prompt_overrides.items():
        normalized_id = str(prompt_id or "").strip()
        if not normalized_id:
            raise ControlPlanePackLoadError("overrides.prompts contains an empty prompt id")
        prompt_path = _resolve_overlay_file(overlay_path=overlay_path, value=prompt_ref)
        prompt_data = _load_yaml_file(prompt_path)
        prompt_data["id"] = str(prompt_data.get("id") or normalized_id).strip()
        if prompt_data["id"] != normalized_id:
            raise ControlPlanePackLoadError(
                f"Prompt override {prompt_path} declares id {prompt_data['id']!r}, expected {normalized_id!r}"
            )
        prompts = _upsert_by_key(
            prompts,
            prompt_data,
            key="id",
            item_label="prompt",
            merge_item=_deep_merge_mapping,
        )

    return ControlPlanePromptsManifest.model_validate(
        {
            "schema_version": "mozaiks.refinement_harness.v1.prompts",
            "prompts": prompts,
        }
    )


def _resolve_overlay_file(*, overlay_path: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ControlPlanePackLoadError("Prompt override path must be a non-empty string")
    raw_path = Path(value.strip())
    if raw_path.is_absolute():
        raise ControlPlanePackLoadError("Prompt override paths must be app-workspace relative")
    workspace_root = overlay_path.parent
    candidates = []
    if raw_path.parts and raw_path.parts[0] == "refinement_harness":
        candidates.append((workspace_root / raw_path).resolve())
    else:
        candidates.append((overlay_path / raw_path).resolve())
    resolved = candidates[0]
    allowed_root = overlay_path.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ControlPlanePackLoadError(
            f"Prompt override path must stay inside refinement_harness/: {value}"
        ) from exc
    if not resolved.exists():
        raise ControlPlanePackLoadError(f"Prompt override path not found: {resolved}")
    return resolved


def _validate_schema_lock(
    base_data: dict[str, Any],
    overlay_data: dict[str, Any],
    *,
    expected: str,
    path: Path,
) -> None:
    if base_data.get("schema_version") != expected:
        raise ControlPlanePackLoadError(f"Default manifest schema_version must be {expected!r}")
    if overlay_data.get("schema_version") != expected:
        raise ControlPlanePackLoadError(f"{path} schema_version must be {expected!r}")


def _merge_list_by_key(
    base_items: list[Any],
    overlay_items: list[Any],
    *,
    key: str,
    item_label: str,
    merge_item: Any,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in base_items:
        normalized = _normalize_keyed_mapping(item, key=key, item_label=item_label)
        item_key = str(normalized[key]).strip()
        if item_key in positions:
            raise ControlPlanePackLoadError(f"Default refinement harness contains duplicate {item_label} {item_key!r}")
        positions[item_key] = len(merged)
        merged.append(normalized)

    for item in overlay_items:
        normalized = _normalize_keyed_mapping(item, key=key, item_label=item_label)
        item_key = str(normalized[key]).strip()
        if normalized.get("remove") is True:
            if item_key in positions:
                index = positions.pop(item_key)
                merged.pop(index)
                positions = {str(entry[key]).strip(): idx for idx, entry in enumerate(merged)}
            continue
        normalized.pop("remove", None)
        merged = _upsert_by_key(
            merged,
            normalized,
            key=key,
            item_label=item_label,
            merge_item=merge_item,
        )
        positions = {str(entry[key]).strip(): idx for idx, entry in enumerate(merged)}

    return merged


def _upsert_by_key(
    items: list[dict[str, Any]],
    overlay_item: dict[str, Any],
    *,
    key: str,
    item_label: str,
    merge_item: Any,
) -> list[dict[str, Any]]:
    item_key = str(overlay_item[key]).strip()
    for index, item in enumerate(items):
        if str(item[key]).strip() == item_key:
            updated = list(items)
            updated[index] = merge_item(item, overlay_item)
            return updated
    return [*items, overlay_item]


def _normalize_keyed_mapping(item: Any, *, key: str, item_label: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ControlPlanePackLoadError(f"Refinement harness {item_label} entries must be YAML objects")
    normalized = deepcopy(item)
    item_key = str(normalized.get(key) or "").strip()
    if not item_key:
        raise ControlPlanePackLoadError(f"Refinement harness {item_label} entries must declare {key}")
    normalized[key] = item_key
    return normalized


def _deep_merge_mapping(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        raise ControlPlanePackLoadError("Refinement harness merge values must be YAML objects")
    merged = deepcopy(base)
    for key, value in overlay.items():
        if (
            key == "schema_version"
            and key in merged
            and str(value or "").strip() != str(merged.get(key) or "").strip()
        ):
            raise ControlPlanePackLoadError("refinement harness overlay cannot change schema_version")
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_mapping(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _validate_pack(
    *,
    manifest: ControlPlaneManifest,
    prompts: ControlPlanePromptsManifest,
    tools: ControlPlaneToolsManifest,
    pack_path: Path,
) -> None:
    prompt_ids = {prompt.id for prompt in prompts.prompts}
    tool_ids = {tool.id for tool in tools.tools}
    checkpoint_targets = CONTROL_PLANE_TOOL_TARGETS

    for tool in tools.tools:
        invalid_targets = [target for target in tool.available_to if target not in checkpoint_targets]
        if invalid_targets:
            raise ControlPlanePackLoadError(
                f"tools.yaml tool '{tool.id}' contains invalid available_to target(s): {', '.join(sorted(invalid_targets))}"
            )

    for checkpoint in manifest.checkpoints:
        expected_output = AG2_CHECKPOINT_OUTPUT_CONTRACTS.get(checkpoint.event)
        if expected_output is not None and not checkpoint.prompt_id:
            raise ControlPlanePackLoadError(
                f"harness.yaml checkpoint '{checkpoint.id}' for event '{checkpoint.event}' "
                "must declare prompt_id"
            )
        if expected_output is None and checkpoint.prompt_id:
            raise ControlPlanePackLoadError(
                f"harness.yaml checkpoint '{checkpoint.id}' for deterministic event '{checkpoint.event}' "
                "must not declare prompt_id"
            )

        if checkpoint.prompt_id and checkpoint.prompt_id not in prompt_ids:
            raise ControlPlanePackLoadError(
                f"harness.yaml checkpoint '{checkpoint.id}' prompt_id '{checkpoint.prompt_id}' was not found in prompts.yaml"
            )
        for tool_id in checkpoint.tool_ids:
            if tool_id not in tool_ids:
                raise ControlPlanePackLoadError(
                    f"harness.yaml checkpoint '{checkpoint.id}' tool_ids references unknown tool '{tool_id}'"
                )
            tool = next(tool for tool in tools.tools if tool.id == tool_id)
            if checkpoint.event not in tool.available_to:
                raise ControlPlanePackLoadError(
                    f"Tool '{tool_id}' is not available to '{checkpoint.event}' in {pack_path / 'config' / 'tools.yaml'}"
                )

    _validate_route_sequences(manifest=manifest, pack_path=pack_path)


def _validate_route_sequences(*, manifest: ControlPlaneManifest, pack_path: Path) -> None:
    route_refs: list[tuple[str, str, str]] = []
    for artifact in manifest.routing.artifacts:
        for change_class in ("patch", "design", "feature", "core"):
            route = getattr(artifact.routes, change_class)
            route_refs.append((artifact.build_family, change_class, route.workflow_sequence))

    if not route_refs:
        return

    pack_graph = load_global_pack_graph()
    if pack_graph is None:
        raise ControlPlanePackLoadError(
            "harness.yaml declares routing workflow_sequence values, but no "
            "extension_registry.json workflow graph is loaded."
        )

    for build_family, change_class, sequence_id in route_refs:
        sequence = get_workflow_sequence(pack_graph, sequence_id)
        if sequence is None:
            raise ControlPlanePackLoadError(
                "harness.yaml route "
                f"{build_family}.{change_class} references unknown workflow_sequence "
                f"'{sequence_id}' in {pack_path / 'config' / 'harness.yaml'}"
            )
        families = [
            str(item).strip()
            for item in getattr(sequence, "affected_declarative_families", [])
            if str(item).strip()
        ]
        if not families:
            raise ControlPlanePackLoadError(
                "workflow_sequence "
                f"'{sequence_id}' used by harness.yaml route {build_family}.{change_class} "
                "must declare affected_declarative_families in extension_registry.json"
            )


resolve_factory_control_plane_root = resolve_factory_refinement_harness_root
resolve_app_control_plane_root = resolve_app_refinement_harness_root
