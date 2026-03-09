# ============================================================================
# FILE: core/workflow/agent_tools.py
# DESCRIPTION:
#   Agent tool function loading from workflows/<flow>/tools.json
#   Loads ALL tools (Agent_Tool and UI_Tool) as agent functions.
#   UI_Tools get special handling during execution but are still bound to agents.
#   
#   AG2-NATIVE APPROACH:
#   Tools that need context should accept 'context_variables' parameter.
#   AG2 automatically injects ContextVariables through its native dependency injection.
#   No manual parameter injection needed - AG2 handles this internally.
#   
#   Available in ContextVariables:
#   - workflow_name, app_id, chat_id, user_id (auto-injected by orchestrator)
#   - concept_overview, schema_overview (loaded by context_variables.py)
#   - Any other workflow-specific data from context_variables.json
#   
#   NOTE: UI interaction handling logic lives in ui_tools.py.
# ============================================================================
from __future__ import annotations
import logging
import importlib
import importlib.util
import sys
import inspect
from functools import wraps
from pathlib import Path
from typing import Callable, Dict, List, Optional
import json

logger = logging.getLogger(__name__)


def _wrap_with_validation(
    *,
    workflow_name: str,
    agent_name: str,
    tool_name: str,
    func: Callable,
    enforce_schema: bool,
) -> Callable:
    """Wrap tool callables with structured-output validation and runtime logging.
    
    This wrapper:
    1. Logs tool execution start/completion/errors to tools.log (workflow-agnostic)
    2. Validates structured outputs when enforce_schema=True
    3. Captures execution timing and parameters
    """
    from logs.tools_logs import get_tool_logger, log_tool_event
    from ..validation.tools import validate_tool_call
    import time

    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def _async_wrapper(*args, **kwargs):
            # Extract context for logging
            chat_id = kwargs.get('chat_id') or (kwargs.get('context_variables', {}) or {}).get('chat_id')
            app_id = kwargs.get('app_id') or (kwargs.get('context_variables', {}) or {}).get('app_id')
            
            # Get workflow-agnostic logger
            tool_logger = get_tool_logger(
                tool_name=tool_name,
                chat_id=chat_id,
                app_id=app_id,
                workflow_name=workflow_name
            )
            
            # Log tool execution start
            start_time = time.time()
            payload = dict(kwargs)
            log_tool_event(
                tool_logger,
                action="start",
                status="info",
                message=f"Tool '{tool_name}' invoked by agent '{agent_name}'",
                agent_name=agent_name,
                args_count=len(payload)
            )
            
            try:
                # Schema validation if enabled
                if enforce_schema:
                    outcome = validate_tool_call(
                        workflow_name=workflow_name,
                        agent_name=agent_name,
                        tool_name=tool_name,
                        raw_payload=payload,
                    )
                    if not outcome.is_valid and outcome.error_payload is not None:
                        log_tool_event(
                            tool_logger,
                            action="validation_failed",
                            status="error",
                            message=f"Schema validation failed for '{tool_name}'",
                            level=logging.ERROR
                        )
                        return outcome.error_payload
                
                # Execute tool
                result = await func(*args, **kwargs)
                
                # Log successful completion
                duration_ms = (time.time() - start_time) * 1000
                log_tool_event(
                    tool_logger,
                    action="complete",
                    status="success",
                    message=f"Tool '{tool_name}' completed successfully",
                    duration_ms=round(duration_ms, 2)
                )
                return result
                
            except Exception as e:
                # Log error
                duration_ms = (time.time() - start_time) * 1000
                log_tool_event(
                    tool_logger,
                    action="error",
                    status="error",
                    message=f"Tool '{tool_name}' failed: {str(e)}",
                    level=logging.ERROR,
                    error_type=type(e).__name__,
                    duration_ms=round(duration_ms, 2)
                )
                raise

        return _async_wrapper

    @wraps(func)
    def _sync_wrapper(*args, **kwargs):
        # Extract context for logging
        chat_id = kwargs.get('chat_id') or (kwargs.get('context_variables', {}) or {}).get('chat_id')
        app_id = kwargs.get('app_id') or (kwargs.get('context_variables', {}) or {}).get('app_id')
        
        # Get workflow-agnostic logger
        tool_logger = get_tool_logger(
            tool_name=tool_name,
            chat_id=chat_id,
            app_id=app_id,
            workflow_name=workflow_name
        )
        
        # Log tool execution start
        start_time = time.time()
        payload = dict(kwargs)
        log_tool_event(
            tool_logger,
            action="start",
            status="info",
            message=f"Tool '{tool_name}' invoked by agent '{agent_name}'",
            agent_name=agent_name,
            args_count=len(payload)
        )
        
        try:
            # Schema validation if enabled
            if enforce_schema:
                outcome = validate_tool_call(
                    workflow_name=workflow_name,
                    agent_name=agent_name,
                    tool_name=tool_name,
                    raw_payload=payload,
                )
                if not outcome.is_valid and outcome.error_payload is not None:
                    log_tool_event(
                        tool_logger,
                        action="validation_failed",
                        status="error",
                        message=f"Schema validation failed for '{tool_name}'",
                        level=logging.ERROR
                    )
                    return outcome.error_payload
            
            # Execute tool
            result = func(*args, **kwargs)
            
            # Log successful completion
            duration_ms = (time.time() - start_time) * 1000
            log_tool_event(
                tool_logger,
                action="complete",
                status="success",
                message=f"Tool '{tool_name}' completed successfully",
                duration_ms=round(duration_ms, 2)
            )
            return result
            
        except Exception as e:
            # Log error
            duration_ms = (time.time() - start_time) * 1000
            log_tool_event(
                tool_logger,
                action="error",
                status="error",
                message=f"Tool '{tool_name}' failed: {str(e)}",
                level=logging.ERROR,
                error_type=type(e).__name__,
                duration_ms=round(duration_ms, 2)
            )
            raise

    return _sync_wrapper

