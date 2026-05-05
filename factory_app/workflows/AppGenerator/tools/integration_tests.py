"""
integration_tests - Lightweight compatibility checks between AppGenerator output and AgentGenerator output.

This tool is intentionally best-effort and offline:
- It validates that the generated frontend code references the expected env vars for agent endpoints.
- It checks for presence/shape of `.env.example` when available (warnings, not hard-fail).
- It records results into ContextVariables for handoff routing.
"""


import json
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from mozaiksai.core.workflow.generator_support.agent_endpoints import (
    resolve_agent_api_url,
    resolve_agent_websocket_url,
)
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    extract_code_file_map_from_payload,
)
from mozaiksai.core.workflow.generator_support.workflow_exports import get_latest_workflow_export
from logs.logging_config import get_workflow_logger


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


async def _resolve_files(
    *,
    files: Optional[Dict[str, str]],
    context_variables: Optional[Any],
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
    return _extract_code_files(collected), str(chat_id), str(app_id)


def _content_contains(files_map: Dict[str, str], needle: str) -> bool:
    if not needle:
        return False
    for content in files_map.values():
        try:
            if isinstance(content, str) and needle in content:
                return True
        except Exception:
            continue
    return False


def _parse_env_value(env_text: str, key: str) -> Optional[str]:
    if not isinstance(env_text, str) or not env_text:
        return None
    target = key.strip()
    if not target:
        return None
    for line in env_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        if k.strip() == target:
            return v.strip()
    return None


async def run_integration_tests(
    files: Dict[str, str],
    agent_context: Optional[Dict[str, Any]] = None,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
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
    resolved_files, chat_id, app_id = await _resolve_files(files=files, context_variables=context_variables)

    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []
    failed_tests: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Test: code references agent env vars (required for runtime wiring)
    # ------------------------------------------------------------------
    uses_ws = _content_contains(resolved_files, "VITE_AGENT_WEBSOCKET_URL")
    uses_api = _content_contains(resolved_files, "VITE_AGENT_API_URL")
    check_passed = bool(uses_ws or uses_api)
    if not check_passed:
        failed_tests.append(
            {
                "test": "agent_env_var_usage",
                "error": "Generated app code does not reference VITE_AGENT_WEBSOCKET_URL or VITE_AGENT_API_URL.",
                "fix_suggestion": "Add an agent client (e.g., src/services/agentClient.js) that reads import.meta.env.VITE_AGENT_WEBSOCKET_URL and uses it for WebSocket connections.",
            }
        )
    checks.append(
        {
            "id": "agent_env_var_usage",
            "passed": check_passed,
            "message": (
                "Generated app code references VITE agent endpoint env vars."
                if check_passed
                else "Generated app code is missing VITE agent endpoint env var usage."
            ),
            "details": {
                "uses_websocket_env": bool(uses_ws),
                "uses_api_env": bool(uses_api),
                "blocking": True,
                **(
                    {"fix_suggestion": failed_tests[-1].get("fix_suggestion")}
                    if not check_passed and failed_tests
                    else {}
                ),
            },
        }
    )

    # ------------------------------------------------------------------
    # Test: .env.example present + keys (warning only; bundler will inject)
    # ------------------------------------------------------------------
    env_text = resolved_files.get(".env.example")
    if not isinstance(env_text, str) or not env_text.strip():
        warnings.append(
            "Missing .env.example in generated files. The runtime bundler will inject one, but it is recommended the generator includes it."
        )
        checks.append(
            {
                "id": "env_example_keys",
                "passed": True,
                "message": "Missing .env.example (bundler will inject one).",
                "details": {"blocking": False, "severity": "warning", "missing_keys": []},
            }
        )
    else:
        required_keys = ("VITE_APP_ID", "VITE_AGENT_WEBSOCKET_URL", "VITE_AGENT_API_URL")
        missing = [k for k in required_keys if k not in env_text]
        if missing:
            warnings.append(f".env.example is missing keys: {missing}")
        ws_val = _parse_env_value(env_text, "VITE_AGENT_WEBSOCKET_URL") or ""
        api_val = _parse_env_value(env_text, "VITE_AGENT_API_URL") or ""
        if not ws_val or not api_val:
            warnings.append(
                "Agent endpoint values in .env.example are empty; ensure templates are configured or AgentGenerator export has run."
            )
        checks.append(
            {
                "id": "env_example_keys",
                "passed": True,
                "message": "Checked .env.example for required agent env keys (warnings may apply).",
                "details": {
                    "blocking": False,
                    "severity": "warning" if missing or (not ws_val or not api_val) else "info",
                    "missing_keys": missing,
                    "websocket_value_present": bool(ws_val),
                    "api_value_present": bool(api_val),
                },
            }
        )

    # ------------------------------------------------------------------
    # Test: Agent endpoints resolvable (warning; depends on platform config)
    # ------------------------------------------------------------------
    resolved_app_id = str(app_id or "").strip()
    ws_url = None
    api_url = None
    if resolved_app_id:
        try:
            ws_url = resolve_agent_websocket_url(resolved_app_id)
            api_url = resolve_agent_api_url(resolved_app_id)
        except Exception:
            ws_url = None
            api_url = None
        if not ws_url or not api_url:
            try:
                export_rec = await get_latest_workflow_export(app_id=resolved_app_id, workflow_type="agent-generator")
            except Exception:
                export_rec = None
            if isinstance(export_rec, dict):
                ws_url = ws_url or export_rec.get("agent_websocket_url")
                api_url = api_url or export_rec.get("agent_api_url")
    if not ws_url or not api_url:
        warnings.append(
            "Agent endpoints could not be resolved. Set MOZAIKS_AGENT_WEBSOCKET_URL_TEMPLATE / MOZAIKS_AGENT_API_URL_TEMPLATE or run AgentGenerator export to populate WorkflowExports."
        )
    checks.append(
        {
            "id": "agent_endpoints_resolvable",
            "passed": True,
            "message": "Resolved agent endpoints for this app_id when available (warnings may apply).",
            "details": {
                "blocking": False,
                "severity": "warning" if (not ws_url or not api_url) else "info",
                "app_id_available": bool(resolved_app_id),
                "websocket_url_present": bool(ws_url),
                "api_url_present": bool(api_url),
            },
        }
    )

    total_tests = len(checks)
    passed_tests = sum(1 for c in checks if c.get("passed") is True)
    blocking_passed = all(c.get("passed") is True for c in checks if c.get("details", {}).get("blocking") is True)

    results: Dict[str, Any] = {
        "contract_version": "1.0",
        "offline": True,
        "passed": bool(blocking_passed),
        "checks": checks,
        "total_tests": total_tests,
        "passed_tests": passed_tests,
        "failed_tests": failed_tests,
        "warnings": warnings,
        "note": "These are offline wiring checks only; they do not validate live WebSocket/API connectivity.",
    }

    # Persist into ContextVariables for routing.
    try:
        if context_variables is not None and hasattr(context_variables, "set"):
            context_variables.set("integration_tests_passed", bool(results.get("passed")))
            context_variables.set("integration_test_result", results)
    except Exception as ctx_err:
        wf_logger.debug(f"Failed to persist integration test results in context: {ctx_err}")

    return results


__all__ = ["run_integration_tests"]
