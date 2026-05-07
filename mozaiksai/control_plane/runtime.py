from __future__ import annotations

import inspect
from importlib import import_module
from typing import Any, Callable, Optional

from .executor import ControlPlaneToolExecutor
from .loader import load_selected_control_plane_pack
from .schema import ControlPlaneCheckpointManifest, LoadedControlPlanePack


class ControlPlaneHandlerResolutionError(RuntimeError):
    """Raised when a declarative control-plane handler cannot be resolved."""


class ControlPlaneCheckpointRuntime:
    """Runtime loader/cache for checkpoint-declared control-plane handlers."""

    def __init__(
        self,
        *,
        pack_loader: Any = load_selected_control_plane_pack,
        pack: LoadedControlPlanePack | dict[str, Any] | None = None,
        tool_executor: ControlPlaneToolExecutor | None = None,
    ) -> None:
        self._pack_loader = pack_loader
        self._pack = self._normalize_pack(pack, pack_loader=pack_loader)
        self._tool_executor = tool_executor or ControlPlaneToolExecutor(pack_loader=pack_loader)
        self._instances: dict[str, Any] = {}

    @property
    def pack(self) -> LoadedControlPlanePack:
        return self._pack

    @property
    def tool_executor(self) -> ControlPlaneToolExecutor:
        return self._tool_executor

    def checkpoint(self, event_name: str) -> Optional[ControlPlaneCheckpointManifest]:
        return self.pack.checkpoint_by_event(event_name)

    def has_checkpoint(self, event_name: str) -> bool:
        return self.checkpoint(event_name) is not None

    def checkpoint_events(self) -> list[str]:
        return [checkpoint.event for checkpoint in self.pack.manifest.checkpoints]

    def get_checkpoint(self, event_name: str) -> Any:
        if event_name in self._instances:
            return self._instances[event_name]
        return self.bind_checkpoint(event_name)

    def bind_checkpoint(self, event_name: str, /, **dependencies: Any) -> Any:
        checkpoint = self.checkpoint(event_name)
        if checkpoint is None:
            return None
        instance = instantiate_control_plane_handler(
            checkpoint.entrypoint,
            pack_loader=self._pack_loader,
            tool_executor=self._tool_executor,
            **dependencies,
        )
        self._instances[event_name] = instance
        return instance

    @staticmethod
    def _normalize_pack(
        pack: LoadedControlPlanePack | dict[str, Any] | None,
        *,
        pack_loader: Any = load_selected_control_plane_pack,
    ) -> LoadedControlPlanePack:
        if isinstance(pack, LoadedControlPlanePack):
            return pack
        if pack is not None:
            return LoadedControlPlanePack.model_validate(pack)
        loaded = pack_loader()
        return loaded if isinstance(loaded, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(loaded)


def resolve_control_plane_handler_entrypoint(entrypoint: str) -> Callable[..., Any]:
    module_name, separator, attr_name = str(entrypoint or "").partition(":")
    if not separator or not module_name or not attr_name:
        raise ControlPlaneHandlerResolutionError(
            f"Invalid control-plane handler entrypoint '{entrypoint}'. Expected 'module.path:callable_name'."
        )
    try:
        module = import_module(module_name)
    except Exception as exc:
        raise ControlPlaneHandlerResolutionError(
            f"Failed to import control-plane handler module '{module_name}': {exc}"
        ) from exc
    try:
        handler = getattr(module, attr_name)
    except AttributeError as exc:
        raise ControlPlaneHandlerResolutionError(
            f"Control-plane handler '{attr_name}' was not found in '{module_name}'."
        ) from exc
    if not callable(handler):
        raise ControlPlaneHandlerResolutionError(
            f"Control-plane handler entrypoint '{entrypoint}' did not resolve to a callable."
        )
    return handler


def instantiate_control_plane_handler(entrypoint: str, /, **dependencies: Any) -> Any:
    handler = resolve_control_plane_handler_entrypoint(entrypoint)
    parameters = inspect.signature(handler).parameters
    kwargs = {
        name: value
        for name, value in dependencies.items()
        if value is not None and name in parameters
    }
    return handler(**kwargs)


def build_selected_control_plane_harness(
    *,
    pack_loader: Any = load_selected_control_plane_pack,
) -> Any:
    pack = pack_loader()
    if not isinstance(pack, LoadedControlPlanePack):
        pack = LoadedControlPlanePack.model_validate(pack)

    checkpoint_runtime = ControlPlaneCheckpointRuntime(
        pack_loader=pack_loader,
        pack=pack,
        tool_executor=ControlPlaneToolExecutor(pack_loader=pack_loader),
    )
    harness_manifest = pack.manifest.harness
    return instantiate_control_plane_handler(
        harness_manifest.implementation,
        checkpoint_runtime=checkpoint_runtime,
        pack_loader=pack_loader,
        tool_executor=checkpoint_runtime.tool_executor,
    )

__all__ = [
    "ControlPlaneCheckpointRuntime",
    "ControlPlaneHandlerResolutionError",
    "build_selected_control_plane_harness",
    "instantiate_control_plane_handler",
    "resolve_control_plane_handler_entrypoint",
]
