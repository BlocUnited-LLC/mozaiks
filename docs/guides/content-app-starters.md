# Content app starters

The `content` capability pack is a reusable publishing foundation for portfolios,
blogs and editorial apps. It supplies deterministic module and data contracts;
the app owns its pages, navigation, branding and editorial experience. It is not
a complete blog, visual theme, rich-text editor or end-to-end qualified app.

## Source and generated locations

The reusable source lives in `factory_app/build_context/content/`:

```text
content/
  context.yaml                 # registered pack, capabilities and declared assets
  contract.yaml                # selection, ownership and agent instructions
  templates/
    modules/content/           # canonical module, backend layers and events
    data/                      # collection contract and additive migration
```

Template paths are relative to the app bundle. AppGenerator stages them under
`generated/apps/{app_id}/{build_id}/app/`. Explicit promotion puts that bundle in
the workspace's `app/`; app-local workflows remain beside it in `workflows/`.
Neither the external reference projects' directory trees nor build-context
contracts are copied into app runtime paths.

## How agents use the foundation

1. Read the approved requirements, experience design and current app artifacts.
   Select `content` when the app needs a curated collection with a publication
   lifecycle. A static brochure needs no database module. Community posts,
   comments and followers belong to the existing Social capability.
2. Resolve the registered descriptor and declared contract through the existing
   build-context path. The selected `AppBuildPlan.capability_packs` entry uses
   `capability_pack_id: content` and `capability_source: generated_module`.
   Retain the verified version and digest with the build's selected source.
3. Use the module's declared action and response schemas to plan app-owned
   pages. The existing pack inventory excludes supplied backend files from model
   tasks. Do not ask an agent to rewrite the publishing service for each app.
4. Carry the app's approved composition and brand through `ExperienceSpec`, page
   intent, theme capture and task acceptance criteria. Use declarative pages
   when they express that experience; use the registered custom React contract
   for a concrete composition or interaction gap. Keep semantic theme tokens.
5. Assemble, validate references and ownership, then execute the relevant user
   journeys. Promote only through the normal validation/review path.

The descriptor is available through trusted build-context discovery. A workspace
using operator selection can select the canonical `content` ID from the installed
Factory registry. This addition does not change global catalog recommendations,
workflow sequences or launch selection policy. Merely mentioning a directory in
prompt text does not register or select a pack.

## One foundation, different apps

The module exposes five actions:

| Action | Behavior |
| --- | --- |
| `list_entries` | Published summaries for public readers; authenticated editors can filter other states. Cursor order is ascending entry ID, not publication date. |
| `get_entry` | Full plain text for one visible entry; missing and unpublished public targets both return not found. |
| `create_entry` | Editor-only draft creation; an omitted slug is generated from the title and server ID. |
| `update_entry` | Editor-only field edits and explicit publication-state changes. |
| `archive_entry` | Editor-only soft archive; preserves the record and removes public visibility. |

Management requires authenticated identity and `content.manage`. Role names and
membership come from the app's approved auth contract, never this pack. Public
requests cannot supply their own app identity or permission grants.

| App | Shared foundation | App-owned experience |
| --- | --- | --- |
| Designer portfolio | Curated entries and draft/published/archive states | Project-led composition, case-study detail, about/contact pages and visual identity |
| Editorial publication | The same publication lifecycle and read boundary | Article index, reading layout, topic navigation and editor workspace |
| Product knowledge library | The same records and permission-protected editing | Search/navigation experience and task-focused reading pages |

These are composition directions, not three shipped, browser-qualified templates.
Search, taxonomy, comments, newsletters, uploads, feeds and SEO are additional
requirements; selecting this pack does not implement them implicitly. New data
fields, actions and integrations need approved contracts and tests. Do not treat
an arbitrary JSON field as an escape from those contracts.

## Determinism and flexibility

Use a mixture, with a specific job for each part:

| Mechanism | Responsibility |
| --- | --- |
| Typed structured outputs | Approved identity, topology, page/action references and bounded work ownership |
| YAML/JSON | Module/action/event/data contracts and schema-driven pages/configuration |
| Static templates | Reviewed generic implementation, copied without a model rewriting it |
| Jinja | Substitution when an existing, explicit input contract actually requires it |
| Agent-authored Python/React | App-specific behavior and presentation behind declared entrypoints |
| Deterministic tests and runtime checks | Contract closure, authorization, persistence, routes and actual behavior |

