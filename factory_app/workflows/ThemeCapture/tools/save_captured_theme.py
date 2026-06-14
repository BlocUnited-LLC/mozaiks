"""Persist the ThemeCapture artifact and emit a review card."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from mozaiksai.core.artifacts import persist_summary_artifact
from mozaiksai.core.workflow.ui_tools import emit_ui_surface

logger = logging.getLogger(__name__)

try:
    from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore

    _HAS_PERSISTENCE = True
except ImportError:
    _HAS_PERSISTENCE = False
    BuilderArtifactStore = None  # type: ignore[assignment,misc]


def _set_context_value(context_variables: Any | None, key: str, value: Any) -> None:
    if context_variables is None or not key:
        return
    try:
        if hasattr(context_variables, "set"):
            context_variables.set(key, value)
            return
    except Exception:
        pass
    try:
        context_variables[key] = value
    except Exception:
        return


async def save_captured_theme(
    context_variables: Annotated[Any | None, "Runtime context"] = None,
) -> dict[str, Any]:
    """Persist the canonical captured theme_config and emit a preview card."""
    if not context_variables:
        return {"success": False, "error": "No context provided"}

    data = context_variables.get("structured_output")
    if not data:
        return {
            "success": False,
            "error": "No structured output from ThemeConfigAssemblerAgent",
        }

    chat_id = context_variables.get("chat_id")
    app_id = context_variables.get("app_id")
    app_url = context_variables.get("app_url")
    user_id = context_variables.get("user_id")
    build_mode = context_variables.get("build_mode")

    agent_message = data.get("agent_message", "Theme captured successfully.")
    theme_config = {key: value for key, value in data.items() if key != "agent_message"}
    identity = theme_config.get("identity") or {}
    colors = theme_config.get("colors") or {}
    fonts = theme_config.get("fonts") or {}
    theme_v2 = theme_config.get("theme") or {}

    persistence_id = str(app_id or identity.get("app_name") or identity.get("name") or "captured-theme")
    now = datetime.now(UTC)

    if _HAS_PERSISTENCE and BuilderArtifactStore:  # type: ignore[truthy-function]
        try:
            store = BuilderArtifactStore()
            await store.save_theme_capture(
                app_id=persistence_id,
                chat_id=str(chat_id) if chat_id else None,
                app_url=app_url,
                identity=identity,
                theme_config=theme_config,
            )
        except Exception as exc:
            logger.warning("[ThemeCapture] Persistence failed: %s", exc)

    try:
        await persist_summary_artifact(
            app_id=persistence_id,
            artifact_kind="theme_capture",
            artifact_key="theme_capture",
            summary_payload={
                "app_id": persistence_id,
                "chat_id": str(chat_id) if chat_id else None,
                "app_url": app_url,
                "identity": identity,
                "theme_config": theme_config,
                "updated_at": now.isoformat(),
            },
            source_workflow="ThemeCapture",
            source_chat_id=str(chat_id) if chat_id else None,
            author_user_id=str(user_id) if user_id else None,
            revision_mode=str(build_mode or "").strip().lower() == "revision",
            input_artifact_kinds=("concept",),
        )
    except Exception as exc:
        logger.warning("[ThemeCapture] Generic theme artifact persistence failed: %s", exc)

    ui_payload = {
        "title": f"Theme: {identity.get('app_name', 'Captured Theme')}",
        "message": agent_message,
        "appearance": theme_v2.get("appearance", "system"),
        "primaryColor": colors.get("primary", {}).get("main", "#06b6d4"),
        "secondaryColor": colors.get("secondary", {}).get("main", "#8b5cf6"),
        "accentColor": colors.get("accent", {}).get("main", "#f59e0b"),
        "backgroundColor": colors.get("background", {}).get("base", "#0b1220"),
        "surfaceColor": colors.get("background", {}).get("surface", "#0f1724"),
        "bodyFont": fonts.get("body", {}).get("family", "system-ui"),
        "headingFont": fonts.get("heading", {}).get("family", "system-ui"),
        "variant": theme_v2.get("variant", "modern"),
        "radius": theme_v2.get("radius", "medium"),
        "themeConfig": theme_config,
    }

    await emit_ui_surface(
        "ThemePreviewCard",
        ui_payload,
        chat_id=str(chat_id) if chat_id else None,
        workflow_name="ThemeCapture",
        agent_name="ThemeConfigAssemblerAgent",
    )

    _set_context_value(context_variables, "captured_theme_config", theme_config)
    _set_context_value(context_variables, "theme_capture_persisted", True)

    logger.info(
        "[ThemeCapture] Saved theme for '%s' (app_id=%s)",
        identity.get("app_name", "unknown"),
        persistence_id,
    )

    return {
        "success": True,
        "message": agent_message,
        "app_id": persistence_id,
        "theme_config_keys": list(theme_config.keys()),
    }
