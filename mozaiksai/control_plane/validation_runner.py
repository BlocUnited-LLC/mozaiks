from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

try:  # pragma: no cover - import failures are surfaced by tests.
    import yaml
except Exception:  # pragma: no cover - import failures are surfaced by tests.
    yaml = None  # type: ignore[assignment]

from factory_app.workflows._shared.generated_ui_contract import (
    audit_app_ui_bundle_integrity,
    audit_generated_react_files,
    audit_page_schemas,
)
from mozaiksai.control_plane.dry_run import RefinementExecutionPlan
from mozaiksai.control_plane.scoped_execution import ScopedRefinementResult
from mozaiksai.control_plane.staging import WORKSPACE_DIRNAME, RefinementStagingResult
from mozaiksai.control_plane.validation_evidence import ValidationEvidence

ValidationStatus = Literal["passed", "failed", "skipped", "warning"]

_TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".js", ".jsx", ".md", ".py"}
_PAGE_SUFFIXES = {".yaml", ".yml"}
_CUSTOM_REACT_SUFFIXES = {".jsx"}
_EXPERIENCE_SPEC_FILENAMES = {
    "experience_spec.json",
    "experience_spec.yaml",
    "experience_spec.yml",
    "ui_schema.json",
    "ui_schema.yaml",
    "ui_schema.yml",
}

_PLAN_VALIDATION_TARGETS: dict[str, list[str]] = {
    "route_component_validation": ["route_component_validation"],
    "ui_theme_primitive_validation": ["ui_theme_primitive_validation"],
    "module_contract_validation": ["module_contract_validation"],
    "integration_readiness": ["integration_readiness_validation"],
    "integration_readiness_validation": ["integration_readiness_validation"],
    "database_migration_review": ["data_contract_validation", "migration_plan_validation"],
    "data_contract_validation": ["data_contract_validation"],
    "migration_plan_validation": ["migration_plan_validation"],
    "managed_facade_validation": ["managed_facade_boundary_validation"],
    "managed_facade_boundary_validation": ["managed_facade_boundary_validation"],
    "experience_spec_update": ["experience_spec_validation"],
    "experience_spec_validation": ["experience_spec_validation"],
    "app_bundle_validation": ["app_bundle_validation"],
}

_VALIDATION_ORDER = [
    "route_component_validation",
    "ui_theme_primitive_validation",
    "module_contract_validation",
    "experience_spec_validation",
    "app_bundle_validation",
    "data_contract_validation",
    "migration_plan_validation",
    "managed_facade_boundary_validation",
    "integration_readiness_validation",
]

_BUNDLE_VALIDATION_NAMES = {
    "route_component_validation",
    "ui_theme_primitive_validation",
    "module_contract_validation",
    "experience_spec_validation",
}

_MANAGED_INTERNAL_TERMS = ("managed_", "provider")


class RefinementValidationItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: ValidationStatus
    reason: str
    artifacts: list[str] = Field(default_factory=list)


class RefinementValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: ValidationEvidence
    items: list[RefinementValidationItemResult] = Field(default_factory=list)


