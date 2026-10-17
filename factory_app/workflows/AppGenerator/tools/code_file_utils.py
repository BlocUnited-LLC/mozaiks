"""
AppGenerator code file utilities.

Wraps mozaiksai.core.workflow.generator_support.code_files with
AppGenerator-specific payload expansion: app_backend_admin_config and
refinement_harness.

Import from here (not the runtime module) in any AppGenerator tool that
needs full payload materialization including admin surface codegen.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from factory_app.workflows.AppGenerator.tools.app_backend_admin_codegen import (
    build_app_backend_admin_code_files,
)
from factory_app.workflows.AppGenerator.tools.module_entitlement_gates import (
    apply_entitlement_gates,
)
from factory_app.workflows.AppGenerator.tools.refinement_harness_codegen import (
    build_refinement_harness_code_files,
)
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    WORKSPACE_PACK_CHANGE_REMEDY,
    RepairOwnershipError,
    mark_repair_rejected,
    mark_repair_responded,
    validate_repair_candidate,
)
from mozaiksai.core.runtime.app.auth_contract import (
    AppAuthContractError,
    compose_app_auth_routes,
    validate_app_auth_contract,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    compile_data_contract,
    discard_pack_owned_outputs,
    extract_deleted_file_paths_from_payload,
    materialize_collection_auth,
    materialize_data_contract,
    safe_relpath,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload as _base_extract,
)
from mozaiksai.core.workflow.generator_support.module_account_data import (
    materialize_task_module_account_handlers,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    SHARED_UI_OUTPUT_PATHS,
    pack_owned_output_paths,
)
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    resolve_subscription_contract,
)
from mozaiksai.core.workflow.generator_support.module_policy import materialize_task_module_policies
from mozaiksai.core.workflow.generator_support.module_read_actions import (
    materialize_module_read_implementations,
)
from mozaiksai.core.workflow.generator_support.module_write_actions import (
    close_module_actions,
    materialize_module_write_implementations,
    materialize_task_module_schemas,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import compile_authored_page_files
from mozaiksai.core.workflow.generator_support.ui_registry_composition import compose_ui_registry

logger = logging.getLogger(__name__)

_PACK_PROVENANCE_PATH = ".mozaiks/pack_provenance.json"
# The context `code_files` overlay of admitted repairs, merged last.
ADMITTED_OVERLAY_OWNER = "app:accumulated_files"
# Text files of each bound revision source assembly composed against, by source
# identity, so the admitted projection composes with the same baseline. The
# whole text map is kept because composition resolves the baseline barrel's
# imports against it; UI roots alone cannot be composed.
_REVISION_UI_BASELINES: dict[tuple[str, ...], dict[str, str]] = {}
_REVISION_UI_BASELINE_LIMIT = 16


def compose_bundle_auth_routes(files_map: dict[str, str]) -> None:
    """Materialize auth routes after app-schema and auth-scaffold outputs merge."""
    raw_contract = files_map.get("config/auth.yaml")
    if raw_contract is None:
        return  # Missing required declarations remain validation errors.
    try:
        contract_document = yaml.safe_load(raw_contract)
        app_manifest = json.loads(files_map.get("app.json", "{}"))
        manifest = json.loads(files_map.get("ui/route_manifest.json", '{"pages": []}'))
    except (yaml.YAMLError, json.JSONDecodeError, TypeError):
        raise AppAuthContractError("Auth route composition requires valid auth YAML and app/route JSON") from None
    contract = validate_app_auth_contract(contract_document)
    if not isinstance(app_manifest, dict) or app_manifest.get("authRequired") is not True:
        raise AppAuthContractError("config/auth.yaml requires app.json.authRequired=true")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list):
        raise AppAuthContractError("ui/route_manifest.json must declare pages")
    manifest["pages"] = compose_app_auth_routes(contract, manifest["pages"])
    files_map["ui/route_manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False)


def extract_code_file_map_from_payload(
    payload: Any,
    *,
    build_timestamp: str | None = None,
) -> dict[str, str]:
    """Materialize all code files from an AppGenerator structured output payload.

    Calls the runtime's generic extraction, then layers in AppGenerator-specific
    typed surfaces. Typed configs win over conflicting raw code_files entries
    for their canonical paths.
    """
    file_map = _base_extract(payload, build_timestamp=build_timestamp)

    if not isinstance(payload, dict):
        return file_map

    raw_admin = payload.get("app_backend_admin_config")
    if raw_admin is not None:
        for item in build_app_backend_admin_code_files(raw_admin):
            safe = safe_relpath(str(item.get("filename") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    raw_refinement_harness = payload.get("refinement_harness")
    if raw_refinement_harness is not None:
        for item in build_refinement_harness_code_files(raw_refinement_harness):
            safe = safe_relpath(str(item.get("filename") or ""))
            content = item.get("content")
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    return file_map


def extract_code_file_entries_from_payload(
    payload: Any,
    *,
    build_timestamp: str | None = None,
) -> list[dict[str, str]]:
    file_map = extract_code_file_map_from_payload(
        payload,
        build_timestamp=build_timestamp,
    )
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


def save_generated_code(context_variables: Any) -> dict[str, Any]:
    """Persist the current validated output before AG2 advances to a quality gate."""
    payload = detach(context_variables.get("structured_output"))
    try:
        if not isinstance(payload, dict):
            raise ValueError("Generated code persistence requires validated structured_output.")
        payload, discarded = discard_pack_owned_outputs(payload, pack_owned_output_paths(context_variables))
        if discarded:
            logger.info(
                "PACK_OWNED_OUTPUT_DISCARDED: repair output paths=%s; selected pack templates provide them",
                discarded,
            )
        subscription_contract = resolve_subscription_contract(context_variables)
        payload = close_module_actions(
            payload,
            app_build_plan=detach(context_variables.get("app_build_plan")),
            data_contract=detach(context_variables.get("data_contract")),
            design_surface_map=detach(context_variables.get("design_surface_map")),
            subscription_contract=subscription_contract,
            companion_files=admitted_app_file_map(context_variables),
        )
        incoming = extract_code_file_map_from_payload(payload)
        incoming = compile_authored_page_files(incoming, payload=payload, context=context_variables)
        deleted = extract_deleted_file_paths_from_payload(payload)
        task = detach(context_variables.get("current_build_task")) or {}
        repair = detach(context_variables.get("bundle_repair_result")) or {}
        active = repair.get("active") or {}
        owned_paths = active.get("allowed_paths") if active else task.get("owned_paths")
        contract = detach(context_variables.get("data_contract"))
        if isinstance(contract, dict):
            contract = compile_data_contract(
                contract, subscription_contract=subscription_contract, context_variables=context_variables,
            )
        incoming = materialize_collection_auth(incoming, data_contract=contract)
        incoming = materialize_data_contract(
            incoming, data_contract=contract, owned_paths=owned_paths or [],
            subscription_contract=subscription_contract, context_variables=context_variables,
        )
        policies = materialize_task_module_policies(
            incoming, task={"owned_paths": owned_paths or []}, data_contract=contract,
        )
        schemas = materialize_task_module_schemas(
            incoming, task={"owned_paths": owned_paths or []},
            app_build_plan=detach(context_variables.get("app_build_plan")), data_contract=contract,
        )
        schemas.update(materialize_task_module_account_handlers(
            incoming, task={"owned_paths": owned_paths or []},
            app_build_plan=detach(context_variables.get("app_build_plan")), data_contract=contract,
        ))
        admitted = admitted_app_file_map(context_variables)
        incoming.update(materialize_module_read_implementations(
            {**admitted, **incoming}, app_build_plan=detach(context_variables.get("app_build_plan")),
            data_contract=contract, owned_paths=owned_paths or [],
            subscription_contract=subscription_contract,
        ))
        incoming.update(materialize_module_write_implementations(
            {**admitted, **incoming}, app_build_plan=detach(context_variables.get("app_build_plan")),
            data_contract=contract, owned_paths=owned_paths or [], subscription_contract=subscription_contract,
        ))
        incoming.update({path: content for path, content in schemas.items() if admitted.get(path) != content})
        incoming.update({path: content for path, content in policies.items() if admitted.get(path) != content})
        incoming = {
            entry["filename"]: entry["content"]
            for entry in apply_entitlement_gates(
                [{"filename": path, "content": content} for path, content in incoming.items()],
                context_variables=context_variables, require_all_modules=False,
            )
        }
    except (TypeError, ValueError) as exc:
        if mark_repair_rejected(context_variables, str(exc)):
            return {"status": "rejected", "error": str(exc), "saved_files": [], "deleted_files": []}
        raise
    existing = detach(context_variables.get("code_files", [])) or []
    files = extract_code_file_map_from_payload({"code_files": existing})
    baseline = detach(context_variables.get("generated_files", {})) or {}
    protected = workspace_pack_output_paths(context_variables)
    current = {**baseline, **files}
    for path in protected:
        # App-owned pack code compares with the bundle's verified copy, never an overlay copy.
        if path in baseline:
            current[path] = baseline[path]
        else:
            current.pop(path, None)
    try:
        incoming = validate_repair_candidate(
            context_variables, incoming, deleted, existing=current, protected=protected,
        )
    except RepairOwnershipError as exc:
        diagnostic = getattr(exc, "diagnostic", None)
        return {
            "status": "rejected", "error": str(exc), "saved_files": [], "deleted_files": [],
            **({"diagnostic": diagnostic} if isinstance(diagnostic, dict) else {}),
        }
    files.update(incoming)
    removed = set(detach(context_variables.get("deleted_files", [])) or [])
    removed.update(deleted)
    removed.difference_update(incoming)
    for path in removed:
        files.pop(path, None)
    values = {
        "code_files": [{"filename": path, "content": content} for path, content in sorted(files.items())],
        "deleted_files": sorted(removed),
    }
    for key, value in values.items():
        if isinstance(context_variables, dict):
            context_variables[key] = value
        else:
            context_variables.set(key, value)
    mark_repair_responded(context_variables, saved_paths=[*incoming, *deleted])
    return {"saved_files": sorted(incoming), "deleted_files": deleted}


def selected_capability_packs(context_variables: Any) -> list[dict[str, Any]] | None:
    """The run's selected pack descriptors, else the approved plan's."""
    packs = detach(context_variables.get("capability_packs"))
    if not packs:
        plan = detach(context_variables.get("app_build_plan"))
        packs = plan.get("capability_packs") if isinstance(plan, dict) else None
    return packs if isinstance(packs, list) else None


def _provenance(files: dict[str, str]) -> dict[str, Any]:
    try:
        provenance = json.loads(files.get(_PACK_PROVENANCE_PATH) or "{}")
    except json.JSONDecodeError:
        return {}
    return provenance if isinstance(provenance, dict) else {}


def _provenance_workspace_paths(files: dict[str, str]) -> frozenset[str]:
    """App-owned (``owner: workspace``) pack outputs the assembled bundle's provenance records."""
    return frozenset(
        str(entry["path"])
        for pack in _provenance(files).get("packs") or [] if isinstance(pack, dict)
        for entry in pack.get("materialized_owned_files") or []
        if isinstance(entry, dict) and entry.get("owner") == "workspace" and entry.get("path")
    )


