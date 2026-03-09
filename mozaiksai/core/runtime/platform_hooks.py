from __future__ import annotations

"""Platform Hook Registry
======================
Allows proprietary platform layers (Mozaiks, or any deployer) to inject
behaviour into the core runtime at well-defined callsites WITHOUT modifying
shared_app.py.

Open-source deployments:
    Leave RUNTIME_PLATFORM_EXTENSIONS unset.  All hook callers return safe
    no-op defaults and the runtime works as a clean AG2 runtime.

Platform deployments (e.g. Mozaiks):
    Set the env var to one or more comma-separated entrypoints:

        RUNTIME_PLATFORM_EXTENSIONS=mozaiksai.platform.extensions:get_bundle

    Each entrypoint is a ``module.path:attr`` string.  The attribute must be
    a callable returning a bundle dict (or object with the same keys), or
    directly be such a bundle.

Bundle keys (all optional):
    on_startup            async (app: FastAPI) -> None
        Mount extra routers, initialise managers, etc.  Called during FastAPI
        startup after persistence_manager and simple_transport are available on
        app.state.

    chat_prereqs          async (*, app_id, user_id, workflow_name, persistence)
                              -> Tuple[bool, Optional[str]]
        Gate check before creating a chat session.  First hook that returns
        (False, reason) wins.

    chat_session_fields   async (*, app_id, user_id, workflow_name, chat_id)
                              -> Dict[str, Any]
        Extra fields to merge into the chat session document at creation time
        (e.g. journey_id, journey_key).

    workflow_ordering     (workflow_names: List[str]) -> List[str]
        Reorder the workflow list returned to the frontend (e.g. by journey
        step sequence).
"""

import importlib
import inspect
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("platform_hooks")

_BUNDLE_KEYS = ("on_startup", "chat_prereqs", "chat_session_fields", "workflow_ordering")


def _resolve_entrypoint(entrypoint: str) -> Any:
    """Load ``module.path:attr`` and return the attribute."""
    if ":" not in entrypoint:
        raise ValueError(f"Invalid entrypoint (expected 'module:attr'): {entrypoint!r}")
    module_path, attr = entrypoint.split(":", 1)
    module = importlib.import_module(module_path.strip())
    obj = getattr(module, attr.strip(), None)
    if obj is None:
        raise ImportError(f"Attribute not found: {entrypoint!r}")
    return obj


