"""
App validation tool for generated applications.

This tool can:
- resolve generated files from an explicit `files` mapping or the current admitted artifacts
- validate the generated app with an explicit strategy: `e2b`, `docker`, `local`, or `skip`
- run build/test commands
- optionally start a preview server (e2b and docker strategies expose a URL)
"""


import ast
import asyncio
import builtins
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile

logger = logging.getLogger(__name__)
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from factory_app.workflows._shared.workflow_integration import (
    workflow_integration_metadata_from_context,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    admitted_app_file_map,
)
from factory_app.workflows.AppGenerator.tools.repair_policy import (
    prepare_bundle_repair as _prepare_bundle_repair,
)
from factory_app.workflows.AppGenerator.tools.repair_policy import (
    prepare_task_recovery,
)
from factory_app.workflows.AppGenerator.tools.task_integrity import (
    artifact_snapshot_digest,
    planned_artifact_diagnostics,
)
from logs.logging_config import get_workflow_logger
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
    local_app_validation_available,
    resolve_app_validation_strategy,
)


def _local_validation_available() -> bool:
    return local_app_validation_available()


def _base_result(*, strategy: str, status: str) -> dict[str, Any]:
    return {
        "success": status != "failed",
        "validation_strategy": strategy,
        "validation_status": status,
        "strategy_reason": "",
        "build_output": "",
        "errors": [],
        "warnings": [],
        "preview_url": None,
        "test_results": None,
        "parsed_errors": [],
    }


def _safe_relpath(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    if not path or path.startswith("/"):
        return None
    p = PurePosixPath(path)
    if p.is_absolute():
        return None
    if any(part in {".."} for part in p.parts) or ":" in path or "\x00" in path or str(p) == ".":
        return None
    return str(p)


def _is_safe_build_command(command: str) -> bool:
    """Return True when *command* looks like a safe build/test shell command.

    Blocks shell metacharacters that enable command chaining or substitution:
    ``;``, ``&&``, ``||``, ``|``, backtick, ``$(…)``, and output redirection
    (``>`` / ``<``).  Also rejects commands that contain null bytes.

    This is defence-in-depth against prompt-injection attacks where a
    compromised or confused agent emits shell payloads inside
    ``validation_commands``.  Legitimate build commands (``npm install``,
    ``npm run build``, ``python -m pytest``, etc.) never need these characters.
    """
    if not command or "\x00" in command:
        return False
    # Reject shell metacharacters used for chaining, substitution, or redirection
    _SHELL_METACHAR_RE = re.compile(r"[;|&`$><]")
    return not _SHELL_METACHAR_RE.search(command)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "passed", "ready"}
    return bool(value)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _append_command_output(result: dict[str, Any], *, command: str, stdout: str, stderr: str) -> None:
    result["build_output"] += f"\n=== {command} ===\n"
    result["build_output"] += stdout or ""
    if stderr:
        result["build_output"] += "\n" + stderr


def _read_package_scripts_from_text(package_text: str) -> dict[str, Any]:
    try:
        pkg = json.loads(package_text) if isinstance(package_text, str) else {}
    except Exception:
        pkg = {}
    scripts = pkg.get("scripts") if isinstance(pkg, dict) else {}
    return scripts if isinstance(scripts, dict) else {}


