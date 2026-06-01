"""
App validation tool for generated applications.

This tool can:
- resolve generated files from an explicit `files` mapping or persisted agent outputs
- validate the generated app with an explicit strategy: `e2b`, `local`, or `skip`
- run build/test commands
- optionally start a preview server for the E2B strategy
"""


import ast
import asyncio
import builtins
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import yaml
from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
    local_app_validation_available,
    resolve_app_validation_strategy,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    collect_generated_app_file_map,
    extract_code_file_map_from_payload,
)

try:
    from e2b_code_interpreter import Sandbox  # type: ignore
except Exception:  # pragma: no cover
    Sandbox = None  # type: ignore

def _local_validation_available() -> bool:
    return local_app_validation_available()


def _base_result(*, strategy: str, status: str) -> Dict[str, Any]:
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


def _create_sandbox(*, timeout_seconds: int) -> Any:
    if Sandbox is None:
        raise RuntimeError("Sandbox SDK not available")

    create_fn = getattr(Sandbox, "create", None)
    if callable(create_fn):
        return create_fn(timeout=timeout_seconds)

    try:
        return Sandbox(api_key=os.getenv("E2B_API_KEY", "").strip(), timeout=timeout_seconds)
    except TypeError:
        return Sandbox(timeout_seconds)


def _sandbox_filesystem(sandbox: Any) -> Any:
    return getattr(sandbox, "files", None) or getattr(sandbox, "filesystem", None)


def _sandbox_run_command(sandbox: Any, cmd: str, *, background: bool = False) -> Any:
    commands = getattr(sandbox, "commands", None)
    if commands is not None and callable(getattr(commands, "run", None)):
        return commands.run(cmd, background=background)
    process = getattr(sandbox, "process", None)
    if process is not None and callable(getattr(process, "start", None)):
        return process.start(cmd, background=background)
    raise AttributeError("Sandbox has no command runner")


def _sandbox_get_host(sandbox: Any, port: int) -> Optional[str]:
    fn = getattr(sandbox, "get_host", None)
    if callable(fn):
        try:
            return str(fn(port))
        except Exception:
            return None

    fn = getattr(sandbox, "get_hostname", None)
    if callable(fn):
        try:
            return str(fn(port))
        except Exception:
            return None
    return None


def _safe_relpath(raw: str) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    path = raw.replace("\\", "/").strip()
    if not path or path.startswith("/"):
        return None
    p = PurePosixPath(path)
    if p.is_absolute():
        return None
    if any(part in {".."} for part in p.parts):
        return None
    return str(p)


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "passed", "ready"}
    return bool(value)


def _extract_code_files(collected: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for _agent_name, data in (collected or {}).items():
        if not isinstance(data, dict):
            continue
        out.update(extract_code_file_map_from_payload(data))
    return out


def _append_command_output(result: Dict[str, Any], *, command: str, stdout: str, stderr: str) -> None:
    result["build_output"] += f"\n=== {command} ===\n"
    result["build_output"] += stdout or ""
    if stderr:
        result["build_output"] += "\n" + stderr


def _read_package_scripts_from_text(package_text: str) -> Dict[str, Any]:
    try:
        pkg = json.loads(package_text) if isinstance(package_text, str) else {}
    except Exception:
        pkg = {}
    scripts = pkg.get("scripts") if isinstance(pkg, dict) else {}
    return scripts if isinstance(scripts, dict) else {}


def _read_package_scripts_from_dir(root: Path) -> Dict[str, Any]:
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
    env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError(f"Command timed out after {timeout_seconds}s: {command}")

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    return int(process.returncode or 0), stdout, stderr


def parse_build_errors(build_output: str) -> List[Dict[str, Any]]:
    if not isinstance(build_output, str) or not build_output:
        return []

    errors: List[Dict[str, Any]] = []

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
    files: Optional[Dict[str, str]],
    context_variables: Optional[Any],
    wf_logger,
) -> Tuple[Dict[str, str], Optional[str], Optional[str]]:
    if isinstance(files, dict) and files:
        safe_files: Dict[str, str] = {}
        for raw_path, content in files.items():
            safe = _safe_relpath(str(raw_path))
            if not safe:
                continue
            safe_files[safe] = str(content)
        return safe_files, None, None

    chat_id = None
    app_id = None
    try:
        if context_variables is not None and hasattr(context_variables, "get"):
            chat_id = context_variables.get("chat_id")
            app_id = context_variables.get("app_id")
            ctx_files = context_variables.get("generated_files")
            if isinstance(ctx_files, dict) and ctx_files:
                safe_ctx: Dict[str, str] = {}
                for raw_path, content in ctx_files.items():
                    safe = _safe_relpath(str(raw_path))
                    if not safe:
                        continue
                    safe_ctx[safe] = str(content)
                if safe_ctx:
                    return safe_ctx, chat_id, app_id
            if _is_truthy(context_variables.get("app_schema_ready")):
                schema_files = collect_generated_app_file_map(
                    context_variables.get("generated_app_dir")
                )
                if schema_files:
                    return schema_files, chat_id, app_id
    except Exception:
        pass

    if not chat_id or not app_id:
        return {}, chat_id, app_id

    pm = AG2PersistenceManager()
    collected = await pm.gather_latest_agent_jsons(chat_id=str(chat_id), app_id=str(app_id))
    resolved = _extract_code_files(collected)
    if not resolved:
        wf_logger.warning("No code_files found in persisted agent outputs for validation.")
    return resolved, str(chat_id), str(app_id)


