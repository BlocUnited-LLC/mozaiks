# ==============================================================================
# FILE: core/workflow/lifecycle_tools.py
# DESCRIPTION: Workflow-agnostic lifecycle tool execution for orchestration hooks
# ==============================================================================

"""
Lifecycle Tools - Declarative Hook System for Workflows

Purpose:
- Execute tools at orchestration boundaries (before_chat, after_chat, before_agent, after_agent)
- Driven by workflows/<workflow>/tools.json "lifecycle_tools" array
- Integrates with existing event system for observability
- AG2-native context injection via ContextVariables

Schema (tools.json):
{
  "lifecycle_tools": [
    {
      "trigger": "before_chat",       # Required: before_chat | after_chat | before_agent | after_agent
      "agent": null,                  # Optional: Agent name for before_agent/after_agent, null for chat-level
      "file": "echo.py",              # Required: Tool file path (supports root or tools/ subdir)
      "function": "echo",             # Required: Function name to invoke
      "description": "..."            # Optional: For logging/observability
    }
  ]
}

Runtime Contract:
- Tools receive context via 'context_variables' parameter (AG2 dependency injection)
- Execution is non-blocking and error-tolerant (failures log but don't halt workflow)
- Events emitted for observability: lifecycle.tool_call, lifecycle.tool_result
"""

from __future__ import annotations
import asyncio
import importlib.util
import inspect
import json
import logging
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from logs.logging_config import get_workflow_logger
from logs.tools_logs import get_tool_logger, log_tool_event

logger = logging.getLogger(__name__)


class LifecycleTrigger(Enum):
    """Valid lifecycle hook trigger points."""
    BEFORE_CHAT = "before_chat"
    AFTER_CHAT = "after_chat"
    BEFORE_AGENT = "before_agent"
    AFTER_AGENT = "after_agent"


@dataclass
class LifecycleTool:
    """Runtime representation of a lifecycle tool binding."""
    trigger: LifecycleTrigger
    agent: Optional[str]  # None for chat-level hooks, agent_name for agent-level
    file: str
    function: str
    description: Optional[str]
    callable: Callable[..., Any]
    accepts_context: bool


