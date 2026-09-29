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
    validate_planned_page,
)

from .assembly_phase import assemble_features
from .code_file_utils import (
    collect_generated_app_file_entries,
    extract_code_file_entries_from_payload,
    extract_deleted_file_paths_from_payload,
)
from .hydrate_app_revision_context import hydrate_app_revision_context
from .materialize_app_config_contracts import materialize_app_config_contracts
from .module_entitlement_gates import apply_entitlement_gates
from .resolve_managed_capability_templates import resolve_managed_capability_templates
from .save_app_schema import resolve_app_theme_config

logger = logging.getLogger(__name__)


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


def _apply_managed_capability_templates(
    code_files: list[dict[str, str]],
    *,
    app_build_plan: Any,
    context_variables: Any,
) -> list[dict[str, str]]:
    if not isinstance(app_build_plan, dict):
        return code_files

    capability_packs = None
    if context_variables and hasattr(context_variables, "get"):
        capability_packs = detach(context_variables.get("capability_packs"))
    if not capability_packs:
        capability_packs = app_build_plan.get("capability_packs")
    if not isinstance(capability_packs, list):
        return code_files

    template_files = resolve_managed_capability_templates(
        capability_packs,
        context_variables=context_variables,
    )
    if not template_files:
        return code_files

    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
    for file in template_files:
        file_map[str(file["filename"])] = str(file["content"])
    return [{"filename": path, "content": content} for path, content in sorted(file_map.items())]


def _apply_app_config_contracts(
    code_files: list[dict[str, str]],
    *,
    app_id: str,
    app_build_plan: Any,
    context_variables: Any,
) -> list[dict[str, str]]:
    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
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
    inject_key: str | None = None

    if context_variables and hasattr(context_variables, "get"):
        existing_files = detach(context_variables.get("generated_files")) or {}
        if existing_files:
            feature_outputs.append({"code_files": [
                {"filename": path, "content": content}
                for path, content in sorted(existing_files.items())
            ]})
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

        from factory_app.workflows._shared.platform.build_target import require_build_binding

        app_id = require_build_binding(context_variables).target_app_id
        inject_key = "app_task_batch_results"

        merged = detach(context_variables.get(inject_key))
        if isinstance(merged, dict):
            candidate_values: Any = (
                merged.get("task_results")
                or merged.get("results")
                or merged.get("tasks")
                or merged
            )
            if isinstance(candidate_values, dict):
                iterable = candidate_values.items()
            elif isinstance(candidate_values, list):
                iterable = enumerate(candidate_values)  # type: ignore[assignment]
            else:
                iterable = []  # type: ignore[assignment]
            for key, value in iterable:
                if key in {"_failed", "_meta", "failed_tasks"}:
                    continue
                if isinstance(value, dict):
                    feature_outputs.append(value)

        context_code_files = _context_code_file_output(context_variables)
        if context_code_files:
            feature_outputs.append(context_code_files)

    if not app_id:
        raise ValueError("app_id is required to assemble task outputs")

    if not feature_outputs and not _failed_batch_task_ids(
        detach(context_variables.get("app_task_batch_results")) if context_variables else None
    ):
        raise ValueError("No AppGenerator schema artifacts, task batch outputs, or accumulated code files are available for assembly")

    # Pack-owned files come only from the selected packs' templates, applied
    # below; no worker, repair or earlier assembly copy of one is merged.
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

    result = await assemble_features(
        app_id=str(app_id),
        feature_outputs=feature_outputs,
        build_timestamp=(
            context_variables.get("build_timestamp")
            if context_variables and hasattr(context_variables, "get")
            else None
        ),
        app_build_plan=(
            detach(context_variables.get("app_build_plan"))
            if context_variables and hasattr(context_variables, "get") else None
        ),
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
    )

    app_build_plan = (
        detach(context_variables.get("app_build_plan"))
        if context_variables and hasattr(context_variables, "get")
        else None
    )
    code_files = result.get("code_files", [])

    code_files = _apply_managed_capability_templates(
        code_files,
        app_build_plan=app_build_plan,
        context_variables=context_variables,
    )
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
    )
    code_files = _apply_module_handler_method_alignment(code_files)
    code_files = _apply_deleted_files(
        code_files, [path for path in _context_deleted_files(context_variables) if path not in pack_outputs],
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
        if context is not None and hasattr(context, "set"):
            context.set("generated_files", previous_files)
            context.set("assembled_source", previous_source)
            context.set("app_assembly_status", "failed")
            context.set("app_assembly_error", error)
            context.set("app_assembly_diagnostic", {"code": "APP_ASSEMBLY_FAILED", "error": str(exc)})
            context.set("app_validation_status", "failed")
            context.set("app_bundle_acceptance_status", "failed")
        return {
            "success": False,
            "status": "failed",
            "error": error,
            "agent_message": f"App assembly failed: {error}. The previous bundle was preserved.",
        }
    if context is not None and hasattr(context, "set"):
        context.set("app_assembly_status", "passed")
    return result


__all__ = ["assemble_app_tasks"]
