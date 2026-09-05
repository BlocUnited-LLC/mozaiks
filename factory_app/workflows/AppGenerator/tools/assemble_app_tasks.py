import ast
from pathlib import PurePosixPath
from typing import Annotated, Any

import yaml
from pydantic import Field

from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    _page_from_plan,
    _page_stem_from_path,
    _page_stems,
)

from .assembly_phase import assemble_features
from .code_file_utils import (
    collect_generated_app_file_entries,
    extract_code_file_entries_from_payload,
    extract_deleted_file_paths_from_payload,
)
from .materialize_app_config_contracts import materialize_app_config_contracts
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
        raw = context_variables.get("code_files")
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
        raw = context_variables.get("deleted_files")
    except Exception:
        raw = None
    return extract_deleted_file_paths_from_payload({"deleted_files": raw})


def _apply_entitlement_gates(
    code_files: list[dict[str, str]],
    *,
    context_variables: Any,
) -> list[dict[str, str]]:
    """Deterministic post-pass: apply entitlement_gate values from subscription_contract.

    SubscriptionContractDesigner emits module_contract_updates that map action ids to
    entitlement_gate capability ids. Task batch agents are prompted to apply these, but
    this pass guarantees the gates survive assembly regardless of individual agent output.
    Only sets the field — never clears an existing entitlement_gate an agent already wrote.
    """
    if not context_variables:
        return code_files

    def _ctx(key: str) -> Any:
        return context_variables.get(key) if hasattr(context_variables, "get") else None

    # Build a map: {module_id: {action_id: capability_id}} from module_contract_updates
    gates_by_module: dict[str, dict[str, str]] = {}
    for key in ("subscription_contract", "subscription_contract_artifact"):
        raw = _ctx(key)
        if not isinstance(raw, dict):
            continue
        for update in raw.get("module_contract_updates") or []:
            if not isinstance(update, dict):
                continue
            module_id = str(update.get("module_id") or "").strip()
            action_id = str(update.get("action_id") or "").strip()
            gate = str(update.get("entitlement_gate") or "").strip()
            if module_id and action_id and gate:
                gates_by_module.setdefault(module_id, {})[action_id] = gate
        if gates_by_module:
            break  # live contract takes priority over artifact

    if not gates_by_module:
        return code_files

    file_map = {str(f["filename"]): str(f["content"]) for f in code_files if f.get("filename")}
    changed = False
    for path, content in list(file_map.items()):
        pure = PurePosixPath(path)
        if len(pure.parts) != 3 or pure.parts[0] != "modules" or pure.parts[2] != "module.yaml":
            continue
        module_id = pure.parts[1]
        gates = gates_by_module.get(module_id)
        if not gates:
            continue
        try:
            data = yaml.safe_load(content) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        actions = data.get("actions")
        if not isinstance(actions, list):
            continue
        file_changed = False
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip()
            if not action_id or action_id not in gates:
                continue
            if action.get("entitlement_gate"):
                continue  # agent already set it — don't overwrite
            action["entitlement_gate"] = gates[action_id]
            file_changed = True
        if file_changed:
            file_map[path] = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
            changed = True

    if not changed:
        return code_files
    return [{"filename": k, "content": v} for k, v in sorted(file_map.items())]


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

        context_code_files = _context_code_file_output(context_variables)
        if context_code_files:
            feature_outputs.append(context_code_files)

    if not app_id:
        raise ValueError("app_id is required to assemble task outputs")

    if not feature_outputs:
        raise ValueError("No AppGenerator schema artifacts, task batch outputs, or accumulated code files are available for assembly")

    result = await assemble_features(
        app_id=str(app_id),
        feature_outputs=feature_outputs,
        build_timestamp=(
            context_variables.get("build_timestamp")
            if context_variables and hasattr(context_variables, "get")
            else None
        ),
    )

    app_build_plan = (
        context_variables.get("app_build_plan")
        if context_variables and hasattr(context_variables, "get")
        else None
    )
    code_files = result.get("code_files", [])

    code_files = _apply_planned_page_contracts(code_files, app_build_plan)
    code_files = _apply_module_handler_method_alignment(code_files)
    code_files = _apply_entitlement_gates(code_files, context_variables=context_variables)
    code_files = _apply_managed_capability_templates(
        code_files,
        app_build_plan=app_build_plan,
        context_variables=context_variables,
    )
    code_files = _apply_deleted_files(code_files, _context_deleted_files(context_variables))
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
    if isinstance(inject_key, str) and inject_key:
        status_note = f"{status_note} (source={inject_key})"

    return {
        "code_files": code_files,
        "agent_message": status_note,
    }


__all__ = ["assemble_app_tasks"]
