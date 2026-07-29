# Multimodal Media Capability

Mozaiks treats multimodal input and generated media as an optional runtime
capability, not as marketing-specific product logic.

## Boundary

OSS `mozaiks` owns the generic primitives:

- media type classification and allowlists
- provider-neutral `MediaInputRef` values for image, audio, video, and document inputs
- generated media metadata through `GeneratedMediaAsset`
- pluggable byte storage via `MediaContentStore` (local, GridFS, Azure Blob built-in)
- harvest middleware that intercepts AG2 reply files and persists them in one call
- AG2 adapter helpers for typed inputs and image-generation configuration
- workflow declarative flags that enable media behavior per agent
- `promote_brand_asset` and `attach_campaign_asset` tools for the promote pattern

Apps own product use cases. A marketing app can use these primitives to generate
campaign images. A branding workflow can use the same primitives to produce logo,
favicon, chat background, or hero-image candidates. A support app can use them to
reason over uploaded screenshots or PDFs.

## AG2 Integration

AG2 1.0.0 exposes typed multimodal input factories:

- `ImageInput`
- `AudioInput`
- `VideoInput`
- `DocumentInput`

It also exposes generated image files as binary results on agent replies.
Provider behavior differs:

- OpenAI image generation uses `ImageGenerationTool` and requires
  `OpenAIResponsesConfig`.
- Gemini image generation uses image response modalities instead of an AG2 tool.

Mozaiks hides those differences behind `mozaiksai.core.media.ag2`:

- `prepare_llm_config_for_media(...)`
- `build_ag2_image_generation_tools(...)`
- `media_input_ref_to_ag2_input(...)`
- `attachment_to_media_input_ref(...)`

The agent factory only attaches generated-image support when a workflow agent
declares `image_generation_enabled: true` or an `image_generation` config block.
Auto-tool-call agents do not receive image generation tools.

## Workflow Contract

Agents opt into media behavior via `agents.yaml`:

```yaml
agents:
  - name: BrandDesignerAgent
    system_message: "Generate brand-safe visual options."
    multimodal_inputs_enabled: true
    image_generation:
      quality: high
      size: "1024x1024"
      background: opaque   # transparent requires gpt-image-1
      output_format: png
    promotion_targets:
      - brand_asset
      - app_asset
```

`image_generation` implies `image_generation_enabled`.
`promotion_targets` declares which promotion slots this agent's output may fill —
the harvest middleware uses this to tag `GeneratedMediaAsset.promotion_targets`.

## Generate → Harvest → Promote

Generated media follows a three-step pattern:

```
AG2 reply.files (BinaryResult)
    ↓ harvest_generated_media_response(...)
MediaAssetStore.persist_generated_binary_result(...)
    ↓ writes bytes to MediaContentStore
    ↓ writes metadata to MediaAssets collection
GeneratedMediaAsset (asset_id, content_ref, sha256, promotion_targets, ...)
    ↓ user/workflow promote action
asset_manifest.json  ←  promote_brand_asset(asset_id, role="logo")
```

**Generate** is stochastic — the model decides the image.
**Promote** is deterministic — an explicit human or workflow gate commits the
asset to `config/asset_manifest.json`. Nothing downstream reads the asset until
it is promoted.

`harvest_generated_media_response` is the OSS middleware that handles step two:

```python
from mozaiksai.core.media.middleware import harvest_generated_media_response

assets = await harvest_generated_media_response(
    model_response,
    agent_name="BrandDesignerAgent",
    workflow_name="BrandAssetGeneratorWorkflow",
    context_variables=context_variables,
    generation_params=image_generation_kwargs,
    promotion_targets=[MediaPromotionTarget.BRAND_ASSET],
    asset_store=asset_store,
    transport=transport,           # emits ui.media.generated events
)
```

## Byte Storage — MediaContentStore

`MediaContentStore` is a `typing.Protocol`. The framework ships three built-in
implementations selected by the `MOZAIKS_MEDIA_CONTENT_BACKEND` environment
variable:

### `local` (default)

Writes bytes to the local filesystem under `MOZAIKS_MEDIA_STORAGE_DIR`
(default: `./generated_media`).

**Use for:** local development and single-instance demos only.
Assets are not shared across replicas and are lost on container restart.