def _read_package_scripts_from_dir(root: Path) -> dict[str, Any]:
    package_path = root / "package.json"
    if not package_path.exists():
        return {}
    try:
        return _read_package_scripts_from_text(package_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def _run_local_command(
    *,
    command: str,
    cwd: Path,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise RuntimeError(f"Command timed out after {timeout_seconds}s: {command}") from exc

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    return int(process.returncode or 0), stdout, stderr


def parse_build_errors(build_output: str) -> list[dict[str, Any]]:
    if not isinstance(build_output, str) or not build_output:
        return []

    errors: list[dict[str, Any]] = []

    ts_pattern = r"([^\s]+):(\d+):(\d+)\s*[-–]\s*error\s+\w+:\s*(.+)"
    for match in re.finditer(ts_pattern, build_output):
        errors.append(
            {
                "file": match.group(1),
                "line": int(match.group(2)),
                "column": int(match.group(3)),
                "message": match.group(4).strip(),
            }
        )

    webpack_pattern = r"ERROR in ([^\s]+)\s*\n.*?(\d+):(\d+)\s*(.+)"
    for match in re.finditer(webpack_pattern, build_output, re.MULTILINE | re.DOTALL):
        errors.append(
            {
                "file": match.group(1),
                "line": int(match.group(2)),
                "column": int(match.group(3)),
                "message": match.group(4).strip(),
            }
        )

    return errors


async def _resolve_files(
    *,
    files: dict[str, str] | None,
    context_variables: Any | None,
    wf_logger,
) -> tuple[dict[str, str], str | None, str | None]:
    if files is not None:
        return _safe_files_map(files), None, None
    return (
        admitted_app_file_map(context_variables),
        _context_get(context_variables, "chat_id"),
        _context_get(context_variables, "app_id"),
    )


def _write_files_to_dir(root: Path, files_map: dict[str, str]) -> None:
    for rel_path, content in files_map.items():
        safe = _safe_relpath(rel_path)
        if not safe:
            continue
        out_path = root / safe
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(str(content), encoding="utf-8", newline="")


def _normalize_module_yaml(path: str, content: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    failed: list[dict[str, Any]] = []
    try:
        parsed = yaml.safe_load(content) or {}
    except Exception as exc:
        return None, [
            {
                "test": "module_yaml_parse",
                "path": path,
                "error": f"{path} could not be parsed as YAML: {exc}",
                "fix_suggestion": "Emit valid YAML for the canonical modules/{module_id}/module.yaml contract.",
            }
        ]

    if not isinstance(parsed, dict):
        return None, [
            {
                "test": "module_yaml_shape",
                "path": path,
                "error": f"{path} must contain a YAML object.",
                "fix_suggestion": "Emit module.yaml as a mapping with schema_version, module, actions, and capabilities.",
            }
        ]

    module_block = parsed.get("module") if isinstance(parsed.get("module"), dict) else parsed
    parts = PurePosixPath(path).parts
    folder_module_id = parts[1] if len(parts) > 1 and parts[0] == "modules" else ""
    module_id = str(module_block.get("id") or folder_module_id).strip()
    handler = str(module_block.get("handler") or parsed.get("handler") or "").strip()
    actions = parsed.get("actions") or []
    if not isinstance(actions, list):
        actions = []

    return {
        "path": path,
        "module_id": module_id,
        "handler": handler,
        "actions": actions,
    }, failed


def _iter_module_yamls(files: dict[str, str]) -> Iterable[tuple[str, str]]:
    for path, content in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if len(parts) == 3 and parts[0] == "modules" and parts[2] == "module.yaml":
            yield path, content


def _iter_backend_python(files: dict[str, str]) -> Iterable[tuple[str, str]]:
    for path, content in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if len(parts) >= 4 and parts[0] == "modules" and parts[2] == "backend" and path.endswith(".py"):
            yield path, content


def _parse_python(path: str, content: str) -> tuple[ast.Module | None, dict[str, Any] | None]:
    try:
        return ast.parse(content, filename=path), None
    except SyntaxError as exc:
        return None, {
            "test": "backend_python_syntax",
            "path": path,
            "error": f"{path}:{exc.lineno or 0}: Python syntax error: {exc.msg}",
            "fix_suggestion": "Emit syntactically valid Python for generated module backend files.",
        }


def _defined_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _module_level_name_warnings(path: str, tree: ast.Module) -> list[dict[str, Any]]:
    defined = _defined_module_names(tree)
    failed: list[dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id not in defined:
                failed.append(
                    {
                        "test": "backend_python_unresolved_class_base",
                        "path": path,
                        "error": (
                            f"{path}:{node.lineno}: class {node.name!r} inherits from "
                            f"{base.id!r}, but that name is not imported or defined."
                        ),
                        "fix_suggestion": (
                            f"Import or define {base.id!r} before using it as a class base, "
                            "or remove the unsupported inheritance."
                        ),
                    }
                )
    return failed


def _backend_pass_statement_failures(path: str, tree: ast.Module) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        if not isinstance(node, ast.Pass):
            continue
        parent = parents.get(node)
        while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = parents.get(parent)
        if parent is None:
            continue
        failed.append(
            {
                "test": "backend_python_pass_statement",
                "path": path,
                "error": (
                    f"{path}:{node.lineno}: function {parent.name!r} contains `pass`; "
                    "generated module runtime code must execute real logic or return an honest value."
                ),
                "fix_suggestion": (
                    "Replace `pass` with repo-backed behavior, an explicit permission check, "
                    "or a concrete empty result."
                ),
            }
        )
    return failed


def _class_method_nodes(
    tree: ast.Module,
    class_name: str,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None:
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        return {
            child.name: child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    return None


def _resolve_handler_methods(
    handler_tree: ast.Module,
    class_name: str,
    module_id: str,
    parsed_backend: dict[str, ast.Module],
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None:
    """Return method nodes for class_name, merging inherited methods from base
    classes declared in the same module's backend directory.

    Supports the base_handler.py + handler.py split pattern where the workspace
    subclass is a thin override layer and all canonical methods live in the OSS
    base handler.  Returns None only when the class itself is not found.
    """
    own = _class_method_nodes(handler_tree, class_name)
    if own is None:
        return None

    # Collect simple base-class names from the class AST definition.
    base_names: list[str] = []
    for node in handler_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for base in node.bases:
                if isinstance(base, ast.Name):
                    base_names.append(base.id)
            break

    if not base_names:
        return own

    # Look for base class methods in any backend file within the same module.
    backend_prefix = f"modules/{module_id}/backend/"
    inherited: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for base_name in base_names:
        for path, tree in parsed_backend.items():
            if not path.startswith(backend_prefix):
                continue
            base_methods = _class_method_nodes(tree, base_name)
            if base_methods is not None:
                for method_name, method_node in base_methods.items():
                    inherited.setdefault(method_name, method_node)

    # Own methods take precedence over inherited ones.
    inherited.update(own)
    return inherited


def _all_method_nodes(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.setdefault(child.name, child)
    return methods


def _input_schema_required_fields(action: dict[str, Any]) -> set[str]:
    input_schema = action.get("input_schema")
    if not isinstance(input_schema, dict):
        return set()
    required = input_schema.get("required") or []
    if not isinstance(required, list):
        return set()
    return {str(item).strip() for item in required if str(item).strip()}


def _input_schema_declared_fields(action: dict[str, Any]) -> set[str]:
    input_schema = action.get("input_schema")
    if not isinstance(input_schema, dict):
        return set()
    declared = set(_input_schema_required_fields(action))
    properties = input_schema.get("properties")
    if isinstance(properties, dict):
        declared.update(str(key).strip() for key in properties if str(key).strip())
    elif isinstance(properties, list):
        for item in properties:
            if isinstance(item, dict) and item.get("name"):
                declared.add(str(item["name"]).strip())
    return {field for field in declared if field}


def _validate_handler_signature(
    *,
    method_node: ast.FunctionDef | ast.AsyncFunctionDef,
    action: dict[str, Any],
    handler_path: str,
    class_name: str,
    action_id: str,
) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    positional = list(method_node.args.posonlyargs) + list(method_node.args.args)
    if positional and positional[0].arg in {"self", "cls"}:
        action_args = positional[1:]
    else:
        action_args = positional

    if not action_args:
        failed.append(
            {
                "test": "module_handler_context_parameter",
                "path": handler_path,
                "error": (
                    f"{handler_path} class {class_name!r} method {method_node.name!r} "
                    "must accept runtime context as the first parameter after self."
                ),
                "fix_suggestion": (
                    f"Use `async def {method_node.name}(self, ctx, **params)` or "
                    f"`async def {method_node.name}(self, ctx, ...)`."
                ),
            }
        )
        return failed

    context_arg = action_args[0].arg
    if context_arg not in {"ctx", "context"}:
        failed.append(
            {
                "test": "module_handler_context_parameter",
                "path": handler_path,
                "error": (
                    f"{handler_path} class {class_name!r} method {method_node.name!r} "
                    f"uses {context_arg!r} as the first runtime argument, but the "
                    "module executor passes ModuleContext there."
                ),
                "fix_suggestion": (
                    f"Make the first parameter after self `ctx` or `context` for action {action_id!r}."
                ),
            }
        )

    if method_node.args.kwarg is not None:
        return failed

    accepted = {arg.arg for arg in action_args[1:]}
    accepted.update(arg.arg for arg in method_node.args.kwonlyargs)
    missing_required = sorted(_input_schema_required_fields(action) - accepted)
    if missing_required:
        failed.append(
            {
                "test": "module_handler_required_input_parameters",
                "path": handler_path,
                "error": (
                    f"{handler_path} class {class_name!r} method {method_node.name!r} "
                    f"does not accept required input field(s) {missing_required} "
                    f"for action {action_id!r} and has no **params catch-all."
                ),
                "fix_suggestion": (
                    f"Add `**params` to {method_node.name} or add named parameters "
                    "matching module.yaml input_schema.required."
                ),
            }
        )

    return failed


def _method_reads_synthetic_payload(method_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for child in ast.walk(method_node):
        if isinstance(child, ast.Subscript):
            if isinstance(child.value, ast.Name) and child.value.id == "params":
                if isinstance(child.slice, ast.Constant) and child.slice.value == "payload":
                    return True
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Name)
            and func.value.id == "params"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and child.args[0].value == "payload"
        ):
            return True
    return False


def _method_params_keys(method_node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    keys: set[str] = set()
    for child in ast.walk(method_node):
        if isinstance(child, ast.Subscript):
            if isinstance(child.value, ast.Name) and child.value.id == "params":
                if isinstance(child.slice, ast.Constant) and isinstance(child.slice.value, str):
                    keys.add(child.slice.value)
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Name)
            and func.value.id == "params"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ):
            keys.add(child.args[0].value)
    return keys


def validate_module_implementation_contract(files: dict[str, str]) -> dict[str, Any]:
    """Validate assembled module contracts against generated backend code.

    This is the final deterministic app validation boundary. Agent-local quality
    gates can miss task-batch drift; this check validates the assembled
    ``generated_files`` bundle that DownloadAgent would otherwise package.
    """

    failed_tests: list[dict[str, Any]] = []
    warnings: list[str] = []
    module_reports: list[dict[str, Any]] = []
    parsed_backend: dict[str, ast.Module] = {}

    for path, content in _iter_backend_python(files):
        tree, failure = _parse_python(path, content)
        if failure:
            failed_tests.append(failure)
            continue
        if tree is None:
            continue
        parsed_backend[path] = tree
        failed_tests.extend(_module_level_name_warnings(path, tree))
        failed_tests.extend(_backend_pass_statement_failures(path, tree))

    for module_yaml_path, module_yaml_content in _iter_module_yamls(files):
        module_info, failures = _normalize_module_yaml(module_yaml_path, module_yaml_content)
        failed_tests.extend(failures)
        if not module_info:
            continue

        module_id = str(module_info["module_id"])
        handler = str(module_info["handler"])
        actions = list(module_info["actions"])
        module_report = {
            "module_id": module_id,
            "module_yaml": module_yaml_path,
            "handler": handler,
            "action_count": len(actions),
            "missing_handler_methods": [],
        }

        if not module_id:
            failed_tests.append(
                {
                    "test": "module_id_required",
                    "path": module_yaml_path,
                    "error": f"{module_yaml_path} must declare module.id matching its folder.",
                    "fix_suggestion": "Set module.id to the modules/{module_id} folder name.",
                }
            )
            module_reports.append(module_report)
            continue

        if not handler or ":" not in handler:
            failed_tests.append(
                {
                    "test": "module_handler_required",
                    "path": module_yaml_path,
                    "error": f"{module_yaml_path} must declare module.handler as backend.path:ClassName.",
                    "fix_suggestion": "Use the canonical handler entrypoint, for example backend.handler:TicketsModule.",
                }
            )
            module_reports.append(module_report)
            continue

        handler_module, class_name = [part.strip() for part in handler.split(":", 1)]
        if not handler_module.startswith("backend.") or not class_name:
            failed_tests.append(
                {
                    "test": "module_handler_entrypoint",
                    "path": module_yaml_path,
                    "error": f"{module_yaml_path} handler {handler!r} must be module-local backend.*:ClassName.",
                    "fix_suggestion": "Point module.handler at a class in modules/{module_id}/backend/handler.py.",
                }
            )
            module_reports.append(module_report)
            continue

        handler_rel = handler_module.replace(".", "/") + ".py"
        handler_path = f"modules/{module_id}/{handler_rel}"
        handler_tree = parsed_backend.get(handler_path)
        if handler_tree is None:
            failed_tests.append(
                {
                    "test": "module_handler_file_exists",
                    "path": module_yaml_path,
                    "error": f"{module_yaml_path} declares handler {handler!r}, but {handler_path} was not generated.",
                    "fix_suggestion": f"Generate {handler_path} with class {class_name} and every declared action handler method.",
                }
            )
            module_reports.append(module_report)
            continue

        methods = _resolve_handler_methods(handler_tree, class_name, module_id, parsed_backend)
        if methods is None:
            failed_tests.append(
                {
                    "test": "module_handler_class_exists",
                    "path": handler_path,
                    "error": f"{handler_path} does not define handler class {class_name!r}.",
                    "fix_suggestion": f"Define class {class_name} in {handler_path}.",
                }
            )
            module_reports.append(module_report)
            continue

        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                failed_tests.append(
                    {
                        "test": "module_action_shape",
                        "path": module_yaml_path,
                        "error": f"{module_yaml_path} actions[{index}] must be a mapping.",
                        "fix_suggestion": "Emit every module action as a mapping with id and handler_method.",
                    }
                )
                continue
            action_id = str(action.get("id") or "").strip() or f"actions[{index}]"
            handler_method = str(action.get("handler_method") or "").strip()
            if not handler_method:
                failed_tests.append(
                    {
                        "test": "module_action_handler_method_required",
                        "path": module_yaml_path,
                        "error": f"{module_yaml_path} action {action_id!r} is missing handler_method.",
                        "fix_suggestion": "Set handler_method to the method implemented on the module handler class.",
                    }
                )
                continue
            method_node = methods.get(handler_method)
            if method_node is None:
                module_report["missing_handler_methods"].append(handler_method)
                failed_tests.append(
                    {
                        "test": "module_action_handler_method_missing",
                        "path": handler_path,
                        "error": (
                            f"{module_yaml_path} action {action_id!r} declares "
                            f"handler_method {handler_method!r}, but class {class_name!r} "
                            f"does not implement it."
                        ),
                        "fix_suggestion": (
                            f"Add async def {handler_method}(self, ctx, **params) to "
                            f"{handler_path} and delegate to the service layer."
                        ),
                    }
                )
                continue

            failed_tests.extend(
                _validate_handler_signature(
                    method_node=method_node,
                    action=action,
                    handler_path=handler_path,
                    class_name=class_name,
                    action_id=action_id,
                )
            )

            service_tree = parsed_backend.get(f"modules/{module_id}/backend/service.py")
            if service_tree is not None:
                service_method = _all_method_nodes(service_tree).get(handler_method)
                required_fields = _input_schema_required_fields(action)
                if (
                    service_method is not None
                    and required_fields
                    and "payload" not in required_fields
                    and _method_reads_synthetic_payload(service_method)
                ):
                    failed_tests.append(
                        {
                            "test": "module_service_synthetic_payload_wrapper",
                            "path": f"modules/{module_id}/backend/service.py",
                            "error": (
                                f"Service method {handler_method!r} reads params['payload'] "
                                f"or params.get('payload'), but action {action_id!r} declares "
                                f"required input field(s) {sorted(required_fields)} and no payload field."
                            ),
                            "fix_suggestion": (
                                "Consume the declared input fields directly from **params, "
                                "or change module.yaml input_schema to explicitly declare payload."
                            ),
                        }
                    )
                declared_fields = _input_schema_declared_fields(action)
                if service_method is not None and declared_fields:
                    undeclared_keys = sorted(_method_params_keys(service_method) - declared_fields)
                    if undeclared_keys:
                        failed_tests.append(
                            {
                                "test": "module_service_undeclared_params_key",
                                "path": f"modules/{module_id}/backend/service.py",
                                "error": (
                                    f"Service method {handler_method!r} reads undeclared "
                                    f"params key(s) {undeclared_keys} for action {action_id!r}. "
                                    f"Declared input field(s): {sorted(declared_fields)}."
                                ),
                                "fix_suggestion": (
                                    "Read only fields declared by module.yaml input_schema, "
                                    "or update the action schema to declare the keys the service expects."
                                ),
                            }
                        )

        module_reports.append(module_report)

    if not module_reports:
        warnings.append("No generated modules/*/module.yaml files found; module implementation validation was advisory.")

    passed = not failed_tests
    check = {
        "id": "module_implementation_contract",
        "passed": passed,
        "message": (
            "All generated module actions resolve to implemented handler methods."
            if passed
            else f"{len(failed_tests)} generated module implementation issue(s) found."
        ),
        "details": {
            "module_count": len(module_reports),
            "backend_python_file_count": len(parsed_backend),
            "failed_test_count": len(failed_tests),
            "modules": module_reports,
        },
    }

    return {
        "contract_version": "1.0",
        "passed": passed,
        "checks": [check],
        "modules": module_reports,
        "failed_tests": failed_tests,
        "warnings": warnings,
    }


def _canonical_workspace_files(files: dict[str, str]) -> dict[str, str]:
    from mozaiksai.core.runtime.app.paths import app_bundle_workspace_path

    staged: dict[str, str] = {}
    for path, content in files.items():
        if not _safe_relpath(path):
            raise ValueError("Invalid app validation file path")
        destination = app_bundle_workspace_path(path)
        if destination in staged:
            raise ValueError("Conflicting app validation file destinations")
        staged[destination] = content
    return staged


def _canonical_build_steps(root: str, shell: str, *, sandbox: bool) -> list[tuple[str, str]]:
    join = shlex.join if sandbox or os.name != "nt" else subprocess.list2cmdline
    python = "python" if sandbox else sys.executable
    return [
        (join([python, "-m", "compileall", "-q", "."]), root),
        (join(["node", f"{shell}/node_modules/vite/bin/vite.js", "build", "--outDir", f"{root}/build"]), shell),
    ]


def _canonical_build_environment(root: str) -> dict[str, str]:
    return {
        "PLATFORM_PATH": f"{root}/app",
        "MOZAIKS_APP_WORKSPACE_PATH": root,
        "MOZAIKS_WORKFLOWS_PATH": f"{root}/workflows",
        "MOZAIKS_HOST": "platform",
        "VITE_MOZAIKS_HOST": "platform",
        "PYTHON_DOTENV_DISABLED": "1",
        "CI": "1",
    }


async def _run_sandbox_validation(
    *,
    strategy: str,
    resolved_files: dict[str, str],
    commands: list[str],
    start_dev_server: bool,
    timeout_seconds: int,
    session_metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run build validation through a SandboxPort adapter (e2b or docker).

    The session is tagged with app/chat identity metadata so orphaned
    provider sandboxes can be reconciled to their app, and the session
    identity is recorded on the result so the run that produced a validation
    outcome is durable evidence rather than an ephemeral local variable.
    """
    from mozaiksai.core.adapters import get_sandbox_adapter
    from mozaiksai.core.sandbox.preview_sessions import (
        sandbox_resource_environment,
        sandbox_workspace_root,
    )

    try:
        adapter = get_sandbox_adapter(strategy)
    except Exception as exc:
        logger.error("sandbox_adapter_unavailable strategy=%s exception=%s", strategy, type(exc).__name__)
        return {
            **_base_result(strategy=strategy, status="failed"),
            "errors": ["Validation infrastructure unavailable."],
        }

    result = _base_result(strategy=strategy, status="passed")
    session_id: str | None = None
    try:
        canonical = "app.json" in resolved_files
        root = sandbox_workspace_root(strategy) if canonical else None
        staged = _canonical_workspace_files(resolved_files) if canonical else resolved_files
        resource_env = sandbox_resource_environment() if canonical else {}
        build_env = _canonical_build_environment(root) if root is not None else {}
        steps = (
            _canonical_build_steps(root, resource_env["MOZAIKS_WEB_SHELL_PATH"], sandbox=True)
            if root is not None else [(cmd, None) for cmd in commands]
        )
        if strategy == "e2b" and os.getenv("E2B_TIMEOUT"):
            configured_timeout = int(os.environ["E2B_TIMEOUT"])
            if configured_timeout <= 0:
                raise ValueError("E2B_TIMEOUT must be positive")
            timeout_seconds = min(timeout_seconds, configured_timeout)
        session = await adapter.create_session(
            timeout_seconds=timeout_seconds,
            metadata=session_metadata or {"purpose": "app_validation"},
            **({"envs": {**resource_env, **build_env}} if canonical else {}),
        )
        session_id = session.session_id
        result["sandbox_session_id"] = session_id
        result["sandbox_provider"] = session.provider

        await adapter.write_files(session_id=session_id, files=staged, **({"cwd": root} if root is not None else {}))

        for cmd, cwd in steps:
            if not _is_safe_build_command(cmd):
                if canonical:
                    raise ValueError("Invalid canonical build command configuration")
                result["warnings"].append(
                    f"Skipped unsafe validation command (contains shell metacharacters): {cmd!r}"
                )
                continue
            run_result = await adapter.run_command(
                session_id=session_id,
                command=cmd,
                timeout_seconds=float(timeout_seconds),
                **({"cwd": cwd, "envs": build_env} if cwd is not None else {}),
            )
            _append_command_output(
                result,
                command=cmd,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
            )
            if not run_result.success:
                result["success"] = False
                result["validation_status"] = "failed"
                result["errors"].append(
                    f"{cmd} failed: {run_result.stderr or run_result.stdout}"
                )
                break
            if run_result.stderr and "warning" in run_result.stderr.lower():
                result["warnings"].append(run_result.stderr)

        result["parsed_errors"] = parse_build_errors(result.get("build_output", ""))

        if not canonical and result["validation_status"] == "passed":
            try:
                pkg_content = await adapter.read_file(session_id=session_id, path="package.json")
                scripts = _read_package_scripts_from_text(str(pkg_content))
                if "test" in scripts:
                    test_run = await adapter.run_command(
                        session_id=session_id,
                        command="npm test -- --watchAll=false",
                        timeout_seconds=float(timeout_seconds),
                    )
                    result["test_results"] = test_run.stdout
                    if not test_run.success:
                        result["warnings"].append(f"Tests failed: {test_run.stderr or ''}")
            except Exception:
                pass

        if not canonical and result["validation_status"] == "passed" and start_dev_server:
            try:
                preview_port = int(os.getenv("SANDBOX_PREVIEW_PORT", "3000"))
                try:
                    pkg_content = await adapter.read_file(session_id=session_id, path="package.json")
                    scripts = _read_package_scripts_from_text(str(pkg_content))
                except Exception:
                    scripts = {}

                if "dev" in scripts:
                    server_cmd = f"npm run dev -- --host 0.0.0.0 --port {preview_port}"
                elif "start" in scripts:
                    server_cmd = f"HOST=0.0.0.0 PORT={preview_port} npm start"
                else:
                    server_cmd = f"npm run dev -- --host 0.0.0.0 --port {preview_port}"

                await adapter.run_command(
                    session_id=session_id,
                    command=server_cmd,
                    background=True,
                    timeout_seconds=float(timeout_seconds),
                )
                await asyncio.sleep(3)

                preview_url = await adapter.get_preview_url(
                    session_id=session_id, port=preview_port
                )
                if preview_url:
                    result["preview_url"] = preview_url
            except Exception as server_err:
                result["warnings"].append(f"Dev server not started: {server_err}")

        return result
    except Exception as exc:
        logger.warning("sandbox_validation_failed strategy=%s exception=%s", strategy, type(exc).__name__)
        result.update(success=False, validation_status="failed", errors=["Sandbox validation failed."], preview_url=None)
        return result
    finally:
        if session_id is not None:
            try:
                result["sandbox_terminated"] = bool(await adapter.terminate_session(session_id=session_id))
            except Exception as exc:
                logger.error("sandbox_cleanup_failed session=%s exception=%s", session_id, type(exc).__name__)
                result["sandbox_terminated"] = False
            result["preview_url"] = None
            if not result["sandbox_terminated"]:
                result.update(success=False, validation_status="failed")
                result["errors"].append("Sandbox cleanup could not be confirmed; retry cleanup using the recorded session ID.")


async def _run_local_validation(
    *,
    resolved_files: dict[str, str],
    commands: list[str],
    start_dev_server: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not _local_validation_available():
        return {
            **_base_result(strategy="local", status="failed"),
            "errors": ["Local validation requested but npm is not available on this runtime host"],
        }

    result = _base_result(strategy="local", status="passed")
    env = os.environ.copy()
    env.setdefault("CI", "1")

    try:
        with tempfile.TemporaryDirectory(prefix="mozaiks-app-validation-") as temp_dir:
            root = Path(temp_dir)
            canonical = "app.json" in resolved_files
            _write_files_to_dir(root, _canonical_workspace_files(resolved_files) if canonical else resolved_files)
            if canonical:
                from mozaiksai.resources import resolve_web_shell_root

                shell = resolve_web_shell_root()
                if shell is None or not (shell / "node_modules/vite/bin/vite.js").is_file():
                    raise ValueError("Install shared web shell dependencies before local app validation")
                env.update(_canonical_build_environment(root.as_posix()))
                steps = _canonical_build_steps(root.as_posix(), shell.as_posix(), sandbox=False)
            else:
                steps = [(cmd, str(root)) for cmd in commands]

            for cmd, cwd in steps:
                if not _is_safe_build_command(cmd):
                    if canonical:
                        raise ValueError("Invalid canonical build command configuration")
                    result["warnings"].append(
                        f"Skipped unsafe validation command (contains shell metacharacters): {cmd!r}"
                    )
                    continue
                exit_code, stdout, stderr = await _run_local_command(
                    command=cmd,
                    cwd=Path(cwd),
                    timeout_seconds=timeout_seconds,
                    env=env,
                )
                _append_command_output(result, command=cmd, stdout=stdout, stderr=stderr)
                if exit_code != 0:
                    result["success"] = False
                    result["validation_status"] = "failed"
                    result["errors"].append(f"{cmd} failed: {stderr or stdout}")
                    break
                if stderr and "warning" in stderr.lower():
                    result["warnings"].append(stderr)

            result["parsed_errors"] = parse_build_errors(result.get("build_output", ""))

            if not canonical and result["validation_status"] == "passed":
                scripts = _read_package_scripts_from_dir(root)
                if "test" in scripts:
                    exit_code, stdout, stderr = await _run_local_command(
                        command="npm test -- --watchAll=false",
                        cwd=root,
                        timeout_seconds=timeout_seconds,
                        env=env,
                    )
                    result["test_results"] = stdout
                    if exit_code != 0:
                        result["warnings"].append(f"Tests failed: {stderr or stdout}")

            if start_dev_server and result["validation_status"] == "passed":
                result["warnings"].append(
                    "Local validation does not start a preview server; preview_url is null."
                )

            return result
    except Exception as exc:
        return {
            **result,
            "success": False,
            "validation_status": "failed",
            "errors": [f"Local validation error: {exc}"],
            "preview_url": None,
        }


def _trim_validation_result(result: dict[str, Any]) -> dict[str, Any]:
    app_validation_result = dict(result)
    build_out = app_validation_result.get("build_output")
    try:
        max_chars = int(os.getenv("APP_VALIDATION_BUILD_OUTPUT_MAX_CHARS", "20000"))
    except Exception:
        max_chars = 20000
    if isinstance(build_out, str):
        if max_chars <= 0:
            app_validation_result.pop("build_output", None)
        elif len(build_out) > max_chars:
            app_validation_result["build_output"] = build_out[-max_chars:]
            app_validation_result["build_output_truncated"] = True
    else:
        app_validation_result.pop("build_output", None)
    return app_validation_result


def _persist_validation_context(*, context_variables: Any | None, result: dict[str, Any]) -> None:
    if context_variables is None or not hasattr(context_variables, "set"):
        return
    try:
        context_variables.set("app_validation_status", result.get("validation_status"))
        context_variables.set("app_validation_strategy_used", result.get("validation_strategy"))
        context_variables.set("app_validation_preview_url", result.get("preview_url"))
        context_variables.set("app_validation_result", _trim_validation_result(result))
    except Exception:
        pass


def _context_set(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if isinstance(context_variables, dict):
        context_variables[key] = value
        return
    if not hasattr(context_variables, "set"):
        return
    context_variables.set(key, value)


def _context_get(context_variables: Any | None, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if isinstance(context_variables, dict):
        return detach(context_variables.get(key, default))
    if not hasattr(context_variables, "get"):
        return default
    try:
        return detach(context_variables.get(key, default))
    except Exception:
        return default


def _capability_packs_from_context(context_variables: Any | None) -> list[dict[str, Any]]:
    if context_variables is None or not hasattr(context_variables, "get"):
        return []
    try:
        app_build_plan = detach(context_variables.get("app_build_plan"))
    except Exception:
        app_build_plan = None
    if not isinstance(app_build_plan, dict):
        return []
    packs = app_build_plan.get("capability_packs")
    if not isinstance(packs, list):
        return []
    return [pack for pack in packs if isinstance(pack, dict)]


def _requires_deployment_artifacts(
    generated_files: dict[str, str],
    context_variables: Any | None,
) -> bool:
    deployment_profile = (
        _context_get(context_variables, "deployment_profile", None)
        or _context_get(context_variables, "deploymentProfile", None)
    )
    if str(deployment_profile or "").strip():
        return True
    app_build_plan = _context_get(context_variables, "app_build_plan", None)
    if isinstance(app_build_plan, dict) and str(app_build_plan.get("deployment_profile") or "").strip():
        return True
    deployment_context_keys = (
        "require_deployment_artifacts",
        "includeDockerfiles",
        "include_dockerfiles",
        "includeDeploymentArtifacts",
        "include_deployment_artifacts",
        "include_deployment_workflow",
        "include_workflow",
        "include_compose",
    )
    if any(_is_truthy(_context_get(context_variables, key, False)) for key in deployment_context_keys):
        return True
    deployment_paths = {
        "Dockerfile",
        "docker-compose.yml",
        "deployment.manifest.json",
        ".github/workflows/readiness.yml",
        ".github/workflows/deploy.yml",
    }
    return bool(deployment_paths & set(generated_files))


def _safe_files_map(files: dict[str, str] | None) -> dict[str, str]:
    if not isinstance(files, dict):
        return {}
    safe_files: dict[str, str] = {}
    for raw_path, content in files.items():
        safe = _safe_relpath(str(raw_path))
        if safe:
            safe_files[safe] = str(content)
    return safe_files


def _check_result(
    *,
    check_id: str,
    passed: bool,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "passed": bool(passed),
        "message": message,
        "details": details or {},
    }


def _result_check(result: dict[str, Any], *, default_id: str, default_message: str) -> dict[str, Any]:
    checks = result.get("checks")
    if isinstance(checks, list) and checks and isinstance(checks[0], dict):
        return dict(checks[0])
    return _check_result(
        check_id=default_id,
        passed=bool(result.get("passed")),
        message=default_message,
    )


def _runtime_quality_result(generated_files: dict[str, str]) -> dict[str, Any]:
    from .module_runtime_quality import audit_module_runtime_quality

    runtime_quality_warnings = audit_module_runtime_quality(
        [
            {"filename": path, "content": content}
            for path, content in sorted(generated_files.items())
        ]
    )
    return {
        "contract_version": "1.0",
        "passed": not runtime_quality_warnings,
        "warnings": runtime_quality_warnings,
        "checks": [
            {
                "id": "module_runtime_quality",
                "passed": not runtime_quality_warnings,
                "message": (
                    "Generated module runtime code contains no placeholder runtime logic."
                    if not runtime_quality_warnings
                    else f"{len(runtime_quality_warnings)} generated module runtime quality issue(s) found."
                ),
                "details": {
                    "warning_count": len(runtime_quality_warnings),
                },
            }
        ],
    }


async def _app_runtime_load_result(generated_files: dict[str, str]) -> dict[str, Any]:
    failed_tests: list[dict[str, Any]] = []
    warnings: list[str] = []
    details: dict[str, Any] = {
        "app_name": None,
        "module_names": [],
        "failed_module_names": [],
        "page_names": [],
        "workflow_names": [],
        "subscriptions_loaded": False,
    }

    if not generated_files:
        failed_tests.append(
            {
                "test": "app_runtime_load",
                "error": "No generated files were available for runtime app loading.",
                "fix_suggestion": "Assemble a complete app bundle with app.json before validation or export.",
            }
        )
    else:
        from mozaiksai.core.runtime.app.loader import AppLoader

        with tempfile.TemporaryDirectory(prefix="mozaiks-app-runtime-load-") as tmp:
            app_root = Path(tmp) / "app"
            _write_files_to_dir(app_root, generated_files)
            # Ensure every Python package directory under app_root has an
            # __init__.py so Python treats them as regular packages, not
            # namespace packages.  Namespace packages aggregate paths from all
            # sys.path entries; regular packages resolve from the first match.
            # Missing __init__.py files lead to import failures when sys.path
            # has stale entries left by previous AppLoader.load() calls in the
            # same process (common in test suites).
            # Process bottom-up (reverse sorted) so parent directories like
            # `services/` inherit the marker from child packages that already
            # contain .py files (e.g. `services/integrations/`).
            for dir_path in sorted(app_root.rglob("*"), reverse=True):
                if not dir_path.is_dir() or dir_path == app_root:
                    continue
                init_file = dir_path / "__init__.py"
                if init_file.exists():
                    continue
                has_py = any(f.suffix == ".py" for f in dir_path.iterdir() if f.is_file())
                has_pkg_child = any(
                    (child / "__init__.py").exists()
                    for child in dir_path.iterdir()
                    if child.is_dir()
                )
                if has_py or has_pkg_child:
                    init_file.write_text("", encoding="utf-8")
            # Snapshot global import state so this call is test-isolated.
            _modules_before = dict(sys.modules)
            try:
                loaded = await AppLoader.load(str(app_root))
                details = {
                    "app_name": loaded.definition.name,
                    "module_names": [module.name for module in loaded.modules],
                    "failed_module_names": list(loaded.failed_module_names),
                    "page_names": [page.name for page in loaded.definition.pages],
                    "workflow_names": [workflow.name for workflow in loaded.definition.workflows],
                    "subscriptions_loaded": loaded.subscriptions_config is not None,
                }
                for module_name in loaded.failed_module_names:
                    failed_tests.append(
                        {
                            "test": "app_runtime_module_load",
                            "module": module_name,
                            "path": f"modules/{module_name}/backend/handler.py",
                            "error": (loaded.module_load_errors.get(module_name) or "AppLoader could not load module.").replace(str(app_root), "app"),
                            "fix_suggestion": (
                                "Fix the module contract, companion manifests, handler entrypoint, "
                                "or app-owned service imports so AppLoader.load() can load every module."
                            ),
                        }
                    )
            except Exception as exc:
                failed_tests.append(
                    {
                        "test": "app_runtime_load",
                        "error": f"AppLoader.load() failed: {exc}",
                        "fix_suggestion": (
                            "Ensure app.json, modules/*/module.yaml, contracts/*.yaml, "
                            "backend handlers, and app-level service imports are loadable."
                        ),
                    }
                )
            finally:
                # Remove only this validation workspace's imports. Other
                # workflows can legitimately import modules while load awaits.
                roots = {str(app_root.resolve()), str(app_root.parent.resolve())}
                sys.path[:] = [entry for entry in sys.path if entry not in roots]
                for key, value in list(sys.modules.items()):
                    filename = getattr(value, "__file__", None)
                    if isinstance(filename, str) and Path(filename).is_relative_to(app_root):
                        if key in _modules_before:
                            sys.modules[key] = _modules_before[key]
                        else:
                            sys.modules.pop(key, None)
                for key, value in _modules_before.items():
                    if key not in sys.modules and (
                        key == "services" or key.startswith(("services.", "mozaiks_runtime_module_"))
                    ):
                        sys.modules[key] = value

    passed = not failed_tests
    return {
        "contract_version": "1.0",
        "passed": passed,
        "checks": [
            _check_result(
                check_id="app_runtime_load",
                passed=passed,
                message=(
                    "Generated app bundle loads through AppLoader."
                    if passed
                    else f"{len(failed_tests)} app runtime load issue(s) found."
                ),
                details={
                    **details,
                    "failed_test_count": len(failed_tests),
                },
            )
        ],
        "failed_tests": failed_tests,
        "warnings": warnings,
        "details": details,
    }


async def _agent_backend_integration_result(context_variables: Any | None) -> dict[str, Any]:
    if _context_has_agent_backend(context_variables):
        from .integration_tests import run_integration_tests

        return await run_integration_tests(
            files={},
            context_variables=context_variables,
        )
    return {
        "contract_version": "1.0",
        "passed": True,
        "checks": [
            {
                "id": "agent_backend_integration_not_required",
                "passed": True,
                "message": "No agent backend context is present for this app build.",
                "details": {"blocking": False, "severity": "info"},
            }
        ],
        "warnings": [],
        "failed_tests": [],
    }


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _load_yaml_mapping_for_gate(path: str, content: str, failed_tests: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        parsed = yaml.safe_load(content) or {}
    except Exception as exc:
        failed_tests.append(
            {
                "test": "workflow_integration_yaml_parse",
                "path": path,
                "error": f"{path} could not be parsed as YAML: {exc}",
                "fix_suggestion": "Emit valid YAML for workflow integration module contracts.",
            }
        )
        return None
    if not isinstance(parsed, dict):
        failed_tests.append(
            {
                "test": "workflow_integration_yaml_shape",
                "path": path,
                "error": f"{path} must contain a YAML object.",
                "fix_suggestion": "Emit workflow integration contracts as mappings.",
            }
        )
        return None
    return parsed


def _collect_workflow_integration_wiring(files: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failed_tests: list[dict[str, Any]] = []
    wiring: dict[str, Any] = {
        "workflow_capabilities": [],
        "declared_events": set(),
        "action_emits": set(),
        "capability_reactions": set(),
    }

    for path, content in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if len(parts) == 3 and parts[0] == "modules" and parts[2] == "module.yaml":
            parsed = _load_yaml_mapping_for_gate(path, content, failed_tests)
            if parsed is None:
                continue
            module_id = str((parsed.get("module") or {}).get("id") or parts[1]).strip()
            for action in parsed.get("actions") or []:
                if not isinstance(action, dict):
                    continue
                for event_type in _as_string_list(action.get("emits")):
                    wiring["action_emits"].add(event_type)
            for capability in parsed.get("capabilities") or []:
                if not isinstance(capability, dict) or capability.get("kind") != "workflow":
                    continue
                capability_id = str(capability.get("capability_id") or "").strip()
                target = str(capability.get("target") or "").strip()
                if capability_id:
                    wiring["workflow_capabilities"].append(
                        {
                            "module_id": module_id,
                            "path": path,
                            "capability_id": capability_id,
                            "target": target,
                        }
                    )
        elif len(parts) == 4 and parts[0] == "modules" and parts[2] == "contracts" and parts[3] == "events.yaml":
            parsed = _load_yaml_mapping_for_gate(path, content, failed_tests)
            if parsed is None:
                continue
            for event in parsed.get("events") or []:
                if isinstance(event, dict) and str(event.get("type") or "").strip():
                    wiring["declared_events"].add(str(event["type"]).strip())
        elif len(parts) == 4 and parts[0] == "modules" and parts[2] == "contracts" and parts[3] == "reactions.yaml":
            parsed = _load_yaml_mapping_for_gate(path, content, failed_tests)
            if parsed is None:
                continue
            for reaction in parsed.get("reactions") or []:
                if not isinstance(reaction, dict):
                    continue
                event_type = str(reaction.get("event_type") or "").strip()
                target = reaction.get("target")
                if not event_type or not isinstance(target, dict):
                    continue
                if target.get("kind") == "capability" and str(target.get("capability_id") or "").strip():
                    wiring["capability_reactions"].add((event_type, str(target["capability_id"]).strip()))

    return wiring, failed_tests


def validate_workflow_integration_contract(
    generated_files: dict[str, str],
    context_variables: Any | None,
) -> dict[str, Any]:
    metadata = workflow_integration_metadata_from_context(context_variables)
    if not metadata:
        return {
            "contract_version": "1.0",
            "passed": True,
            "checks": [
                _check_result(
                    check_id="workflow_integration_not_required",
                    passed=True,
                    message="No workflow_bundle integration metadata is present for this app build.",
                    details={"blocking": False, "severity": "info"},
                )
            ],
            "failed_tests": [],
            "warnings": [],
            "workflow_count": 0,
        }

    wiring, failed_tests = _collect_workflow_integration_wiring(generated_files)
    workflow_capabilities = wiring["workflow_capabilities"]
    declared_events = wiring["declared_events"]
    action_emits = wiring["action_emits"]
    capability_reactions = wiring["capability_reactions"]
    expected_workflow_pairs: set[tuple[str, str]] = set()
    expected_capability_ids_by_event: dict[str, set[str]] = {}
    declared_workflow_capability_ids = {
        capability["capability_id"]
        for capability in workflow_capabilities
        if str(capability.get("capability_id") or "").strip()
    }

    for workflow in metadata.get("workflows") or []:
        if not isinstance(workflow, dict):
            continue
        workflow_name = str(workflow.get("workflow_name") or "").strip()
        capability_id = str(workflow.get("capability_id") or "").strip()
        if not workflow_name or not capability_id:
            continue
        expected_workflow_pairs.add((capability_id, workflow_name))
        for trigger_event in workflow.get("trigger_events") or []:
            if not isinstance(trigger_event, dict):
                continue
            event_type = str(trigger_event.get("event_type") or "").strip()
            if event_type:
                expected_capability_ids_by_event.setdefault(event_type, set()).add(capability_id)

    for capability in workflow_capabilities:
        capability_id = str(capability.get("capability_id") or "").strip()
        target = str(capability.get("target") or "").strip()
        if not capability_id or not target:
            continue
        if (capability_id, target) not in expected_workflow_pairs:
            failed_tests.append(
                {
                    "test": "workflow_capability_not_in_metadata",
                    "path": capability.get("path") or "modules/*/module.yaml",
                    "error": (
                        f"Workflow capability {capability_id!r} targets {target!r}, but that "
                        "capability/target pair is not present in AgentGenerator workflow metadata."
                    ),
                    "fix_suggestion": (
                        "Remove invented workflow capabilities, or regenerate from the current "
                        "AgentGenerator workflow_integration_metadata."
                    ),
                }
            )

    for workflow in metadata.get("workflows") or []:
        if not isinstance(workflow, dict):
            continue
        workflow_name = str(workflow.get("workflow_name") or "").strip()
        capability_id = str(workflow.get("capability_id") or "").strip()
        if not workflow_name or not capability_id:
            continue
        matching_capability = next(
            (
                capability
                for capability in workflow_capabilities
                if capability["capability_id"] == capability_id
                and capability["target"] == workflow_name
            ),
            None,
        )
        if matching_capability is None:
            failed_tests.append(
                {
                    "test": "workflow_capability_declared",
                    "path": "modules/*/module.yaml",
                    "error": (
                        f"Workflow {workflow_name!r} must be declared as a module workflow capability "
                        f"with capability_id {capability_id!r}."
                    ),
                    "fix_suggestion": (
                        "Add a capabilities[] entry with kind: workflow, "
                        f"capability_id: {capability_id}, and target: {workflow_name}."
                    ),
                }
            )
        for trigger_event in workflow.get("trigger_events") or []:
            if not isinstance(trigger_event, dict):
                continue
            event_type = str(trigger_event.get("event_type") or "").strip()
            if not event_type:
                continue
            trigger_capability_id = str(trigger_event.get("capability_id") or "").strip()
            if trigger_capability_id and trigger_capability_id != capability_id:
                failed_tests.append(
                    {
                        "test": "workflow_trigger_capability_id_mismatch",
                        "path": "workflow_integration_metadata",
                        "error": (
                            f"Workflow trigger event {event_type!r} declares capability_id "
                            f"{trigger_capability_id!r}, but workflow {workflow_name!r} uses "
                            f"capability_id {capability_id!r}."
                        ),
                        "fix_suggestion": (
                            "Keep trigger_events[].capability_id aligned with the workflow "
                            "capability_id from AgentGenerator metadata before AppGenerator wiring."
                        ),
                    }
                )
            if event_type not in declared_events:
                failed_tests.append(
                    {
                        "test": "workflow_trigger_event_declared",
                        "path": "modules/*/contracts/events.yaml",
                        "error": (
                            f"Workflow trigger event {event_type!r} is not declared in contracts/events.yaml."
                        ),
                        "fix_suggestion": (
                            "Declare the domain/platform/hosted event in the owning module's "
                            "contracts/events.yaml."
                        ),
                    }
                )
            if event_type not in action_emits:
                failed_tests.append(
                    {
                        "test": "workflow_trigger_event_emitted",
                        "path": "modules/*/module.yaml",
                        "error": (
                            f"Workflow trigger event {event_type!r} is not emitted by any module action."
                        ),
                        "fix_suggestion": (
                            "Add the event type to the emits[] list on the action that commits "
                            "the triggering domain fact."
                        ),
                    }
                )
            if (event_type, capability_id) not in capability_reactions:
                failed_tests.append(
                    {
                        "test": "workflow_trigger_reaction_declared",
                        "path": "modules/*/contracts/reactions.yaml",
                        "error": (
                            f"Workflow trigger event {event_type!r} is not routed to capability "
                            f"{capability_id!r} by contracts/reactions.yaml."
                        ),
                        "fix_suggestion": (
                            "Add a reactions[] entry with event_type, target.kind: capability, "
                            f"and target.capability_id: {capability_id}."
                        ),
                    }
                )
            expected_capability_ids = expected_capability_ids_by_event.get(event_type) or {capability_id}
            for routed_event, routed_capability_id in sorted(capability_reactions):
                if routed_event != event_type:
                    continue
                if routed_capability_id not in declared_workflow_capability_ids:
                    continue
                if routed_capability_id in expected_capability_ids:
                    continue
                failed_tests.append(
                    {
                        "test": "workflow_trigger_ambiguous_workflow_reaction",
                        "path": "modules/*/contracts/reactions.yaml",
                        "error": (
                            f"Workflow trigger event {event_type!r} is also routed to workflow "
                            f"capability {routed_capability_id!r}, which is not part of the "
                            "AgentGenerator metadata for that event."
                        ),
                        "fix_suggestion": (
                            "Route AgentGenerator trigger events only to the workflow capability ids "
                            "declared in workflow_integration_metadata."
                        ),
                    }
                )

    passed = not failed_tests
    return {
        "contract_version": "1.0",
        "passed": passed,
        "checks": [
            _check_result(
                check_id="workflow_integration_contract",
                passed=passed,
                message=(
                    "Generated app bundle wires AgentGenerator workflow metadata through module capabilities, events, and reactions."
                    if passed
                    else f"{len(failed_tests)} workflow integration issue(s) found."
                ),
                details={
                    "workflow_count": len(metadata.get("workflows") or []),
                    "workflow_capability_count": len(workflow_capabilities),
                    "failed_test_count": len(failed_tests),
                },
            )
        ],
        "failed_tests": failed_tests,
        "warnings": [],
        "metadata": metadata,
    }


def _wiring_repair_errors(
    wiring_result: dict[str, Any], generated_files: dict[str, str]
) -> list[str]:
    """Phrase orphaned endpoints as repairable per-page errors.

    A wiring failure already blocks acceptance, but it was never handed to
    _prepare_bundle_repair, so the build failed without attempting a fix. The
    repair loop already routes ui/pages/* to AppSchemaAgent, is bounded by
    max_attempts, and stops on a repeated failure fingerprint - the errors just
    never arrived.

    Each message names the declared actions. A page agent told only "unknown
    action" would guess again, which is how /api/habits and a delete_habit that
    was never declared reached a bundle whose module contract was right there.
    """
    from mozaiksai.core.workflow.generator_support.page_plan_utils import (
        module_action_index,
    )

    if wiring_result.get("passed"):
        return []
    orphaned = wiring_result.get("orphaned_pages") or []
    if not orphaned:
        return []

    index = module_action_index(generated_files)
    declared = sorted(
        f"/api/modules/{module_id}/{action}"
        for module_id, actions in index.items()
        for action in actions
    )
    available = ", ".join(declared) if declared else "none declared"

    errors: list[str] = []
    for item in orphaned:
        page = str(item.get("page") or "").strip()
        if not page:
            continue
        section = str(item.get("section") or "").strip()
        where = f" section {section!r}" if section else ""
        errors.append(
            f"ui/pages/{page}.yaml:{where} endpoint {item.get('endpoint')!r} "
            f"references no declared module action. Declared actions: {available}. "
            "Bind the section to one of them, or remove the section if the app "
            "does not need it. Do not invent an action id."
        )
    return errors


async def run_app_bundle_acceptance_gate(
    *,
    files: dict[str, str] | None = None,
    context_variables: Any | None = None,
    capability_packs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the deterministic app-bundle acceptance gate.

    This is the no-live-call boundary before an app bundle can be exported,
    registered as validated, or promoted.
    """

    explicit_files = _safe_files_map(files)
    if files is not None:
        generated_files = explicit_files
    else:
        generated_files = admitted_app_file_map(context_variables)
    selected_capability_packs = (
        [pack for pack in capability_packs if isinstance(pack, dict)]
        if isinstance(capability_packs, list)
        else _capability_packs_from_context(context_variables)
    )

    _context_set(context_variables, "generated_files", generated_files)

    from mozaiksai.core.validation.functional_generated_app import (
        scan_functional_generated_app,
    )

    from .generated_bundle_scanner import scan_generated_bundle
    from .validate_wiring import validate_wiring

    required_bundle_errors: list[str] = []
    if not generated_files:
        required_bundle_errors.append("No generated files were available for app-bundle acceptance.")
    if "app.json" not in generated_files:
        required_bundle_errors.append("Generated app bundles must include app.json.")

    scanner_errors = scan_generated_bundle(
        generated_files,
        capability_packs=selected_capability_packs,
        planned_data_contract=(_context_get(context_variables, "app_build_plan", {}) or {}).get("data_contract"),
        require_deployment_artifacts=_requires_deployment_artifacts(
            generated_files,
            context_variables,
        ),
    )
    planned_diagnostics = planned_artifact_diagnostics(context_variables, generated_files)
    all_scan_errors = [*required_bundle_errors, *scanner_errors]
    bundle_scan_result = {
        "contract_version": "1.0",
        "passed": not all_scan_errors,
        "errors": all_scan_errors,
        "checks": [
            _check_result(
                check_id="generated_bundle_scan",
                passed=not all_scan_errors,
                message=(
                    "Generated app bundle passed canonical path, data, secret, and managed-capability boundary scanning."
                    if not all_scan_errors
                    else "Generated app bundle failed canonical scanner checks."
                ),
                details={
                    "error_count": len(all_scan_errors),
                    "errors": all_scan_errors,
                },
            )
        ],
    }

    agent_integration_result = await _agent_backend_integration_result(context_variables)
    # Wiring must inspect the accepted snapshot, not stale pages or a context write-back.
    wiring_result = await validate_wiring(context_variables={"generated_files": generated_files})
    _context_set(context_variables, "wiring_validation_passed", wiring_result["passed"])
    _context_set(context_variables, "wiring_validation_result", wiring_result)
    module_implementation_result = validate_module_implementation_contract(generated_files)
    runtime_quality_result = _runtime_quality_result(generated_files)
    functional_diagnostics = scan_functional_generated_app(
        generated_files,
        capability_packs=selected_capability_packs,
    )
    functional_errors = [item for item in functional_diagnostics if item.severity == "error"]
    functional_result = {
        "contract_version": "1.0",
        "passed": not functional_errors,
        "checks": [
            _check_result(
                check_id="generated_app_functional_completeness",
                passed=not functional_errors,
                message=(
                    "Generated app routes, module references, capability facades, and expected handlers are functionally coherent."
                    if not functional_errors
                    else f"{len(functional_errors)} generated app functional completeness issue(s) found."
                ),
                details={
                    "diagnostic_count": len(functional_diagnostics),
                    "error_count": len(functional_errors),
                    "diagnostics": [
                        {
                            "code": item.code,
                            "message": item.message,
                            "path": item.path,
                            "severity": item.severity,
                        }
                        for item in functional_diagnostics
                    ],
                },
            )
        ],
        "failed_tests": [
            {
                "test": item.code,
                "path": item.path,
                "error": item.message,
                "fix_suggestion": (
                    "Regenerate or repair the canonical app artifact so declared "
                    "routes, module actions, workflow/tool references, capability "
                    "facades, and implementation files resolve before export."
                ),
            }
            for item in functional_errors
        ],
        "warnings": [
            item.message for item in functional_diagnostics if item.severity == "warning"
        ],
        "diagnostics": [
            {
                "code": item.code,
                "message": item.message,
                "path": item.path,
                "severity": item.severity,
            }
            for item in functional_diagnostics
        ],
    }
    workflow_integration_result = validate_workflow_integration_contract(
        generated_files,
        context_variables,
    )
    app_runtime_load_result = await _app_runtime_load_result(generated_files)

    completeness_result = {
        "passed": not planned_diagnostics,
        "diagnostics": planned_diagnostics,
        "failed_tests": [{"test": item["code"], **item} for item in planned_diagnostics],
    }
    schema_quality_passed = (
        not _is_truthy(_context_get(context_variables, "app_schema_ready"))
        or _context_get(context_variables, "app_ui_quality_status") == "passed"
    )
    schema_task = _context_get(context_variables, "current_build_task", {}) or {}
    schema_quality_result = {
        "passed": schema_quality_passed,
        "failed_tests": [] if schema_quality_passed else [{
            "test": "app_schema_quality",
            "path": next((path for path in schema_task.get("owned_paths", []) if path.startswith("ui/pages/")), "app.json"),
            "error": "Schema quality has not passed: " + "; ".join(
                str(warning) for warning in _context_get(context_variables, "app_ui_quality_warnings", []) or []
            ),
        }],
    }
    subresults = {
        "planned_completeness": completeness_result,
        "schema_quality": schema_quality_result,
        "bundle_scan": bundle_scan_result,
        "agent_backend": agent_integration_result,
        "module_wiring": wiring_result,
        "module_implementation": module_implementation_result,
        "module_runtime_quality": runtime_quality_result,
        "functional_completeness": functional_result,
        "workflow_integration": workflow_integration_result,
        "app_runtime_load": app_runtime_load_result,
    }
    passed_by_check = {
        name: bool(result.get("passed"))
        for name, result in subresults.items()
    }
    failed = sorted(name for name, passed in passed_by_check.items() if not passed)
    completed = sorted(name for name, passed in passed_by_check.items() if passed)
    acceptance_passed = not failed

    failed_tests: list[dict[str, Any]] = []
    warnings: list[str] = []
    for name, result in subresults.items():
        for item in result.get("failed_tests") or []:
            if isinstance(item, dict):
                failed_tests.append({"gate": name, **item})
        for warning in result.get("warnings") or []:
            warnings.append(f"{name}: {warning}")
        if name == "bundle_scan":
            for error in result.get("errors") or []:
                failed_tests.append(
                    {
                        "gate": name,
                        "test": "generated_bundle_scan",
                        "error": str(error),
                        "fix_suggestion": (
                            "Regenerate the app bundle using only canonical app files "
                            "and declared module/page/data contracts."
                        ),
                    }
                )

    result = {
        "contract_version": "1.0",
        "status": "passed" if acceptance_passed else "failed",
        "passed": acceptance_passed,
        "checks": [
            _result_check(completeness_result, default_id="planned_completeness", default_message="Approved plan completeness checked."),
            _result_check(schema_quality_result, default_id="schema_quality", default_message="Schema quality checked."),
            _result_check(bundle_scan_result, default_id="generated_bundle_scan", default_message="Generated bundle scan completed."),
            _result_check(agent_integration_result, default_id="agent_backend", default_message="Agent backend integration check completed."),
            _result_check(wiring_result, default_id="module_wiring", default_message="Module wiring check completed."),
            _result_check(module_implementation_result, default_id="module_implementation", default_message="Module implementation check completed."),
            _result_check(runtime_quality_result, default_id="module_runtime_quality", default_message="Module runtime quality check completed."),
            _result_check(functional_result, default_id="functional_completeness", default_message="Functional completeness check completed."),
            _result_check(workflow_integration_result, default_id="workflow_integration", default_message="Workflow integration check completed."),
            _result_check(app_runtime_load_result, default_id="app_runtime_load", default_message="App runtime load check completed."),
        ],
        "validation_evidence": {
            "completed": completed,
            "failed": failed,
        },
        "failed_tests": failed_tests,
        "warnings": warnings,
        **subresults,
    }
    repair_diagnostics = [
        *planned_diagnostics,
        *[{"error": error} for error in all_scan_errors],
        *[{"error": error} for error in _wiring_repair_errors(wiring_result, generated_files)],
        *[{"error": error} for error in runtime_quality_result.get("warnings", [])],
        *[
            {**item, "error": f"{item.get('test', 'validation')}: {item['error']}"}
            for check in (module_implementation_result, app_runtime_load_result, functional_result, workflow_integration_result, schema_quality_result)
            for item in check.get("failed_tests", [])
        ],
    ]
    recovery_request = prepare_task_recovery(context_variables)
    bundle_repair = _prepare_bundle_repair(
        {"passed": acceptance_passed, "diagnostics": repair_diagnostics},
        context_variables, select_repairs=recovery_request is None,
    )
    result["bundle_repair"] = bundle_repair
    result["task_recovery_request"] = recovery_request
    if acceptance_passed:
        result["snapshot_digest"] = artifact_snapshot_digest(context_variables, generated_files)

    _context_set(context_variables, "bundle_scan_passed", bundle_scan_result["passed"])
    _context_set(context_variables, "bundle_scan_result", bundle_scan_result)
    _context_set(context_variables, "wiring_validation_passed", wiring_result.get("passed"))
    _context_set(context_variables, "wiring_validation_result", wiring_result)
    _context_set(context_variables, "module_implementation_validation_passed", module_implementation_result.get("passed"))
    _context_set(context_variables, "module_implementation_validation_result", module_implementation_result)
    _context_set(context_variables, "module_runtime_quality_status", "passed" if runtime_quality_result["passed"] else "blocked")
    _context_set(context_variables, "module_runtime_quality_warnings", runtime_quality_result.get("warnings") or [])
    _context_set(context_variables, "module_runtime_quality_result", runtime_quality_result)
    _context_set(context_variables, "generated_app_functional_completeness_passed", functional_result.get("passed"))
    _context_set(context_variables, "generated_app_functional_completeness_result", functional_result)
    _context_set(context_variables, "workflow_integration_validation_passed", workflow_integration_result.get("passed"))
    _context_set(context_variables, "workflow_integration_validation_result", workflow_integration_result)
    _context_set(context_variables, "app_runtime_load_passed", app_runtime_load_result.get("passed"))
    _context_set(context_variables, "app_runtime_load_result", app_runtime_load_result)
    _context_set(context_variables, "integration_tests_passed", acceptance_passed)
    _context_set(context_variables, "integration_test_result", {
        **subresults,
        "bundle_repair": bundle_repair,
        "passed": acceptance_passed,
    })
    _context_set(context_variables, "app_bundle_acceptance_status", result["status"])
    _context_set(context_variables, "app_bundle_acceptance_result", result)
    _context_set(context_variables, "app_bundle_validation_evidence", result["validation_evidence"])

    return result


async def validate_app_build(
    files: dict[str, str],
    commands: list[str] | None = None,
    start_dev_server: bool = True,
    timeout_seconds: int = 120,
    validation_strategy: str | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    try:
        env_timeout = os.getenv("E2B_TIMEOUT")
        if env_timeout and timeout_seconds == 120:
            timeout_seconds = int(env_timeout)
    except Exception:
        pass

    workflow_name = "AppGenerator"
    chat_id = None
    app_id = None
    try:
        if context_variables is not None and hasattr(context_variables, "get"):
            workflow_name = context_variables.get("workflow_name") or workflow_name
            chat_id = context_variables.get("chat_id")
            app_id = context_variables.get("app_id")
    except Exception:
        pass

    wf_logger = get_workflow_logger(workflow_name=workflow_name, chat_id=chat_id, app_id=app_id)
    resolved_files, chat_id, app_id = await _resolve_files(
        files=files,
        context_variables=context_variables,
        wf_logger=wf_logger,
    )
    if not resolved_files:
        result = {
            **_base_result(strategy="skip", status="failed"),
            "errors": ["No files provided/resolved for app validation"],
        }
        _persist_validation_context(context_variables=context_variables, result=result)
        return result

    if commands is None:
        commands = ["npm install", "npm run build"]

    try:
        strategy, strategy_reason = resolve_app_validation_strategy(
            requested=validation_strategy,
            context_value=(
                context_variables.get("app_validation_strategy")
                if context_variables is not None and hasattr(context_variables, "get")
                else None
            ),
        )
    except Exception as exc:
        result = {
            **_base_result(strategy="skip", status="failed"),
            "errors": [str(exc)],
        }
        _persist_validation_context(context_variables=context_variables, result=result)
        return result

    if strategy == "skip":
        result = _base_result(strategy="skip", status="skipped")
        result["strategy_reason"] = strategy_reason
        result["warnings"].append("App validation was explicitly skipped by strategy.")
        _persist_validation_context(context_variables=context_variables, result=result)
        return result

    if strategy == "local":
        result = await _run_local_validation(
            resolved_files=resolved_files,
            commands=list(commands),
            start_dev_server=bool(start_dev_server),
            timeout_seconds=timeout_seconds,
        )
        result["strategy_reason"] = strategy_reason
        _persist_validation_context(context_variables=context_variables, result=result)
        return result

    # e2b and docker both route through the SandboxPort abstraction
    result = await _run_sandbox_validation(
        strategy=strategy,
        resolved_files=resolved_files,
        commands=list(commands),
        start_dev_server=bool(start_dev_server),
        timeout_seconds=timeout_seconds,
        session_metadata={
            "purpose": "app_validation",
            **({"app_id": str(app_id)} if app_id else {}),
            **({"chat_id": str(chat_id)} if chat_id else {}),
        },
    )
    result["strategy_reason"] = strategy_reason
    _persist_validation_context(context_variables=context_variables, result=result)
    return result


def _context_has_agent_backend(context_variables: Any | None) -> bool:
    if context_variables is None or not hasattr(context_variables, "get"):
        return False
    for key in (
        "agent_websocket_url",
        "agent_api_url",
        "agent_repo_url",
        "agent_names",
        "available_agents",
        "available_tools",
        "tool_names",
    ):
        try:
            value = detach(context_variables.get(key))
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (list, dict)) and value:
            return True
    return False


async def validate_app_bundle_from_request(
    AppValidationRequest: dict[str, Any],
    agent_message: str | None = None,
    context_variables: Any | None = None,
) -> dict[str, Any]:
    """Auto-run deterministic app validation from AppValidationAgent's request.

    AppValidationAgent should not call validation tools directly. It emits a strict
    request, then this auto tool owns the runtime checks and persists the resulting
    gate fields into context_variables for routing.
    """

    request = AppValidationRequest if isinstance(AppValidationRequest, dict) else {}
    commands = request.get("commands")
    if not isinstance(commands, list):
        commands = None

    acceptance_result = await run_app_bundle_acceptance_gate(context_variables=context_variables)
    if acceptance_result["passed"]:
        validation = await validate_app_build(
            files=admitted_app_file_map(context_variables), commands=commands,
            start_dev_server=bool(request.get("start_dev_server", True)),
            timeout_seconds=int(request.get("timeout_seconds") or 120),
            validation_strategy=request.get("validation_strategy"), context_variables=context_variables,
        )
    else:
        validation = _base_result(strategy="skip", status="pending")
        validation["success"] = False
        validation["strategy_reason"] = "Deterministic acceptance must pass before validation-environment execution."
        _persist_validation_context(context_variables=context_variables, result=validation)
    agent_integration_result = acceptance_result["agent_backend"]
    wiring_result = acceptance_result["module_wiring"]
    module_implementation_result = acceptance_result["module_implementation"]
    runtime_quality_result = acceptance_result["module_runtime_quality"]
    functional_result = acceptance_result["functional_completeness"]
    workflow_integration_result = acceptance_result["workflow_integration"]
    app_runtime_load_result = acceptance_result["app_runtime_load"]
    bundle_repair = acceptance_result.get("bundle_repair")
    validation_passed = str(validation.get("validation_status") or "").strip().lower() in {"passed", "skipped"}
    combined_passed = bool(validation_passed and acceptance_result.get("passed"))
    _context_set(context_variables, "integration_tests_passed", combined_passed)
    integration_test_result = {
        "bundle_scan": acceptance_result["bundle_scan"],
        "agent_backend": agent_integration_result,
        "module_wiring": wiring_result,
        "module_implementation": module_implementation_result,
        "module_runtime_quality": runtime_quality_result,
        "functional_completeness": functional_result,
        "workflow_integration": workflow_integration_result,
        "app_runtime_load": app_runtime_load_result,
        "bundle_repair": bundle_repair,
        "passed": combined_passed,
    }
    _context_set(context_variables, "integration_test_result", integration_test_result)

    return {
        "status": "success" if combined_passed else "failed",
        "message": agent_message or "App validation and integration gates completed.",
        "app_validation_result": validation,
        "app_bundle_acceptance_result": acceptance_result,
        "bundle_scan_result": acceptance_result["bundle_scan"],
        "agent_backend_integration_result": agent_integration_result,
        "wiring_validation_result": wiring_result,
        "module_implementation_validation_result": module_implementation_result,
        "module_runtime_quality_result": runtime_quality_result,
        "generated_app_functional_completeness_result": functional_result,
        "workflow_integration_validation_result": workflow_integration_result,
        "app_runtime_load_result": app_runtime_load_result,
        "bundle_repair": bundle_repair,
        "integration_tests_passed": combined_passed,
    }


__all__ = [
    "_is_safe_build_command",
    "parse_build_errors",
    "run_app_bundle_acceptance_gate",
    "validate_module_implementation_contract",
    "validate_workflow_integration_contract",
    "validate_app_build",
    "validate_app_bundle_from_request",
]

