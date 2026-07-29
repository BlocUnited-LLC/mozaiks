from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mozaiksai.core.adapters.llm_fallback import llm_config_to_ag2_config
from mozaiksai.core.media.ag2 import (
    attachment_to_media_input_ref,
    build_ag2_image_generation_tools,
    image_generation_enabled,
    media_input_ref_to_ag2_input,
    prepare_llm_config_for_media,
    provider_api_type,
)
from mozaiksai.core.media.artifacts import generated_media_artifact_payload, media_asset_content_url
from mozaiksai.core.media.middleware import harvest_generated_media_response
from mozaiksai.core.media.store import (
    LocalMediaContentStore,
    MediaAssetStore,
    MediaContentNotFoundError,
)
from mozaiksai.core.media.types import (
    CHAT_ATTACHMENT_MIME_TYPES,
    MediaInputRef,
    MediaKind,
    MediaPromotionTarget,
    media_kind_from_mime,
)
from mozaiksai.core.workflow.declarative.contracts import AgentSpec


def _set_dotted(doc: dict, path: str, value) -> None:  # noqa: ANN001
    parts = path.split(".")
    target = doc
    for part in parts[:-1]:
        nested = target.get(part)
        if not isinstance(nested, dict):
            nested = {}
            target[part] = nested
        target = nested
    target[parts[-1]] = value