### `gridfs`

Stores bytes in MongoDB GridFS. Shares the existing `MONGO_URI` connection.

**Use for:** small-to-medium deployments that already run MongoDB and do not
need CDN delivery. GridFS chunks files at 255 KB — fine for logo PNGs,
awkward for large video assets.

```
MOZAIKS_MEDIA_CONTENT_BACKEND=gridfs
```

### `azure_blob`

Stores bytes in Azure Blob Storage using `azure-storage-blob>=12.19.0`.

**Recommended for production:** durable, cheap, scales without touching MongoDB,
CDN-ready.

```
MOZAIKS_MEDIA_CONTENT_BACKEND=azure_blob
```

Three authentication modes — use whichever matches your deployment:

| Mode | Required env vars |
|------|-------------------|
| Connection string | `AZURE_STORAGE_CONNECTION_STRING` |
| Account + SAS token | `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_SAS_TOKEN` |
| Account + managed identity | `AZURE_STORAGE_ACCOUNT_NAME` (no token — uses `DefaultAzureCredential`) |

Container configuration:

```
AZURE_STORAGE_CONTAINER_NAME=mozaiks-media   # must exist before deployment
```

**Optional CDN redirect:** Set `AZURE_STORAGE_CDN_BASE_URL` to a CDN endpoint
or the container's public base URL. When set, the `azure_blob` backend returns
the full public URL as the `content_ref`, enabling the API serve route to issue
`302` redirects instead of proxying bytes. Without this, the route streams bytes
from the blob client.

```
AZURE_STORAGE_CDN_BASE_URL=https://cdn.example.com/media
```

### Custom backends

Any class that satisfies the `MediaContentStore` Protocol can be used:

```python
from mozaiksai.core.media.store import MediaContentStore, MediaAssetStore

class MyS3ContentStore:
    backend_name = "s3"

    async def put_media(self, data, *, app_id, media_id, filename, media_type, metadata=None): ...
    async def get_media(self, content_ref): ...
    async def exists(self, content_ref): ...
    async def delete(self, content_ref): ...

asset_store = MediaAssetStore(content_store=MyS3ContentStore())
```

Pass the custom store explicitly to `MediaAssetStore`. The `get_media_content_store()`
factory only knows about the three built-in backends.

## Persistence

Chat uploads remain metadata on `ChatSessions.attachments`; attachment bytes stay
in upload storage. For AG2 input, workflows convert eligible attachments into
`MediaInputRef` values and then into AG2 typed inputs.

Generated media must not be stored only in chat text or short-lived AG2 stream
events. `MediaAssetStore.persist_generated_binary_result(...)` writes:

- bytes through `MediaContentStore`
- metadata into the framework-owned `MediaAssets` collection
- provenance: `source_workflow`, `source_chat_id`, prompt, provider, model,
  media type, sha256 checksum, and promotion targets

## Promotion

Generated media remains a proposal until a workflow or user action promotes it.
Promotion targets are generic:

- `brand_asset` — committed to `config/asset_manifest.json` via `promote_brand_asset`
- `app_asset` — app-bundle media inventory
- `page_asset` — page-specific imagery
- `campaign_asset` — product modules such as marketing campaigns
- `artifact` — review-only workflow output

`config/asset_manifest.json` is the deterministic source of truth for promoted
assets. Downstream consumers (e.g. `create_listing` in a marketplace module)
read from it rather than querying `MediaAssets` directly.

```json
{
  "assets": {
    "logo": {
      "asset_id": "media_abc123",
      "content_url": "/api/media/assets/{app_id}/media_abc123/content",
      "alt_text": "",
      "promoted": true
    }
  }
}
```

## Reference Workflows

The OSS factory ships two reference workflows that demonstrate the full pipeline:

| Workflow | Purpose |
|----------|---------|
| `BrandAssetGeneratorWorkflow` | Logo and brand imagery. Promotes to `role=logo` in `asset_manifest.json`. |
| `CampaignAssetGeneratorWorkflow` | Campaign hero images. Attaches to marketplace/campaign placement records via `attach_campaign_asset`. |

Both workflows follow the same generate → harvest → promote pattern and can be
customized via `build_context` overlays or used as archetypes for app-specific
media workflows.
