"""
App validation tool for generated applications.

This tool can:
- resolve generated files from an explicit `files` mapping or persisted agent outputs
- validate the generated app with an explicit strategy: `e2b`, `local`, or `skip`
- run build/test commands
- optionally start a preview server for the E2B strategy
"""


import asyncio
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from logs.logging_config import get_workflow_logger
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
    local_app_validation_available,
    resolve_app_validation_strategy,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
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


__all__ = ["parse_build_errors", "validate_app_build"]
