import ast
import json
import logging
from pathlib import PurePosixPath
from typing import Annotated, Any

import yaml
from pydantic import Field

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import (
    discard_pack_owned_outputs,
    safe_relpath,
)
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    SHARED_UI_OUTPUT_PATHS,
    PackOwnedOutput,
    pack_owned_outputs,
)
from mozaiksai.core.workflow.generator_support.module_entitlement_gates import (
    resolve_subscription_contract,
)
from mozaiksai.core.workflow.generator_support.page_action_bindings import generated_workflow_names
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    _page_stem_from_path,
    _page_stems,
    module_action_index,
    normalize_planned_page_content,
    pack_template_page_errors,
    validate_planned_custom_routes,
    validate_planned_page,
)
from mozaiksai.core.workflow.generator_support.ui_registry_composition import (
    compose_ui_registry,
)

from .assembly_phase import assemble_features
from .code_file_utils import (
    ADMITTED_OVERLAY_OWNER,
    collect_generated_app_file_entries,
    extract_code_file_entries_from_payload,
    extract_deleted_file_paths_from_payload,
    remember_revision_baseline,
    repair_superseded_roots,
    task_batch_outputs,
    without_auth_route_projection,
)
from .hydrate_app_revision_context import (
    hydrate_app_revision_context,
    read_bound_revision_files,
    revision_baseline_required,
    revision_source_artifact_id,
)
from .materialize_app_config_contracts import materialize_app_config_contracts
from .module_entitlement_gates import apply_entitlement_gates
from .resolve_managed_capability_templates import (
    resolve_declared_pack_output_paths,
    resolve_managed_capability_templates,
)
from .save_app_schema import resolve_app_theme_config
from .task_integrity import WORKSPACE_PACK_CHANGE_DIAGNOSTIC, WORKSPACE_PACK_CHANGE_REMEDY

logger = logging.getLogger(__name__)

_ADMITTED_OVERLAY_OWNER = ADMITTED_OVERLAY_OWNER


class WorkspacePackOutputChangeUnsupported(ValueError):
    """A current plan task changed app-owned pack code that AppGenerator cannot admit."""

    def __init__(self, *, path: str, task_id: str, retained: str) -> None:
        super().__init__(
            f"{path}: plan task {task_id!r} changes app-owned capability pack code, which "
            f"AppGenerator cannot admit. {WORKSPACE_PACK_CHANGE_REMEDY}"
        )
        self.diagnostic = {
            "code": WORKSPACE_PACK_CHANGE_DIAGNOSTIC["code"],
            "path": path, "task_id": task_id, "retained": retained,
            "missing_capability": WORKSPACE_PACK_CHANGE_DIAGNOSTIC["missing_capability"],
            "block_reason": WORKSPACE_PACK_CHANGE_DIAGNOSTIC["block_reason"],
        }


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "passed", "ready"}
    return bool(value)


def _failed_batch_task_ids(task_results: Any) -> set[str]:
    """Task ids the batch recorded as failed, from either record it writes.

    `_build_batch_outputs` writes both `_failed` (id -> detail) and
    `_meta.failed_tasks` (id list); read both so a shape change in one does not
    silently turn a partial build back into a hard stop.
    """
    if not isinstance(task_results, dict):
        return set()
    failed: set[str] = set()
    raw_failed = task_results.get("_failed")
    if isinstance(raw_failed, dict):
        failed.update(str(task_id) for task_id in raw_failed)
    meta = task_results.get("_meta")
    if isinstance(meta, dict):
        for task_id in meta.get("failed_tasks") or []:
            failed.add(str(task_id))
    return failed