class PlatformHookRegistry:
    """Singleton registry for optional platform-layer hooks.

    Loaded lazily on first ``get_instance()`` call from the
    ``RUNTIME_PLATFORM_EXTENSIONS`` environment variable.
    """

    _instance: Optional["PlatformHookRegistry"] = None

    def __init__(self) -> None:
        self._startup_hooks: List[Callable] = []
        self._chat_prereqs_hooks: List[Callable] = []
        self._chat_session_fields_hooks: List[Callable] = []
        self._workflow_ordering_hooks: List[Callable] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "PlatformHookRegistry":
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._load()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        raw = os.getenv("RUNTIME_PLATFORM_EXTENSIONS", "").strip()
        if not raw:
            logger.debug(
                "PLATFORM_HOOKS: RUNTIME_PLATFORM_EXTENSIONS not set — "
                "running as open-source runtime (all hooks are no-ops)"
            )
            return

        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                obj = _resolve_entrypoint(entry)
                bundle = obj() if callable(obj) else obj
                self._register_bundle(bundle, source=entry)
                logger.info(f"PLATFORM_HOOKS_LOADED: {entry}")
            except Exception as exc:
                logger.warning(f"PLATFORM_HOOKS_LOAD_FAILED: {entry} — {exc}")

    def _register_bundle(self, bundle: Any, source: str = "") -> None:
        def _get(key: str) -> Any:
            if isinstance(bundle, dict):
                return bundle.get(key)
            return getattr(bundle, key, None)

        slot_map = {
            "on_startup": self._startup_hooks,
            "chat_prereqs": self._chat_prereqs_hooks,
            "chat_session_fields": self._chat_session_fields_hooks,
            "workflow_ordering": self._workflow_ordering_hooks,
        }
        for key, target in slot_map.items():
            val = _get(key)
            if callable(val):
                target.append(val)
                logger.debug(f"PLATFORM_HOOKS_REGISTERED: {key!r} from {source!r}")

    # ------------------------------------------------------------------
    # Hook callers
    # ------------------------------------------------------------------

    async def run_startup(self, app: Any) -> None:
        """Run all platform startup handlers.

        Called from ``shared_app.startup()`` after ``app.state.persistence_manager``
        and ``app.state.simple_transport`` have been set.
        """
        for hook in self._startup_hooks:
            try:
                res = hook(app)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                logger.warning(f"PLATFORM_HOOKS_STARTUP_ERROR: {exc}")

        if self._startup_hooks:
            logger.info(f"PLATFORM_HOOKS: ran {len(self._startup_hooks)} startup hook(s)")

    async def call_chat_prereqs(
        self,
        app_id: str,
        user_id: str,
        workflow_name: str,
        persistence: Any,
    ) -> Tuple[bool, Optional[str]]:
        """Gate check before creating/resuming a chat session.

        Returns ``(True, None)`` when no hooks are registered or all pass.
        Returns ``(False, reason)`` on the first failing hook.
        """
        for hook in self._chat_prereqs_hooks:
            try:
                res = hook(
                    app_id=app_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    persistence=persistence,
                )
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, tuple) and len(res) == 2:
                    ok, reason = res
                    if not ok:
                        return False, str(reason) if reason else "Prerequisite not met"
            except Exception as exc:
                logger.warning(f"PLATFORM_HOOKS_PREREQS_ERROR: {exc}")
        return True, None

    async def call_chat_session_fields(
        self,
        app_id: str,
        user_id: str,
        workflow_name: str,
        chat_id: str,
    ) -> Dict[str, Any]:
        """Collect extra fields to inject into the chat session document.

        Returns an empty dict when no hooks are registered.
        Multiple hooks are merged (later hooks win on key collision).
        """
        extra: Dict[str, Any] = {}
        for hook in self._chat_session_fields_hooks:
            try:
                res = hook(
                    app_id=app_id,
                    user_id=user_id,
                    workflow_name=workflow_name,
                    chat_id=chat_id,
                )
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, dict):
                    extra.update(res)
            except Exception as exc:
                logger.warning(f"PLATFORM_HOOKS_SESSION_FIELDS_ERROR: {exc}")
        return extra

    def call_workflow_ordering(self, workflow_names: List[str]) -> List[str]:
        """Reorder the workflow list for frontend display.

        Returns the original list unchanged when no hooks are registered.
        """
        result = list(workflow_names)
        for hook in self._workflow_ordering_hooks:
            try:
                ordered = hook(result)
                if isinstance(ordered, list):
                    result = ordered
            except Exception as exc:
                logger.warning(f"PLATFORM_HOOKS_ORDERING_ERROR: {exc}")
        return result

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def has_prereqs(self) -> bool:
        return bool(self._chat_prereqs_hooks)

    @property
    def has_session_fields(self) -> bool:
        return bool(self._chat_session_fields_hooks)

    @property
    def has_startup(self) -> bool:
        return bool(self._startup_hooks)

    def summary(self) -> Dict[str, Any]:
        return {
            "startup_hooks": len(self._startup_hooks),
            "chat_prereqs_hooks": len(self._chat_prereqs_hooks),
            "chat_session_fields_hooks": len(self._chat_session_fields_hooks),
            "workflow_ordering_hooks": len(self._workflow_ordering_hooks),
        }


def get_platform_hooks() -> PlatformHookRegistry:
    """Return the singleton PlatformHookRegistry, loading env extensions on first call."""
    return PlatformHookRegistry.get_instance()
