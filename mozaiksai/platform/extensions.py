from __future__ import annotations

"""Mozaiks Platform Extension Bundle
====================================
Registered via RUNTIME_PLATFORM_EXTENSIONS=mozaiksai.platform.extensions:get_bundle

Provides four hook callables that plug into the core runtime:

    on_startup(app)
        Mounts the Mozaiks platform APIRouter (themes, OAuth webhook, build
        export, general chats, available workflows) and initialises
        ThemeManager on app.state.

    chat_prereqs(*, app_id, user_id, workflow_name, persistence)
        Enforces pack prerequisite gates before a chat session is created.

    chat_session_fields(*, app_id, user_id, workflow_name, chat_id)
        Injects journey metadata (journey_id, journey_key, step index) into
        the ChatSession document at creation time.

    workflow_ordering(workflow_names)
        Reorders the workflow list by pack journey step sequence so the
        frontend's default selection aligns with prerequisites.
"""

from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("mozaiks_platform")


# ===========================================================================
# on_startup
# ===========================================================================

async def on_startup(app: Any) -> None:
    """Mount platform routes and initialise shared managers on app.state."""
    from mozaiksai.platform.routers import get_platform_router

    # Initialise ThemeManager backed by the runtime persistence layer.
    # persistence_manager is set on app.state by shared_app.py before hooks run.
    try:
        from mozaiksai.core.data.themes.theme_manager import ThemeManager
        pm = getattr(app.state, "persistence_manager", None)
        if pm is not None:
            app.state.platform_theme_manager = ThemeManager(pm.persistence)
            logger.info("MOZAIKS_PLATFORM: ThemeManager initialised on app.state")
        else:
            logger.warning("MOZAIKS_PLATFORM: persistence_manager not on app.state — ThemeManager skipped")
    except Exception as exc:
        logger.warning(f"MOZAIKS_PLATFORM: ThemeManager init failed: {exc}")

    # Initialise optional build-events processor (no-op when env vars absent).
    try:
        import importlib, os
        module_path = os.getenv("MOZAIKS_PLATFORM_BUILD_EVENTS_PROCESSOR_MODULE", "")
        class_name  = os.getenv("MOZAIKS_PLATFORM_BUILD_EVENTS_PROCESSOR_CLASS", "BuildEventsProcessor")
        if module_path:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name, None)
            if cls is not None:
                app.state.platform_build_events_processor = cls()
                logger.info(f"MOZAIKS_PLATFORM: BuildEventsProcessor loaded from {module_path}:{class_name}")
    except Exception as exc:
        logger.debug(f"MOZAIKS_PLATFORM: build events processor unavailable: {exc}")

    # Mount all Mozaiks-platform API routes.
    router = get_platform_router()
    app.include_router(router)
    logger.info("MOZAIKS_PLATFORM: platform routes mounted")


# ===========================================================================
# chat_prereqs
# ===========================================================================

async def chat_prereqs(
    *,
    app_id: str,
    user_id: str,
    workflow_name: str,
    persistence: Any,
) -> Tuple[bool, Optional[str]]:
    """Enforce pack prerequisite gates (required upstream workflows completed)."""
    try:
        from mozaiksai.core.workflow.pack.gating import validate_pack_prereqs
        return await validate_pack_prereqs(
            app_id=app_id,
            user_id=user_id,
            workflow_name=workflow_name,
            persistence=persistence,
        )
    except ImportError:
        return True, None
    except Exception as exc:
        logger.warning(f"MOZAIKS_PLATFORM: chat_prereqs error (failing open): {exc}")
        return True, None


# ===========================================================================
# chat_session_fields
# ===========================================================================

async def chat_session_fields(
    *,
    app_id: str,
    user_id: str,
    workflow_name: str,
    chat_id: str,
) -> Dict[str, Any]:
    """Return journey metadata fields to embed in the ChatSession document."""
    try:
        from mozaiksai.core.workflow.pack.config import (
            load_pack_config,
            infer_auto_journey_for_start,
        )
        pack = load_pack_config()
        journey = infer_auto_journey_for_start(pack, workflow_name) if pack else None
        if not journey:
            return {}
        steps: List = journey.get("steps") if isinstance(journey.get("steps"), list) else []
        return {
            "journey_id": str(uuid4()),
            "journey_key": str(journey.get("id") or "").strip(),
            "journey_step_index": 0,
            "journey_total_steps": len(steps),
        }
    except ImportError:
        return {}
    except Exception as exc:
        logger.debug(f"MOZAIKS_PLATFORM: chat_session_fields skipped: {exc}")
        return {}


# ===========================================================================
# workflow_ordering
# ===========================================================================

def workflow_ordering(workflow_names: List[str]) -> List[str]:
    """Order workflow list by the first pack journey's step sequence."""
    try:
        from mozaiksai.core.workflow.pack.config import load_pack_config
        pack = load_pack_config()
        journeys = pack.get("journeys") if isinstance(pack, dict) else None
        if not isinstance(journeys, list) or not journeys:
            return workflow_names

        steps = journeys[0].get("steps") if isinstance(journeys[0], dict) else None
        if not isinstance(steps, list):
            return workflow_names

        # Flatten nested step arrays.
        flattened: List[str] = []
        for step in steps:
            if isinstance(step, str):
                flattened.append(step)
            elif isinstance(step, list):
                for item in step:
                    if isinstance(item, str):
                        flattened.append(item)

        ordered: List[str] = []
        for wf in flattened:
            if wf in workflow_names and wf not in ordered:
                ordered.append(wf)
        for wf in workflow_names:
            if wf not in ordered:
                ordered.append(wf)
        return ordered
    except Exception:
        return workflow_names


# ===========================================================================
# Bundle factory
# ===========================================================================

def get_bundle() -> Dict[str, Any]:
    """Return the Mozaiks platform extension bundle.

    Referenced by RUNTIME_PLATFORM_EXTENSIONS=mozaiksai.platform.extensions:get_bundle
    """
    return {
        "on_startup": on_startup,
        "chat_prereqs": chat_prereqs,
        "chat_session_fields": chat_session_fields,
        "workflow_ordering": workflow_ordering,
    }
