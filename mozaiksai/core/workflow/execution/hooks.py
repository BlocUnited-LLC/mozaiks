"""Hook loading and registration utilities for AG2 hooks.

This module reads a workflow's `hooks.yaml` and registers the
declared hook functions on the appropriate `ConversableAgent` instances.

HOOK ENTRY FORMAT:

  - hook_type: process_message_before_send | update_agent_state | process_last_received_message | process_all_messages_before_reply
    hook_agent: <AgentName> | "all"   # Use "all" to apply hook to every agent in workflow
    filename: redaction.py          # Python filename relative to workflow root OR tools/ directory
    function: echo_before_send       # Name of the function inside the file

Resolution rules:
1. If `filename` present, we form import path relative to workflow directory.
   - If the file is inside `<workflow>/tools/`, we add `workflows.<workflow>.tools.<module>`.
   - Otherwise we use `workflows.<workflow>.<module>`.
2. If only `function` provided and it contains ':' or '.', we attempt to parse module + function directly.
3. Fallback function name default: if only module path given with no explicit function
   we look for a top-level symbol named exactly as provided in hook config `function`.

Safety & Validation:
 - Missing agent: logged and skipped.
 - Import or attribute errors: logged and skipped.
 - Signature validation (best-effort) warns on mismatches (does not raise).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
import contextlib
import importlib
import importlib.util
import inspect
import logging
import sys
import time
from functools import wraps
import yaml

from ..declarative import parse_hooks_config

logger = logging.getLogger("hooks_loader")

VALID_HOOK_TYPES = {
    "process_message_before_send": "sender,message,recipient,silent -> (dict|str)",
    "update_agent_state": "agent,messages -> None",
    "process_last_received_message": "message(list|str) -> str",
    "process_all_messages_before_reply": "messages(list[dict]) -> list[dict]",
}


@dataclass
class RegisteredHook:
    workflow: str
    agent: str
    hook_type: str
    function_qualname: str


def _ensure_workflow_import_paths(workflow_path: Path) -> None:
    """Expose the active workflows package before importing hook modules."""
    desired_order = (
        workflow_path.parent.parent,
        workflow_path.parent,
        workflow_path,
        workflow_path / "tools",
    )
    normalized: List[str] = []
    for candidate in desired_order:
        try:
            value = str(candidate.resolve())
        except Exception:
            value = str(candidate)
        if value:
            normalized.append(value)
    for value in reversed(normalized):
        with contextlib.suppress(ValueError):
            sys.path.remove(value)
        if value:
            sys.path.insert(0, value)


def _resolve_import(workflow_name: str, file_value: Optional[str], function_value: str, workflow_path: Path) -> tuple[Optional[Callable], str]:
    """Resolve and import the function.

    Returns (callable_or_none, qualname_for_logging).
    """
    module_name: Optional[str] = None
    fn_name: Optional[str] = None

    # Case 1: explicit file provided
    if file_value:
        file_path = Path(file_value)
        if file_path.is_absolute():
            # Map absolute path back inside workflow (unlikely); strip suffix
            rel = file_path.name
        else:
            rel = file_path.name
        stem = Path(rel).stem
        fn_name = function_value
        resolved_file = workflow_path / "tools" / f"{stem}.py"
        if not resolved_file.exists():
            resolved_file = workflow_path / f"{stem}.py"
        if not resolved_file.exists():
            logger.warning(
                "Hook import failed: workflow=%s filename=%s not found under %s",
                workflow_name,
                file_value,
                workflow_path,
            )
            return None, f"{workflow_name}.{stem}.{fn_name}"

        try:
            _ensure_workflow_import_paths(workflow_path)
            module_name = f"_mz_workflow_hook_{workflow_name}_{stem}_{abs(hash(str(resolved_file.resolve())))}"
            spec = importlib.util.spec_from_file_location(module_name, resolved_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load spec for {resolved_file}")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
        except Exception as e:  # pragma: no cover
            logger.warning(f"Hook import failed: {resolved_file}: {e}")
            return None, f"{resolved_file}:{fn_name}"

        try:
            fn = getattr(mod, fn_name)
        except AttributeError as e:  # pragma: no cover
            logger.warning(f"Hook attribute not found: {resolved_file}:{fn_name}: {e}")
            return None, f"{resolved_file}:{fn_name}"

        return fn, f"{resolved_file}:{fn_name}"
    else:
        # Case 2: function field encodes module + function (colon or last dot)
        if ':' in function_value:
            module_name, fn_name = function_value.split(':', 1)
        elif '.' in function_value:
            parts = function_value.split('.')
            module_name = '.'.join(parts[:-1])
            fn_name = parts[-1]
        else:
            # Single token – treat as module where function has same name
            module_name = function_value
            fn_name = function_value.split('.')[-1]

    try:
        _ensure_workflow_import_paths(workflow_path)
        mod = importlib.import_module(module_name)  # type: ignore[arg-type]
    except Exception as e:  # pragma: no cover
        logger.warning(f"Hook import failed: {module_name}: {e}")
        return None, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"

    try:
        fn = getattr(mod, fn_name)  # type: ignore[arg-type]
    except AttributeError as e:  # pragma: no cover
        logger.warning(f"Hook attribute not found: {module_name}.{fn_name}: {e}")
        return None, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"

    return fn, f"{module_name}.{fn_name}" if fn_name else module_name or "<unknown>"


def _validate_signature(hook_type: str, fn: Callable) -> None:
    """Best-effort signature validation; logs warnings only."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # pragma: no cover
        return

    params = list(sig.parameters.values())
    if hook_type == "process_message_before_send" and len(params) < 4:
        logger.warning(f"Hook {fn.__name__} signature may be invalid for {hook_type}: expected 4 params, got {len(params)}")
    elif hook_type == "update_agent_state" and len(params) < 2:
        logger.warning(f"Hook {fn.__name__} signature may be invalid for {hook_type}: expected 2 params, got {len(params)}")
    elif hook_type == "process_last_received_message" and len(params) < 1:
        logger.warning(f"Hook {fn.__name__} signature may be invalid for {hook_type}: expected 1 param, got {len(params)}")
    elif hook_type == "process_all_messages_before_reply" and len(params) < 1:
        logger.warning(f"Hook {fn.__name__} signature may be invalid for {hook_type}: expected 1 param, got {len(params)}")


