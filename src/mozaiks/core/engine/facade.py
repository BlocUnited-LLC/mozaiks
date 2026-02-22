from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import AsyncIterator
from typing import Any, Callable

from mozaiks.core.context import RuntimeContext
from mozaiks.contracts import AI_RUNNER_PROTOCOL_VERSION, DomainEvent, ResumeRequest, RunRequest
from mozaiks.contracts.ports import AIWorkflowRunnerPort


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class AIUnavailableError(RuntimeError):
    pass


class AIEngineFacade:
    def __init__(self, *, enabled: bool | None = None, adapter: str | None = None) -> None:
        self._enabled = _env_bool("MOZAIKS_ENABLE_AI", False) if enabled is None else enabled
        self._adapter = (adapter or os.getenv("MOZAIKS_AI_ADAPTER", "mock")).strip() or "mock"
        self._runner: AIWorkflowRunnerPort | None = None

    def availability(self) -> tuple[bool, str | None]:
        if not self._enabled:
            return False, "AI execution is disabled (set MOZAIKS_ENABLE_AI=true to enable run execution)."

        try:
            runner = self._get_runner()
        except AIUnavailableError as exc:
            return False, str(exc)

        capabilities = runner.capabilities()
        protocol_version = str(capabilities.get("protocol_version", ""))
        if protocol_version != AI_RUNNER_PROTOCOL_VERSION:
            return (
                False,
                "Protocol mismatch: expected "
                f"{AI_RUNNER_PROTOCOL_VERSION}, got {protocol_version or 'unknown'}",
            )
        return True, None

    def runner_capabilities(self) -> dict[str, Any]:
        """Return the current runner's capability dict.

        Raises :class:`AIUnavailableError` if no runner can be resolved.
        """
        runner = self._get_runner()
        return dict(runner.capabilities())

    async def run(self, request: RunRequest, *, runtime_context: RuntimeContext | None = None) -> AsyncIterator[DomainEvent]:
        available, reason = self.availability()
        if not available:
            raise AIUnavailableError(reason or "AI execution is unavailable.")

        runner = self._get_runner()
        await self._bind_runtime_context(runner, runtime_context)
        async for domain_event in runner.run(request):
            yield domain_event

    async def resume(
        self,
        request: ResumeRequest,
        *,
        runtime_context: RuntimeContext | None = None,
    ) -> AsyncIterator[DomainEvent]:
        available, reason = self.availability()
        if not available:
            raise AIUnavailableError(reason or "AI execution is unavailable.")

        runner = self._get_runner()
        await self._bind_runtime_context(runner, runtime_context)
        async for domain_event in runner.resume(request):
            yield domain_event

    async def cancel(self, run_id: str) -> None:
        available, reason = self.availability()
        if not available:
            raise AIUnavailableError(reason or "AI execution is unavailable.")
        runner = self._get_runner()
        await runner.cancel(run_id)

    def _get_runner(self) -> AIWorkflowRunnerPort:
        if self._runner is not None:
            return self._runner

        module = self._try_import("mozaiks_ai")
        if module is None:
            raise AIUnavailableError("mozaiks-ai is not installed.")

        factory = self._resolve_factory(module)
        if factory is None:
            raise AIUnavailableError("mozaiks-ai does not expose create_ai_workflow_runner() or create_runner().")

        try:
            candidate = self._call_sync(factory, {"adapter": self._adapter})
        except Exception as exc:
            raise AIUnavailableError(f"Failed to initialize mozaiks-ai runner: {exc}") from exc

        if not isinstance(candidate, AIWorkflowRunnerPort):
            raise AIUnavailableError("mozaiks-ai runner does not implement AIWorkflowRunnerPort.")

        self._runner = candidate
        return candidate

    def _call_sync(self, callable_obj: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
        signature = inspect.signature(callable_obj)
        params = signature.parameters
        if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in params.values()):
            filtered = kwargs
        else:
            filtered = {key: value for key, value in kwargs.items() if key in params}

        result = callable_obj(**filtered)
        if inspect.isawaitable(result):
            raise AIUnavailableError("Runner factory unexpectedly returned awaitable value.")
        return result

    def _resolve_factory(self, module: Any) -> Callable[..., Any] | None:
        candidate = getattr(module, "create_ai_workflow_runner", None)
        if callable(candidate):
            return candidate
        candidate = getattr(module, "create_runner", None)
        if callable(candidate):
            return candidate
        return None

    @staticmethod
    def _try_import(module_name: str) -> Any | None:
        try:
            return importlib.import_module(module_name)
        except ImportError:
            return None

    async def _bind_runtime_context(
        self,
        runner: AIWorkflowRunnerPort,
        runtime_context: RuntimeContext | None,
    ) -> None:
        if runtime_context is None:
            return

        for method_name in ("set_runtime_context", "set_context"):
            method = getattr(runner, method_name, None)
            if callable(method):
                result = method(runtime_context)
                if inspect.isawaitable(result):
                    await result
                return

        # Best-effort fallback for runners that expose context as an attribute.
        try:
            setattr(runner, "runtime_context", runtime_context)
        except Exception:
            return