def workspace_pack_output_paths(context_variables: Any) -> frozenset[str]:
    """App-owned (``owner: workspace``) outputs of the selected packs, in every build phase.

    Declared by the selected pack contracts, plus any the current bundle's pack
    provenance records. AppGenerator never changes these: only the verified
    revision source, the admitted genesis copy or the starter template supplies one.
    """
    from .resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
        resolve_declared_pack_output_paths,
    )

    generated = detach(context_variables.get("generated_files"))
    recorded = _provenance_workspace_paths(generated) if isinstance(generated, dict) else frozenset()
    packs = selected_capability_packs(context_variables)
    if not packs:
        return recorded
    try:
        declared = resolve_declared_pack_output_paths(packs, context_variables=context_variables, owner="workspace")
    except (ManagedCapabilityTemplateError, OSError, yaml.YAMLError) as exc:
        logger.warning("WORKSPACE_PACK_OUTPUTS_UNRESOLVED: %s; protecting the recorded provenance paths", exc)
        return recorded
    return recorded | declared


def task_batch_outputs(context_variables: Any) -> list[tuple[str, dict[str, Any]]]:
    """Accepted task outputs in assembly order, as (task key, payload)."""
    merged = detach(context_variables.get("app_task_batch_results"))
    if not isinstance(merged, dict):
        return []
    candidate_values: Any = merged.get("task_results") or merged.get("results") or merged.get("tasks") or merged
    if isinstance(candidate_values, dict):
        items: list[tuple[Any, Any]] = list(candidate_values.items())
    elif isinstance(candidate_values, list):
        items = list(enumerate(candidate_values))
    else:
        return []
    return [
        (str(key), value) for key, value in items
        if key not in {"_failed", "_meta", "failed_tasks"} and isinstance(value, dict)
    ]


