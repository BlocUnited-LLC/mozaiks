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
import zipfile
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
    app_auth_route_entries,
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
# A memo of load_revision_baseline: the text files of recently read bound
# revision sources, by immutable source identity. The whole text map is kept
# because composition resolves the baseline barrel's imports against it; UI
# roots alone cannot be composed. Only load_revision_baseline reads it, and a
# missing entry is read again from the verified source, so it only saves a read.
_REVISION_UI_BASELINES: dict[tuple[str, ...], dict[str, str]] = {}
_REVISION_UI_BASELINE_LIMIT = 16


def compose_bundle_auth_routes(files_map: dict[str, str], *, validate_bindings: bool = True) -> None:
    """Materialize auth routes after app-schema and auth-scaffold outputs merge.

    ``validate_bindings=False`` adds the same routes without checking the
    manifest's auth route bindings, for a projection that leaves that check to
    the acceptance gate. The auth contract itself is always validated. A
    login or callback route binding error names ``ui/route_manifest.json``, as
    the acceptance gate's does, so an assembly or reassembly that raises it
    routes the repair to that file's owner.
    """
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
    if validate_bindings:
        try:
            manifest["pages"] = compose_app_auth_routes(contract, manifest["pages"])
        except AppAuthContractError as exc:
            if str(exc).startswith("Auth routes."):
                raise AppAuthContractError(f"ui/route_manifest.json: {exc}") from exc
            raise
    else:
        declared = {page.get("path") for page in manifest["pages"] if isinstance(page, dict)}
        manifest["pages"].extend(page for page in app_auth_route_entries(contract) if page["path"] not in declared)
    files_map["ui/route_manifest.json"] = json.dumps(manifest, indent=2, ensure_ascii=False)