class LifecycleToolManager:
    """Manages loading and execution of lifecycle tools for workflows."""

    def __init__(self, workflow_name: str):
        self.workflow_name = workflow_name
        self.tools: Dict[LifecycleTrigger, List[LifecycleTool]] = {
            LifecycleTrigger.BEFORE_CHAT: [],
            LifecycleTrigger.AFTER_CHAT: [],
            LifecycleTrigger.BEFORE_AGENT: [],
            LifecycleTrigger.AFTER_AGENT: [],
        }
        self._loaded = False

    def load_lifecycle_tools(self) -> None:
        """Load lifecycle tools from workflows/<workflow>/tools.yaml + platform defaults."""
        if self._loaded:
            return

        # Load platform-default lifecycle tools (always enabled for all workflows)
        self._load_platform_defaults()

        # Then load workflow-specific lifecycle tools

        base_dir = Path('workflows') / self.workflow_name
        tools_yaml_path = base_dir / 'tools.yaml'

        if not tools_yaml_path.exists():
            logger.debug(f"[LIFECYCLE] No tools.yaml for workflow '{self.workflow_name}'")
            self._loaded = True
            return

        try:
            import yaml
            data = yaml.safe_load(tools_yaml_path.read_text(encoding='utf-8')) or {}
        except Exception as err:
            logger.warning(f"[LIFECYCLE] Failed to parse tools.yaml for '{self.workflow_name}': {err}")
            self._loaded = True
            return

        lifecycle_entries = data.get('lifecycle_tools', [])
        if not isinstance(lifecycle_entries, list):
            logger.warning(f"[LIFECYCLE] 'lifecycle_tools' is not a list in '{self.workflow_name}'")
            self._loaded = True
            return

        logger.info(f"[LIFECYCLE] Loading {len(lifecycle_entries)} lifecycle tools for '{self.workflow_name}'")

        for idx, entry in enumerate(lifecycle_entries, start=1):
            if not isinstance(entry, dict):
                logger.warning(f"[LIFECYCLE] Entry {idx} is not a dict, skipping")
                continue

            # Validate required fields
            trigger_str = entry.get('trigger')
            file_name = entry.get('file')
            func_name = entry.get('function')

            if not all([trigger_str, file_name, func_name]):
                logger.warning(
                    f"[LIFECYCLE] Entry {idx} missing required fields (trigger/file/function), skipping"
                )
                continue

            # Validate trigger
            try:
                trigger = LifecycleTrigger(trigger_str)
            except ValueError:
                logger.warning(
                    f"[LIFECYCLE] Invalid trigger '{trigger_str}' in entry {idx}, skipping. "
                    f"Valid: {[t.value for t in LifecycleTrigger]}"
                )
                continue

            agent_name = entry.get('agent')  # Optional, None for chat-level hooks
            description = entry.get('description', f"{func_name} ({trigger_str})")

            # Load the tool function
            tool_callable = self._load_tool_function(base_dir, file_name, func_name)
            if not tool_callable:
                logger.warning(f"[LIFECYCLE] Failed to load {func_name} from {file_name}, skipping")
                continue

            # Check if function accepts context_variables parameter
            sig = inspect.signature(tool_callable)
            accepts_context = 'context_variables' in sig.parameters

            # Create binding
            lifecycle_tool = LifecycleTool(
                trigger=trigger,
                agent=agent_name,
                file=file_name,
                function=func_name,
                description=description,
                callable=tool_callable,
                accepts_context=accepts_context,
            )

            self.tools[trigger].append(lifecycle_tool)
            logger.info(
                f"[LIFECYCLE] Registered {func_name} for {trigger.value}"
                f"{f' (agent={agent_name})' if agent_name else ''} - context_aware={accepts_context}"
            )

        total = sum(len(v) for v in self.tools.values())
        logger.info(f"[LIFECYCLE] Loaded {total} lifecycle tools for '{self.workflow_name}'")
        self._loaded = True

    def _load_platform_defaults(self) -> None:
        """Platform defaults are intentionally disabled.

        The runtime emits lifecycle events centrally from the LifecycleToolManager
        (lightweight tool_call/tool_result/tool_error via tools logging/dispatcher).
        Historically we loaded a separate `logs/workflow_lifecycle.py` module here,
        but that file has been removed and lifecycle logging is handled centrally.
        """
        logger.debug("[LIFECYCLE] No platform-default lifecycle tools registered (disabled)")
        return

    def _load_tool_function(
        self, base_dir: Path, file_name: str, func_name: str
    ) -> Optional[Callable]:
        """Load a single tool function from file."""
        # Support both root and tools/ subdir
        base_dir_tools = base_dir / 'tools'
        candidate_paths = [base_dir / file_name, base_dir_tools / file_name]
        file_path: Optional[Path] = next((p for p in candidate_paths if p.exists()), None)

        if not file_path:
            logger.warning(f"[LIFECYCLE] Tool file '{file_name}' not found in workflow '{self.workflow_name}'")
            return None

        # Load module (ephemeral, no sys.modules caching)
        module_name = f"mozaiks_lifecycle_{self.workflow_name}_{file_path.stem}_{id(file_path)}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if not spec or not spec.loader:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as err:
            logger.warning(f"[LIFECYCLE] Failed to import {file_path}: {err}")
            return None

        # Get function
        try:
            func = getattr(module, func_name)
        except AttributeError:
            logger.warning(f"[LIFECYCLE] Function '{func_name}' not found in {file_path}")
            return None

        if not callable(func):
            logger.warning(f"[LIFECYCLE] '{func_name}' in {file_path} is not callable")
            return None

        return func

    async def trigger_before_chat(self, context_variables: Any = None) -> None:
        """Execute all before_chat lifecycle tools."""
        await self._execute_tools(LifecycleTrigger.BEFORE_CHAT, context_variables=context_variables)

    async def trigger_after_chat(self, context_variables: Any = None) -> None:
        """Execute all after_chat lifecycle tools."""
        await self._execute_tools(LifecycleTrigger.AFTER_CHAT, context_variables=context_variables)

    async def trigger_before_agent(self, agent_name: str, context_variables: Any = None) -> None:
        """Execute before_agent lifecycle tools for specific agent."""
        await self._execute_tools(
            LifecycleTrigger.BEFORE_AGENT,
            agent_name=agent_name,
            context_variables=context_variables,
        )

    async def trigger_after_agent(self, agent_name: str, context_variables: Any = None) -> None:
        """Execute after_agent lifecycle tools for specific agent."""
        await self._execute_tools(
            LifecycleTrigger.AFTER_AGENT,
            agent_name=agent_name,
            context_variables=context_variables,
        )

    async def execute_trigger(
        self,
        trigger: "LifecycleTrigger | str",
        **kwargs: Any,
    ) -> None:
        """Compatibility shim for legacy trigger dispatchers."""

        # Normalize trigger value to LifecycleTrigger enum where possible
        resolved_trigger: Optional[LifecycleTrigger]
        if isinstance(trigger, LifecycleTrigger):
            resolved_trigger = trigger
        else:
            try:
                resolved_trigger = LifecycleTrigger(str(trigger))
            except Exception:
                logger.debug(f"[LIFECYCLE] Unknown trigger '{trigger}', skipping")
                return

        ctx_vars = kwargs.get("context_variables")

        if resolved_trigger is LifecycleTrigger.BEFORE_CHAT:
            await self.trigger_before_chat(context_variables=ctx_vars)
            return

        if resolved_trigger is LifecycleTrigger.AFTER_CHAT:
            await self.trigger_after_chat(context_variables=ctx_vars)
            return

        if resolved_trigger is LifecycleTrigger.BEFORE_AGENT:
            agent_name = kwargs.get("agent_name") or kwargs.get("agent")
            if not agent_name:
                logger.debug("[LIFECYCLE] before_agent trigger missing agent_name; skipping")
                return
            await self.trigger_before_agent(agent_name=agent_name, context_variables=ctx_vars)
            return

        if resolved_trigger is LifecycleTrigger.AFTER_AGENT:
            agent_name = kwargs.get("agent_name") or kwargs.get("agent")
            if not agent_name:
                logger.debug("[LIFECYCLE] after_agent trigger missing agent_name; skipping")
                return
            await self.trigger_after_agent(agent_name=agent_name, context_variables=ctx_vars)
            return

        logger.debug(f"[LIFECYCLE] Trigger '{resolved_trigger.value}' not supported in compatibility shim")

    async def _execute_tools(
        self,
        trigger: LifecycleTrigger,
        agent_name: Optional[str] = None,
        context_variables: Any = None,
    ) -> None:
        """Execute all tools for a given trigger point."""
        tools = self.tools.get(trigger, [])
        if not tools:
            return

        # Filter by agent if agent-level trigger
        if agent_name:
            # For agent-level triggers, only run tools targeting this agent or null (all agents)
            tools = [t for t in tools if t.agent is None or t.agent == agent_name]

        if not tools:
            return

        wf_logger = get_workflow_logger(
            workflow_name=self.workflow_name,
            chat_id=getattr(context_variables, 'data', {}).get('chat_id') if context_variables else None,
        )

        wf_logger.info(
            f"[LIFECYCLE] Executing {len(tools)} tools for {trigger.value}"
            f"{f' (agent={agent_name})' if agent_name else ''}"
        )

        # Execute all tools for this trigger (in parallel, non-blocking)
        tasks = [
            self._execute_single_tool(tool, context_variables, wf_logger)
            for tool in tools
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_single_tool(
        self,
        tool: LifecycleTool,
        context_variables: Any,
        wf_logger: logging.Logger,
    ) -> None:
        """Execute a single lifecycle tool with error handling."""
        start_time = asyncio.get_event_loop().time()
        tool_name = f"{tool.function} ({tool.file})"

        try:
            # Build kwargs
            kwargs = {}
            if tool.accepts_context and context_variables:
                kwargs['context_variables'] = context_variables

            wf_logger.info(
                f"[LIFECYCLE] Calling {tool_name} for {tool.trigger.value}"
                f"{f' (agent={tool.agent})' if tool.agent else ''}"
            )

            # Emit lightweight 'tool_call' event via tools logger (no heavy metrics)
            try:
                tool_logger = get_tool_logger(
                    tool_name="lifecycle_tool",
                    chat_id=getattr(context_variables, 'data', {}).get('chat_id') if context_variables else None,
                    app_id=getattr(context_variables, 'data', {}).get('app_id') if context_variables else None,
                    workflow_name=self.workflow_name,
                )
                log_tool_event(
                    tool_logger,
                    action="tool_call",
                    status="started",
                    message=f"lifecycle:{tool.function}:call",
                    trigger=tool.trigger.value,
                    agent=tool.agent,
                    function=tool.function,
                )
            except Exception:
                # non-fatal; continue execution
                wf_logger.debug("[LIFECYCLE] failed to emit lightweight tool_call log")

            # Invoke (async or sync)
            if inspect.iscoroutinefunction(tool.callable):
                result = await tool.callable(**kwargs)
            else:
                result = tool.callable(**kwargs)

            elapsed = asyncio.get_event_loop().time() - start_time
            wf_logger.info(
                f"[LIFECYCLE] ✓ {tool_name} completed in {elapsed:.3f}s"
                f"{f' -> {result}' if result else ''}"
            )

            # Emit observability event (optional - can integrate with UnifiedEventDispatcher)
            await self._emit_lifecycle_event(
                "lifecycle.tool_result",
                {
                    "trigger": tool.trigger.value,
                    "agent": tool.agent,
                    "function": tool.function,
                    "file": tool.file,
                    "status": "success",
                    "elapsed_ms": elapsed * 1000,
                    "result": str(result) if result else None,
                },
                wf_logger,
            )

        except Exception as err:
            elapsed = asyncio.get_event_loop().time() - start_time
            wf_logger.error(
                f"[LIFECYCLE] ✗ {tool_name} failed in {elapsed:.3f}s: {err}",
                exc_info=True,
            )

            # Emit failure event
            await self._emit_lifecycle_event(
                "lifecycle.tool_error",
                {
                    "trigger": tool.trigger.value,
                    "agent": tool.agent,
                    "function": tool.function,
                    "file": tool.file,
                    "status": "error",
                    "elapsed_ms": elapsed * 1000,
                    "error": str(err),
                },
                wf_logger,
            )

    async def _emit_lifecycle_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        wf_logger: logging.Logger,
    ) -> None:
        """Emit lifecycle event for observability (integrates with existing event system)."""
        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
            dispatcher = get_event_dispatcher()
            
            # Emit as business event for logging/observability
            await dispatcher.emit_business_event(
                log_event_type=event_type,
                description=f"Lifecycle tool {payload.get('function')} {payload.get('status')}",
                context=payload,
                level="INFO" if payload.get('status') == 'success' else "ERROR",
            )
        except Exception as emit_err:
            wf_logger.debug(f"[LIFECYCLE] Failed to emit event: {emit_err}")


def get_lifecycle_manager(workflow_name: str) -> LifecycleToolManager:
    """Factory function to get a lifecycle tool manager for a workflow."""
    manager = LifecycleToolManager(workflow_name)
    manager.load_lifecycle_tools()
    return manager


__all__ = [
    'LifecycleTrigger',
    'LifecycleTool',
    'LifecycleToolManager',
    'get_lifecycle_manager',
]