def _read_hook_config(workflow_path: Path) -> tuple[Dict[str, Any], str | None]:
    """Load hook config from hooks.yaml."""
    hooks_yaml = workflow_path / "hooks.yaml"

    if hooks_yaml.exists():
        try:
            with open(hooks_yaml, "r", encoding="utf-8") as f:
                payload = yaml.safe_load(f) or {}
            parsed = parse_hooks_config(payload)
            return parsed, str(hooks_yaml)
        except Exception as e:  # pragma: no cover
            logger.error("Failed reading hooks.yaml for workflow at %s: %s", workflow_path, e)

    return {}, None


def load_hook_entries(workflow_name: str, *, base_path: str = "workflows") -> List[Dict[str, Any]]:
    """Load hook entries from hooks.yaml."""
    workflow_path = Path(base_path) / workflow_name
    data, source = _read_hook_config(workflow_path)
    if not source:
        return []
    entries = data.get("hooks") or []
    if not isinstance(entries, list):
        logger.warning("Hook config has invalid 'hooks' list in %s", source)
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def register_hooks_for_workflow(workflow_name: str, agents: Dict[str, Any], *, base_path: str = "workflows") -> List[RegisteredHook]:
    """Load hooks config for `workflow_name` and register hooks on provided agents.

    Parameters
    ----------
    workflow_name: Name of the workflow directory under `base_path`.
    agents: Mapping of agent names to ConversableAgent instances.
    base_path: Root workflows directory.

    Returns
    -------
    list[RegisteredHook]
        Hooks successfully registered.
    """
    workflow_path = Path(base_path) / workflow_name
    data, source = _read_hook_config(workflow_path)
    if not source:
        logger.debug(f"No hooks.yaml for workflow {workflow_name}")
        return []
    logger.info(f"Loading hooks for workflow '{workflow_name}' from: {source}")

    entries = data.get("hooks") or []
    if not isinstance(entries, list):
        logger.warning(f"Hook config invalid structure (hooks not list) for {workflow_name}")
        return []

    registered: List[RegisteredHook] = []
    # Counters for summary
    total = 0
    skipped_invalid_entry = 0
    skipped_unknown_type = 0
    skipped_missing_agent = 0
    skipped_missing_function = 0
    import_failures = 0

    for entry in entries:
        total += 1
        try:
            if not isinstance(entry, dict):
                skipped_invalid_entry += 1
                logger.debug(f"Skipping non-dict hook entry in {workflow_name}: {entry}")
                continue
            hook_type = entry.get("hook_type")
            hook_agent = entry.get("hook_agent")
            file_value = entry.get("filename")  # Updated to use "filename" instead of "file"
            fn_value = entry.get("function")

            if hook_type not in VALID_HOOK_TYPES:
                skipped_unknown_type += 1
                logger.warning(f"Unknown hook_type '{hook_type}' in workflow {workflow_name}; skipping entry")
                continue

            if not hook_agent:
                skipped_missing_agent += 1
                logger.warning(f"Missing hook_agent for hook_type '{hook_type}' in workflow {workflow_name}; skipping entry")
                continue

            # Support "all" to apply hook to every agent in the workflow
            if hook_agent.lower() == "all":
                target_agents = list(agents.items())
            else:
                agent_obj = agents.get(hook_agent)
                if agent_obj is None:
                    skipped_missing_agent += 1
                    logger.warning(f"Hook agent '{hook_agent}' not found for workflow {workflow_name}; skipping entry")
                    continue
                target_agents = [(hook_agent, agent_obj)]

            if not fn_value:
                skipped_missing_function += 1
                logger.warning(f"Missing function for hook_type '{hook_type}' (agent={hook_agent}) in workflow {workflow_name}; skipping entry")
                continue

            fn, qual = _resolve_import(workflow_name, file_value, fn_value, workflow_path)
            if fn is None:
                import_failures += 1
                continue

            _validate_signature(hook_type, fn)

            # ------------------------------------------------------------------
            # Register hook on each target agent (single agent or all agents)
            # ------------------------------------------------------------------
            for target_agent_name, target_agent_obj in target_agents:
                # ------------------------------------------------------------------
                # Instrument hook execution with timing + error logging.
                # We wrap only once (idempotent) to avoid stacking decorators if
                # register_hooks is forced multiple times.
                # ------------------------------------------------------------------
                if getattr(fn, "_mozaiks_hook_wrapped", False):
                    wrapped_fn = getattr(fn, "_mozaiks_hook_wrapper", fn)
                else:
                    orig_fn = fn

                    @wraps(orig_fn)
                    def _wrapped_hook(*args, __wf=workflow_name, __agent=target_agent_name, __type=hook_type, __orig=orig_fn, **kwargs):  # type: ignore[override]
                        start = time.perf_counter()
                        logger.info(
                            "🪝 [HOOK_EXEC] status=start type=%s agent=%s workflow=%s function=%s",
                            __type,
                            __agent,
                            __wf,
                            f"{__orig.__module__}.{__orig.__name__}",
                        )
                        try:
                            result = __orig(*args, **kwargs)
                            duration = (time.perf_counter() - start) * 1000.0
                            logger.info(
                                "🪝 [HOOK_EXEC] status=done type=%s agent=%s workflow=%s duration_ms=%.2f",
                                __type,
                                __agent,
                                __wf,
                                duration,
                            )
                            return result
                        except Exception as hook_err:  # pragma: no cover
                            duration = (time.perf_counter() - start) * 1000.0
                            logger.error(
                                "🪝 [HOOK_EXEC] status=error type=%s agent=%s workflow=%s duration_ms=%.2f err=%s",
                                __type,
                                __agent,
                                __wf,
                                duration,
                                hook_err,
                                exc_info=True,
                            )
                            raise

                    setattr(_wrapped_hook, "_mozaiks_hook_wrapped", True)
                    try:
                        setattr(orig_fn, "_mozaiks_hook_wrapped", True)
                        setattr(orig_fn, "_mozaiks_hook_wrapper", _wrapped_hook)
                    except Exception:  # pragma: no cover - some callables may deny attribute assignment
                        pass
                    wrapped_fn = _wrapped_hook

                try:
                    target_agent_obj.register_hook(hook_type, wrapped_fn)  # type: ignore[attr-defined]
                    registered.append(RegisteredHook(workflow=workflow_name, agent=target_agent_name, hook_type=hook_type, function_qualname=qual))
                    logger.debug(f"Registered hook {hook_type} -> {qual} for agent {target_agent_name}")
                except Exception as e:  # pragma: no cover
                    logger.error(f"Failed registering hook {hook_type} for {target_agent_name}: {e}")
                    continue
        except Exception as e:  # pragma: no cover
            logger.error(f"Unexpected error processing hook entry for {workflow_name}: {e}", exc_info=True)

    skipped_total = skipped_invalid_entry + skipped_unknown_type + skipped_missing_agent + skipped_missing_function + import_failures
    logger.debug(
        f"Hook loading summary for '{workflow_name}': processed={total}, registered={len(registered)}, skipped={skipped_total} "
        f"(invalid_entry={skipped_invalid_entry}, unknown_type={skipped_unknown_type}, missing_agent={skipped_missing_agent}, missing_fn={skipped_missing_function}, import_failures={import_failures})"
    )
    
    # Use consolidated logging for summary
    if registered:
        from logs.logging_config import get_workflow_session_logger
        workflow_logger = get_workflow_session_logger(workflow_name)
        workflow_logger.log_hook_registration_summary(workflow_name, len(registered))

    if registered:
        quals = ", ".join(r.function_qualname for r in registered)
        logger.debug(f"Registered hook functions: {quals}")

    return registered


def summarize_hooks(workflow_name: str) -> List[Dict[str, Any]]:
    """Return raw hook declarations (without importing) for inspection."""
    return load_hook_entries(workflow_name, base_path="workflows")

__all__ = [
    "register_hooks_for_workflow",
    "load_hook_entries",
    "summarize_hooks",
    "RegisteredHook",
]