def repair_superseded_roots(context_variables: Any) -> dict[str, list[str]]:
    """Shared UI roots an admitted repair of each task saved, by ``task:<id>`` owner.

    A bounded repair runs after its task's accepted output and saves into the
    admitted overlay, so the overlay copy of a root it saved is newer than that
    task's output: there the overlay supersedes the task even where it restates
    the verified baseline. A copy without this evidence (an earlier full-root
    save) never reverts a task's approved change.
    """
    result = detach(context_variables.get("bundle_repair_result")) or {}
    if not isinstance(result, dict):
        return {}
    roots: dict[str, set[str]] = {}
    for record in [*(result.get("history") or []), result.get("active")]:
        if not isinstance(record, dict) or record.get("status") != "responded" or not record.get("task_id"):
            continue
        saved = SHARED_UI_OUTPUT_PATHS & {str(path) for path in record.get("saved_paths") or []}
        if saved:
            roots.setdefault(f"task:{record['task_id']}", set()).update(saved)
    return {owner: sorted(paths) for owner, paths in roots.items()}


def _revision_source_key(context_variables: Any) -> tuple[str, ...] | None:
    binding = detach(context_variables.get("run_build_binding"))
    binding = binding if isinstance(binding, dict) else {}
    source = context_variables.get("revision_source_artifact_version_id") or context_variables.get("artifact_version_id")
    key = (
        str(context_variables.get("user_id") or ""), str(binding.get("target_app_id") or ""),
        str(binding.get("build_registry_id") or ""), str(source or ""),
    )
    return key if all(key) else None