def load_agent_tool_functions(workflow_name: str) -> Dict[str, List[Callable]]:
    """Discover and import per-agent tool functions for a workflow.

    Reads workflows/<workflow_name>/tools.json and returns a mapping of
    agent_name -> list[callable] so callers can pass functions=... to
    ConversableAgent at construction time.

    Loads ALL tools (both Agent_Tool and UI_Tool types) as agent functions.
    UI_Tools get special handling during execution but still need to be
    registered with their agents for proper function binding.
    
    All tools are automatically wrapped with runtime logging that captures:
    - Tool execution start/completion/errors
    - Execution timing
    - Validation failures (when schema enforcement is enabled)
    - Logs to logs/logs/tools.log (workflow-agnostic)
    """
    mapping: Dict[str, List[Callable]] = {}
    base_dir = Path('workflows') / workflow_name
    tools_yaml_path = base_dir / 'tools.yaml'
    
    if not tools_yaml_path.exists():
        logger.debug(f"[TOOLS] No tools.yaml for workflow '{workflow_name}'")
        return mapping
    
    try:
        import yaml
        data = yaml.safe_load(tools_yaml_path.read_text(encoding='utf-8')) or {}
    except Exception as jerr:
        logger.warning(f"[TOOLS] Failed to parse tools.yaml for '{workflow_name}': {jerr}")
        return mapping
    entries = data.get('tools', []) or []
    if not isinstance(entries, list):
        logger.warning(f"[TOOLS] tools.json 'tools' section not a list in '{workflow_name}'")
        return mapping
    # Discover which agents have structured outputs for schema enforcement
    try:
        from ..outputs.structured import get_structured_outputs_for_workflow

        structured_registry = get_structured_outputs_for_workflow(workflow_name)
    except Exception as reg_err:  # pragma: no cover - introspection only
        structured_registry = {}
        logger.debug(
            "[TOOLS][TRACE] Structured outputs registry unavailable for '%s': %s",
            workflow_name,
            reg_err,
        )

    # Disable per-process tool module caching to always load fresh tool code
    logger.debug(f"[TOOLS][TRACE] Starting tool load for workflow '{workflow_name}' (entries={len(entries)})")
    for idx, tool in enumerate(entries, start=1):
        if not isinstance(tool, dict):
            continue
        # NOTE: We load ALL tools (including UI_Tools) as agent functions here.
        # UI_Tools get special handling during execution but still need to be
        # registered with the agent for proper function binding.
        file_name = tool.get('file')
        func_name = tool.get('function')
        agent_field = tool.get('agent')
        if not file_name or not func_name or not agent_field:
            logger.warning(f"[TOOLS][TRACE] Skipping entry #{idx}: missing one of file/function/agent -> file={file_name} func={func_name} agent={agent_field}")
            continue
        # Support agent as str or list
        if isinstance(agent_field, (list, tuple)):
            agent_targets = [a for a in agent_field if isinstance(a, str)]
        else:
            agent_targets = [agent_field] if isinstance(agent_field, str) else []
        if not agent_targets:
            continue
        # Resolve file path (support both root and tools/ subdir)
        base_dir_tools = base_dir / 'tools'
        candidate_paths = [base_dir / file_name, base_dir_tools / file_name]
        file_path: Optional[Path] = next((p for p in candidate_paths if p.exists()), None)
        if not file_path:
            logger.warning(f"[TOOLS][TRACE] File not found for entry #{idx}: {file_name} (searched: {candidate_paths})")
            continue
        # Always load a fresh module instance under an ephemeral name (no sys.modules caching)
        module = None
        try:
            spec = importlib.util.spec_from_file_location(f"mozaiks_{workflow_name}_{file_path.stem}_ephemeral", file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[attr-defined]
                logger.debug(f"[TOOLS] Loaded module fresh (no cache): {file_path.name}")
            else:
                logger.warning(f"[TOOLS] Could not load spec for {file_path}")
                continue
        except Exception as imp_err:
            logger.warning(f"[TOOLS][TRACE] Import failed for {file_path}: {imp_err}")
            continue
        try:
            func = getattr(module, func_name)
        except AttributeError:
            logger.warning(f"[TOOLS][TRACE] Function '{func_name}' missing in {file_path.name}")
            continue
        if not callable(func):
            logger.warning(f"[TOOLS][TRACE] Attribute '{func_name}' in {file_path.name} not callable")
            continue
        
        # AG2-native: No manual context injection needed
        # Tools that need context should accept 'context_variables' parameter
        # AG2 handles dependency injection automatically
        
        # Log binding details BEFORE adding
        logger.debug(
            "[TOOLS][TRACE] Preparing to bind function -> workflow=%s agent_targets=%s file=%s func=%s module=%s",
            workflow_name, agent_targets, file_path.name, func_name, getattr(func, '__module__', None)
        )
        tool_identifier = tool.get('name') or func_name
        tool_type = tool.get('tool_type') or tool.get('type')
        is_ui_tool = tool_type and str(tool_type).upper() == "UI_TOOL"
        auto_invoke_raw = tool.get("auto_invoke")
        if auto_invoke_raw is None:
            # Match auto_tool_handler's default: UI tools auto-invoke by default, agent tools do not.
            should_auto_invoke = bool(is_ui_tool)
        else:
            try:
                should_auto_invoke = bool(auto_invoke_raw)
            except Exception:
                should_auto_invoke = False
        
        for ag in agent_targets:
            # UI_Tools are validated by auto_tool_handler at the model level (before invocation)
            # and should NOT be validated again at the tool wrapper level because the wrapper
            # receives decomposed kwargs that don't match the structured output schema.
            # Example: mermaid_sequence_diagram receives {MermaidSequenceDiagram: dict, agent_name: str}
            # but the model is MermaidSequenceDiagramCall with nested structure.
            # For agent tools that are manually invoked (auto_invoke=false), validating kwargs against the
            # agent's structured output model will reject legitimate tool payloads. Only enforce schema
            # for auto-invoked tools where kwargs are expected to match the agent's structured output.
            enforce_schema = ag in structured_registry and should_auto_invoke and not is_ui_tool
            wrapped_func = _wrap_with_validation(
                workflow_name=workflow_name,
                agent_name=ag,
                tool_name=str(tool_identifier),
                func=func,
                enforce_schema=enforce_schema,
            )
            mapping.setdefault(ag, []).append(wrapped_func)
            logger.debug(
                "[TOOLS][TRACE] Bound function to agent -> workflow=%s agent=%s func=%s id=%s enforced=%s",
                workflow_name,
                ag,
                func_name,
                hex(id(wrapped_func)),
                enforce_schema,
            )
    # Emit a structured summary for post-mortem debugging
    summary = {agent: [getattr(f, '__name__', '<noname>') for f in funcs] for agent, funcs in mapping.items()}
    total_funcs = sum(len(v) for v in mapping.values())
    logger.info(f"[TOOLS] Bound {total_funcs} tool functions across {len(mapping)} agents for '{workflow_name}'")
    logger.debug(f"[TOOLS][TRACE] Tool binding summary for '{workflow_name}': {summary}")
    return mapping

def clear_tool_cache(workflow_name: Optional[str] = None) -> int:
    """Clear cached tool modules to force fresh reload.
    
    Args:
        workflow_name: If provided, only clear modules for this workflow.
                      If None, clear all mozaiks_* modules.
    
    Returns:
        Number of modules cleared from sys.modules cache.
    """
    cleared_count = 0
    modules_to_clear = []
    
    # Find modules to clear
    for module_name in sys.modules.keys():
        if workflow_name:
            # Clear only specific workflow modules
            if module_name.startswith(f"mozaiks_{workflow_name}_"):
                modules_to_clear.append(module_name)
        else:
            # Clear all mozaiks modules
            if module_name.startswith("mozaiks_"):
                modules_to_clear.append(module_name)
    
    # Clear the modules
    for module_name in modules_to_clear:
        try:
            del sys.modules[module_name]
            cleared_count += 1
            logger.debug(f"[TOOLS] Cleared cached module: {module_name}")
        except KeyError:
            # Module was already removed by another thread
            pass
    
    if cleared_count > 0:
        logger.info(f"[TOOLS] Cleared {cleared_count} cached tool modules")
    else:
        logger.debug("[TOOLS] No cached tool modules found to clear")
    
    return cleared_count

__all__ = [
    'load_agent_tool_functions',
    'clear_tool_cache',
]

