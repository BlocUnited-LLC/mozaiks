from __future__ import annotations

import inspect
from collections.abc import Callable
from importlib import import_module
from typing import Any

from logs.logging_config import get_workflow_logger

from .contracts import ControlPlaneToolCall, ControlPlaneToolContext, ControlPlaneToolResult
from .loader import load_selected_control_plane_pack
from .schema import LoadedControlPlanePack

logger = get_workflow_logger("control_plane.executor")


class ControlPlaneToolExecutionError(RuntimeError):
    """Raised when a control-plane tool cannot be resolved or executed."""


def resolve_control_plane_tool_entrypoint(entrypoint: str) -> Callable[..., Any]:
    module_name, separator, attr_name = str(entrypoint or "").partition(":")
    if not separator or not module_name or not attr_name:
        raise ControlPlaneToolExecutionError(
            f"Invalid control-plane tool entrypoint '{entrypoint}'. Expected 'module.path:callable_name'."
        )
    try:
        module = import_module(module_name)
    except Exception as exc:
        raise ControlPlaneToolExecutionError(
            f"Failed to import control-plane tool module '{module_name}': {exc}"
        ) from exc
    try:
        tool_callable = getattr(module, attr_name)
    except AttributeError as exc:
        raise ControlPlaneToolExecutionError(
            f"Control-plane tool callable '{attr_name}' was not found in '{module_name}'."
        ) from exc
    if not callable(tool_callable):
        raise ControlPlaneToolExecutionError(
            f"Control-plane tool entrypoint '{entrypoint}' did not resolve to a callable."
        )
    return tool_callable  # type: ignore[no-any-return]


class ControlPlaneToolExecutor:
    """Generic executor for declarative control-plane tools."""

    def __init__(self, *, pack_loader: Any = load_selected_control_plane_pack) -> None:
        self._pack_loader = pack_loader

    async def execute_tool(
        self,
        call: ControlPlaneToolCall,
        *,
        context: ControlPlaneToolContext | dict[str, Any] | None = None,
    ) -> ControlPlaneToolResult:
        pack = self._load_pack()
        tool = pack.tool_by_id(call.tool_id)
        if tool is None:
            raise ControlPlaneToolExecutionError(f"Unknown control-plane tool '{call.tool_id}'.")
        if call.target and call.target not in tool.available_to:
            raise ControlPlaneToolExecutionError(
                f"Control-plane tool '{call.tool_id}' is not available to target '{call.target}'."
            )

        normalized_context = self._normalize_context(context, target=call.target)
        tool_callable = resolve_control_plane_tool_entrypoint(tool.entrypoint)
        try:
            result = await self._invoke(tool_callable, call=call, context=normalized_context)
        except ControlPlaneToolExecutionError:
            raise
        except Exception as exc:
            logger.warning(
                "Control-plane tool execution failed",
                extra={"tool_id": call.tool_id, "target": call.target, "error": str(exc)},
            )
            return ControlPlaneToolResult(success=False, error=str(exc), metadata={"tool_id": call.tool_id})

        if isinstance(result, ControlPlaneToolResult):
            return result
        return ControlPlaneToolResult(success=True, output=result, metadata={"tool_id": call.tool_id})

    def _load_pack(self) -> LoadedControlPlanePack:
        pack = self._pack_loader()
        return pack if isinstance(pack, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(pack)

    @staticmethod
    def _normalize_context(
        context: ControlPlaneToolContext | dict[str, Any] | None,
        *,
        target: str | None,
    ) -> ControlPlaneToolContext:
        if isinstance(context, ControlPlaneToolContext):
            if target and not context.checkpoint:
                return context.model_copy(update={"checkpoint": target})
            return context
        payload = dict(context or {})
        if target and not payload.get("checkpoint"):
            payload["checkpoint"] = target
        return ControlPlaneToolContext.model_validate(payload)

    @staticmethod
    async def _invoke(
        tool_callable: Callable[..., Any],
        *,
        call: ControlPlaneToolCall,
        context: ControlPlaneToolContext,
    ) -> Any:
        kwargs = dict(call.arguments or {})
        parameters = inspect.signature(tool_callable).parameters
        if "context" in parameters and "context" not in kwargs:
            kwargs["context"] = context
        if "tool_context" in parameters and "tool_context" not in kwargs:
            kwargs["tool_context"] = context

        result = tool_callable(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


__all__ = [
    "ControlPlaneToolExecutionError",
    "ControlPlaneToolExecutor",
    "resolve_control_plane_tool_entrypoint",
]
