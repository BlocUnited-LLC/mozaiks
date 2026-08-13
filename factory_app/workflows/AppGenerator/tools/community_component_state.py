"""Deterministic installed-state helpers for local Community Components.

Installed Community Components are workspace build-context facts. The
canonical installed state is path-free and reproducible; the local source path
needed to verify and materialize a developer checkout lives in a separate
non-portable sidecar.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_STATE_SCHEMA_VERSION = "mozaiks.installed_components.v1"
_SOURCES_SCHEMA_VERSION = "mozaiks.installed_component_sources.v1"
_STATE_PATH = ".mozaiks/installed_components.json"
_SOURCES_PATH = ".mozaiks/installed_component_sources.json"
_SAFE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class InstalledComponentStateError(ValueError):
    """Raised when installed component state is invalid."""


def installed_components_path(build_context_root: Path) -> Path:
    return build_context_root.resolve() / _STATE_PATH


def installed_component_sources_path(build_context_root: Path) -> Path:
    return build_context_root.resolve() / _SOURCES_PATH


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise InstalledComponentStateError(f"Invalid JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InstalledComponentStateError(f"Installed component file must be a JSON object: {path}")
    return data


def _validate_dependency_block(raw: Any, *, field: str) -> dict[str, list[dict[str, str]]]:
    if raw is None:
        return {"packs": [], "capabilities": []}
    if not isinstance(raw, dict):
        raise InstalledComponentStateError(f"{field} must be an object")
    unknown = sorted(set(raw) - {"packs", "capabilities"})
    if unknown:
        raise InstalledComponentStateError(f"{field} has unsupported fields: {unknown}")

    packs: list[dict[str, str]] = []
    raw_packs = raw.get("packs") or []
    if not isinstance(raw_packs, list):
        raise InstalledComponentStateError(f"{field}.packs must be an array")
    for index, item in enumerate(raw_packs):
        if not isinstance(item, dict):
            raise InstalledComponentStateError(f"{field}.packs[{index}] must be an object")
        unknown_pack = sorted(set(item) - {"pack_id", "version"})
        if unknown_pack:
            raise InstalledComponentStateError(f"{field}.packs[{index}] has unsupported fields: {unknown_pack}")
        pack_id = str(item.get("pack_id") or "").strip()
        version = str(item.get("version") or "").strip()
        if not _SAFE_ID_RE.fullmatch(pack_id):
            raise InstalledComponentStateError(f"{field}.packs[{index}].pack_id is invalid")
        entry = {"pack_id": pack_id}
        if version:
            entry["version"] = version
        packs.append(entry)

    capabilities: list[dict[str, str]] = []
    raw_caps = raw.get("capabilities") or []
    if not isinstance(raw_caps, list):
        raise InstalledComponentStateError(f"{field}.capabilities must be an array")
    for index, item in enumerate(raw_caps):
        if not isinstance(item, dict):
            raise InstalledComponentStateError(f"{field}.capabilities[{index}] must be an object")
        unknown_cap = sorted(set(item) - {"capability_id"})
        if unknown_cap:
            raise InstalledComponentStateError(
                f"{field}.capabilities[{index}] has unsupported fields: {unknown_cap}"
            )
        capability_id = str(item.get("capability_id") or "").strip()
        if not capability_id:
            raise InstalledComponentStateError(f"{field}.capabilities[{index}].capability_id is required")
        capabilities.append({"capability_id": capability_id})
    return {
        "packs": sorted(packs, key=lambda item: item["pack_id"]),
        "capabilities": sorted(capabilities, key=lambda item: item["capability_id"]),
    }


def canonical_component_entry(entry: dict[str, Any]) -> dict[str, Any]:
    pack_id = str(entry.get("pack_id") or "").strip()
    version = str(entry.get("version") or "").strip()
    digest = str(entry.get("digest") or "").strip()
    source = str(entry.get("source") or "local").strip()
    if not _SAFE_ID_RE.fullmatch(pack_id):
        raise InstalledComponentStateError(f"Installed component pack_id is invalid: {pack_id!r}")
    if not version:
        raise InstalledComponentStateError(f"Installed component {pack_id} version is required")
    if not _DIGEST_RE.fullmatch(digest):
        raise InstalledComponentStateError(f"Installed component {pack_id} digest must be a canonical sha256 digest")
    dependencies = _validate_dependency_block(entry.get("dependencies"), field=f"components[{pack_id}].dependencies")

    capabilities: list[dict[str, str]] = []
    raw_caps = entry.get("capabilities") or []
    if not isinstance(raw_caps, list):
        raise InstalledComponentStateError(f"components[{pack_id}].capabilities must be an array")
    for index, item in enumerate(raw_caps):
        if not isinstance(item, dict):
            raise InstalledComponentStateError(f"components[{pack_id}].capabilities[{index}] must be an object")
        unknown = sorted(set(item) - {"capability_id"})
        if unknown:
            raise InstalledComponentStateError(
                f"components[{pack_id}].capabilities[{index}] has unsupported fields: {unknown}"
            )
        capability_id = str(item.get("capability_id") or "").strip()
        if capability_id:
            capabilities.append({"capability_id": capability_id})

    return {
        "pack_id": pack_id,
        "version": version,
        "digest": digest,
        "source": source,
        "dependencies": dependencies,
        "capabilities": sorted(capabilities, key=lambda item: item["capability_id"]),
    }


def canonical_installed_state(components: list[dict[str, Any]]) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    for component in components:
        entry = canonical_component_entry(component)
        existing = by_id.get(entry["pack_id"])
        if existing and existing != entry:
            raise InstalledComponentStateError(f"Duplicate installed component with different state: {entry['pack_id']}")
        by_id[entry["pack_id"]] = entry
    return {
        "schema_version": _STATE_SCHEMA_VERSION,
        "components": [by_id[key] for key in sorted(by_id)],
    }


def load_installed_components(build_context_root: Path) -> dict[str, Any]:
    path = installed_components_path(build_context_root)
    raw = _read_json(path)
    if raw is None:
        return canonical_installed_state([])
    unknown = sorted(set(raw) - {"schema_version", "components"})
    if unknown:
        raise InstalledComponentStateError(f"{path} has unsupported fields: {unknown}")
    if raw.get("schema_version") != _STATE_SCHEMA_VERSION:
        raise InstalledComponentStateError(
            f"{path} schema_version must be {_STATE_SCHEMA_VERSION!r}"
        )
    components = raw.get("components")
    if not isinstance(components, list):
        raise InstalledComponentStateError(f"{path} components must be an array")
    for index, item in enumerate(components):
        if not isinstance(item, dict):
            raise InstalledComponentStateError(f"{path} components[{index}] must be an object")
    return canonical_installed_state(components)


def write_installed_components(build_context_root: Path, state: dict[str, Any]) -> Path:
    canonical = canonical_installed_state(list(state.get("components") or []))
    path = installed_components_path(build_context_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(canonical, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_installed_component_sources(build_context_root: Path) -> dict[str, str]:
    path = installed_component_sources_path(build_context_root)
    raw = _read_json(path)
    if raw is None:
        return {}
    unknown = sorted(set(raw) - {"schema_version", "sources"})
    if unknown:
        raise InstalledComponentStateError(f"{path} has unsupported fields: {unknown}")
    if raw.get("schema_version") != _SOURCES_SCHEMA_VERSION:
        raise InstalledComponentStateError(
            f"{path} schema_version must be {_SOURCES_SCHEMA_VERSION!r}"
        )
    sources = raw.get("sources")
    if not isinstance(sources, list):
        raise InstalledComponentStateError(f"{path} sources must be an array")
    result: dict[str, str] = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise InstalledComponentStateError(f"{path} sources[{index}] must be an object")
        unknown_source = sorted(set(source) - {"pack_id", "local_source_path"})
        if unknown_source:
            raise InstalledComponentStateError(f"{path} sources[{index}] has unsupported fields: {unknown_source}")
        pack_id = str(source.get("pack_id") or "").strip()
        local_source_path = str(source.get("local_source_path") or "").strip()
        if not _SAFE_ID_RE.fullmatch(pack_id):
            raise InstalledComponentStateError(f"{path} sources[{index}].pack_id is invalid")
        if not local_source_path:
            raise InstalledComponentStateError(f"{path} sources[{index}].local_source_path is required")
        result[pack_id] = local_source_path
    return result


def write_installed_component_sources(build_context_root: Path, sources: dict[str, str]) -> Path:
    path = installed_component_sources_path(build_context_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _SOURCES_SCHEMA_VERSION,
        "sources": [
            {"pack_id": pack_id, "local_source_path": sources[pack_id]}
            for pack_id in sorted(sources)
        ],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def component_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["pack_id"]): item for item in state.get("components") or [] if isinstance(item, dict)}


__all__ = [
    "InstalledComponentStateError",
    "canonical_component_entry",
    "canonical_installed_state",
    "component_by_id",
    "installed_component_sources_path",
    "installed_components_path",
    "load_installed_component_sources",
    "load_installed_components",
    "write_installed_component_sources",
    "write_installed_components",
]