def _apply_planned_page_contracts(
    code_files: list[dict[str, Any]],
    app_build_plan: Any,
    failed_task_ids: set[str] | None = None,
    context_variables: Any = None,
    pack_outputs: dict[str, PackOwnedOutput] | None = None,
    verified_pack_routes: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    if not isinstance(app_build_plan, dict):
        return [{"filename": str(f["filename"]), "content": str(f["content"])} for f in code_files]
    pages = [page for page in app_build_plan.get("pages") or [] if isinstance(page, dict)]
    tasks = [task for task in app_build_plan.get("build_tasks") or [] if isinstance(task, dict)]
    if not pages or not tasks:
        return [{"filename": str(f["filename"]), "content": str(f["content"])} for f in code_files]

    planned_by_stem: dict[str, dict[str, Any]] = {}
    for page in pages:
        for stem in _page_stems(page):
            planned_by_stem.setdefault(stem, page)

    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename") and f.get("content") is not None}
    modules = module_action_index(file_map)
    workflows = generated_workflow_names(file_map, context_variables)
    # Typed page output is re-materialized here from the worker's structured
    # answer, so the same contract-determined constructions the worker's compile
    # wrote must be written again from the same approved inputs.
    data_contract = detach(context_variables.get("data_contract")) if context_variables is not None else None
    surface_map = detach(context_variables.get("design_surface_map")) if context_variables is not None else None
    failed_tasks = failed_task_ids or set()
    pack_outputs = pack_outputs or {}
    for task in tasks:
        if str(task.get("task_type") or "").strip() != "page_bundle":
            continue
        task_id = str(task.get("task_id") or "").strip()
        if task_id not in failed_tasks:
            validate_planned_custom_routes(
                file_map, pages=pages,
                owned_paths={
                    path for raw_path in task.get("owned_paths") or []
                    if (path := safe_relpath(str(raw_path or ""))) and path not in pack_outputs
                },
                baseline_files=(
                    detach(context_variables.get("generated_files"))
                    if context_variables is not None and context_variables.get("build_mode") == "revision" else None
                ),
                verified_pack_routes=verified_pack_routes,
            )
        for raw_path in task.get("owned_paths") or []:
            # file_map keys are safe_relpath-canonical because every producer
            # runs them through it. Canonicalize this side with the same rule so
            # a plan path written as "./ui/pages/x.yaml" still matches the file
            # the worker emitted as "ui/pages/x.yaml".
            path = safe_relpath(str(raw_path or ""))
            if not path or path in pack_outputs:
                continue  # a pack template page is checked below, never re-derived
            stem = _page_stem_from_path(path)  # type: ignore[assignment]
            if not stem or stem not in planned_by_stem:
                continue
            if path not in file_map:
                # A task that failed never got to write its files. The batch runs
                # failure_policy: continue_with_available and the router sends a
                # partial batch here on purpose, so acceptance can judge what was
                # produced and the repair loop can act on it. Raising here instead
                # ends the run on the one path that was built to survive.
                if task_id and task_id in failed_tasks:
                    logger.info(
                        "[AppGenerator] planned page %s not assembled: owning task %r failed",
                        path,
                        task_id,
                    )
                    continue
                owner = f" owned by task {task_id!r}" if task_id else ""
                raise ValueError(
                    f"{path}: missing planned page during assembly{owner}; "
                    "the task reported no failure, so the page was expected to exist"
                )
            try:
                file_map[path] = normalize_planned_page_content(
                    file_map[path], path=path, modules=modules, workflow_names=workflows,
                    data_contract=data_contract, design_surface_map=surface_map,
                )
                validate_planned_page(file_map[path], planned_by_stem[stem], path)
            except ValueError as exc:
                raise ValueError(f"{path}: {exc}") from exc
    template_errors: list[str] = []
    for path, owned in sorted(pack_outputs.items()):
        template_stem = _page_stem_from_path(path)
        if not template_stem or path not in file_map:
            continue
        errors = pack_template_page_errors(
            file_map[path], path=path, modules=modules, workflow_names=workflows,
            planned=planned_by_stem.get(template_stem),
        )
        if errors:
            template_errors.append(
                f"{path}: selected pack {owned.pack_id!r} template (from {owned.source or 'its registered contract'}) "
                "fails assembly checks; no task authors it, so the pack itself must be fixed: " + " | ".join(errors)
            )
    if template_errors:
        raise ValueError("\n".join(template_errors))
    return [{"filename": path, "content": content} for path, content in sorted(file_map.items())]


def _handler_methods(content: str, class_name: str) -> set[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        return {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    return set()


def _apply_module_handler_method_alignment(
    code_files: list[dict[str, str]],
) -> list[dict[str, str]]:
    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
    for path, content in list(file_map.items()):
        pure = PurePosixPath(path)
        if len(pure.parts) != 3 or pure.parts[0] != "modules" or pure.parts[2] != "module.yaml":
            continue
        module_id = pure.parts[1]
        try:
            data = yaml.safe_load(content) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        module_block = data.get("module") if isinstance(data.get("module"), dict) else data
        if not isinstance(module_block, dict):
            continue
        handler = str(module_block.get("handler") or "").strip()
        if ":" not in handler:
            continue
        handler_module, class_name = [part.strip() for part in handler.split(":", 1)]
        if not handler_module.startswith("backend.") or not class_name:
            continue
        handler_path = f"modules/{module_id}/{handler_module.replace('.', '/')}.py"
        methods = _handler_methods(file_map.get(handler_path, ""), class_name)
        if not methods:
            continue

        changed = False
        for action in data.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            handler_method = str(action.get("handler_method") or "").strip()
            if (
                action_id
                and handler_method
                and handler_method not in methods
                and action_id in methods
            ):
                action["handler_method"] = action_id
                changed = True

        if changed:
            file_map[path] = yaml.safe_dump(
                data,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
    return [{"filename": path, "content": content} for path, content in sorted(file_map.items())]


def _selected_capability_packs(app_build_plan: Any, context_variables: Any) -> list[dict[str, Any]] | None:
    if not isinstance(app_build_plan, dict):
        return None
    capability_packs = None
    if context_variables and hasattr(context_variables, "get"):
        capability_packs = detach(context_variables.get("capability_packs"))
    if not capability_packs:
        capability_packs = app_build_plan.get("capability_packs")
    return capability_packs if isinstance(capability_packs, list) else None


async def _verified_revision_source(app_build_plan: Any, context_variables: Any) -> dict[str, str] | None:
    """Text files of the verified revision source a pack revision is bound to, else None.

    App-owned (``owner: workspace``) pack code comes only from verified
    source: this bound revision source (the authorized patch-lane child once it
    is the selected source), else the admitted genesis copy, else the starter
    template. ``conceptual_replan`` and ``full_rebuild`` skip hydrating the old
    implementation but still keep the app's own code from this source; a run
    without the existing refinement binding to one has none.
    """
    if (
        context_variables is None
        or context_variables.get("build_mode") != "revision"
        or not revision_source_artifact_id(context_variables)
        or not _selected_capability_packs(app_build_plan, context_variables)
    ):
        return None
    from factory_app.workflows._shared.platform.build_target import require_build_binding

    if require_build_binding(context_variables).phase != "refinement":
        return None
    # Text-only read (include_binary=False); pack code and registries are text.
    saved = await read_bound_revision_files(context_variables)
    return {path: content for path, content in saved.items() if isinstance(content, str)}


def _workspace_paths(app_build_plan: Any, context_variables: Any) -> frozenset[str]:
    capability_packs = _selected_capability_packs(app_build_plan, context_variables)
    if not capability_packs:
        return frozenset()
    return resolve_declared_pack_output_paths(capability_packs, context_variables=context_variables, owner="workspace")


def _apply_managed_capability_templates(
    code_files: list[dict[str, str]],
    *,
    app_build_plan: Any,
    context_variables: Any,
    ui_contributions: list[dict[str, Any]],
    task_files: list[tuple[str, dict[str, str]]] | None = None,
    verified_source: dict[str, str] | None = None,
    overlay_files: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    capability_packs = _selected_capability_packs(app_build_plan, context_variables)
    if capability_packs is None:
        return code_files

    template_files = resolve_managed_capability_templates(
        capability_packs,
        context_variables=context_variables,
        ui_contributions=ui_contributions,
    )
    if not template_files:
        return code_files

    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
    workspace_paths = resolve_declared_pack_output_paths(
        capability_packs, context_variables=context_variables, owner="workspace",
    )
    # The admitted genesis copy: the assembled bundle, never the repair overlay.
    generated = detach(context_variables.get("generated_files")) if context_variables is not None else None
    admitted = generated if isinstance(generated, dict) else {}
    planned_owners = {
        str(task.get("task_id") or ""): set(task.get("owned_paths") or [])
        for task in (app_build_plan.get("build_tasks") or []) if isinstance(task, dict)
    }
    for file in template_files:
        path = str(file["filename"])
        if path not in workspace_paths:
            file_map[path] = str(file["content"])
            continue
        # Workspace extension templates are initial defaults, never replacements
        # for the app's own verified code; no repair overlay or task output is.
        retained, content = next(
            ((label, layer[path]) for label, layer in (
                ("verified_revision_source", verified_source or {}), ("admitted", admitted),
            ) if path in layer and isinstance(layer[path], str)),
            ("template", file["content"]),
        )
        if path in (overlay_files or {}) and (overlay_files or {})[path] != content:
            logger.warning(
                "[AppGenerator] WORKSPACE_PACK_OUTPUT_EXCLUDED at assembly: path=%s owner=%s retained=%s; "
                "a repair overlay copy cannot change app-owned capability pack code. %s",
                path, _ADMITTED_OVERLAY_OWNER, retained, WORKSPACE_PACK_CHANGE_REMEDY,
            )
        for owner, files in task_files or []:
            if path not in files or files[path] == content:
                continue
            task_id = owner.removeprefix("task:")
            if path in planned_owners.get(task_id, set()):
                # A current plan task asked for this change; dropping it and
                # reporting success would hide that the change never shipped.
                raise WorkspacePackOutputChangeUnsupported(path=path, task_id=task_id, retained=retained)
            logger.warning(
                "[AppGenerator] WORKSPACE_PACK_OUTPUT_IGNORED at assembly: path=%s discarded_owner=%s "
                "retained=%s; output from no current plan task owning this path cannot supersede admitted source",
                path, owner, retained,
            )
        file_map[path] = str(content)
    return [{"filename": path, "content": content} for path, content in sorted(file_map.items())]


def _apply_app_config_contracts(
    code_files: list[dict[str, str]],
    *,
    app_id: str,
    app_build_plan: Any,
    context_variables: Any,
) -> list[dict[str, str]]:
    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
    if "app.json" in file_map:
        app_manifest = json.loads(file_map["app.json"])
        if not isinstance(app_manifest, dict):
            raise ValueError("app.json must be a JSON object")
        if app_manifest.get("appId") != app_id:
            app_manifest["appId"] = app_id
            file_map["app.json"] = json.dumps(app_manifest, indent=2, ensure_ascii=False) + "\n"
    captured_theme = (
        context_variables.get("captured_theme_config") if context_variables is not None else None
    )
    theme_path = "brand/theme_config.json"
    theme_patch = json.loads(file_map[theme_path]) if theme_path in file_map else None
    resolved_theme = resolve_app_theme_config(captured_theme, theme_patch)
    if resolved_theme is not None:
        file_map[theme_path] = json.dumps(resolved_theme, indent=2, ensure_ascii=False)
    for file in materialize_app_config_contracts(
        app_id=app_id,
        app_build_plan=app_build_plan,
        context_variables=context_variables,
    ):
        file_map[str(file["filename"])] = str(file["content"])
    return [{"filename": path, "content": content} for path, content in sorted(file_map.items())]


def _context_code_file_output(context_variables: Any | None) -> dict[str, list[dict[str, str]]] | None:
    if context_variables is None or not hasattr(context_variables, "get"):
        return None
    try:
        raw = detach(context_variables.get("code_files"))
    except Exception:
        raw = None
    code_files = extract_code_file_entries_from_payload({"code_files": raw})
    if not code_files:
        return None
    return {"code_files": code_files}


def _context_deleted_files(context_variables: Any | None) -> list[str]:
    if context_variables is None or not hasattr(context_variables, "get"):
        return []
    try:
        raw = detach(context_variables.get("deleted_files"))
    except Exception:
        raw = None
    return extract_deleted_file_paths_from_payload({"deleted_files": raw})



def _apply_deleted_files(
    code_files: list[dict[str, str]],
    deleted_files: list[str],
) -> list[dict[str, str]]:
    if not deleted_files:
        return code_files
    deleted = set(deleted_files)
    return [
        file
        for file in code_files
        if str(file.get("filename") or "") not in deleted
    ]


async def _assemble_app_tasks(
    *,
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> dict[str, Any]:
    await hydrate_app_revision_context(context_variables)
    app_id = None
    feature_outputs: list[dict[str, Any]] = []
    ui_source_owners: list[tuple[str, bool]] = []
    inject_key: str | None = None

    if context_variables and hasattr(context_variables, "get"):
        existing_files = detach(context_variables.get("generated_files")) or {}
        if existing_files:
            feature_outputs.append({"code_files": [
                {"filename": path, "content": content}
                for path, content in sorted(existing_files.items())
            ]})
            ui_source_owners.append(("app:generated_files", True))
        schema_ready = _is_truthy(context_variables.get("app_schema_ready"))
        if schema_ready:
            quality_status = context_variables.get("app_ui_quality_status")
            if quality_status != "passed":
                warnings = detach(context_variables.get("app_ui_quality_warnings")) or []
                warning_text = ""
                if isinstance(warnings, list) and warnings:
                    warning_text = " Warnings: " + "; ".join(str(item) for item in warnings)
                raise ValueError(
                    "app_ui_quality_status must be 'passed' before schema-driven assembly. "
                    f"Current status: {quality_status or 'missing'}.{warning_text}"
                )
            generated_app_dir = context_variables.get("generated_app_dir")
            schema_code_files = collect_generated_app_file_entries(generated_app_dir)
            if not schema_code_files:
                raise ValueError(
                    "app_schema_ready is true, but generated_app_dir does not contain "
                    "collectable app artifacts."
                )
            feature_outputs.append({"code_files": schema_code_files})
            ui_source_owners.append(("app:compiled_schema", True))

        from factory_app.workflows._shared.platform.build_target import require_build_binding

        app_id = require_build_binding(context_variables).target_app_id
        inject_key = "app_task_batch_results"

        for key, value in task_batch_outputs(context_variables):
            feature_outputs.append(value)
            ui_source_owners.append((f"task:{key}", False))

        context_code_files = _context_code_file_output(context_variables)
        if context_code_files:
            feature_outputs.append(context_code_files)
            ui_source_owners.append((_ADMITTED_OVERLAY_OWNER, True))

    if not app_id:
        raise ValueError("app_id is required to assemble task outputs")

    if not feature_outputs and not _failed_batch_task_ids(
        detach(context_variables.get("app_task_batch_results")) if context_variables else None
    ):
        raise ValueError("No AppGenerator schema artifacts, task batch outputs, or accumulated code files are available for assembly")

    # Pack-owned files come only from the selected packs' templates; no worker,
    # repair or earlier assembly copy of one is merged. Resolve them before
    # compilation so declared pack data participates in the app contract while
    # complete verified template backends retain their policy and implementations.
    pack_outputs = pack_owned_outputs(context_variables)
    kept_outputs: list[dict[str, Any]] = []
    for output in feature_outputs:
        kept, discarded = discard_pack_owned_outputs(output, frozenset(pack_outputs))
        if discarded:
            logger.info(
                "[AppGenerator] PACK_OWNED_OUTPUT_DISCARDED at assembly: task=%s paths=%s; selected pack templates provide them",
                output.get("_task_id") or "accumulated", discarded,
            )
        kept_outputs.append(kept)
    feature_outputs = kept_outputs

    build_timestamp = (
        context_variables.get("build_timestamp")
        if context_variables and hasattr(context_variables, "get")
        else None
    )
    # Shared declarations survive exclusive pack filtering. Prior admitted
    # snapshots may repeat current declarations, but separate task owners may not.
    # The accumulated repair overlay is the newest admitted app layer.
    ui_contributions: list[dict[str, Any]] = []
    task_files: list[tuple[str, dict[str, str]]] = []
    overlay_files: dict[str, str] = {}
    superseded = repair_superseded_roots(context_variables) if context_variables is not None else {}
    for output, (owner, baseline) in zip(feature_outputs, ui_source_owners, strict=True):
        files = extract_code_file_entries_from_payload(output, build_timestamp=build_timestamp)
        if owner.startswith("task:"):
            task_files.append((owner, {item["filename"]: item["content"] for item in files}))
        if owner == _ADMITTED_OVERLAY_OWNER:
            overlay_files = {item["filename"]: item["content"] for item in files}
        if any(item["filename"] in SHARED_UI_OUTPUT_PATHS for item in files):
            ui_contributions.append({
                "owner": owner, "baseline": baseline, "files": files,
                "admitted_overlay": owner == _ADMITTED_OVERLAY_OWNER,
                **({"overlay_supersedes": superseded[owner]} if owner in superseded else {}),
            })

    app_build_plan = (
        detach(context_variables.get("app_build_plan"))
        if context_variables and hasattr(context_variables, "get") else None
    )
    verified_source = await _verified_revision_source(app_build_plan, context_variables)
    template_files = _apply_managed_capability_templates(
        [], app_build_plan=app_build_plan, context_variables=context_variables,
        ui_contributions=ui_contributions, task_files=task_files,
        verified_source=verified_source, overlay_files=overlay_files,
    )
    # App-owned pack code is never deleted by a tombstone either.
    workspace_paths = _workspace_paths(app_build_plan, context_variables) if template_files else frozenset()
    ignored_tombstones = sorted(workspace_paths & set(_context_deleted_files(context_variables)))
    if ignored_tombstones:
        logger.warning(
            "[AppGenerator] WORKSPACE_PACK_OUTPUT_EXCLUDED at assembly: deleted_files=%s; a tombstone cannot "
            "remove app-owned capability pack code. %s", ignored_tombstones, WORKSPACE_PACK_CHANGE_REMEDY,
        )
    kept_paths = frozenset(pack_outputs) | workspace_paths
    if template_files:
        feature_outputs.append({"code_files": template_files})

    if any(
        str(contribution["owner"]).startswith("pack:")
        and any(item["filename"] in SHARED_UI_OUTPUT_PATHS for item in contribution["files"])
        for contribution in ui_contributions
    ):
        # Flatten only after typed payloads are materialized. Shared roots have
        # one final writer; all other file ownership still uses the existing merge.
        materialized = [
            extract_code_file_entries_from_payload(output, build_timestamp=build_timestamp)
            for output in feature_outputs
        ]
        # Compose against the final file set: _apply_deleted_files removes these
        # tombstones below, so a registration of one cannot resolve here either.
        deleted = frozenset(path for path in _context_deleted_files(context_variables) if path not in kept_paths)
        file_map = {
            item["filename"]: item["content"]
            for entries in materialized for item in entries if item["filename"] not in deleted
        }
        revision_baseline = None
        if revision_baseline_required(context_variables):
            # Text-only read (include_binary=False); registries are text sources.
            revision_baseline = verified_source
            if revision_baseline is None:
                saved = await read_bound_revision_files(context_variables)
                revision_baseline = {path: content for path, content in saved.items() if isinstance(content, str)}
            # The admitted projection composes repair copies with this same baseline.
            remember_revision_baseline(context_variables, revision_baseline)
        # The auth contract's routes are composed once, by materialize_auth_scaffold
        # below; carried copies would compete as owners of the same routes.
        composable, composable_baseline = without_auth_route_projection(ui_contributions, file_map, revision_baseline)
        shared = compose_ui_registry(
            composable, file_map, revision_baseline=composable_baseline, deleted_paths=deleted,
        )
        feature_outputs = [{"code_files": [item for item in entries if item["filename"] not in SHARED_UI_OUTPUT_PATHS]} for entries in materialized]
        feature_outputs.append({"code_files": [{"filename": path, "content": content} for path, content in sorted(shared.items())]})

    result = await assemble_features(
        app_id=str(app_id),
        feature_outputs=feature_outputs,
        build_timestamp=build_timestamp,
        app_build_plan=app_build_plan,
        data_contract=(
            detach(context_variables.get("data_contract"))
            if context_variables and hasattr(context_variables, "get") else None
        ),
        design_surface_map=(
            detach(context_variables.get("design_surface_map"))
            if context_variables and hasattr(context_variables, "get") else None
        ),
        subscription_contract=resolve_subscription_contract(context_variables),
        context_variables=context_variables,
        # The bound revision source's contract is carried even when a later
        # copy replaced it in generated_files or the outputs.
        carried_contracts=tuple(
            content for content in [(verified_source or {}).get("data/contract.json")]
            if isinstance(content, str) and revision_baseline_required(context_variables)
        ),
    )

    code_files = result.get("code_files", [])
    verified_pack_routes = [
        route
        for contribution in ui_contributions if str(contribution["owner"]).startswith("pack:")
        for item in contribution["files"] if item["filename"] == "ui/route_manifest.json"
        for route in json.loads(item["content"])["pages"]
    ]
    code_files = _apply_planned_page_contracts(
        code_files,
        app_build_plan,
        context_variables=context_variables,
        failed_task_ids=_failed_batch_task_ids(
            detach(context_variables.get("app_task_batch_results"))
            if context_variables and hasattr(context_variables, "get")
            else None
        ),
        pack_outputs=pack_outputs,
        verified_pack_routes=verified_pack_routes,
    )
    code_files = _apply_module_handler_method_alignment(code_files)
    code_files = _apply_deleted_files(
        code_files, [path for path in _context_deleted_files(context_variables) if path not in kept_paths],
    )
    code_files = apply_entitlement_gates(
        code_files, context_variables=context_variables,
        failed_task_ids=_failed_batch_task_ids(
            detach(context_variables.get("app_task_batch_results")) if context_variables is not None else None
        ),
    )
    code_files = _apply_app_config_contracts(
        code_files,
        app_id=str(app_id),
        app_build_plan=app_build_plan,
        context_variables=context_variables,
    )

    # Write the assembled flat file map to context so the carry-forward
    # preservation resolver (which runs after AssemblyAgent's turn) can read
    # the full generated output and detect conflicts correctly.
    if context_variables and hasattr(context_variables, "set"):
        task_results = detach(context_variables.get("app_task_batch_results"))
        if isinstance(task_results, dict):
            meta = task_results.get("_meta") if isinstance(task_results.get("_meta"), dict) else {}
            context_variables.set(
                "app_task_batch_results_summary",
                {
                    "status": meta.get("status") or context_variables.get("app_task_batch_status"),  # type: ignore[union-attr]
                    "task_count": meta.get("task_count"),  # type: ignore[union-attr]
                    "completed_tasks": meta.get("completed_tasks") or [],  # type: ignore[union-attr]
                    "failed_tasks": meta.get("failed_tasks") or [],  # type: ignore[union-attr]
                    "result_keys": [
                        str(key)
                        for key in task_results
                        if not str(key).startswith("_")
                    ],
                },
            )
        context_variables.set("assembled_source", "schema_and_task_batch_outputs")
        context_variables.set(
            "generated_files",
            {str(f["filename"]): str(f["content"]) for f in code_files},
        )

    status_note = result.get("message") or "Assembled app task outputs into one bundle."
    if isinstance(inject_key, str) and inject_key:
        status_note = f"{status_note} (source={inject_key})"

    return {
        "success": True,
        "status": "passed",
        "code_files": code_files,
        "agent_message": status_note,
    }


async def assemble_app_tasks(
    *,
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> dict[str, Any]:
    """Publish a complete assembly or preserve the previous bundle and cause."""
    context = context_variables
    previous_files = detach(context.get("generated_files")) if context is not None else None
    previous_source = context.get("assembled_source") if context is not None else None
    if context is not None and hasattr(context, "set"):
        context.set("app_assembly_status", "pending")
        context.set("app_assembly_error", None)
        context.set("app_assembly_diagnostic", None)
        context.set("integration_tests_passed", False)
        context.set("app_validation_status", "pending")
        context.set("app_bundle_acceptance_status", "pending")
    try:
        result = await _assemble_app_tasks(context_variables=context)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("App assembly failed: %s", error)
        diagnostic = getattr(exc, "diagnostic", None)
        diagnostic = dict(diagnostic) if isinstance(diagnostic, dict) else {}
        if context is not None and hasattr(context, "set"):
            context.set("generated_files", previous_files)
            context.set("assembled_source", previous_source)
            context.set("app_assembly_status", "failed")
            context.set("app_assembly_error", error)
            context.set("app_assembly_diagnostic", {"code": "APP_ASSEMBLY_FAILED", **diagnostic, "error": str(exc)})
            context.set("app_validation_status", "failed")
            context.set("app_bundle_acceptance_status", "failed")
        return {
            "success": False,
            "status": "failed",
            "error": error,
            **({"diagnostic": diagnostic} if diagnostic else {}),
            "agent_message": f"App assembly failed: {error}. The previous bundle was preserved.",
        }
    if context is not None and hasattr(context, "set"):
        context.set("app_assembly_status", "passed")
    return result


__all__ = ["assemble_app_tasks"]