def _write_files_to_dir(root: Path, files_map: Dict[str, str]) -> None:
    for rel_path, content in files_map.items():
        safe = _safe_relpath(rel_path)
        if not safe:
            continue
        out_path = root / safe
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(str(content), encoding="utf-8")


def _generated_files_from_context(context_variables: Optional[Any]) -> Dict[str, str]:
    if context_variables is None or not hasattr(context_variables, "get"):
        return {}
    try:
        raw = context_variables.get("generated_files")
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        return {}

    files: Dict[str, str] = {}
    for raw_path, content in raw.items():
        safe = _safe_relpath(str(raw_path))
        if safe:
            files[safe] = str(content)
    return files


def _normalize_module_yaml(path: str, content: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    failed: List[Dict[str, Any]] = []
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


def _iter_module_yamls(files: Dict[str, str]) -> Iterable[Tuple[str, str]]:
    for path, content in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if len(parts) == 3 and parts[0] == "modules" and parts[2] == "module.yaml":
            yield path, content


def _iter_backend_python(files: Dict[str, str]) -> Iterable[Tuple[str, str]]:
    for path, content in sorted(files.items()):
        parts = PurePosixPath(path).parts
        if len(parts) >= 4 and parts[0] == "modules" and parts[2] == "backend" and path.endswith(".py"):
            yield path, content


def _parse_python(path: str, content: str) -> Tuple[Optional[ast.Module], Optional[Dict[str, Any]]]:
    try:
        return ast.parse(content, filename=path), None
    except SyntaxError as exc:
        return None, {
            "test": "backend_python_syntax",
            "path": path,
            "error": f"{path}:{exc.lineno or 0}: Python syntax error: {exc.msg}",
            "fix_suggestion": "Emit syntactically valid Python for generated module backend files.",
        }


def _defined_module_names(tree: ast.Module) -> Set[str]:
    names: Set[str] = set(dir(builtins))
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


def _module_level_name_warnings(path: str, tree: ast.Module) -> List[Dict[str, Any]]:
    defined = _defined_module_names(tree)
    failed: List[Dict[str, Any]] = []
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


def _backend_pass_statement_failures(path: str, tree: ast.Module) -> List[Dict[str, Any]]:
    failed: List[Dict[str, Any]] = []
    parents: Dict[ast.AST, ast.AST] = {}
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
) -> Optional[Dict[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        return {
            child.name: child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
    return None


def _all_method_nodes(tree: ast.Module) -> Dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    methods: Dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.setdefault(child.name, child)
    return methods


def _input_schema_required_fields(action: Dict[str, Any]) -> Set[str]:
    input_schema = action.get("input_schema")
    if not isinstance(input_schema, dict):
        return set()
    required = input_schema.get("required") or []
    if not isinstance(required, list):
        return set()
    return {str(item).strip() for item in required if str(item).strip()}


def _input_schema_declared_fields(action: Dict[str, Any]) -> Set[str]:
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
    action: Dict[str, Any],
    handler_path: str,
    class_name: str,
    action_id: str,
) -> List[Dict[str, Any]]:
    failed: List[Dict[str, Any]] = []
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


def _method_params_keys(method_node: ast.FunctionDef | ast.AsyncFunctionDef) -> Set[str]:
    keys: Set[str] = set()
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


def validate_module_implementation_contract(files: Dict[str, str]) -> Dict[str, Any]:
    """Validate assembled module contracts against generated backend code.

    This is the final deterministic app validation boundary. Agent-local quality
    gates can miss task-batch drift; this check validates the assembled
    ``generated_files`` bundle that DownloadAgent would otherwise package.
    """

    failed_tests: List[Dict[str, Any]] = []
    warnings: List[str] = []
    module_reports: List[Dict[str, Any]] = []
    parsed_backend: Dict[str, ast.Module] = {}

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

        methods = _class_method_nodes(handler_tree, class_name)
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


async def _run_e2b_validation(
    *,
    resolved_files: Dict[str, str],
    commands: List[str],
    start_dev_server: bool,
    timeout_seconds: int,
) -> Dict[str, Any]:
    e2b_api_key = os.getenv("E2B_API_KEY", "").strip()
    if not e2b_api_key:
        return {
            **_base_result(strategy="e2b", status="failed"),
            "errors": ["E2B_API_KEY not configured"],
        }

    if Sandbox is None:
        return {
            **_base_result(strategy="e2b", status="failed"),
            "errors": ["e2b_code_interpreter is not installed"],
        }

    result = _base_result(strategy="e2b", status="passed")
    sandbox = None
    try:
        sandbox = _create_sandbox(timeout_seconds=timeout_seconds)
        fs = _sandbox_filesystem(sandbox)
        if fs is None:
            raise RuntimeError("Sandbox filesystem unavailable")

        for filepath, content in resolved_files.items():
            dir_path = str(PurePosixPath(filepath).parent)
            if dir_path and dir_path != ".":
                try:
                    fs.make_dir(dir_path)
                except Exception:
                    pass
            fs.write(filepath, content)

        for cmd in commands:
            proc = _sandbox_run_command(sandbox, cmd)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            _append_command_output(result, command=cmd, stdout=stdout, stderr=stderr)
            if int(proc.exit_code) != 0:
                result["success"] = False
                result["validation_status"] = "failed"
                result["errors"].append(f"{cmd} failed: {stderr or stdout}")
                break
            if stderr and "warning" in stderr.lower():
                result["warnings"].append(stderr)

        result["parsed_errors"] = parse_build_errors(result.get("build_output", ""))

        if result["validation_status"] == "passed":
            try:
                scripts = _read_package_scripts_from_text(fs.read("package.json"))
                if "test" in scripts:
                    test_proc = _sandbox_run_command(sandbox, "npm test -- --watchAll=false")
                    result["test_results"] = test_proc.stdout or ""
                    if int(test_proc.exit_code) != 0:
                        result["warnings"].append(f"Tests failed: {test_proc.stderr or ''}")
            except Exception:
                pass

        if result["validation_status"] == "passed" and start_dev_server:
            try:
                try:
                    preview_port = int(os.getenv("E2B_PREVIEW_PORT", "3000"))
                except Exception:
                    preview_port = 3000

                try:
                    scripts = _read_package_scripts_from_text(fs.read("package.json"))
                except Exception:
                    scripts = {}

                if "dev" in scripts:
                    server_cmd = f"npm run dev -- --host 0.0.0.0 --port {preview_port}"
                elif "start" in scripts:
                    server_cmd = f"HOST=0.0.0.0 PORT={preview_port} npm start"
                else:
                    server_cmd = f"npm run dev -- --host 0.0.0.0 --port {preview_port}"

                _sandbox_run_command(sandbox, server_cmd, background=True)
                await asyncio.sleep(3)

                host = _sandbox_get_host(sandbox, preview_port)
                if host and host.strip():
                    preview_url = host.strip()
                    if not preview_url.startswith("http"):
                        preview_url = f"https://{preview_url}"
                    result["preview_url"] = preview_url
            except Exception as server_err:
                result["warnings"].append(f"Dev server not started: {server_err}")

        return result
    except Exception as exc:
        return {
            **result,
            "success": False,
            "validation_status": "failed",
            "errors": [f"E2B validation error: {exc}"],
            "preview_url": None,
        }
    finally:
        try:
            if sandbox is not None and hasattr(sandbox, "close"):
                sandbox.close()
        except Exception:
            pass


async def _run_local_validation(
    *,
    resolved_files: Dict[str, str],
    commands: List[str],
    start_dev_server: bool,
    timeout_seconds: int,
) -> Dict[str, Any]:
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
            _write_files_to_dir(root, resolved_files)

            for cmd in commands:
                exit_code, stdout, stderr = await _run_local_command(
                    command=cmd,
                    cwd=root,
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

            if result["validation_status"] == "passed":
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


def _trim_validation_result(result: Dict[str, Any]) -> Dict[str, Any]:
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


def _persist_validation_context(*, context_variables: Optional[Any], result: Dict[str, Any]) -> None:
    if context_variables is None or not hasattr(context_variables, "set"):
        return
    try:
        context_variables.set("app_validation_status", result.get("validation_status"))
        context_variables.set("app_validation_strategy_used", result.get("validation_strategy"))
        context_variables.set("app_validation_preview_url", result.get("preview_url"))
        context_variables.set("app_validation_result", _trim_validation_result(result))
    except Exception:
        pass


async def validate_app_build(
    files: Dict[str, str],
    commands: Optional[List[str]] = None,
    start_dev_server: bool = True,
    timeout_seconds: int = 120,
    validation_strategy: Optional[str] = None,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
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

    result = await _run_e2b_validation(
        resolved_files=resolved_files,
        commands=list(commands),
        start_dev_server=bool(start_dev_server),
        timeout_seconds=timeout_seconds,
    )
    result["strategy_reason"] = strategy_reason
    _persist_validation_context(context_variables=context_variables, result=result)
    return result


def _context_has_agent_backend(context_variables: Optional[Any]) -> bool:
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
            value = context_variables.get(key)
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (list, dict)) and value:
            return True
    return False


async def validate_app_bundle_from_request(
    AppValidationRequest: Dict[str, Any],
    agent_message: Optional[str] = None,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    """Auto-run deterministic app validation from AppValidationAgent's request.

    AppValidationAgent should not call validation tools directly. It emits a strict
    request, then this auto tool owns the runtime checks and persists the resulting
    gate fields into context_variables for routing.
    """

    request = AppValidationRequest if isinstance(AppValidationRequest, dict) else {}
    commands = request.get("commands")
    if not isinstance(commands, list):
        commands = None

    validation = await validate_app_build(
        files={},
        commands=commands,
        start_dev_server=bool(request.get("start_dev_server", True)),
        timeout_seconds=int(request.get("timeout_seconds") or 120),
        validation_strategy=request.get("validation_strategy"),
        context_variables=context_variables,
    )

    agent_integration_result: Dict[str, Any]
    if _context_has_agent_backend(context_variables):
        from .integration_tests import run_integration_tests

        agent_integration_result = await run_integration_tests(
            files={},
            context_variables=context_variables,
        )
        agent_integration_passed = bool(agent_integration_result.get("passed"))
    else:
        agent_integration_result = {
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
        agent_integration_passed = True

    from .validate_wiring import validate_wiring

    wiring_result = await validate_wiring(context_variables=context_variables)
    wiring_passed = bool(wiring_result.get("passed"))
    module_implementation_result = validate_module_implementation_contract(
        _generated_files_from_context(context_variables)
    )
    module_implementation_passed = bool(module_implementation_result.get("passed"))
    generated_files = _generated_files_from_context(context_variables)
    from .module_runtime_quality import audit_module_runtime_quality

    runtime_quality_warnings = audit_module_runtime_quality(
        [
            {"filename": path, "content": content}
            for path, content in sorted(generated_files.items())
        ]
    )
    runtime_quality_result = {
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
    runtime_quality_passed = bool(runtime_quality_result["passed"])
    validation_passed = str(validation.get("validation_status") or "").strip().lower() != "failed"
    combined_passed = bool(
        validation_passed
        and agent_integration_passed
        and wiring_passed
        and module_implementation_passed
        and runtime_quality_passed
    )

    if context_variables is not None and hasattr(context_variables, "set"):
        try:
            context_variables.set(
                "module_runtime_quality_status",
                "passed" if runtime_quality_passed else "blocked",
            )
            context_variables.set(
                "module_runtime_quality_warnings",
                runtime_quality_warnings,
            )
            context_variables.set(
                "module_runtime_quality_result",
                runtime_quality_result,
            )
            context_variables.set(
                "module_implementation_validation_passed",
                module_implementation_passed,
            )
            context_variables.set(
                "module_implementation_validation_result",
                module_implementation_result,
            )
            context_variables.set("integration_tests_passed", combined_passed)
            context_variables.set(
                "integration_test_result",
                {
                    "agent_backend": agent_integration_result,
                    "module_wiring": wiring_result,
                    "module_implementation": module_implementation_result,
                    "module_runtime_quality": runtime_quality_result,
                    "passed": combined_passed,
                },
            )
        except Exception:
            pass

    return {
        "status": "success" if combined_passed else "failed",
        "message": agent_message or "App validation and integration gates completed.",
        "app_validation_result": validation,
        "agent_backend_integration_result": agent_integration_result,
        "wiring_validation_result": wiring_result,
        "module_implementation_validation_result": module_implementation_result,
        "module_runtime_quality_result": runtime_quality_result,
        "integration_tests_passed": combined_passed,
    }


__all__ = [
    "parse_build_errors",
    "validate_module_implementation_contract",
    "validate_app_build",
    "validate_app_bundle_from_request",
]