def remember_revision_baseline(context_variables: Any, files: dict[str, Any]) -> None:
    """Record the text files of the bound revision source assembly just read and composed with."""
    key = _revision_source_key(context_variables)
    if key is None:
        return
    _REVISION_UI_BASELINES.pop(key, None)
    _REVISION_UI_BASELINES[key] = {path: content for path, content in files.items() if isinstance(content, str)}
    while len(_REVISION_UI_BASELINES) > _REVISION_UI_BASELINE_LIMIT:
        _REVISION_UI_BASELINES.pop(next(iter(_REVISION_UI_BASELINES)))


def _pack_composed_roots(files: dict[str, str]) -> frozenset[str]:
    """Shared UI roots the pack provenance records as composed with pack declarations."""
    composed = _provenance(files).get("composed_outputs")
    if not isinstance(composed, dict):
        return frozenset()
    return frozenset(path for path in SHARED_UI_OUTPUT_PATHS if path in composed and path in files)


def _entries(files: dict[str, str]) -> list[dict[str, str]]:
    return [{"filename": path, "content": content} for path, content in sorted(files.items())]


def _compose_repair_roots(
    context_variables: Any, roots: dict[str, str], overlay: dict[str, str], files: dict[str, str],
    deleted: list[str], replaced: set[str], revision_baseline: dict[str, str] | None,
) -> dict[str, str]:
    """Compose admitted repair copies onto pack-composed roots with assembly's own rules.

    The contributions are assembly's: the assembled roots as the carried
    snapshot, every current task output, the admitted overlay and the verified
    pack templates, composed against the same bound revision baseline (in a
    revision) and the same admitted deletions. A repair copy therefore
    supersedes only what it declares, a stale copy of the baseline cannot
    revert a task's approved change, and a pack declaration is never patched.
    When those rules cannot be applied here, or reject the copy, the assembled
    pack-composed roots are kept; only an app without resolvable packs keeps
    the copy as written for the acceptance gates to judge.
    """
    from .hydrate_app_revision_context import revision_baseline_required
    from .resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
        resolve_managed_capability_templates,
    )

    kept = {path: roots[path] for path in sorted(replaced)}
    if revision_baseline is None and revision_baseline_required(context_variables):
        source_key = _revision_source_key(context_variables)
        revision_baseline = _REVISION_UI_BASELINES.get(source_key) if source_key is not None else None
        if revision_baseline is None:
            logger.warning(
                "ADMITTED_UI_ROOT_NOT_COMPOSED: paths=%s; the revision baseline assembly composed with is not "
                "available to this projection, so the assembled roots are kept until the next assembly",
                sorted(replaced),
            )
            return kept
    pack_outputs = pack_owned_output_paths(context_variables)
    superseded = repair_superseded_roots(context_variables)
    build_timestamp = context_variables.get("build_timestamp")
    contributions: list[dict[str, Any]] = [
        {"owner": "app:generated_files", "baseline": True, "files": _entries(roots)},
    ]
    for key, output in task_batch_outputs(context_variables):
        task_output, _ = discard_pack_owned_outputs(output, pack_outputs)
        entries = extract_code_file_entries_from_payload(task_output, build_timestamp=build_timestamp)
        if any(item["filename"] in SHARED_UI_OUTPUT_PATHS for item in entries):
            owner = f"task:{key}"
            contributions.append({
                "owner": owner, "baseline": False, "files": entries,
                **({"overlay_supersedes": superseded[owner]} if owner in superseded else {}),
            })
    contributions.append({
        "owner": ADMITTED_OVERLAY_OWNER, "baseline": True, "admitted_overlay": True, "files": _entries(overlay),
    })
    packs: list[dict[str, Any]] = []
    capability_packs = selected_capability_packs(context_variables)
    if capability_packs:
        try:
            resolve_managed_capability_templates(
                capability_packs, context_variables=context_variables, ui_contributions=packs,
            )
        except (ManagedCapabilityTemplateError, OSError, yaml.YAMLError) as exc:
            logger.warning(
                "ADMITTED_UI_ROOT_NOT_COMPOSED: paths=%s; the selected packs cannot be resolved, so the "
                "assembled pack-composed roots are kept: %s", sorted(replaced), exc,
            )
            return kept
    try:
        shared = compose_ui_registry(
            [*contributions, *packs], files, revision_baseline=revision_baseline,
            deleted_paths=[path for path in deleted if path not in pack_outputs],
        )
    except ValueError as exc:
        if packs:
            logger.warning(
                "ADMITTED_UI_ROOT_NOT_COMPOSED: paths=%s; assembly rejects this admitted copy, so the assembled "
                "pack-composed roots are kept: %s", sorted(replaced), exc,
            )
            return kept
        logger.warning(
            "ADMITTED_UI_ROOT_NOT_COMPOSED: paths=%s; the admitted repair copy replaces the pack-composed root: %s",
            sorted(replaced), exc,
        )
        return {}
    return {path: shared[path] for path in sorted(replaced) if path in shared}