class _Collection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def create_index(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None

    async def insert_one(self, doc: dict):
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def find_one(self, query: dict):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None

    async def update_one(self, query: dict, update: dict):
        doc = await self.find_one(query)
        if doc is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for key, value in dict(update.get("$set") or {}).items():
            _set_dotted(doc, key, value)
        for key, value in dict(update.get("$addToSet") or {}).items():
            existing = doc
            parts = key.split(".")
            for part in parts[:-1]:
                existing = existing.setdefault(part, {})
            current = existing.setdefault(parts[-1], [])
            if value not in current:
                current.append(value)
        return SimpleNamespace(matched_count=1, modified_count=1)


class _Database:
    def __init__(self, collection: _Collection) -> None:
        self.collection = collection

    def __getitem__(self, name: str) -> _Collection:
        return self.collection


class _Client:
    def __init__(self, collection: _Collection) -> None:
        self.collection = collection

    def __getitem__(self, name: str) -> _Database:
        return _Database(self.collection)


def test_media_kind_from_mime_classifies_supported_types() -> None:
    assert media_kind_from_mime("image/png") is MediaKind.IMAGE
    assert media_kind_from_mime("audio/mpeg") is MediaKind.AUDIO
    assert media_kind_from_mime("video/mp4") is MediaKind.VIDEO
    assert media_kind_from_mime("application/pdf") is MediaKind.DOCUMENT
    assert media_kind_from_mime("image/png; charset=binary") is MediaKind.IMAGE
    assert media_kind_from_mime("application/octet-stream") is None


def test_chat_attachment_policy_uses_shared_media_mime_set() -> None:
    assert "image/png" in CHAT_ATTACHMENT_MIME_TYPES
    assert "application/pdf" in CHAT_ATTACHMENT_MIME_TYPES
    assert "text/markdown" in CHAT_ATTACHMENT_MIME_TYPES


def test_media_input_ref_requires_source_reference() -> None:
    with pytest.raises(ValidationError, match="one of url"):
        MediaInputRef(kind=MediaKind.IMAGE, media_type="image/png")


def test_media_input_ref_rejects_kind_mismatch() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        MediaInputRef(kind=MediaKind.DOCUMENT, media_type="image/png", url="https://example.test/a.png")


def test_agent_spec_accepts_multimodal_and_image_generation_options() -> None:
    spec = AgentSpec.model_validate({
        "name": "BrandDesigner",
        "system_message": "Design brand media.",
        "multimodal_inputs_enabled": True,
        "image_generation": {
            "quality": "high",
            "size": "1024x1024",
            "background": "transparent",
            "output_format": "png",
            "promotion_targets": ["brand_asset", "campaign_asset"],
        },
    })

    assert spec.multimodal_inputs_enabled is True
    assert spec.image_generation_enabled is True
    assert spec.image_generation is not None
    assert spec.image_generation.size == "1024x1024"
    assert spec.image_generation.promotion_targets == ["brand_asset", "campaign_asset"]
    assert image_generation_enabled({"image_generation": {}}) is True


def test_prepare_llm_config_for_openai_image_generation_uses_responses_api() -> None:
    base = {
        "config_list": [{"api_type": "openai", "model": "gpt-4.1", "api_key": "test"}],
        "temperature": 0.0,
    }
    prepared = prepare_llm_config_for_media({"image_generation_enabled": True}, base)

    assert prepared["use_responses_api"] is True
    assert "use_responses_api" not in base
    assert provider_api_type(prepared) == "openai"


def test_prepare_llm_config_for_gemini_sets_image_response_modalities() -> None:
    prepared = prepare_llm_config_for_media(
        {"image_generation_enabled": True},
        {"config_list": [{"api_type": "google", "model": "gemini-test"}]},
    )

    assert prepared["response_modalities"] == ["TEXT", "IMAGE"]


def test_build_ag2_image_generation_tools_is_openai_only() -> None:
    tools = build_ag2_image_generation_tools(
        {"image_generation": {"quality": "high", "output_format": "png"}},
        {"config_list": [{"api_type": "openai", "model": "gpt-4.1"}]},
    )
    gemini_tools = build_ag2_image_generation_tools(
        {"image_generation_enabled": True},
        {"config_list": [{"api_type": "google", "model": "gemini-test"}]},
    )

    assert len(tools) == 1
    assert tools[0].__class__.__name__ == "ImageGenerationTool"
    assert gemini_tools == []


def test_llm_config_to_ag2_config_can_use_openai_responses_config() -> None:
    from ag2.config import OpenAIConfig, OpenAIResponsesConfig

    base_config = llm_config_to_ag2_config({
        "config_list": [{"api_type": "openai", "model": "gpt-4.1", "api_key": "test"}],
    })
    responses_config = llm_config_to_ag2_config({
        "config_list": [{"api_type": "openai", "model": "gpt-4.1", "api_key": "test"}],
        "use_responses_api": True,
    })

    assert isinstance(base_config, OpenAIConfig)
    assert isinstance(responses_config, OpenAIResponsesConfig)


def test_media_input_ref_to_ag2_input_converts_binary_image() -> None:
    ag2_input = media_input_ref_to_ag2_input(
        MediaInputRef(
            kind=MediaKind.IMAGE,
            media_type="image/png",
            data=b"png-bytes",
            metadata={"detail": "low"},
            vendor_metadata={"detail": "low"},
        )
    )

    assert ag2_input.media_type == "image/png"
    assert ag2_input.data == b"png-bytes"
    assert ag2_input.vendor_metadata == {"detail": "low"}


def test_attachment_to_media_input_ref_uses_stored_path() -> None:
    ref = attachment_to_media_input_ref({
        "attachment_id": "att_1",
        "filename": "logo.png",
        "stored_path": "C:/tmp/logo.png",
        "content_type": "image/png",
        "intent": "context",
    })

    assert ref is not None
    assert ref.kind is MediaKind.IMAGE
    assert ref.path == "C:/tmp/logo.png"
    assert ref.attachment_id == "att_1"


@pytest.mark.asyncio
async def test_local_media_content_store_round_trips_bytes(tmp_path) -> None:
    store = LocalMediaContentStore(root=tmp_path)
    ref = await store.put_media(
        b"image-bytes",
        app_id="app_1",
        media_id="media_1",
        filename="hero.png",
        media_type="image/png",
    )

    assert await store.exists(ref)
    assert await store.get_media(ref) == b"image-bytes"
    assert await store.delete(ref) is True
    assert await store.exists(ref) is False


@pytest.mark.asyncio
async def test_local_media_content_store_rejects_paths_outside_root(tmp_path) -> None:
    store = LocalMediaContentStore(root=tmp_path / "media")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"nope")

    assert await store.exists(str(outside)) is False
    with pytest.raises(MediaContentNotFoundError, match="outside"):
        await store.get_media(str(outside))


