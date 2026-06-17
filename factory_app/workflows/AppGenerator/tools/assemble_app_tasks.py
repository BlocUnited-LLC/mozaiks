import ast
from pathlib import PurePosixPath
from typing import Annotated, Any

import yaml
from autogen.tools.dependency_injection import Field

from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    _page_from_plan,
    _page_stem_from_path,
    _page_stems,
)

from .assembly_phase import assemble_features
from .code_file_utils import collect_generated_app_file_entries
from .generate_module_interface_files import generate_module_interface_files
from .resolve_managed_capability_templates import resolve_managed_capability_templates


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "passed", "ready"}
    return bool(value)


def _apply_planned_page_contracts(
    code_files: list[dict[str, Any]],
    app_build_plan: Any,
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
    for task in tasks:
        if str(task.get("task_type") or "").strip() != "page_bundle":
            continue
        for raw_path in task.get("owned_paths") or []:
            path = str(raw_path or "").replace("\\", "/").strip()
            stem = _page_stem_from_path(path)  # type: ignore[assignment]
            if not stem or stem not in planned_by_stem:
                continue
            file_map[path] = yaml.safe_dump(
                _page_from_plan(planned_by_stem[stem], stem),
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
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
        capability_packs = context_variables.get("capability_packs")
    if not capability_packs:
        capability_packs = app_build_plan.get("capability_packs")
    if not isinstance(capability_packs, list):
        return code_files

    template_files = resolve_managed_capability_templates(capability_packs)
    if not template_files:
        return code_files

    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
    for file in template_files:
        file_map[str(file["filename"])] = str(file["content"])
    return [{"filename": path, "content": content} for path, content in sorted(file_map.items())]


async def assemble_app_tasks(
    *,
    context_variables: Annotated[
        Any | None,
        Field(description="AG2-injected workflow context variables."),
    ] = None,
) -> dict[str, Any]:
    app_id = None
    feature_outputs: list[dict[str, Any]] = []
    inject_key: str | None = None

    if context_variables and hasattr(context_variables, "get"):
        schema_ready = _is_truthy(context_variables.get("app_schema_ready"))
        if schema_ready:
            quality_status = context_variables.get("app_ui_quality_status")
            if quality_status != "passed":
                warnings = context_variables.get("app_ui_quality_warnings") or []
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

        app_id = context_variables.get("app_id")
        inject_key = "app_task_batch_results"

        merged = context_variables.get(inject_key)
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

    if not app_id:
        raise ValueError("app_id is required to assemble task outputs")

    if not feature_outputs:
        raise ValueError("No AppGenerator schema artifacts or task batch outputs are available for assembly")

    result = await assemble_features(
        app_id=str(app_id),
        feature_outputs=feature_outputs,
    )

    # Merge module_interface.yaml files generated from agent_backend_integration tasks.
    # AppPlanAgent already reasoned about module action dependencies and stored them as
    # structured context_variables on each task — this tool just serializes them to YAML.
    app_build_plan = (
        context_variables.get("app_build_plan")
        if context_variables and hasattr(context_variables, "get")
        else None
    )
    interface_files = generate_module_interface_files(app_build_plan)
    code_files = result.get("code_files", [])
    if interface_files:
        # Merge by filename (interface files take precedence over any stub)
        file_map = {f["filename"]: f["content"] for f in code_files}
        for f in interface_files:
            file_map[f["filename"]] = f["content"]
        code_files = [{"filename": k, "content": v} for k, v in sorted(file_map.items())]

    code_files = _apply_planned_page_contracts(code_files, app_build_plan)
    code_files = _apply_module_handler_method_alignment(code_files)
    code_files = _apply_managed_capability_templates(
        code_files,
        app_build_plan=app_build_plan,
        context_variables=context_variables,
    )

    # Write the assembled flat file map to context so the carry-forward
    # preservation resolver (which runs after AssemblyAgent's turn) can read
    # the full generated output and detect conflicts correctly.
    if context_variables and hasattr(context_variables, "set"):
        try:
            context_variables.set(
                "generated_files",
                {str(f["filename"]): str(f["content"]) for f in code_files},
            )
            context_variables.set("assembled_source", "schema_and_task_batch_outputs")
            task_results = context_variables.get("app_task_batch_results")
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
                context_variables.set(
                    "app_task_batch_results",
                    {
                        "_meta": meta,
                        "assembled": True,
                        "result_context_key": "app_task_batch_results_summary",
                    },
                )
        except Exception:
            pass

    status_note = result.get("message") or "Assembled app task outputs into one bundle."
    if interface_files:
        status_note = f"{status_note} (+{len(interface_files)} module_interface.yaml)"
    if isinstance(inject_key, str) and inject_key:
        status_note = f"{status_note} (source={inject_key})"

    return {
        "code_files": code_files,
        "agent_message": status_note,
    }


__all__ = ["assemble_app_tasks"]
