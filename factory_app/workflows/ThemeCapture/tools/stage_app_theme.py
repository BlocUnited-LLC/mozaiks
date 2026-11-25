"""Stage a visual-only redesign on the verified app selected in Studio."""

from __future__ import annotations

import json
from typing import Any

from factory_app.workflows._shared.brand_revision import stage_brand_revision
from factory_app.workflows._shared.hook_utils import merge_config_mapping
from factory_app.workflows._shared.platform.build_target import require_build_binding
from mozaiksai.core.artifacts import get_artifact_store, persist_summary_artifact
from mozaiksai.core.runtime.app.theme_contract import APP_THEME_PATH, validate_page_title_theme
from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run
from mozaiksai.core.workflow.context.frozen import detach


def apply_visual_tokens(parent: dict[str, Any], captured: dict[str, Any]) -> dict[str, Any]:
    """Only a visual change was approved. Keep identity, assets and other app settings."""
    tokens = {key: captured[key] for key in ("theme", "colors", "fonts", "primitives", "shadows", "ui") if key in captured}
    if isinstance(tokens.get("theme"), dict):
        tokens["theme"] = {key: value for key, value in tokens["theme"].items() if key != "branding"}
    theme = merge_config_mapping(parent, tokens)
    validate_page_title_theme(theme)
    return theme


async def stage_app_theme(context: Any, captured: dict[str, Any]) -> tuple[dict[str, Any], str]:
    run = active_workflow_tool_run()
    workflow, _, chat, user = run
    binding = require_build_binding(context)
    if (workflow != "ThemeCapture" or binding.phase != "refinement" or context.get("chat_id") != chat
            or context.get("user_id") != user):
        raise ValueError("A saved app theme requires its registered ThemeCapture session")
    parent = detach(context.get("parent_theme_config"))
    if not isinstance(parent, dict):
        raise ValueError("The saved app theme is unavailable")
    theme = apply_visual_tokens(parent, captured)
    store = get_artifact_store()
    prior = await store.list_build_records(
        app_id=binding.target_app_id, build_family="theme_capture", build_key="theme_capture", source_chat_id=chat, limit=1,
    )
    payload = {
        "app_id": binding.target_app_id, "chat_id": chat, "identity": theme.get("identity") or {}, "theme_config": theme,
        "source_build_record_id": context.get("artifact_version_id"), "change_request_id": context.get("change_request_id"),
    }
    if prior:
        summary = prior[0]
        if summary.commit_metadata.metadata.get("summary_payload") != payload:
            raise ValueError("A different theme was already captured in this chat")
    else:
        summary = await persist_summary_artifact(
            app_id=binding.target_app_id, artifact_kind="theme_capture", artifact_key="theme_capture",
            summary_payload=payload, source_workflow=workflow, source_chat_id=chat, author_user_id=user,
            input_artifact_kinds=("concept",), draft_only=True, artifact_store=store,
        )

    def require_active_run() -> None:
        if active_workflow_tool_run() != run:
            raise PermissionError("ThemeCapture session changed")

    async def update_theme(files: dict[str, str | bytes]) -> dict[str, Any]:
        if json.loads(files.get(APP_THEME_PATH, "{}")) != parent:
            raise ValueError("ThemeCapture input no longer matches the selected app theme")
        files[APP_THEME_PATH] = json.dumps(theme, indent=2, ensure_ascii=False) + "\n"
        return {"path": APP_THEME_PATH}

    draft = await stage_brand_revision(
        context, run=run, workflow_sequence="app_theme_revision", theme_capture=summary,
        selection={"theme_capture_id": summary.id}, apply=update_theme, guard=require_active_run,
    )
    return theme, draft.id