def admitted_app_file_map(
    context_variables: Any, *, revision_baseline: dict[str, str] | None = None,
) -> dict[str, str]:
    """Project the current bundle and admitted repairs, never raw worker history.

    App-owned (``owner: workspace``) pack outputs keep the bundle's verified
    copy: an overlay copy that changes one, or a tombstone for one, is
    excluded and reported. A repair copy of a pack-composed UI root is
    composed onto it with assembly's contributions, baseline and rules.
    """
    if context_variables is None or not hasattr(context_variables, "get"):
        return {}
    raw = detach(context_variables.get("generated_files"))
    files: dict[str, str] = {}
    if isinstance(raw, dict):
        for path, content in raw.items():
            safe = safe_relpath(str(path))
            if safe and safe != "." and ":" not in safe and "\x00" not in safe:
                files[safe] = str(content)
    roots = {path: files[path] for path in SHARED_UI_OUTPUT_PATHS if path in files}
    composed = _pack_composed_roots(files)
    overlay = extract_code_file_map_from_payload({
        "code_files": detach(context_variables.get("code_files")),
    })
    deleted = extract_deleted_file_paths_from_payload({
        "deleted_files": detach(context_variables.get("deleted_files")),
    })
    workspace = _provenance_workspace_paths(files)
    excluded = sorted(
        {path for path in workspace & overlay.keys() if overlay[path] != files.get(path)} | (workspace & set(deleted))
    )
    if excluded:
        logger.warning(
            "WORKSPACE_PACK_OUTPUT_EXCLUDED: paths=%s; the admitted bundle keeps the verified copy of app-owned "
            "capability pack code. %s", excluded, WORKSPACE_PACK_CHANGE_REMEDY,
        )
        overlay = {path: content for path, content in overlay.items() if path not in excluded}
        deleted = [path for path in deleted if path not in excluded]
    files.update(overlay)
    for path in deleted:
        files.pop(path, None)
    # Assembly composed these roots with verified pack declarations; a repair
    # copy is composed onto them rather than replacing them as a whole file.
    replaced = {path for path in composed & overlay.keys() if overlay[path] != roots[path] and path not in deleted}
    if replaced:
        files.update(_compose_repair_roots(context_variables, roots, overlay, files, deleted, replaced, revision_baseline))
    return files