@pytest.mark.asyncio
async def test_media_asset_store_persists_generated_binary_metadata(tmp_path) -> None:
    collection = _Collection()
    asset_store = MediaAssetStore(
        client=_Client(collection),
        content_store=LocalMediaContentStore(root=tmp_path),
    )
    binary_result = SimpleNamespace(data=b"image-bytes", metadata={"media_type": "image/png"})

    asset = await asset_store.persist_generated_binary_result(
        binary_result,
        app_id="app_1",
        source_workflow="ThemeCapture",
        source_chat_id="chat_1",
        prompt="Generate a brand background",
        provider="openai",
        model="gpt-4.1",
        promotion_targets=[MediaPromotionTarget.BRAND_ASSET],
    )

    assert asset.kind is MediaKind.IMAGE
    assert asset.source_workflow == "ThemeCapture"
    assert asset.content_backend == "local"
    assert collection.docs[0]["asset_id"] == asset.asset_id
    assert collection.docs[0]["app_id"] == "app_1"

    loaded = await asset_store.get_asset(app_id="app_1", asset_id=asset.asset_id)
    assert loaded is not None
    assert loaded.asset_id == asset.asset_id
    assert await asset_store.get_asset_content(loaded) == b"image-bytes"

    promoted = await asset_store.mark_asset_promoted(
        app_id="app_1",
        asset_id=asset.asset_id,
        promotion_target=MediaPromotionTarget.BRAND_ASSET,
        promoted_by="user_1",
    )
    assert promoted is not None
    assert promoted.metadata["promotion_status"] == "promoted"
    assert "brand_asset" in promoted.metadata["promoted_targets"]


@pytest.mark.asyncio
async def test_media_asset_store_detects_output_format_metadata(tmp_path) -> None:
    collection = _Collection()
    asset_store = MediaAssetStore(
        client=_Client(collection),
        content_store=LocalMediaContentStore(root=tmp_path),
    )
    binary_result = SimpleNamespace(data=b"image-bytes", metadata={"output_format": ".webp"})

    asset = await asset_store.persist_generated_binary_result(binary_result, app_id="app_1")

    assert asset.media_type == "image/webp"
    assert asset.filename.endswith(".webp")


def test_generated_media_artifact_payload_builds_preview_and_actions() -> None:
    from mozaiksai.core.media.types import GeneratedMediaAsset, MediaSource

    asset = GeneratedMediaAsset(
        _id="media_1",
        asset_id="media_1",
        app_id="app_1",
        kind=MediaKind.IMAGE,
        media_type="image/png",
        source=MediaSource.GENERATED,
        filename="hero.png",
        size_bytes=10,
        sha256="0" * 64,
        content_ref="content-ref",
        content_backend="local",
        promotion_targets=[MediaPromotionTarget.BRAND_ASSET],
    )

    payload = generated_media_artifact_payload(
        [asset],
        app_id="app_1",
        workflow_name="ThemeCapture",
        chat_id="chat_1",
        agent_name="Designer",
    )

    assert payload["artifact_type"] == "core.media.generated_asset"
    assert payload["assets"][0]["preview_url"] == media_asset_content_url(app_id="app_1", asset_id="media_1")
    assert payload["assets"][0]["actions"][0]["tool"] == "media.promote"


