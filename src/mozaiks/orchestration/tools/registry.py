"""Tool registry and binder implementation."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from ..domain.runtime_context import RuntimeContext
from ..domain.tool_spec import ToolCall, ToolResult, ToolSpec
from ..interfaces.tool_binder import ToolBinder
from .ui_flow import build_ui_tool_id, is_ui_blocking, normalize_ui_submission

ToolHandler = Callable[..., Any] | Callable[..., Awaitable[Any]]
_MAX_STDIO_CHARS = 8 * 1024


@dataclass(slots=True, kw_only=True)
class _RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    """In-memory tool registry."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        self._tools[spec.name] = _RegisteredTool(spec=spec, handler=handler)

    def register_callable(
        self,
        *,
        name: str,
        description: str,
        handler: ToolHandler,
        input_schema: dict[str, Any] | None = None,
        auto_invoke: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ToolSpec:
        spec = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema or {},
            auto_invoke=auto_invoke,
            metadata=metadata or {},
        )
        self.register(spec, handler)
        return spec

    def get_spec(self, name: str) -> ToolSpec | None:
        registered = self._tools.get(name)
        if registered is None:
            return None
        return registered.spec

    def list_specs(self) -> list[ToolSpec]:
        return [registered.spec for registered in self._tools.values()]

    def get_handler(self, name: str) -> ToolHandler | None:
        registered = self._tools.get(name)
        if registered is None:
            return None
        return registered.handler


