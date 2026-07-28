from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from ag2 import Context
from ag2.events import BaseEvent, ModelResponse
from ag2.middleware import BaseMiddleware, LLMCall, Middleware

from .artifacts import generated_media_artifact_payload
from .store import MediaAssetStore, get_media_asset_store
from .types import GeneratedMediaAsset, MediaPromotionTarget

logger = logging.getLogger("mozaiks.media.middleware")


def _ctx_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            return context_variables.get(key, default)
        except Exception:
            return default
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _ctx_set(context_variables: Any, key: str, value: Any) -> None:
    if context_variables is None:
        return
    if hasattr(context_variables, "set"):
        context_variables.set(key, value)
        return
    if hasattr(context_variables, "__setitem__"):
        context_variables[key] = value


def _normalize_promotion_targets(values: Iterable[MediaPromotionTarget | str] | None) -> list[MediaPromotionTarget]:
    targets: list[MediaPromotionTarget] = []
    seen: set[str] = set()
    for value in values or []:
        try:
            target = value if isinstance(value, MediaPromotionTarget) else MediaPromotionTarget(str(value))
        except ValueError:
            continue
        if target.value in seen:
            continue
        seen.add(target.value)
        targets.append(target)
    return targets or [MediaPromotionTarget.ARTIFACT]


def _append_generated_assets_context(
    context_variables: Any,
    assets: list[GeneratedMediaAsset],
) -> None:
    entries = [asset.model_dump(by_alias=False, mode="json") for asset in assets]
    existing = _ctx_get(context_variables, "generated_media_assets", [])
    if not isinstance(existing, list):
        existing = []
    _ctx_set(context_variables, "generated_media_assets", [*existing, *entries])
    _ctx_set(context_variables, "last_generated_media_assets", entries)


def _prompt_from_events(events: Sequence[BaseEvent]) -> str | None:
    for event in reversed(list(events or [])):
        content = getattr(event, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()[:4000]
    return None


async def harvest_generated_media_response(
    response: ModelResponse,
    *,
    agent_name: str,
    workflow_name: str,
    context_variables: Any,
    events: Sequence[BaseEvent] = (),
    generation_params: dict[str, Any] | None = None,
    promotion_targets: Iterable[MediaPromotionTarget | str] | None = None,
    asset_store: MediaAssetStore | None = None,
    transport: Any | None = None,
    emit_ui: bool = True,
) -> list[GeneratedMediaAsset]:
    files = list(getattr(response, "files", []) or [])
    if not files:
        return []

    app_id = str(_ctx_get(context_variables, "app_id", "") or "").strip()
    chat_id = str(_ctx_get(context_variables, "chat_id", "") or "").strip()
    if not app_id or not chat_id:
        logger.debug("Generated media harvest skipped: missing app_id/chat_id")
        return []

    store = asset_store or get_media_asset_store()
    resolved_targets = _normalize_promotion_targets(promotion_targets)
    prompt = _prompt_from_events(events)
    provider = getattr(response, "provider", None)
    model = getattr(response, "model", None)
    assets: list[GeneratedMediaAsset] = []

    for file_result in files:
        try:
            asset = await store.persist_generated_binary_result(
                file_result,
                app_id=app_id,
                source_workflow=workflow_name,
                source_chat_id=chat_id,
                prompt=prompt,
                provider=str(provider) if provider else None,
                model=str(model) if model else None,
                generation_params={
                    "agent_name": agent_name,
                    **dict(generation_params or {}),
                },
                promotion_targets=resolved_targets,
            )
        except Exception as exc:
            logger.warning(
                "Generated media persist failed workflow=%s agent=%s chat=%s: %s",
                workflow_name,
                agent_name,
                chat_id,
                exc,
            )
            continue
        assets.append(asset)

    if not assets:
        return []

    _append_generated_assets_context(context_variables, assets)

    if emit_ui:
        try:
            resolved_transport = transport
            if resolved_transport is None:
                from mozaiksai.core.transport.simple_transport import SimpleTransport

                resolved_transport = await SimpleTransport.get_instance()
            if resolved_transport is not None and hasattr(resolved_transport, "send_tool_call_event"):
                first_asset_id = assets[0].asset_id
                await resolved_transport.send_tool_call_event(
                    f"media-{first_asset_id}",
                    chat_id,
                    "core.media.generated_asset",
                    "core.media.generated_asset",
                    "artifact",
                    generated_media_artifact_payload(
                        assets,
                        app_id=app_id,
                        workflow_name=workflow_name,
                        chat_id=chat_id,
                        agent_name=agent_name,
                    ),
                    awaiting_response=False,
                    agent_name=agent_name,
                )
        except Exception as exc:
            logger.debug("Generated media UI emission skipped chat=%s: %s", chat_id, exc)

    return assets


class MozaiksMediaHarvestMiddleware(BaseMiddleware):
    """Persist AG2 generated binary files and surface them as Mozaiks artifacts."""

    def __init__(
        self,
        event: BaseEvent,
        context: Context,
        *,
        agent_name: str,
        workflow_name: str,
        context_variables: Any,
        generation_params: dict[str, Any] | None = None,
        promotion_targets: Iterable[MediaPromotionTarget | str] | None = None,
    ) -> None:
        super().__init__(event, context)
        self._agent_name = agent_name
        self._workflow_name = workflow_name
        self._context_variables = context_variables
        self._generation_params = dict(generation_params or {})
        self._promotion_targets = list(promotion_targets or [])

    async def on_llm_call(
        self,
        call_next: LLMCall,
        events: Sequence[BaseEvent],
        context: Context,
    ) -> ModelResponse:
        response = await call_next(events, context)
        await harvest_generated_media_response(
            response,
            agent_name=self._agent_name,
            workflow_name=self._workflow_name,
            context_variables=self._context_variables,
            events=events,
            generation_params=self._generation_params,
            promotion_targets=self._promotion_targets,
        )
        return response


def build_ag2_media_harvest_middleware(
    *,
    agent_name: str,
    workflow_name: str,
    context_variables: Any,
    generation_params: dict[str, Any] | None = None,
    promotion_targets: Iterable[MediaPromotionTarget | str] | None = None,
) -> Middleware:
    return Middleware(
        MozaiksMediaHarvestMiddleware,
        agent_name=agent_name,
        workflow_name=workflow_name,
        context_variables=context_variables,
        generation_params=generation_params,
        promotion_targets=list(promotion_targets or []),
    )


__all__ = [
    "MozaiksMediaHarvestMiddleware",
    "build_ag2_media_harvest_middleware",
    "harvest_generated_media_response",
]