@pytest.mark.asyncio
async def test_harvest_generated_media_response_persists_context_and_emits_ui(tmp_path) -> None:
    from ag2.events import BinaryResult, ModelMessage, ModelResponse

    class _Transport:
        def __init__(self) -> None:
            self.events: list[dict] = []

        async def send_tool_call_event(self, event_id, chat_id, tool_name, component_name, display_type, payload, **kwargs):  # noqa: ANN001, ANN002, ANN003
            self.events.append({
                "event_id": event_id,
                "chat_id": chat_id,
                "tool_name": tool_name,
                "component_name": component_name,
                "display_type": display_type,
                "payload": payload,
                "kwargs": kwargs,
            })

    collection = _Collection()
    asset_store = MediaAssetStore(
        client=_Client(collection),
        content_store=LocalMediaContentStore(root=tmp_path),
    )
    transport = _Transport()
    context = {"app_id": "app_1", "chat_id": "chat_1"}
    response = ModelResponse(
        ModelMessage("Generated a hero image."),
        model="gpt-image-1",
        provider="openai",
        files=[BinaryResult(b"image-bytes", metadata={"media_type": "image/png", "filename": "hero.png"})],
    )

    assets = await harvest_generated_media_response(
        response,
        agent_name="Designer",
        workflow_name="ThemeCapture",
        context_variables=context,
        generation_params={"quality": "high"},
        promotion_targets=[MediaPromotionTarget.BRAND_ASSET],
        asset_store=asset_store,
        transport=transport,
    )

    assert len(assets) == 1
    assert context["generated_media_assets"][0]["asset_id"] == assets[0].asset_id
    assert context["last_generated_media_assets"][0]["filename"] == "hero.png"
    assert transport.events[0]["tool_name"] == "core.media.generated_asset"
    assert transport.events[0]["payload"]["assets"][0]["asset_id"] == assets[0].asset_id


def test_azure_blob_content_store_implements_media_content_store_interface() -> None:
    """AzureBlobMediaContentStore satisfies the MediaContentStore Protocol without
    requiring live Azure credentials — it raises RuntimeError only when import or
    credential resolution is attempted at call time, not at construction time."""
    from mozaiksai.core.media.store import AzureBlobMediaContentStore, MediaContentStore

    store = AzureBlobMediaContentStore(
        account_name="test-account",
        container_name="test-container",
    )
    assert isinstance(store, MediaContentStore)
    assert store.backend_name == "azure_blob"


def test_azure_blob_content_store_raises_on_missing_credentials() -> None:
    """Constructing with no credentials at all raises RuntimeError when any
    IO method is called (lazy credential check)."""
    from mozaiksai.core.media.store import AzureBlobMediaContentStore

    store = AzureBlobMediaContentStore()
    # _ensure_client() must raise — either ImportError wrapped as RuntimeError
    # (azure-storage-blob not installed) or RuntimeError for missing credentials.
    import asyncio

    with pytest.raises((RuntimeError, Exception)):
        asyncio.get_event_loop().run_until_complete(
            store.put_media(b"x", app_id="a", media_id="b", filename="b.png", media_type="image/png")
        )


def test_azure_blob_cdn_ref_strips_base_url_on_get() -> None:
    """_blob_name_from_ref correctly strips the CDN prefix for get/delete/exists."""
    from mozaiksai.core.media.store import AzureBlobMediaContentStore

    store = AzureBlobMediaContentStore(
        account_name="acc",
        container_name="c",
        cdn_base_url="https://cdn.example.com/media",
    )
    blob_name = "app1/media_abc/logo.png"
    full_url = f"https://cdn.example.com/media/{blob_name}"
    assert store._blob_name_from_ref(full_url) == blob_name
    assert store._content_ref(blob_name) == full_url


def test_get_media_content_store_selects_azure_blob(monkeypatch) -> None:
    """get_media_content_store() returns AzureBlobMediaContentStore when env var is set."""
    import mozaiksai.core.media.store as store_mod
    from mozaiksai.core.media.store import AzureBlobMediaContentStore

    monkeypatch.setenv("MOZAIKS_MEDIA_CONTENT_BACKEND", "azure_blob")
    monkeypatch.setattr(store_mod, "_content_store", None)

    result = store_mod.get_media_content_store()
    assert isinstance(result, AzureBlobMediaContentStore)

    # cleanup
    monkeypatch.setattr(store_mod, "_content_store", None)


def test_core_media_generated_asset_primitive_is_registered() -> None:
    root = Path(__file__).resolve().parents[1]
    renderer = (root / "chat-ui/src/primitives/PrimitiveRenderer.jsx").read_text(encoding="utf-8")
    index = (root / "chat-ui/src/primitives/index.js").read_text(encoding="utf-8")

    assert "core.media.generated_asset" in renderer
    assert "CoreMediaGeneratedAsset" in index