class RegistryToolBinder(ToolBinder):
    """Tool binder backed by an in-memory registry."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._allowed: set[str] | None = None
        self._bound_specs: dict[str, ToolSpec] = {}

    async def bind(self, tools: Sequence[ToolSpec]) -> None:
        if not tools:
            self._allowed = None
            self._bound_specs = {}
            return
        self._allowed = {tool.name for tool in tools}
        self._bound_specs = {tool.name: tool for tool in tools}

    async def invoke(
        self,
        call: ToolCall,
        *,
        run_id: str,
        runtime_context: RuntimeContext | None = None,
    ) -> ToolResult:
        spec = self._resolve_spec(call.tool_name)
        tool_id = self._tool_id(spec, call.tool_name)
        tool_type = self._tool_type(spec)
        is_ui_tool = self._is_ui_tool_spec(spec)
        ui_component = self._ui_component(spec)
        ui_mode = self._ui_mode(spec)
        ui_blocking = self._ui_blocking(spec)
        ui_tool_id = build_ui_tool_id(
            run_id=run_id,
            task_id=call.task_id,
            tool_name=call.tool_name,
            arguments=call.arguments,
        )
        ui_submission = self._ui_submission(runtime_context, ui_tool_id)
        ui_submission_received = ui_submission is not None
        effective_arguments = dict(call.arguments)
        if is_ui_tool and ui_blocking:
            if ui_submission is None:
                return ToolResult(
                    tool_name=call.tool_name,
                    success=False,
                    error="ui_input_required",
                    metadata={
                        "tool_id": tool_id,
                        "execution_mode": "in_process",
                        "tool_type": tool_type,
                        "is_ui_tool": is_ui_tool,
                        "ui_component": ui_component,
                        "ui_mode": ui_mode,
                        "ui_tool_id": ui_tool_id,
                        "ui_blocking": ui_blocking,
                        "ui_submission_received": False,
                        "ui_submission": None,
                        "awaiting_input": True,
                        "stdout": "",
                        "stderr": "",
                    },
                )
            effective_arguments.update(ui_submission)

        effective_call = ToolCall(
            tool_name=call.tool_name,
            arguments=effective_arguments,
            task_id=call.task_id,
        )
        requested_mode = self._requested_execution_mode(spec)
        execution_mode = self._effective_execution_mode(
            requested_mode=requested_mode,
            runtime_context=runtime_context,
        )

        if self._allowed is not None and effective_call.tool_name not in self._allowed:
            return ToolResult(
                tool_name=effective_call.tool_name,
                success=False,
                error="tool_not_bound_for_task",
                metadata={
                    "tool_id": tool_id,
                    "execution_mode": execution_mode,
                    "tool_type": tool_type,
                    "is_ui_tool": is_ui_tool,
                    "ui_component": ui_component,
                    "ui_mode": ui_mode,
                    "ui_tool_id": ui_tool_id if is_ui_tool else None,
                    "ui_blocking": ui_blocking,
                    "ui_submission_received": ui_submission_received,
                    "ui_submission": dict(ui_submission) if ui_submission_received else None,
                    "awaiting_input": False,
                    "stdout": "",
                    "stderr": "",
                },
            )

        if execution_mode in {"sandbox_python", "sandbox_node"} and runtime_context is not None:
            sandbox_result = await self._invoke_sandbox(
                run_id=run_id,
                call=effective_call,
                spec=spec,
                runtime_context=runtime_context,
                execution_mode=execution_mode,
                tool_id=tool_id,
            )
            sandbox_result.metadata["ui_tool_id"] = ui_tool_id if is_ui_tool else None
            sandbox_result.metadata["ui_blocking"] = ui_blocking
            sandbox_result.metadata["ui_submission_received"] = ui_submission_received
            sandbox_result.metadata["ui_submission"] = (
                dict(ui_submission) if ui_submission_received else None
            )
            sandbox_result.metadata["awaiting_input"] = False
            return sandbox_result

        handler = self._registry.get_handler(effective_call.tool_name)
        if handler is None:
            return ToolResult(
                tool_name=effective_call.tool_name,
                success=False,
                error="tool_not_found",
                metadata={
                    "tool_id": tool_id,
                    "execution_mode": "in_process",
                    "tool_type": tool_type,
                    "is_ui_tool": is_ui_tool,
                    "ui_component": ui_component,
                    "ui_mode": ui_mode,
                    "ui_tool_id": ui_tool_id if is_ui_tool else None,
                    "ui_blocking": ui_blocking,
                    "ui_submission_received": ui_submission_received,
                    "ui_submission": dict(ui_submission) if ui_submission_received else None,
                    "awaiting_input": False,
                    "stdout": "",
                    "stderr": "",
                },
            )

        try:
            result = handler(**effective_call.arguments)
            if inspect.isawaitable(result):
                result = await result
            return ToolResult(
                tool_name=effective_call.tool_name,
                success=True,
                output=result,
                metadata={
                    "tool_id": tool_id,
                    "execution_mode": "in_process",
                    "tool_type": tool_type,
                    "is_ui_tool": is_ui_tool,
                    "ui_component": ui_component,
                    "ui_mode": ui_mode,
                    "ui_tool_id": ui_tool_id if is_ui_tool else None,
                    "ui_blocking": ui_blocking,
                    "ui_submission_received": ui_submission_received,
                    "ui_submission": dict(ui_submission) if ui_submission_received else None,
                    "awaiting_input": False,
                    "stdout": "",
                    "stderr": "",
                },
            )
        except Exception as exc:  # pragma: no cover - defensive path
            return ToolResult(
                tool_name=effective_call.tool_name,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                metadata={
                    "tool_id": tool_id,
                    "execution_mode": "in_process",
                    "tool_type": tool_type,
                    "is_ui_tool": is_ui_tool,
                    "ui_component": ui_component,
                    "ui_mode": ui_mode,
                    "ui_tool_id": ui_tool_id if is_ui_tool else None,
                    "ui_blocking": ui_blocking,
                    "ui_submission_received": ui_submission_received,
                    "ui_submission": dict(ui_submission) if ui_submission_received else None,
                    "awaiting_input": False,
                    "stdout": "",
                    "stderr": "",
                },
            )

    def _resolve_spec(self, tool_name: str) -> ToolSpec | None:
        registry_spec = self._registry.get_spec(tool_name)
        if registry_spec is not None:
            return registry_spec
        return self._bound_specs.get(tool_name)

    def _tool_id(self, spec: ToolSpec | None, fallback_name: str) -> str:
        if spec is None:
            return fallback_name
        raw_id = spec.metadata.get("tool_id")
        if raw_id is None:
            return spec.name
        return str(raw_id)

    def _tool_type(self, spec: ToolSpec | None) -> str | None:
        if spec is None:
            return None
        raw_type = spec.metadata.get("tool_type")
        if raw_type is None:
            return None
        return str(raw_type)

    def _ui_component(self, spec: ToolSpec | None) -> str | None:
        if spec is None:
            return None
        raw_component = spec.metadata.get("ui_component")
        if raw_component is None:
            raw_ui = spec.metadata.get("ui")
            if isinstance(raw_ui, dict):
                raw_component = raw_ui.get("component")
        if raw_component is None:
            return None
        return str(raw_component)

    def _ui_mode(self, spec: ToolSpec | None) -> str | None:
        if spec is None:
            return None
        raw_mode = spec.metadata.get("ui_mode")
        if raw_mode is None:
            raw_ui = spec.metadata.get("ui")
            if isinstance(raw_ui, dict):
                raw_mode = raw_ui.get("mode")
        if raw_mode is None:
            return None
        return str(raw_mode)

    def _is_ui_tool_spec(self, spec: ToolSpec | None) -> bool:
        if spec is None:
            return False
        raw_ui = spec.metadata.get("is_ui_tool")
        if isinstance(raw_ui, bool):
            return raw_ui
        if isinstance(raw_ui, str):
            return raw_ui.strip().lower() in {"1", "true", "yes", "on"}

        tool_type = self._tool_type(spec)
        if tool_type is not None and tool_type.strip().lower().replace("-", "_") in {
            "ui_tool",
            "ui",
        }:
            return True

        if isinstance(spec.metadata.get("ui"), dict):
            return True

        raw_mode = spec.metadata.get("interaction_mode")
        if raw_mode is None:
            raw_mode = self._ui_mode(spec)
        if raw_mode is None:
            return False
        return str(raw_mode).strip().lower() in {"inline", "artifact"}

    def _ui_blocking(self, spec: ToolSpec | None) -> bool:
        if spec is None:
            return False
        raw_blocking = spec.metadata.get("blocking")
        if raw_blocking is None:
            raw_ui = spec.metadata.get("ui")
            if isinstance(raw_ui, dict):
                raw_blocking = raw_ui.get("blocking")
        return is_ui_blocking(raw_blocking)

    def _ui_submission(
        self,
        runtime_context: RuntimeContext | None,
        ui_tool_id: str,
    ) -> dict[str, Any] | None:
        if runtime_context is None:
            return None
        raw_submission = runtime_context.ui_tool_submissions.get(ui_tool_id)
        return normalize_ui_submission(raw_submission)

    def _requested_execution_mode(self, spec: ToolSpec | None) -> str:
        if spec is None:
            return "in_process"
        raw_mode = spec.metadata.get("execution", "in_process")
        mode = str(raw_mode)
        if mode not in {"in_process", "sandbox_python", "sandbox_node"}:
            return "in_process"
        return mode

    def _effective_execution_mode(
        self,
        *,
        requested_mode: str,
        runtime_context: RuntimeContext | None,
    ) -> str:
        if requested_mode not in {"sandbox_python", "sandbox_node"}:
            return "in_process"
        if runtime_context is None or runtime_context.sandbox is None:
            return "in_process"
        return requested_mode

    async def _invoke_sandbox(
        self,
        *,
        run_id: str,
        call: ToolCall,
        spec: ToolSpec | None,
        runtime_context: RuntimeContext,
        execution_mode: str,
        tool_id: str,
    ) -> ToolResult:
        sandbox = runtime_context.sandbox
        if sandbox is None:
            return ToolResult(
                tool_name=call.tool_name,
                success=False,
                error="sandbox_unavailable",
                metadata={
                    "tool_id": tool_id,
                    "execution_mode": "in_process",
                    "stdout": "",
                    "stderr": "",
                },
            )

        secrets: dict[str, str] = {}
        if runtime_context.secrets is not None:
            secrets = await runtime_context.secrets.get_secrets(scope=f"run:{run_id}")

        env = {str(key): str(value) for key, value in runtime_context.env.items()}
        env.update({str(key): str(value) for key, value in secrets.items()})
        env["MOZAIKS_RUN_ID"] = run_id
        env["MOZAIKS_TASK_ID"] = call.task_id or ""
        env["MOZAIKS_TOOL_NAME"] = call.tool_name
        env["MOZAIKS_TOOL_EXECUTION_MODE"] = execution_mode

        metadata = spec.metadata if spec is not None else {}
        source_files = self._normalize_source_files(metadata.get("source_files"))

        if execution_mode == "sandbox_python":
            requirements = self._normalize_string_list(metadata.get("requirements"))
            result = await sandbox.execute_python(
                tool_name=call.tool_name,
                arguments=call.arguments,
                env=env,
                requirements=requirements,
                source_files=source_files,
            )
        else:
            npm_packages = self._normalize_string_list(metadata.get("npm_packages"))
            result = await sandbox.execute_node(
                tool_name=call.tool_name,
                arguments=call.arguments,
                env=env,
                npm_packages=npm_packages,
                source_files=source_files,
            )

        bounded_stdout = self._truncate(str(result.stdout))
        bounded_stderr = self._truncate(str(result.stderr))
        result_metadata = {
            "tool_id": tool_id,
            "execution_mode": execution_mode,
            "tool_type": self._tool_type(spec),
            "is_ui_tool": self._is_ui_tool_spec(spec),
            "ui_component": self._ui_component(spec),
            "ui_mode": self._ui_mode(spec),
            "stdout": bounded_stdout,
            "stderr": bounded_stderr,
            **{str(k): v for k, v in result.metadata.items()},
        }
        return ToolResult(
            tool_name=call.tool_name,
            success=result.success,
            output=result.output,
            error=result.error,
            metadata=result_metadata,
        )

    def _normalize_string_list(self, raw: object) -> list[str] | None:
        if raw is None:
            return None
        if not isinstance(raw, (list, tuple, set)):
            return None
        return [str(value) for value in raw]

    def _normalize_source_files(self, raw: object) -> dict[str, str] | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        normalized: dict[str, str] = {}
        for key, value in raw.items():
            normalized[str(key)] = str(value)
        return normalized

    def _truncate(self, raw: str) -> str:
        if len(raw) <= _MAX_STDIO_CHARS:
            return raw
        return raw[:_MAX_STDIO_CHARS]
