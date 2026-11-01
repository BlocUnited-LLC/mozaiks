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
- `promote_brand_asset` for the promote pattern

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
  - name: LandscapeImageMakerAgent
    structured_outputs_required: false
    system_message: "Create exactly one image from the message."
    image_generation:
      quality: medium
      size: "1536x1024"
      background: opaque   # transparent requires gpt-image-1
      output_format: webp
      output_compression: 85
      promotion_targets:
        - app_asset
```

`image_generation` implies `image_generation_enabled`.
`promotion_targets` sits inside the `image_generation` block and declares which
promotion slots this agent's output may fill; the harvest middleware uses it to
tag `GeneratedMediaAsset.promotion_targets`. When the agent runs as a task-batch
worker, the harvest also records `task_batch_id` and `task_id` in
`generation_params`, so a later step can match each image to its job.

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

- `brand_asset` — brand imagery such as logos
- `app_asset` — app-bundle media inventory, published by the App Media Step below
- `page_asset` — page-specific imagery
- `campaign_asset` — app-defined campaign or placement records
- `artifact` — review-only workflow output

`config/asset_manifest.json` is the deterministic source of truth for promoted
assets. Downstream consumers (e.g. `create_listing` in a marketplace module)
read from it rather than querying `MediaAssets` directly.

The manifest is a versioned list; AppGenerator validates it in `save_app_schema`:

```json
{
  "version": "1.0",
  "assets": [
    {
      "asset_id": "home-hero",
      "kind": "hero_image",
      "source": "generated",
      "brief": "A warm coffee counter in morning light.",
      "aspect": "landscape",
      "alt": "Barista pouring a latte",
      "usage": ["home"],
      "path": "ui/assets/home-hero.webp",
      "tags": ["ai_generated"]
    }
  ]
}
```

## App Media Step In The Build

AppGenerator plans the images an app's pages show; it does not make them.

- A **planned** entry has a `brief` and `aspect` and no `path` or `url`.
  `source: generated` is decorative or brand art made with AI.
  `source: uploaded` must depict something real (a product, property, person,
  or dish) and is reserved for the owner to supply; it is never AI art.
- Pages show an entry with the `Image` primitive: `{asset_id, alt, caption,
  variant: banner|inline}`. The host serves bundle images at
  `GET /api/app-assets/{asset_id}` from the entry's `path`; a planned entry
  answers 404 and the primitive shows a neutral placeholder.

`AppMediaGenerator` runs after AppGenerator and before SecurityReadiness in every
sequence where AppGenerator hands off to SecurityReadiness:

1. A `before_chat` hook reads the planned `generated` entries (at most eight)
   and brand context from the bound build's current bundle.
2. `MediaPlanAgent` writes one shared art direction and one prompt per entry;
   `compose_media_jobs` turns them into one job per asset and adds the rule
   that images carry no text, so they cannot invent prices or names.
3. The `app_media_jobs` task batch runs one square, landscape, or portrait image
   maker per job; a failed job leaves its slot planned and never fails the build.
4. `publish_app_media` writes each image under `ui/assets/`, a labeled SVG
   placeholder for each `uploaded` entry, and the updated manifest into a new
   **draft child version** of the app bundle, then points the registry's current
   build at it. SecurityReadiness, AppReview, preview, and download read that
   version. A rerun finds no planned entries and changes nothing.

## Reference Workflows

| Workflow | Purpose |
|----------|---------|
| `AppMediaGenerator` | The build's app media step described above. |
| `BrandAssetGeneratorWorkflow` | Standalone logo workflow outside every build sequence. Its `promote_brand_asset` writes a role-keyed manifest shape that AppGenerator rejects; it is retired together with the hosted product test that still imports it. |

Apps build their own promote-side tools on these primitives. A workflow that
attaches generated media to an app's own records supplies that tool itself;
only the generic pieces above ship here.