def collect_generated_app_file_map(
    generated_app_dir: Any,
    *,
    allowed_roots: tuple[str, ...] = ("app.json", "provenance.yaml", "ui", "brand", "config"),
) -> dict[str, str]:
    """Read persisted schema artifacts from generated/apps/{app_id}/{build_id}/app.

    This is the deterministic handoff from AppSchemaAgent/save_app_schema into
    assembly, validation, and download. It intentionally collects only runtime
    app artifact roots, not logs, cache files, or workflow internals.
    """

    if not isinstance(generated_app_dir, (str, Path)):
        return {}
    root = Path(generated_app_dir).resolve()
    if not root.is_dir():
        return {}

    allowed = set(allowed_roots)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        safe = safe_relpath(rel)
        if not safe:
            continue
        first_part = safe.split("/", 1)[0]
        if safe not in allowed and first_part not in allowed:
            continue
        try:
            files[safe] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return files


def collect_generated_app_file_entries(generated_app_dir: Any) -> list[dict[str, str]]:
    file_map = collect_generated_app_file_map(generated_app_dir)
    return [{"filename": name, "content": content} for name, content in sorted(file_map.items())]


__all__ = [
    "admitted_app_file_map",
    "compose_bundle_auth_routes",
    "collect_generated_app_file_entries",
    "collect_generated_app_file_map",
    "extract_code_file_entries_from_payload",
    "extract_code_file_map_from_payload",
    "extract_deleted_file_paths_from_payload",
    "safe_relpath",
    "save_generated_code",
]