def _dedupe_ordered(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _normalize_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    return "/".join(parts)


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    pure_path = PurePosixPath(path)
    return any(pure_path.match(pattern) for pattern in patterns)


def _is_text_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in _TEXT_SUFFIXES or path.name in _EXPERIENCE_SPEC_FILENAMES


def _collect_workspace_files(workspace_root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    if not workspace_root.exists():
        return files
    for path in sorted(workspace_root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not _is_text_file(path):
            continue
        relative_path = path.relative_to(workspace_root).as_posix()
        try:
            files[relative_path] = path.read_text(encoding="utf-8")
        except Exception:
            files[relative_path] = path.read_text(encoding="utf-8", errors="replace")
    return files


def _code_file_list(files: dict[str, str], *, predicate: Any | None = None) -> list[dict[str, str]]:
    entries = []
    for filename in sorted(files):
        if predicate is not None and not predicate(filename):
            continue
        entries.append({"filename": filename, "content": files[filename]})
    return entries


def _page_schema_entries(files: dict[str, str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    pages: list[dict[str, Any]] = []
    warnings: list[str] = []
    artifacts: list[str] = []
    if yaml is None:
        return pages, ["PyYAML is unavailable; page schema validation cannot run."], artifacts

    for filename in sorted(files):
        path = PurePosixPath(filename)
        if path.parts[:2] != ("ui", "pages"):
            continue
        if path.parent.name == "custom":
            continue
        if path.suffix.lower() not in _PAGE_SUFFIXES:
            continue
        artifacts.append(filename)
        try:
            parsed = yaml.safe_load(files[filename])
        except Exception as exc:
            warnings.append(f"{filename} is not valid YAML: {exc}")
            continue
        if not isinstance(parsed, dict):
            warnings.append(f"{filename} must parse to a YAML mapping.")
            continue
        pages.append(parsed)
    return pages, warnings, artifacts


def _custom_react_entries(files: dict[str, str]) -> list[dict[str, str]]:
    def _is_custom_react(filename: str) -> bool:
        path = PurePosixPath(filename)
        return path.parts[:2] == ("ui", "pages") and path.parent.name == "custom" and path.suffix.lower() in _CUSTOM_REACT_SUFFIXES

    return _code_file_list(files, predicate=_is_custom_react)


def _module_contract_entries(files: dict[str, str]) -> list[dict[str, str]]:
    def _is_module_contract(filename: str) -> bool:
        path = PurePosixPath(filename)
        return path.parts[:2] == ("modules", path.parts[1] if len(path.parts) > 1 else "") and (
            path.name == "module.yaml"
            or (len(path.parts) >= 4 and path.parts[2] == "contracts" and path.suffix.lower() in {".yaml", ".yml"})
        )

    return _code_file_list(files, predicate=_is_module_contract)


def _module_contract_module_dir(filename: str) -> str | None:
    path = PurePosixPath(filename)
    if len(path.parts) < 2 or path.parts[0] != "modules":
        return None
    return "/".join(path.parts[:2])


def _route_bundle_entries(files: dict[str, str]) -> list[dict[str, str]]:
    def _is_route_surface(filename: str) -> bool:
        path = PurePosixPath(filename)
        if filename in {"ui/route_manifest.json", "ui/index.js"}:
            return True
        if path.parts[:2] == ("ui", "pages") and path.parent.name == "custom" and path.suffix.lower() == ".jsx":
            return True
        if filename == "admin/admin_registry.yaml":
            return True
        if path.parts[:2] == ("ui", "pages") and path.suffix.lower() in _PAGE_SUFFIXES:
            return True
        return False

    return _code_file_list(files, predicate=_is_route_surface)


def _integration_surface_entries(files: dict[str, str]) -> list[dict[str, str]]:
    def _is_integration_surface(filename: str) -> bool:
        path = PurePosixPath(filename)
        if _matches_any(
            filename,
            (
                "services/integrations/*_client.py",
                "services/adapters/**/*.py",
                "config/integrations*.json",
                "docs/integrations*.md",
            ),
        ):
            return True
        return len(path.parts) >= 4 and path.parts[0] == "modules" and path.parts[2] == "backend" and path.name in {"service.py", "schemas.py", "policy.py"}

    return _code_file_list(files, predicate=_is_integration_surface)


def _database_surface_entries(files: dict[str, str]) -> list[dict[str, str]]:
    def _is_database_surface(filename: str) -> bool:
        return filename == "data/contract.json" or _matches_any(filename, ("data/migrations/*.json",))

    return _code_file_list(files, predicate=_is_database_surface)


def _experience_spec_entries(files: dict[str, str]) -> list[dict[str, str]]:
    def _is_experience_spec_surface(filename: str) -> bool:
        path = PurePosixPath(filename)
        return path.name in _EXPERIENCE_SPEC_FILENAMES

    return _code_file_list(files, predicate=_is_experience_spec_surface)


def _is_module_internal_managed_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) < 2 or parts[0] != "modules":
        return False
    module_id = parts[1].lower()
    return any(term in module_id for term in _MANAGED_INTERNAL_TERMS)


def _plan_validation_targets(plan: RefinementExecutionPlan) -> list[str]:
    targets: list[str] = []
    for item in plan.validation_plan.items:
        if not item.required:
            continue
        targets.extend(_PLAN_VALIDATION_TARGETS.get(item.id, [item.id]))

    lane = str(plan.refinement_lane or "").strip()
    families = {str(family or "").strip() for family in plan.affected_declarative_families}
    if lane == "experience_design" or "experience_spec" in families:
        targets.extend(["experience_spec_validation", "app_bundle_validation"])
    return _dedupe_ordered(targets)


def _normalize_selected_targets(selected: Sequence[str] | None) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    unknown: list[str] = []
    if not selected:
        return normalized, unknown
    for name in selected:
        key = str(name or "").strip()
        if not key:
            continue
        targets = _PLAN_VALIDATION_TARGETS.get(key)
        if targets is None:
            unknown.append(key)
            continue
        normalized.extend(targets)
    return _dedupe_ordered(normalized), _dedupe_ordered(unknown)


def _result(
    *,
    name: str,
    status: ValidationStatus,
    reason: str,
    artifacts: Sequence[str] | None = None,
) -> RefinementValidationItemResult:
    return RefinementValidationItemResult(
        name=name,
        status=status,
        reason=reason,
        artifacts=_dedupe_ordered(list(artifacts or [])),
    )


def _route_component_validation(files: dict[str, str]) -> RefinementValidationItemResult:
    entries = _route_bundle_entries(files)
    if not entries:
        return _result(
            name="route_component_validation",
            status="skipped",
            reason="No route/component surfaces were present in the staged workspace.",
        )

    warnings = audit_app_ui_bundle_integrity(entries, source_label="staged app bundle")
    if warnings:
        return _result(
            name="route_component_validation",
            status="failed",
            reason="Route/component validation found drift: " + "; ".join(warnings),
            artifacts=[entry["filename"] for entry in entries],
        )
    return _result(
        name="route_component_validation",
        status="passed",
        reason="Route/component surfaces are internally consistent.",
        artifacts=[entry["filename"] for entry in entries],
    )


def _ui_theme_primitive_validation(files: dict[str, str]) -> RefinementValidationItemResult:
    page_entries, page_warnings, page_artifacts = _page_schema_entries(files)
    react_entries = _custom_react_entries(files)
    if not page_entries and not react_entries:
        return _result(
            name="ui_theme_primitive_validation",
            status="skipped",
            reason="No page schema or custom React surfaces were present in the staged workspace.",
        )

    warnings = list(page_warnings)
    if page_entries:
        warnings.extend(audit_page_schemas(page_entries, source_label="staged page schemas"))
    if react_entries:
        warnings.extend(
            audit_generated_react_files(
                react_entries,
                source_label="staged custom React",
                require_jsx=True,
            )
        )

    artifacts = page_artifacts + [entry["filename"] for entry in react_entries]
    if warnings:
        return _result(
            name="ui_theme_primitive_validation",
            status="failed",
            reason="UI theme/primitive validation found drift: " + "; ".join(warnings),
            artifacts=artifacts,
        )
    return _result(
        name="ui_theme_primitive_validation",
        status="passed",
        reason="UI page schemas and custom React surfaces are structurally sound.",
        artifacts=artifacts,
    )


def _module_contract_validation(files: dict[str, str]) -> RefinementValidationItemResult:
    entries = _module_contract_entries(files)
    if not entries:
        return _result(
            name="module_contract_validation",
            status="skipped",
            reason="No module contract files were present in the staged workspace.",
        )

    if yaml is None:
        return _result(
            name="module_contract_validation",
            status="warning",
            reason="PyYAML is unavailable; module contract validation cannot parse staged YAML artifacts.",
        )

    warnings: list[str] = []
    events_by_module: set[str] = set()
    module_yamls: list[tuple[str, dict[str, Any]]] = []
    for entry in entries:
        filename = entry["filename"]
        try:
            parsed = yaml.safe_load(entry["content"])
        except Exception as exc:
            warnings.append(f"{filename} is not valid YAML: {exc}")
            continue
        if not isinstance(parsed, dict):
            warnings.append(f"{filename} must parse to a YAML mapping.")
            continue
        if PurePosixPath(filename).name == "events.yaml":
            module_dir = _module_contract_module_dir(filename)
            if module_dir:
                events_by_module.add(module_dir)
        elif PurePosixPath(filename).name == "module.yaml":
            module_yamls.append((filename, parsed))

    for filename, parsed in module_yamls:
        module = parsed.get("module")
        if not isinstance(module, dict):
            warnings.append(f"{filename} is missing required 'module' object.")
            continue
        if not str(module.get("id") or "").strip():
            warnings.append(f"{filename} is missing required module.id.")
        actions = parsed.get("actions")
        emits_events = False
        if isinstance(actions, list):
            emits_events = any(isinstance(action, dict) and action.get("emits") for action in actions)
        if emits_events:
            module_dir = _module_contract_module_dir(filename)
            if module_dir and module_dir not in events_by_module:
                warnings.append(f"{filename} declares emitted events but no companion events.yaml was found.")

    if warnings:
        return _result(
            name="module_contract_validation",
            status="failed",
            reason="Module contract validation found drift: " + "; ".join(warnings),
            artifacts=[entry["filename"] for entry in entries],
        )
    return _result(
        name="module_contract_validation",
        status="passed",
        reason="Module contract YAML files are internally consistent.",
        artifacts=[entry["filename"] for entry in entries],
    )


def _experience_spec_validation(
    files: dict[str, str],
    *,
    plan: RefinementExecutionPlan,
) -> RefinementValidationItemResult:
    entries = _experience_spec_entries(files)
    if not entries:
        if plan.refinement_lane == "experience_design" or "experience_spec" in {str(family or "").strip() for family in plan.affected_declarative_families}:
            return _result(
                name="experience_spec_validation",
                status="warning",
                reason="No staged ExperienceSpec artifact was found; validation cannot be completed from the current workspace snapshot.",
            )
        return _result(
            name="experience_spec_validation",
            status="skipped",
            reason="No ExperienceSpec artifact was present in the staged workspace.",
        )

    if yaml is None:
        return _result(
            name="experience_spec_validation",
            status="warning",
            reason="PyYAML is unavailable; ExperienceSpec validation cannot parse staged YAML artifacts.",
            artifacts=[entry["filename"] for entry in entries],
        )

    warnings: list[str] = []
    for entry in entries:
        try:
            parsed = yaml.safe_load(entry["content"])
        except Exception as exc:
            warnings.append(f"{entry['filename']} is not valid YAML: {exc}")
            continue
        if not isinstance(parsed, dict):
            warnings.append(f"{entry['filename']} must parse to an object.")
            continue
        if not parsed:
            warnings.append(f"{entry['filename']} is empty and cannot act as an ExperienceSpec artifact.")

    if warnings:
        return _result(
            name="experience_spec_validation",
            status="failed",
            reason="ExperienceSpec validation found drift: " + "; ".join(warnings),
            artifacts=[entry["filename"] for entry in entries],
        )
    return _result(
        name="experience_spec_validation",
        status="passed",
        reason="ExperienceSpec artifact shape is valid.",
        artifacts=[entry["filename"] for entry in entries],
    )


def _data_contract_validation(
    files: dict[str, str],
    *,
    required: bool,
) -> RefinementValidationItemResult:
    filename = "data/contract.json"
    content = files.get(filename)
    if content is None:
        return _result(
            name="data_contract_validation",
            status="skipped" if not required else "warning",
            reason="data/contract.json was not present in the staged workspace.",
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return _result(
            name="data_contract_validation",
            status="failed",
            reason=f"{filename} is not valid JSON: {exc.msg}.",
            artifacts=[filename],
        )
    if not isinstance(parsed, dict):
        return _result(
            name="data_contract_validation",
            status="failed",
            reason=f"{filename} must parse to a JSON object.",
            artifacts=[filename],
        )
    return _result(
        name="data_contract_validation",
        status="passed",
        reason="data/contract.json is valid JSON.",
        artifacts=[filename],
    )


def _migration_plan_validation(
    files: dict[str, str],
    *,
    required: bool,
) -> RefinementValidationItemResult:
    entries = _database_surface_entries(files)
    migration_entries = [entry for entry in entries if entry["filename"] != "data/contract.json"]
    if not migration_entries:
        return _result(
            name="migration_plan_validation",
            status="skipped" if not required else "warning",
            reason="No data/migrations/*.json files were present in the staged workspace.",
        )

    warnings: list[str] = []
    for entry in migration_entries:
        try:
            parsed = json.loads(entry["content"])
        except json.JSONDecodeError as exc:
            warnings.append(f"{entry['filename']} is not valid JSON: {exc.msg}.")
            continue
        if not isinstance(parsed, dict):
            warnings.append(f"{entry['filename']} must parse to a JSON object.")

    if warnings:
        return _result(
            name="migration_plan_validation",
            status="failed",
            reason="Migration plan validation found drift: " + "; ".join(warnings),
            artifacts=[entry["filename"] for entry in migration_entries],
        )
    return _result(
        name="migration_plan_validation",
        status="passed",
        reason="Migration plan JSON files are valid.",
        artifacts=[entry["filename"] for entry in migration_entries],
    )


def _managed_facade_boundary_validation(
    files: dict[str, str],
) -> RefinementValidationItemResult:
    paths = sorted(files)
    managed_internal_paths = [path for path in paths if _is_module_internal_managed_path(path)]
    integration_paths = [
        path
        for path in paths
        if _matches_any(path, ("services/integrations/*_client.py", "services/adapters/**/*.py"))
        or path.startswith("modules/")
    ]
    if not managed_internal_paths and not integration_paths:
        return _result(
            name="managed_facade_boundary_validation",
            status="skipped",
            reason="No managed capability adapter/facade surfaces were present in the staged workspace.",
        )
    if managed_internal_paths:
        return _result(
            name="managed_facade_boundary_validation",
            status="failed",
            reason="Managed/provider internal module paths are not allowed: " + ", ".join(managed_internal_paths),
            artifacts=managed_internal_paths,
        )
    return _result(
        name="managed_facade_boundary_validation",
        status="passed",
        reason="Managed capability boundaries stay app-owned.",
        artifacts=integration_paths,
    )


def _integration_readiness_validation(files: dict[str, str]) -> RefinementValidationItemResult:
    entries = _integration_surface_entries(files)
    if not entries:
        return _result(
            name="integration_readiness_validation",
            status="skipped",
            reason="No integration surfaces were present in the staged workspace.",
        )
    return _result(
        name="integration_readiness_validation",
        status="warning",
        reason="Deterministic integration readiness checks are not implemented yet; this remains a manual review signal.",
        artifacts=[entry["filename"] for entry in entries],
    )


def _app_bundle_validation(files: dict[str, str], *, plan: RefinementExecutionPlan) -> RefinementValidationItemResult:
    route_entries = _route_bundle_entries(files)
    page_entries, page_warnings, page_artifacts = _page_schema_entries(files)
    module_entries = _module_contract_entries(files)
    experience_entries = _experience_spec_entries(files)

    if not route_entries and not page_entries and not module_entries and not experience_entries:
        return _result(
            name="app_bundle_validation",
            status="skipped",
            reason="No bundle-level declarative files were present in the staged workspace.",
        )

    warnings = list(page_warnings)
    if route_entries:
        warnings.extend(audit_app_ui_bundle_integrity(route_entries, source_label="staged app bundle"))
    if page_entries:
        warnings.extend(audit_page_schemas(page_entries, source_label="staged page schemas"))
    if module_entries:
        module_validation = _module_contract_validation(files)
        if module_validation.status != "passed":
            warnings.append(module_validation.reason)
    if experience_entries:
        if yaml is None:
            warnings.append("PyYAML is unavailable; ExperienceSpec bundle checks cannot run.")
        else:
            for entry in experience_entries:
                try:
                    parsed = yaml.safe_load(entry["content"])
                except Exception as exc:
                    warnings.append(f"{entry['filename']} is not valid YAML: {exc}")
                    continue
                if not isinstance(parsed, dict) or not parsed:
                    warnings.append(f"{entry['filename']} must parse to a non-empty object.")

    artifacts = [entry["filename"] for entry in route_entries + module_entries] + page_artifacts + [entry["filename"] for entry in experience_entries]
    artifacts = _dedupe_ordered(artifacts)
    if warnings:
        return _result(
            name="app_bundle_validation",
            status="failed",
            reason="App bundle validation found drift: " + "; ".join(warnings),
            artifacts=artifacts,
        )
    return _result(
        name="app_bundle_validation",
        status="passed",
        reason="Bundle-level declarative surfaces are internally consistent.",
        artifacts=artifacts,
    )


def _unknown_validation_item(name: str) -> RefinementValidationItemResult:
    return _result(
        name=name,
        status="warning",
        reason="No deterministic validator is registered for this name yet.",
    )


def _validation_targets(
    *,
    plan: RefinementExecutionPlan,
    selected: Sequence[str] | None,
) -> list[str]:
    targets = _plan_validation_targets(plan)
    selected_targets, unknown_selected = _normalize_selected_targets(selected)
    targets.extend(selected_targets)
    targets.extend(unknown_selected)
    return _dedupe_ordered(targets)


def run_refinement_validations(
    plan: RefinementExecutionPlan,
    staging_result: RefinementStagingResult,
    scoped_result: ScopedRefinementResult | None = None,
    *,
    selected: Sequence[str] | None = None,
) -> RefinementValidationResult:
    if plan.request_id != staging_result.request_id:
        raise ValueError("Plan and staging request_id values must match for validation.")
    if scoped_result is not None and scoped_result.request_id != plan.request_id:
        raise ValueError("Scoped execution request_id must match the refinement plan.")
    if plan.execution_mode != "staged":
        raise ValueError("Validation runner only supports staged refinement plans.")

    staging_area = Path(staging_result.staging_area)
    workspace_root = staging_area / WORKSPACE_DIRNAME
    files = _collect_workspace_files(workspace_root)
    targets = _validation_targets(plan=plan, selected=selected)

    items: list[RefinementValidationItemResult] = []
    for name in _VALIDATION_ORDER:
        if name not in targets:
            continue
        if name == "route_component_validation":
            items.append(_route_component_validation(files))
        elif name == "ui_theme_primitive_validation":
            items.append(_ui_theme_primitive_validation(files))
        elif name == "module_contract_validation":
            items.append(_module_contract_validation(files))
        elif name == "experience_spec_validation":
            items.append(_experience_spec_validation(files, plan=plan))
        elif name == "app_bundle_validation":
            items.append(_app_bundle_validation(files, plan=plan))
        elif name == "data_contract_validation":
            items.append(
                _data_contract_validation(
                    files,
                    required="data_contract_validation" in targets,
                )
            )
        elif name == "migration_plan_validation":
            items.append(
                _migration_plan_validation(
                    files,
                    required="migration_plan_validation" in targets,
                )
            )
        elif name == "managed_facade_boundary_validation":
            items.append(_managed_facade_boundary_validation(files))
        elif name == "integration_readiness_validation":
            items.append(_integration_readiness_validation(files))

    known_names = {item.name for item in items}
    for name in targets:
        if name in known_names:
            continue
        if name in _VALIDATION_ORDER:
            continue
        items.append(_unknown_validation_item(name))

    evidence = ValidationEvidence(
        completed=[item.name for item in items if item.status == "passed"],
        failed=[item.name for item in items if item.status == "failed"],
        warnings=[item.reason for item in items if item.status == "warning" and item.reason],
        artifacts=_dedupe_ordered(
            [artifact for item in items for artifact in item.artifacts]
        ),
        checked_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        source="refinement_validation_runner",
    )
    return RefinementValidationResult(evidence=evidence, items=items)


__all__ = [
    "RefinementValidationItemResult",
    "RefinementValidationResult",
    "ValidationStatus",
    "run_refinement_validations",
]