def _auth_route_projection(file_sets: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The sign-in and callback routes assembly adds for each valid auth contract in these file sets."""
    routes: list[dict[str, Any]] = []
    for files in file_sets:
        raw = files.get("config/auth.yaml")
        if not isinstance(raw, str):
            continue
        try:
            contract = validate_app_auth_contract(yaml.safe_load(raw))
        except (AppAuthContractError, yaml.YAMLError):
            continue  # Composition reports what an unreadable contract leaves behind.
        routes.extend(route for route in app_auth_route_entries(contract) if route not in routes)
    return routes


def without_auth_route_projection(
    contributions: list[dict[str, Any]], files: dict[str, str], revision_baseline: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    """Shared UI contributions and revision baseline without the auth contract's own routes.

    Assembly composes the shared roots first and adds the sign-in and callback
    routes afterwards (``compose_bundle_auth_routes``). A carried, saved or
    repaired manifest that already holds them would otherwise declare them
    once per contribution, which composition refuses as competing owners of
    the same routes. Only the exact canonical projection of an auth
    contract these files declare is set aside; any other route still composes,
    or fails, as written. Pack contributions are never changed.
    """
    projection = _auth_route_projection([
        files, revision_baseline or {},
        *({item["filename"]: item["content"] for item in contribution["files"]} for contribution in contributions),
    ])
    if not projection:
        return contributions, revision_baseline

    def strip(source: str) -> str:
        try:
            manifest = json.loads(source)
        except (TypeError, ValueError):
            return source
        if not isinstance(manifest, dict) or not isinstance(manifest.get("pages"), list):
            return source
        pages = [route for route in manifest["pages"] if route not in projection]
        return source if len(pages) == len(manifest["pages"]) else json.dumps({**manifest, "pages": pages})

    stripped = [
        contribution if str(contribution["owner"]).startswith("pack:") else {**contribution, "files": [
            {**item, "content": strip(item["content"])} if item["filename"] == "ui/route_manifest.json" else item
            for item in contribution["files"]
        ]}
        for contribution in contributions
    ]
    if revision_baseline is not None and "ui/route_manifest.json" in revision_baseline:
        revision_baseline = {
            **revision_baseline, "ui/route_manifest.json": strip(revision_baseline["ui/route_manifest.json"]),
        }
    return stripped, revision_baseline


def _auth_required(files: dict[str, str]) -> bool:
    try:
        manifest = json.loads(files.get("app.json") or "{}")
    except ValueError:
        return False
    return isinstance(manifest, dict) and manifest.get("authRequired") is True


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
            companion_files=admitted_module_file_map(context_variables),
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
        bundle_contract = contract
        if "data/contract.json" in (owned_paths or []):
            # The owner writes data/contract.json as assembly compiles it, with
            # the verified pack contributions, and what is checked against that
            # file uses the same contract. Module synthesis keeps the app's
            # contract: selected pack modules ship their verified implementation
            # and are never synthesized, as at assembly.
            bundle_contract = _assembled_data_contract(context_variables, subscription_contract)
        incoming = materialize_collection_auth(incoming, data_contract=bundle_contract)
        incoming = materialize_data_contract(
            incoming, data_contract=bundle_contract, owned_paths=owned_paths or [],
            subscription_contract=subscription_contract, context_variables=context_variables,
        )
        policies = materialize_task_module_policies(
            incoming, task={"owned_paths": owned_paths or []}, data_contract=bundle_contract,
        )
        schemas = materialize_task_module_schemas(
            incoming, task={"owned_paths": owned_paths or []},
            app_build_plan=detach(context_variables.get("app_build_plan")), data_contract=contract,
        )
        schemas.update(materialize_task_module_account_handlers(
            incoming, task={"owned_paths": owned_paths or []},
            app_build_plan=detach(context_variables.get("app_build_plan")), data_contract=contract,
        ))
        admitted = admitted_module_file_map(context_variables)
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


def _assembled_data_contract(context_variables: Any, subscription_contract: Any) -> Any:
    """The approved data contract compiled as assembly compiles it, with the verified pack contributions.

    A save after assembly (a bounded repair of the data/contract.json owner)
    materializes the same contract assembly did, so the admitted bundle keeps
    the selected packs' collections. Without an approved contract that is the
    packs' contributions on an empty base, which, as at assembly, may not
    replace a carried contract that holds the app's own data: the save is
    refused with assembly's reason instead of writing a contract that drops it.
    The carried contract is the bundle's copy. In a revision that is the bound
    source's copy, which the run's revision hydration loads into
    generated_files before any agent reasons, unless an assembly has already
    replaced it with a composition it checked against that source; assembly
    checks the source's copy again (carried_contracts) whenever it runs.
    """
    from .resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
        verified_pack_data_contracts,
        verified_pack_template_inventory,
    )

    contract = detach(context_variables.get("data_contract"))
    try:
        pack_data_contracts = verified_pack_data_contracts(
            verified_pack_template_inventory(context_variables=context_variables),
        )
    except ManagedCapabilityTemplateError as exc:
        raise ValueError(f"The selected capability packs' data contributions cannot be verified: {exc}") from exc
    pack_only_base = contract is None and bool(pack_data_contracts)
    if pack_only_base:
        contract = {"version": "1", "surfaces": [], "shared_collections": []}
    if not isinstance(contract, dict):
        return contract
    compiled = compile_data_contract(
        contract, subscription_contract=subscription_contract, context_variables=context_variables,
        pack_data_contracts=pack_data_contracts,
    )
    if pack_only_base:
        from .assembly_phase import _carried_contract_conflict

        carried = (detach(context_variables.get("generated_files")) or {}).get("data/contract.json")
        if isinstance(carried, str):
            conflict = _carried_contract_conflict(carried, compiled)
            if conflict is not None:
                raise ValueError(conflict)
    return compiled


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


_OBSOLETE_TASK_RESULT_WRAPPERS = frozenset({"task_results", "results", "tasks"})


def _planned_task_ids(context_variables: Any) -> set[str]:
    """Task ids of the approved plan: its build_tasks, else the batch's projection of them (app_task_batch_items)."""
    plan = detach(context_variables.get("app_build_plan"))
    tasks = plan.get("build_tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list):
        tasks = detach(context_variables.get("app_task_batch_items"))
    if not isinstance(tasks, list):
        return set()
    return {str(task["task_id"]) for task in tasks if isinstance(task, dict) and task.get("task_id")}


def task_batch_outputs(context_variables: Any) -> list[tuple[str, dict[str, Any]]]:
    """Accepted task outputs in assembly order, as (task id, payload).

    ``app_task_batch_results`` is the task_batch runtime's result map: each
    completed task's output under its task id, plus ``_meta`` and, when tasks
    failed, ``_failed``. Every other key must be a task id of the approved
    plan (its ``build_tasks``, or the batch's ``app_task_batch_items``
    projection of them). Any other shape is refused with a diagnostic rather
    than read as a guess, including the obsolete wrappers that nested the map
    under ``task_results``, ``results`` or ``tasks``.
    """
    merged = detach(context_variables.get("app_task_batch_results"))
    if merged is None:
        return []
    shape = (
        "the task batch result map: task outputs keyed by the approved plan's task ids, plus _meta and _failed"
    )
    if not isinstance(merged, dict):
        raise ValueError(
            f"app_task_batch_results must be {shape}; got {type(merged).__name__}. Rerun the task batch to rebuild it."
        )
    task_ids = _planned_task_ids(context_variables)
    outputs: list[tuple[str, dict[str, Any]]] = []
    for key, value in merged.items():
        if key in {"_meta", "_failed"}:
            continue
        if key not in task_ids:
            obsolete = (
                f"{key!r} is the obsolete wrapper shape that nested the result map under one key"
                if key in _OBSOLETE_TASK_RESULT_WRAPPERS else f"{key!r} is not a task id of the approved plan"
            )
            raise ValueError(
                f"app_task_batch_results must be {shape}; {obsolete} (approved task ids: {sorted(task_ids)}). "
                "Rerun the task batch to rebuild the result map."
            )
        if not isinstance(value, dict):
            raise ValueError(
                f"app_task_batch_results[{key!r}] must be that task's output object; got {type(value).__name__}. "
                "Rerun the task batch to rebuild the result map."
            )
        outputs.append((str(key), value))
    return outputs


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
    """Record the text files of the bound revision source, as read from its verified record."""
    key = _revision_source_key(context_variables)
    if key is None:
        return
    _REVISION_UI_BASELINES.pop(key, None)
    _REVISION_UI_BASELINES[key] = {path: content for path, content in files.items() if isinstance(content, str)}
    while len(_REVISION_UI_BASELINES) > _REVISION_UI_BASELINE_LIMIT:
        _REVISION_UI_BASELINES.pop(next(iter(_REVISION_UI_BASELINES)))


def _cached_revision_baseline(context_variables: Any) -> dict[str, str] | None:
    key = _revision_source_key(context_variables)
    if key is None:
        return None
    baseline = _REVISION_UI_BASELINES.pop(key, None)
    if baseline is not None:
        # A read keeps the entry: the source a run still uses is evicted last.
        _REVISION_UI_BASELINES[key] = baseline
    return baseline


async def load_revision_baseline(context_variables: Any) -> dict[str, str] | None:
    """The bound revision source an admitted repair copy of a pack-composed root composes with.

    Assembly composes those roots against the bound revision source. The
    admitted projection must compose a repair copy saved later against the
    same source, so this reads it again from its verified, immutable record
    when this process does not hold it (a restarted host, a replayed turn,
    another worker, an evicted entry). Async validation and export pass the
    result to :func:`admitted_app_file_map` as ``revision_baseline``, so the
    projection does not depend on the process that assembled the bundle. None
    outside a revision, when no repair copy of a pack-composed root is
    admitted, or when the source cannot be read; acceptance reports an
    unreadable source itself.
    """
    from mozaiksai.core.artifacts.content_store import ContentNotFoundError

    from .hydrate_app_revision_context import read_bound_revision_files, revision_baseline_required

    if context_variables is None or not hasattr(context_variables, "get"):
        return None
    if not revision_baseline_required(context_variables):
        return None
    if not overlay_pack_composed_roots(context_variables):
        return None
    baseline = _cached_revision_baseline(context_variables)
    if baseline is not None:
        return baseline
    try:
        # Text-only read (include_binary=False), exactly as assembly reads it.
        saved = await read_bound_revision_files(context_variables)
    except (OSError, ValueError, zipfile.BadZipFile, ContentNotFoundError) as exc:
        logger.warning("REVISION_BASELINE_UNAVAILABLE: the bound revision source cannot be read: %s", exc)
        return None
    baseline = {path: content for path, content in saved.items() if isinstance(content, str)}
    remember_revision_baseline(context_variables, baseline)
    return baseline


def _pack_composed_roots(files: dict[str, str]) -> frozenset[str]:
    """Shared UI roots the pack provenance records as composed with pack declarations."""
    composed = _provenance(files).get("composed_outputs")
    if not isinstance(composed, dict):
        return frozenset()
    return frozenset(path for path in SHARED_UI_OUTPUT_PATHS if path in composed and path in files)


def overlay_pack_composed_roots(context_variables: Any) -> frozenset[str]:
    """Pack-composed UI roots the admitted overlay holds a copy of, which the projection composes."""
    files = _generated_file_map(context_variables)
    overlay = _overlay_file_map(context_variables)
    return frozenset(path for path in _pack_composed_roots(files) if path in overlay)


def _entries(files: dict[str, str]) -> list[dict[str, str]]:
    return [{"filename": path, "content": content} for path, content in sorted(files.items())]


def _generated_file_map(context_variables: Any) -> dict[str, str]:
    """The current bundle's files at safe relative paths."""
    raw = detach(context_variables.get("generated_files"))
    files: dict[str, str] = {}
    if isinstance(raw, dict):
        for path, content in raw.items():
            safe = safe_relpath(str(path))
            if safe and safe != "." and ":" not in safe and "\x00" not in safe:
                files[safe] = str(content)
    return files


def _overlay_file_map(context_variables: Any) -> dict[str, str]:
    """The context ``code_files`` overlay of admitted repairs."""
    return extract_code_file_map_from_payload({"code_files": detach(context_variables.get("code_files"))})


def _compose_repair_roots(
    context_variables: Any, roots: dict[str, str], overlay: dict[str, str], files: dict[str, str],
    deleted: list[str], replaced: set[str], revision_baseline: dict[str, str] | None,
) -> dict[str, str]:
    """Compose admitted repair copies onto pack-composed roots with assembly's own rules.

    The contributions are assembly's: the bundle's roots as the carried
    snapshot, every current task output, the admitted overlay and the verified
    pack templates, composed against the same bound revision baseline (in a
    revision) and the same admitted deletions. As in assembly, the auth
    contract's sign-in and callback routes are set aside while the roots
    compose and added to the composed manifest afterwards. A repair copy
    therefore supersedes only what it declares, a stale copy of the baseline
    cannot revert a task's approved change, and a pack declaration is never
    patched. When those rules cannot be applied here, or reject the copy, the
    assembled pack-composed roots are kept; only an app without resolvable
    packs keeps the copy as written for the acceptance gates to judge. A copy
    that composes but breaks an auth route binding is returned with the auth
    routes added unchecked, so the acceptance gate rejects it with that error,
    attributed to ui/route_manifest.json for the repair loop to route. In a
    revision the baseline is only ever the one the caller passes: there is no
    other source of it here.
    """
    from .hydrate_app_revision_context import revision_baseline_required
    from .resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
        resolve_managed_capability_templates,
    )

    kept = {path: roots[path] for path in sorted(replaced)}
    unbound = revision_baseline is None and revision_baseline_required(context_variables)
    pack_outputs = pack_owned_output_paths(context_variables)
    superseded = repair_superseded_roots(context_variables)
    build_timestamp = context_variables.get("build_timestamp")
    snapshot = {"owner": "app:generated_files", "baseline": True, "files": _entries(roots)}
    tasks: list[dict[str, Any]] = []
    for key, output in task_batch_outputs(context_variables):
        task_output, _ = discard_pack_owned_outputs(output, pack_outputs)
        entries = extract_code_file_entries_from_payload(task_output, build_timestamp=build_timestamp)
        if any(item["filename"] in SHARED_UI_OUTPUT_PATHS for item in entries):
            owner = f"task:{key}"
            tasks.append({
                "owner": owner, "baseline": False, "files": entries,
                **({"overlay_supersedes": superseded[owner]} if owner in superseded else {}),
            })
    admitted = {"owner": ADMITTED_OVERLAY_OWNER, "baseline": True, "admitted_overlay": True, "files": _entries(overlay)}
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

    def compose(contributions: list[dict[str, Any]], baseline: dict[str, str] | None) -> dict[str, str]:
        composable, baseline = without_auth_route_projection(contributions, files, baseline)
        shared = compose_ui_registry(
            composable, files, revision_baseline=baseline,
            deleted_paths=[path for path in deleted if path not in pack_outputs],
        )
        if "ui/route_manifest.json" in shared and _auth_required(files):
            # As materialize_auth_scaffold does after assembly composes the roots.
            with_auth = {**files, **shared}
            try:
                compose_bundle_auth_routes(with_auth)
            except AppAuthContractError as exc:
                # The copy composes, but the manifest breaks an auth route
                # binding. The acceptance gate checks the same bindings, so the
                # composition reaches it with the auth routes added unchecked
                # and fails there with this error. The gate attributes a
                # route's binding error to ui/route_manifest.json, so the
                # repair loop routes it to that file's owner; kept roots would
                # repeat the pre-repair diagnostic. Any other auth contract
                # error raises again and keeps them.
                compose_bundle_auth_routes(with_auth, validate_bindings=False)
                logger.warning(
                    "ADMITTED_AUTH_ROUTE_BINDING_INVALID: paths=%s; the admitted repair copy breaks an auth "
                    "route binding, which the acceptance gate reports: %s", sorted(replaced), exc,
                )
            shared["ui/route_manifest.json"] = with_auth["ui/route_manifest.json"]
        return {path: shared[path] for path in sorted(replaced) if path in shared}

    if unbound:
        # Assembly's revision rules need the bound baseline, so the assembled
        # roots are kept. Once validation has written its composition back they
        # hold every admitted copy already: composing the overlay onto them
        # alone changes nothing. A repair is dropped only where it does.
        try:
            held = compose([snapshot, admitted, *packs], None) == kept
        except ValueError:
            held = False
        if held:
            logger.info(
                "ADMITTED_UI_ROOT_KEPT: paths=%s; no revision baseline was passed to this projection, and the "
                "kept roots already hold the admitted repair copies.", sorted(replaced),
            )
        else:
            logger.warning(
                "ADMITTED_UI_ROOT_NOT_COMPOSED: paths=%s; no revision baseline was passed to this projection, "
                "so the assembled roots are kept without the admitted repair copies. Pass the result of "
                "load_revision_baseline, which reads it from the verified revision source and is None only "
                "when that source cannot be read.", sorted(replaced),
            )
        return kept
    try:
        return compose([snapshot, *tasks, admitted, *packs], revision_baseline)
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


def _admitted_projection(
    context_variables: Any,
) -> tuple[dict[str, str], dict[str, str], frozenset[str], dict[str, str], list[str]]:
    """The bundle with admitted repairs applied, before a pack-composed root is recomposed.

    Returns the projected files, the bundle's shared UI roots, the roots the
    pack provenance records as composed, the admitted overlay and the admitted
    deletions.
    """
    files = _generated_file_map(context_variables)
    roots = {path: files[path] for path in SHARED_UI_OUTPUT_PATHS if path in files}
    composed = _pack_composed_roots(files)
    overlay = _overlay_file_map(context_variables)
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
    return files, roots, composed, overlay, deleted


def admitted_app_file_map(
    context_variables: Any, *, revision_baseline: dict[str, str] | None = None,
) -> dict[str, str]:
    """Project the current bundle and admitted repairs, never raw worker history.

    App-owned (``owner: workspace``) pack outputs keep the bundle's verified
    copy: an overlay copy that changes one, or a tombstone for one, is
    excluded and reported. A repair copy of a pack-composed UI root is
    composed onto it with assembly's contributions, baseline and rules. In a
    revision that baseline is ``revision_baseline``, which async callers load
    with :func:`load_revision_baseline`; without it the projection keeps the
    assembled roots and reports them. A synchronous caller that reads no UI
    root uses :func:`admitted_module_file_map` instead.
    """
    if context_variables is None or not hasattr(context_variables, "get"):
        return {}
    files, roots, composed, overlay, deleted = _admitted_projection(context_variables)
    # Assembly composed these roots with verified pack declarations; a repair
    # copy is composed onto them rather than replacing them as a whole file.
    replaced = {path for path in composed & overlay.keys() if path not in deleted}
    if replaced:
        files.update(_compose_repair_roots(context_variables, roots, overlay, files, deleted, replaced, revision_baseline))
    return files


def admitted_module_file_map(context_variables: Any) -> dict[str, str]:
    """The admitted projection without the shared UI roots, for synchronous callers.

    Module action closure, module implementations and auth scopes read module,
    config and data files only. A pack-composed UI root composes against the
    verified revision baseline, which only an async read can supply
    (:func:`load_revision_baseline` with :func:`admitted_app_file_map`), so the
    shared UI roots are left out here rather than projected uncomposed.
    """
    if context_variables is None or not hasattr(context_variables, "get"):
        return {}
    files, _roots, _composed, _overlay, _deleted = _admitted_projection(context_variables)
    return {path: content for path, content in files.items() if path not in SHARED_UI_OUTPUT_PATHS}


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
    "admitted_module_file_map",
    "compose_bundle_auth_routes",
    "collect_generated_app_file_entries",
    "collect_generated_app_file_map",
    "extract_code_file_entries_from_payload",
    "extract_code_file_map_from_payload",
    "extract_deleted_file_paths_from_payload",
    "load_revision_baseline",
    "safe_relpath",
    "save_generated_code",
]

