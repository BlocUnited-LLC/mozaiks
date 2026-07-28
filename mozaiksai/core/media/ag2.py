from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .types import (
    MediaInputRef,
    MediaKind,
    MediaSource,
    is_ag2_input_media_type,
    media_kind_from_mime,
    normalize_media_type,
)

_IMAGE_GENERATION_TOOL_PARAMS = {
    "quality",
    "size",
    "background",
    "output_format",
    "output_compression",
    "partial_images",
}


def _agent_media_config(agent_config: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(agent_config, Mapping):
        return {}
    raw = agent_config.get("image_generation")
    if isinstance(raw, Mapping):
        return dict(raw)
    if raw is True:
        return {}
    return {}


def image_generation_enabled(agent_config: Mapping[str, Any] | None) -> bool:
    if not isinstance(agent_config, Mapping):
        return False
    if bool(agent_config.get("image_generation_enabled")):
        return True
    return "image_generation" in agent_config and agent_config.get("image_generation") is not None


def multimodal_inputs_enabled(agent_config: Mapping[str, Any] | None) -> bool:
    if not isinstance(agent_config, Mapping):
        return False
    return bool(agent_config.get("multimodal_inputs_enabled") or image_generation_enabled(agent_config))


def provider_api_type(llm_config: Mapping[str, Any] | None) -> str:
    config_list = (llm_config or {}).get("config_list")
    if isinstance(config_list, list) and config_list and isinstance(config_list[0], Mapping):
        return str(config_list[0].get("api_type") or "openai").strip().lower()
    return "openai"


def prepare_llm_config_for_media(
    agent_config: Mapping[str, Any] | None,
    llm_config: dict[str, Any],
) -> dict[str, Any]:
    """Return an AG2 llm_config copy adjusted for declared media generation."""

    prepared = copy.deepcopy(llm_config or {})
    if not image_generation_enabled(agent_config):
        return prepared

    api_type = provider_api_type(prepared)
    if api_type == "openai":
        prepared["use_responses_api"] = True
        return prepared
    if api_type == "google":
        prepared.setdefault("response_modalities", ["TEXT", "IMAGE"])
        generation_config = _agent_media_config(agent_config)
        image_config = generation_config.get("image_config")
        if image_config is not None:
            prepared["image_config"] = image_config
        return prepared
    return prepared


def build_ag2_image_generation_tools(
    agent_config: Mapping[str, Any] | None,
    llm_config: Mapping[str, Any] | None,
    *,
    logger: Any | None = None,
    agent_name: str | None = None,
) -> list[Any]:
    """Return provider-appropriate AG2 image generation tools for an agent."""

    if not image_generation_enabled(agent_config):
        return []

    api_type = provider_api_type(llm_config)
    if api_type == "google":
        return []
    if api_type != "openai":
        if logger is not None:
            logger.warning(
                "[MEDIA] image_generation_enabled ignored for agent=%s provider=%s",
                agent_name or "<unknown>",
                api_type,
            )
        return []

    try:
        from ag2.tools import ImageGenerationTool
    except Exception as exc:
        if logger is not None:
            logger.warning(
                "[MEDIA] ImageGenerationTool unavailable for agent=%s: %s",
                agent_name or "<unknown>",
                exc,
            )
        return []

    tool_kwargs = {
        key: value
        for key, value in _agent_media_config(agent_config).items()
        if key in _IMAGE_GENERATION_TOOL_PARAMS and value is not None
    }
    return [ImageGenerationTool(**tool_kwargs)]


def media_input_ref_to_ag2_input(ref: MediaInputRef) -> Any:
    """Convert a provider-neutral media input ref to an AG2 input object."""

    if not is_ag2_input_media_type(ref.media_type):
        raise ValueError(f"media_type {ref.media_type!r} is not supported by AG2 input factories")

    from ag2.events import AudioInput, DocumentInput, ImageInput, VideoInput

    factory_by_kind = {
        MediaKind.IMAGE: ImageInput,
        MediaKind.AUDIO: AudioInput,
        MediaKind.VIDEO: VideoInput,
        MediaKind.DOCUMENT: DocumentInput,
    }
    factory = cast(Any, factory_by_kind[ref.kind])
    if ref.data is not None:
        ag2_input = factory(data=ref.data, media_type=ref.media_type)
    elif ref.path:
        ag2_input = factory(path=ref.path)
    elif ref.url:
        ag2_input = factory(ref.url)
    elif ref.file_id:
        kwargs: dict[str, Any] = {"file_id": ref.file_id}
        if ref.filename:
            kwargs["filename"] = ref.filename
        ag2_input = factory(**kwargs)
    else:
        raise ValueError("MediaInputRef requires data, path, url, or file_id for AG2 conversion")

    if ref.metadata:
        try:
            ag2_input.metadata = dict(ref.metadata)
        except Exception:
            pass
    if ref.vendor_metadata:
        try:
            ag2_input.vendor_metadata = dict(ref.vendor_metadata)
        except Exception:
            pass
    return ag2_input


def media_input_refs_to_ag2_inputs(refs: list[MediaInputRef] | tuple[MediaInputRef, ...]) -> list[Any]:
    return [media_input_ref_to_ag2_input(ref) for ref in refs]


def attachment_to_media_input_ref(
    attachment: Mapping[str, Any],
    *,
    include_data: bool = False,
) -> MediaInputRef | None:
    """Convert a ChatSessions.attachments item to a MediaInputRef when possible."""

    media_type = normalize_media_type(attachment.get("content_type"))
    kind = media_kind_from_mime(media_type)
    if kind is None:
        return None
    stored_path = str(attachment.get("stored_path") or "").strip()
    data = None
    if include_data and stored_path:
        path = Path(stored_path)
        if path.exists() and path.is_file():
            data = path.read_bytes()

    if not stored_path and data is None:
        return None
    return MediaInputRef(
        kind=kind,
        source=MediaSource.UPLOADED,
        media_type=media_type,
        filename=str(attachment.get("filename") or "").strip() or None,
        path=None if data is not None else stored_path,
        data=data,
        attachment_id=str(attachment.get("attachment_id") or "").strip() or None,
        metadata={
            "source": "chat_attachment",
            "intent": attachment.get("intent"),
            "bundle_path": attachment.get("bundle_path"),
        },
    )


__all__ = [
    "attachment_to_media_input_ref",
    "build_ag2_image_generation_tools",
    "image_generation_enabled",
    "media_input_ref_to_ag2_input",
    "media_input_refs_to_ag2_inputs",
    "multimodal_inputs_enabled",
    "prepare_llm_config_for_media",
    "provider_api_type",
]