The initial content backend does not need Jinja: its identifiers and behavior
are reusable unchanged. Branding belongs in `app/brand/`, shell configuration in
`app/config/`, and presentation in `app/ui/`. Adding a templating layer merely to
rename the same backend would increase drift and testing cost.

Repeatable assembly means the same approved artifacts, custom source, pack
versions/digests, framework version and rendering inputs (including timestamp)
produce the same bytes. It does not mean a fresh model conversation will make
the same design decisions, or that runtime-generated IDs and timestamps are
constant.

## Maintain and refine

`contract.yaml.required_outputs` is the ownership map. Template-owned files are
replaced from the verified pack source. The workspace-owned `backend/handler.py`
is seeded once as a thin subclass and preserved from the admitted app source.
App-owned pages and branding are separate from the pack's implementation.

The app also authors `modules/content/contracts/fixtures.yaml` through the
existing typed smoke-fixture contract. It names valid test inputs and the app's
actual approved editor role, including prerequisite creates and returned record
IDs where needed. It grants no runtime permission. This one declared
`owner: generator` companion can coexist with the exact pack backend; arbitrary
module helpers, service replacements and additional ownership exceptions cannot.

The existing structural task gate treats module companion manifests as optional;
backend certification alone therefore does not prove that fixtures were supplied.
Keep explicit fixture tasks and successful positive runtime probes in acceptance.
A fixture file, or an unauthorized-request test by itself, is not proof that an
authorized editor can complete an action.

For a page/brand change, refine those declared app files and preserve unrelated
backend bytes. For an authorized handler customization, use the existing scoped
backend patch/refinement path against the accepted current build, retaining the
thin dispatch contract. Ordinary AppGenerator tasks cannot overwrite an admitted
workspace-owned pack file; they report
`WORKSPACE_PACK_OUTPUT_CHANGE_UNSUPPORTED` rather than silently applying it.
Do not mutate `generated_files` to simulate an accepted refinement.

For a new product capability, plan another canonical module or an explicit
contract change. Use declared actions and domain events for composition rather
than importing a sibling module's persistence or service implementation. Keep
provider mechanics behind app-owned adapters or selected managed facades.

For a shared foundation improvement, change the source pack, bump its pack
version and qualify consumers. Do not hand-edit template-owned copies in every
app. The existing-app installer/upgrade work is coordinated separately in
[PR #1017](https://github.com/BlocUnited-LLC/mozaiks/pull/1017); this guide does
not assume its commands are present on the base revision.

Agents maintaining an app should inspect its current contracts, source ownership,
pack pins, migration history and validation evidence before planning a patch.
After the patch, record what changed and which journeys were actually exercised.
Canonical structure can evolve, but changes must update the producing schema,
templates, consumers, guidance and acceptance together; a starter must not invent
its own runtime convention.

## Costs and evidence

The content module makes no model or external-provider calls. It uses the
existing app persistence boundary, backed by local MongoDB for local runtime
verification. This avoids a new hosted service dependency. Agent generation,
optional media creation and later integrations have their own costs; no hard
provider spend cap is claimed by this pack.

Entries contain plain text. The initial foundation has no HTML/MDX execution,
rich-text rendering, multi-editor conflict merge or public upload mechanism.

Owning checks live in `tests/test_content_pack_actions.py`,
`tests/test_build_context_content_pack.py` and
`tests/test_content_starter_materialization.py`. The generic shipped-pack suite
also discovers the pack and has an explicit reviewed editor permission entry.
Unit tests with persistence doubles, real Mongo tests, HTTP fixture checks,
browser journeys and live-model builds are different evidence levels. Passing
one does not establish another.

The external references informed the product direction, not the implementation:
[Next-forge](https://github.com/vercel/next-forge),
[Vercel Examples](https://github.com/vercel/examples), and
[Backblaze AI SaaS Starter Kit](https://github.com/backblaze-labs/ai-saas-starter-kit).
No source from them is copied in this increment. If future work copies substantial
source, retain its license notices and inspect the relevant dependencies/assets.
