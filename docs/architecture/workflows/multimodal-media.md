# Multimodal Media Capability

Mozaiks treats multimodal input and generated media as an optional runtime
capability, not as marketing-specific product logic.

## Boundary

OSS `mozaiks` owns the generic primitives:

- media type classification and allowlists
- provider-neutral `MediaInputRef` values for image, audio, video, and document inputs
- generated media metadata through `GeneratedMediaAsset`
- local/GridFS generated-media byte storage
- AG2 adapter helpers for typed inputs and image-generation configuration
- workflow declarative flags that enable media behavior per agent

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

Agents can opt into media behavior:

```yaml
agents:
  - name: BrandDesignerAgent
    system_message: "Generate brand-safe visual options."
    multimodal_inputs_enabled: true
    image_generation:
      quality: high
      size: "1024x1024"
      background: transparent
      output_format: png
```

`image_generation` implies `image_generation_enabled`.

## Persistence

Chat uploads remain metadata on `ChatSessions.attachments`; attachment bytes stay
in upload storage. For AG2 input, workflows convert eligible attachments into
`MediaInputRef` values and then into AG2 typed inputs.

Generated media must not be stored only in chat text or short-lived AG2 stream
events. `MediaAssetStore.persist_generated_binary_result(...)` writes:

- bytes through `MediaContentStore`
- metadata into the framework-owned `MediaAssets` collection
- provenance such as `source_workflow`, `source_chat_id`, prompt, provider, model,
  media type, checksum, and promotion targets

Local development defaults to filesystem storage under `generated_media/`.
Production can use GridFS via `MOZAIKS_MEDIA_CONTENT_BACKEND=gridfs`.

## Promotion

Generated media remains a proposal until a workflow or user action promotes it.
Promotion targets are generic:

- `brand_asset` for `app/brand/assets`
- `app_asset` for app-bundle media inventory
- `page_asset` for page-specific imagery
- `campaign_asset` for product modules such as marketing campaigns
- `artifact` for review-only workflow output

AppGenerator already owns `config/asset_manifest.json` for reusable media
inventory. Generated media can become an asset-manifest entry through
`GeneratedMediaAsset.to_asset_manifest_entry(...)`.

## Current Gaps

This first foundation does not yet add a frontend media artifact renderer or
automatic AG2 reply-file harvesting inside every workflow turn. Workflows can
persist generated `reply.files` explicitly through `MediaAssetStore`. A future
runtime slice should add:

- a core media artifact UI component
- transport events for generated media proposals
- explicit accept/promote actions into brand assets or campaign modules
- replay of media refs alongside text messages

