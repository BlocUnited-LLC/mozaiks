# Build Context Packs

This document defines build-time context packages that specialize Mozaiks'
factory workflows without copying or forking those workflows.

The purpose is factory customization. A build context can make the shared
Studio/factory build system behave like a different kind of builder, such as a
SaaS app builder, game builder, mobile app builder, enterprise workflow builder,
or operator-specific hosted product builder.

The key rule: build context is targeted input. It is only active for workflows
listed in `applies_to_workflows`, and prompt-heavy assets are only shown to
agents listed in the asset projection `recipients`.

This logic is separate from AppGenerator's internal capability planning labels.
Documented separately in
[AppGenerator Capability Planning](../modules-systems/appgenerator-capability-planning.md).

For dev packs that change what the factory is building, see
[Domain-Agnostic Build Factory](domain-agnostic-build-factory.md). Build context
packs supply targeted build-time input; domain-specific workflow contracts still
own structured outputs, task models, and validation.

## Boundary

There are two coupled contracts:

- `workflows/{WorkflowName}/context_variables.yaml` is the workflow-level ABI.
  It declares what state the workflow can accept, expose to agents, and mutate.
- `build_context/{context_name}/context.yaml` is the factory customization
  registry. It declares which factory workflows receive extra catalogs,
  contracts, templates, pack descriptors, and projected small values.

Build context does not replace workflow contracts. If a specialization requires
new structured outputs, new agents, new tools, or a different task graph, the
workflow contract must be changed first. Build context can then supply the
domain-specific options for that declared contract.

That is the deterministic boundary:

- build context chooses among declared lanes
- workflow structured outputs define what agents are allowed to produce
- middleware renders declared context into prompts
- tools materialize only declared artifacts

Do not rely on prompt injection to create artifact shapes that the workflow
structured outputs do not validate.

## Shape

Each context is a named directory with one required registry file:

```text
build_context/
└── {context_name}/
    ├── context.yaml
    └── files declared by context.yaml assets[]
```

Folder names are organization, not policy. A file is build-context input only
when `context.yaml` declares it in `assets[]`. Factory workflow catalog YAMLs
usually sit directly beside `context.yaml`.

Example:

```yaml
context_id: mozaikspay
applies_to_workflows:
  - AppGenerator
assets:
  - path: contract.yaml
    kind: contract
  - path: templates/
    kind: templates
pack:
  id: mozaikspay
  version: "0.1.11"
  status: active
  capability_source: managed_capability
  required_integrations:
    - service: mozaikspay
      provider: mozaiks_pay
      kind: api_key
      required_fields:
        - name: api_base
          type: url
          frontend_safe: true
        - name: client_id
          type: text
          frontend_safe: true
        - name: client_secret
          type: secret
          frontend_safe: false
capabilities:
  - capability_id: mozaikspay.billing_portal
    facade_recommended: billing_portal
facades:
  - module_id: billing_portal
    provider_module: mozaikspay
projections:
  context_variables:
    capability_packs:
      from: capability_packs
    operator_contracts:
      from: operator_contracts
```

Required fields:

- `context_id`: stable id; should match the directory name.
- `applies_to_workflows`: workflow ids that may receive this context. This is
  the first targeting boundary.
- `assets`: explicit files or directories this context contributes.

Optional fields:

- `description`: short structural registry metadata for human inspection. It is
  not projected into workflow context variables unless repeated through
  `values` or an explicit projection source.
- `pack`: marks the context as a selectable build pack.
- `pack.version`: required version string for selectable packs.
- `pack.required_integrations`: connector requirements that selected packs add
  to AppGenerator integration readiness. Declare each requirement as a
  structured object with `service`, `provider`, `kind`, `purpose`,
  `required_at`, and `required_fields`. Secret fields must be marked
  `type: secret` and `frontend_safe: false`; non-secret provider config such as
  API base URLs and client ids may be frontend safe.
- `capabilities`, `facades`: structured pack metadata for planning agents.
- `projections.context_variables`: values projected into workflow launch state.
- `values`: static provider values used by projection rules. Put custom
  projection inputs here rather than adding free-form top-level keys.

Keep `context.yaml` structural. Do not put human guidance, semantic purpose
text, generated file mappings, endpoint rewrites, resolver code, build tasks,
or secrets there.

## First-Party Packs

The OSS factory ships several first-party build-context packs. They all follow
the same shape above: `context.yaml` registers the pack, `contract.yaml` defines
typed selection/output/boundary rules, and `templates/` mirrors the generated
app bundle paths.

- `messaging`: generated module templates for threads, messages, read state,
  app/workspace conversation scope, and message-related contracts.
- `support`: generated module templates for support request metadata and a
  support inbox. It requires `messaging`; support stores `message_thread_id`
  and never owns a second transcript store.
- `social`: generated module templates for friends, invitations, user posts,
  reactions, comments, and activity feeds. Social may pair with `messaging`
  for DMs, but it owns social graph state.
- `commerce`: generated module templates for single-merchant storefronts:
  product catalog, inventory, cart, checkout requests, orders, and fulfillment
  state. Payment processor mechanics stay outside the commerce module and are
  connected through a selected payment pack such as `mozaikspay` or an explicit
  bring-your-own provider adapter.
- `mozaikspay`: managed-capability templates and connector contracts for apps
  that consume the MozaiksPay payment/subscription boundary.
- `operator_readiness`: deterministic launch/evidence templates for
  host-operator apps, SaaS workspaces, and other platform-style products that
  need reproducible no-spend validation. This pack is intentionally narrow: it
  is for platform/operator workspaces, not ordinary customer apps or brochure
  sites.

UI primitives stay first-class in `chat-ui`. Build-context packs compose page
schemas from those primitives; they do not define their own primitive layer.

### Persistent template contributions

A selected pack may declare `templates/data/contract.json` as an
`owner: templates` required output. The verified resolver contributes its
module-owned collection surfaces to the approved app data contract. The data
compiler preserves unrelated app surfaces and rejects conflicting ownership or
collection definitions. It emits one `data/contract.json`; a pack template never
replaces that shared app artifact directly. Shared outputs record contributor
pack identities in `.mozaiks/pack_provenance.json`, separately from exclusively
owned template files. Several selected packs may declare the same shared root
(`data/contract.json`, `ui/index.js`, `ui/route_manifest.json`); any other
declared output path belongs to exactly one selected pack.

A composed route may target one of the shell's routable core pages
(`SchemaPage`, `ChatPage`, `ProfilePage`) without a `ui/index.js`
registration, and a route may omit its `id`: composition then matches it by
path and keeps it as written.
An id-less route's path is its identity, so a revision that changes the path
adds a route rather than moving it, and a route with an id at the same path
conflicts with the carried id-less one: an existing id-less route keeps its
path once it is composed beside a pack. Declare a route with an id from the
start to make it movable. An id may not start with `path:`. Only the shell's
routable core pages (`ChatPage`, `SchemaPage`, `ProfilePage`) need no
registration; the other core components (transition screens, the token tab,
the usage panels) are not route pages. Any other route component must be
registered by a contribution.

The resolver never materializes a shared root as one pack's file, whoever calls
it. It leaves the root out of its output and lists it under `composed_outputs`
with every contributing pack's identity; a pack whose templates are only shared
roots still gets a provenance entry, so each contributor names a selected pack.
Assembly composes the roots from those contributions. Provenance that lists a
shared root in a pack's `materialized_owned_files` predates composition: the
bundle scanner rejects it and asks for a reassembly, which rewrites the
provenance.

Without an approved DesignDocs data contract, assembly composes the selected
packs' contributions (and any subscription assignment stores) onto an empty
contract. A `data/contract.json` the bundle already carries is not an approval,
so that composition replaces it only when the carried contract holds nothing
but what the composition derives. The guard compares exactly these:

- each carried surface's `surface_id` and `surface_kind` must be those of a
  composition surface (a selected pack's contributed surface or the
  subscription assignment store surface);
- each collection under a carried surface must be one the composition declares
  under that same surface, matched by collection `name` and physical collection
  (`mongo_collection`, else `collection`, else `name`);
- each row of the top-level `aliases` list, as an (alias, collection) pair, must
  be a pair the composition declares;
- `shared_collections` must be empty;
- every other top-level key except `version` must equal the composition's.

Nothing else is compared. Within a collection the composition declares, its
`fields`, `indexes`, `tenancy`, `owner_field`, entity, lifecycle and
collection-level aliases (`data_alias`, `primary_alias`, `shared_with`,
`read_by`) may differ; so may surface-level keys other than `surface_id`,
`surface_kind` and `collections`, `version`, and surface order. The carried
contract may also lack surfaces, collections or aliases the composition adds.
The composition's declaration replaces all of these, so an earlier assembly,
the pack lifecycle's recomposition, a changed pack contribution or a newly added
assignment store all recompose. It follows that a collection-level alias, or a
changed tenancy, owner field or index, written on a declared collection is
replaced without an error. An app-authored collection-level alias can come only
from an imported or hand-edited contract, since the DesignDocs collection schema
has no `data_alias`.

Assembly checks every copy of `data/contract.json` it is given (the bundle's
snapshot, task outputs and the repair overlay) and, in a revision, the bound
revision source's copy, not only the copy that wins the merge. A pack-only copy
written over the carried contract therefore cannot launder the app's data out
of it, and a bounded repair of the `data/contract.json` owner, whose save would
write that copy, is refused with the guard's reason.

When the guard fails, assembly fails closed instead of replacing the carried
contract, and the error names the reason. A carried contract that holds
app-owned data (any other surface, a composition surface under another
`surface_kind`, a collection or top-level alias the composition does not
declare, a shared collection or differing metadata, including an app surface
that shares a pack's surface id or an app collection inside a pack or
assignment store surface) is reported as holding app-owned data, naming each
item. A carried contract whose only differences are derived data whose identity
changed (a declared collection stored in another physical collection, a surface
without its `surface_kind`, or a subscription assignment store or alias the
current subscription contract no longer compiles) is reported as derived data
that no longer matches the current pack/assignment composition, naming each
change: its stored rows need an approved DesignDocs data contract or a
migration, never a silent re-point.

Canonical policy, schema, and CRUD synthesis still owns ordinary generated and
partially templated modules, including OutcomeFeedback. A complete template
implementation keeps its own actions and participant-aware policy only when
dependency closure, current source identity, declared output closure, and exact
template bytes are verified. The factory's shipped packs and the packs
registered for AppGenerator directly under the workspace build context are
trusted sources;
any other source requires matching version and digest pins, either on the
selected pack or from installed Community Component state verified against the
source's current bytes, and an external source without them is not resolved at
all. A workspace-owned `backend/handler.py` may extend the
verified template base handler. Missing, changed, or undeclared implementation
files fail admission; the exemption does not skip runtime, schema,
implementation, or browser validation. How a selected row resolves to a
verified source is described in [Pack identity authority](#pack-identity-authority).

Messaging `0.1.14` declares `threads`, `messages`, and `thread_reads` under the
`messages` module. These app-wide collections retain participant and verified
workspace authorization in the shipped service and policy; generic collection
CRUD must not expose private conversations. Its additive migration uses only
`ensure_collection` and `ensure_index`, retaining the existing index names and
keys. Reapplying the same migration hash skips it without rewriting records.
Account export and deletion use the same app-bound persistence collections and
preserve messages and conversations belonging to other participants. Its
`message_sent` notification carries no message text (a fixed title and body, a
context of the thread, message and related-record ids, and a `source_event`
reference), and its lifecycle events
declare the subject app and related record as nullable, so plain direct
conversations still dispatch them. Neither the migration nor account deletion
touches platform notifications, so a notification rendered by a pack version
before `0.1.14` (the released `0.1.12` and earlier) keeps its copied preview
until the recipient clears it.

## Targeting

Targeting has two layers.

Workflow targeting happens in `context.yaml`:

```yaml
context_id: game_builder
applies_to_workflows:
  - AppGenerator
  - AgentGenerator
```

The build-context provider ignores this context for any workflow not listed in
`applies_to_workflows`.

Agent targeting happens on prompt projections for catalog assets:

```yaml
assets:
  - path: engine_patterns.yaml
    kind: catalog
    projections:
      - id: game_patterns_for_planning
        records: patterns
        recipients:
          - AppPlanAgent
        render: summary
        marker: GAME_BUILD_CONTEXT
      - id: selected_engine_pattern_for_schema
        records: patterns
        recipients:
          - AppSchemaAgent
        render: selected_record
        selected_by: selected_game_engine_pattern_id
        record_id_field: id
        marker: GAME_BUILD_CONTEXT
```

Use workflow targeting to decide whether the context participates in a run. Use
agent targeting to keep each agent's prompt narrow. A decomposition or planning
agent may need a compact catalog summary; an implementation agent should
usually receive only the selected record.

If a specific agent needs a selected option, selection should be written to a
declared workflow context variable first. The projection can then read that
value through `selected_by`.

## Asset Kinds

Current canonical asset kinds:

- `catalog`: prompt catalog or taxonomy YAML.
- `contract`: typed agent-facing rule contract.
- `templates`: directory of deterministic generated app files.

Unknown asset kinds fail pack validation before prompt projection or
materialization.

Catalog prompt projection is declared on the catalog asset:

```yaml
assets:
  - path: capability_directory.yaml
    kind: catalog
    projections:
      - id: appgenerator_capability_directory_for_planning
        records: capabilities
        recipients:
          - AppPlanAgent
        render: summary
        marker: CAPABILITY_DIRECTORY_CONTEXT
        heading: Capability Directory
```

The generic middleware owns loading and rendering:

```yaml
prompt_middleware:
  - agent: AppPlanAgent
    function: mozaiksai.core.workflow.context.projection.inject_build_context_projections
```

## Contracts

A contract asset is not a prose prompt. It is a typed instruction contract for
agents that consume the context:

```yaml
contract_id: mozaikspay
contract_type: build_pack_instructions
selection_rules:
  - id: select_for_saas_billing
    when:
      intent_any:
        - saas billing
        - subscriptions
    action: select_pack
required_integrations:
  - service: mozaikspay
    provider: mozaiks_pay
    kind: api_key
    required_fields:
      - name: api_base
        type: url
        frontend_safe: true
      - name: client_id
        type: text
        frontend_safe: true
      - name: client_secret
        type: secret
        frontend_safe: false
required_outputs:
  - path: services/integrations/mozaikspay_client.py
    owner: templates
forbidden_outputs:
  - path_prefix: modules/mozaikspay/
runtime_boundaries:
  - id: usage_runtime
    rule: Read runtime token usage through platform usage APIs; use the OSS token wallet ledger for balances.
facades:
  - module_id: billing_portal
    provider_module: mozaikspay
```

Use bounded fields such as `selection_rules`, `required_outputs`, `requires`,
`provides_capabilities`, `forbidden_outputs`, `runtime_boundaries`, and
`facades`. Do not use top-level narrative fields such as `purpose`,
`description`, `generation_rules`, `recommended_facades`, or alternate schema
languages.

Pack dependencies use one canonical contract:

```yaml
requires:
  packs:
    - pack_id: messaging
      version: "0.1.11"
      reason: Support tickets reuse messaging threads.
  capabilities:
    - capability_id: messaging.threads.create
      reason: Support needs a canonical thread creation capability.
```

`requires.packs[].version` is exact when present. Mozaiks does not solve semver
ranges for build-context packs.

## Templates

A `templates` asset mirrors the generated app tree under `app/`:

```text
templates/
├── modules/
├── services/
├── ui/
└── config/
```

Examples:

- `templates/modules/billing_portal/module.yaml` emits
  `app/modules/billing_portal/module.yaml`.
- `templates/services/integrations/mozaikspay_client.py` emits
  `app/services/integrations/mozaikspay_client.py`.
- `templates/ui/pages/billing.yaml` emits `app/ui/pages/billing.yaml`.

YAML files inside `templates/` are generated app declaratives, not build-context
contracts. A selected pack copies every file under each declared `templates`
asset to the same relative path in the generated app bundle.

## Trust And Integrity

Community Components are verified local `CapabilityPack` directories. Discovery
of a descriptor does not install it, installation does not verify it, and
verification does not grant production authority.

Before materialization, a selected local pack must pass deterministic checks for:

- identity and version;
- declared asset closure;
- catalog and contract schema shape;
- dependency declarations and exact dependency versions when declared;
- canonical content digest.

The digest is one `sha256:` value computed from `context.yaml` and every
declared asset file. It excludes timestamps, absolute paths, worktree location,
and transient generated metadata. The same pack content produces the same
digest; material pack-content changes produce a different digest. It hashes raw
file bytes, so a checkout's line endings are part of it: a pin or recorded
digest is compared on the host that computed it, never across checkouts (see
[Pack identity authority](#pack-identity-authority)).

Cross-repository generator inputs use the same content-addressed principle
through `mozaiks.contract_identity.v1`. The identity records the immutable
source commit, the canonical manifest of contract paths and file hashes, and
the manifest digest. Consumers should resolve and pin the source commit before
generation, record the identity in generated provenance, and refuse generation
or promotion when the recorded identity no longer verifies. The reusable
implementation is `mozaiksai.core.contract_identity`; the offline gate is:

```text
python scripts/verify_contract_identity.py \
  --root <generated-bundle> \
  --metadata <generated-bundle>/.mozaiks/contract_identity.json \
  --source-commit <pinned-source-commit>
```

CI must compare against the consumer's pinned source commit, not the current
tip of the source repository. A source repository may advance after a valid
consumer build; that is a dependency-update event, not automatic drift.

Generated bundles retain pack metadata in the existing
`.mozaiks/pack_provenance.json` manifest:

```json
{
  "schema_version": "mozaiks.pack_provenance.v1",
  "packs": [
    {
      "pack_id": "greetings",
      "version": "0.1.0",
      "source": "https://example.com/community-packs/greetings",
      "digest": "sha256:...",
      "materialized_owned_files": [
        { "path": "modules/greetings/module.yaml", "owner": "templates" }
      ]
    }
  ],
  "composed_outputs": {
    "ui/route_manifest.json": [
      {
        "pack_id": "greetings",
        "version": "0.1.0",
        "source": "https://example.com/community-packs/greetings",
        "digest": "sha256:..."
      }
    ]
  }
}
```

`materialized_owned_files` never lists a shared root.

Local installation pins a verified Community Component in workspace
build-context metadata:

```text
build_context/
└── .mozaiks/
    ├── installed_components.json
    └── installed_component_sources.json
```

`installed_components.json` is the canonical deterministic state:

```json
{
  "schema_version": "mozaiks.installed_components.v1",
  "components": [
    {
      "pack_id": "greetings",
      "version": "0.1.0",
      "digest": "sha256:...",
      "source": "https://example.com/community-packs/greetings",
      "dependencies": {
        "packs": [],
        "capabilities": []
      },
      "capabilities": [
        { "capability_id": "greetings.say_hello" }
      ]
    }
  ]
}
```

It does not store absolute developer-machine paths. The non-portable
`installed_component_sources.json` sidecar maps installed `pack_id` values to
local source paths so deterministic verification and materialization can be
re-run in that workspace checkout.

Installation verifies the local pack, validates dependencies against already
installed components, and writes pinned version/digest state. It does not
materialize files and does not select the pack for a build.

Upgrade planning is a dry run:

```text
installed A@v1 + verified local candidate A@v2 -> UpgradePlan
```

The plan reports exact version/digest movement, dependency changes, added,
removed, and changed owned files, and potential conflicts. Shared roots are
never owned files. Applying an upgrade is allowed only when the operation can
compare the workspace files against the old pack's rendered content. It refuses
to overwrite locally modified owned files, files owned by another installed
pack, or workspace-owned/custom files. It validates the upgraded provenance
against the app as it will be before it writes anything. It does not execute
migration scripts.

An upgrade compares the app with the installed source only after that
source verifies to the version and digest installed state pinned; a source
changed in place since installation refuses the upgrade, and nothing
changes. Restore the installed source and plan the upgrade from a separate
candidate directory.

A component pinned with a digest that left out its `contract.yaml` (a
contract the context does not declare, pinned under the earlier digest rule,
or a contract added after installation) cannot show which contract it was
installed with. The upgrade plan, a repeated installation and installed
resolution all refuse it, and nothing changes. The refusal compares what the
pin recorded (the version, the declared files and the capabilities they
declare, and the dependencies it read from the contract) and says that this
does not prove the contract's ownership, provided capabilities or
restrictions unchanged. No lifecycle step adopts a contract its pin never
covered and installation never re-labels an existing pin, so the remedy is
to install the pack from its verified source into a build context without
that pin; installation verifies and pins the whole pack, `contract.yaml`
included.

When the app's provenance claims any shared root as a pack's exclusive file,
whichever pack claims it and in any entry order, the plan blocks with
`exclusive_shared_root_claim` before anything is written. That provenance
predates composition; reassemble the app to recompose its shared roots, then
plan the upgrade again.

`data/contract.json` is never an owned file. When a pack's data contribution
changes, the plan lists it in `recomposed_files`, and applying replaces only
that pack's surfaces in the app contract, preserving app-owned surfaces and
other contributions. When the contribution changes and the app has no
contract, the lifecycle creates one whenever the new version ships a data
contract template, even one that declares no surfaces. In that case it writes
the same empty contract (`"surfaces": []`) that assembly composes for the pack
when no approved DesignDocs contract exists. If the new version ships no data
contract template, no contract is created. The plan blocks before anything is
written when a pack surface was modified locally
(`locally_modified_composed_output`), or when the app contract fails the
runtime data contract loader or conflicts with the contribution
(`composed_output_conflict`).

Provenance keeps other packs' composed contributors. A shared UI root
(`ui/index.js`, `ui/route_manifest.json`) merges the app's and every
contributing pack's declarations into one file, so the lifecycle does not
recompose it. An unchanged template only updates the contributor identity, and
only when the app has that root and its provenance records the pack as one of
its contributors. When an upgrade adds, changes, or removes the pack's template
for a shared UI root, or the app's root was not composed with the pack, the
plan blocks with `composed_output_requires_reassembly` and nothing is written.

For an installed Community Component, that block currently has no supported
way through: the lifecycle cannot adopt that pack version. Applying the
upgrade is the only operation that moves the installed pin, and it is the
blocked step. Installing the new version is refused while another version of
the pack is installed. Reassembly that selects the pack by id resolves the
pinned old source. A reassembly whose pack descriptor names the new version's
`pack_source_path` directly renders the new templates but does not touch
`installed_components.json` or its source sidecar, so the next upgrade plan
still compares against the old version's output and blocks again.

Mozaiks does not perform remote downloads, registry lookup, marketplace ranking,
trust scoring, cryptographic signing, or automatic semver range upgrades for
this local lifecycle layer.

### Pack identity authority

Every selected `capability_packs` row is resolved to one verified source before
anything uses it (`verified_selected_pack_sources`). The resolver tries, in
order:

1. `explicit`: the row's own `pack_source_path`;
2. `installed`: the workspace's installed Community Component with the row's id
   under `build_context_root`, whose source workspace state verified against its
   pinned version and digest;
3. `factory_build_context`: the installed package's shipped catalog,
   `resolve_factory_app_root()/build_context/<id>`, when `<id>` is a safe
   identifier and that directory holds a `context.yaml` with a `pack` block.

A caller may omit `version` and `digest` only where an authority vouches for
the source, and the resolved identity records which one did (`authority`):

- `factory_build_context`: the source is a pack the shipped catalog holds, a
  direct child of `resolve_factory_app_root()/build_context`, whether the row
  names it by id or by that path;
- `installed`: the source is the installed Community Component with the row's
  id, verified against the version and digest workspace state pinned, whether
  the row names it by id or by its source path;
- `workspace_build_context`: the row's `pack_source_path` is a direct child of
  the trusted workspace build-context root (the context's `build_context_root`,
  else the root `load_trusted_build_context` registers packs from:
  `MOZAIKS_BUILD_CONTEXT_PATH`, then `MOZAIKS_APP_WORKSPACE_PATH/build_context`)
  that `load_trusted_build_context` registers for AppGenerator. The resolver
  runs the loader itself over that root, with the AppGenerator declarations
  the installed factory ships (`workflows/AppGenerator/context_variables.yaml`),
  and trusts the row only when the loader registers a pack with the row's id
  whose source is this directory. The loader's own rules decide: a root it
  refuses (an invalid, undeclared or conflicting projection in any context
  for AppGenerator, an invalid `operator_capabilities` value, a pack id
  registered twice) vouches for nothing, and neither does a context
  retargeted to another workflow, one that no longer projects its packs, or
  an inactive pack. A workspace pack must therefore apply to AppGenerator,
  which assembles it: a pack registered only for another workflow, such as
  DesignDocs, is selected with both pins. This is the documented operator-pack flow: the build-context
  projection (`normalize_pack_descriptor`) carries the pack's
  `pack_source_path` and `version` but no digest, because the pack block
  cannot hold one. The row must carry that `version`, which is compared with
  the fresh source, and the fresh digest is recorded. Because the registry
  vouches at a version only, and a pack's bytes can change in place without
  a version change, a recorded build pins every row that names a pack
  directly under the workspace root (unless that root is the shipped catalog
  itself, whose packs follow the installed catalog like any other workspace's
  and are recorded as selected) to the `version` and `digest` its
  acceptance verified, whatever authority acceptance resolved it through
  (`pin_workspace_pack_selections`, from the acceptance gate's
  `resolved_capability_packs`). Refinement admission and Studio's ownership
  checks read recorded rows back as a recorded selection
  (`verified_pack_selection(..., recorded=True)`): a workspace pack row must
  carry its digest, and the fresh source must match it. A pack edited in place
  after the build was recorded therefore fails with `PackIntegrityError`
  (`PACK_OWNERSHIP_UNRESOLVED` at admission) instead of being trusted on the
  registry alone, and so does a build recorded before builds pinned workspace
  packs, or a refinement that copied its rows; an AppGenerator revision
  records a pinned build from the current pack.
  A recorded row whose registration was later withdrawn still verifies
  through its own pins (`selected_pins`). A build cannot be recorded with a
  workspace pack row that acceptance verified no identity for. The registry
  match is on the resolved path, so a pack reached through a junction or
  symbolic link directly under the root resolves to its target outside the
  root and needs both pins, although the loader registers it: link the pack's
  files, not its directory, or select it pinned;
- `selected_pins`: any other `pack_source_path` (a directory outside the
  catalog and outside the workspace build context, such as a copy of a
  workspace pack elsewhere or a lifecycle upgrade candidate) must be selected
  with both `version` and `digest`, and its bytes must verify to them.

An explicit source with neither authority nor both pins fails with
`PackIntegrityError` before its contract is read: a directory's own contract
never vouches for itself, so it grants no capability, layout or output
ownership. `verify_pack_integrity` then computes a fresh version and digest
from the resolved source. Any `version` or `digest` on the row (and, for an
installed pack, the pins workspace state recorded) must equal them. The
resolver records `{pack_id, version, digest, source, resolution, authority}`;
`resolution` names the step that found the source, and `source` stays the
pack's declared source. A pack selected by several rows resolves once, and
only when they agree.

The digest hashes the raw bytes of `context.yaml`, `contract.yaml` (always,
because it decides output ownership and dependencies, even when the context
does not declare it as an asset) and every declared asset, so it is a
same-host identity: a CRLF and an LF checkout of the same pack have different digests.
Row-level digest pins (and installed-state pins) keep comparing against
`compute_pack_digest` of the resolved source on the validating host, so a pin
is meaningful on the host and checkout that recorded it. Across hosts, the
portable identity is the pack id and version, and content is compared as
described below.

A row claims a pack, and must resolve or fail, when it carries a `version`,
`digest` or `source`; when its `capability_source` is a registered pack source
of the AppBuildPlan schema (`managed_capability`, `operator_pack`,
`framework_pack`, `config_file` or `operator_extension`); or when it is not a
`generated_module` row and its id names a pack the shipped catalog holds. A row
whose id names an installed component resolves from installed state whatever
its source. A claiming row that none of the three steps resolves fails with
`PackIntegrityError`, naming the reason: an unsafe id (`../mozaikspay`), an id
the catalog does not ship, or a host where `resolve_factory_app_root()`
resolves nothing. No warning admits an unverified, unresolvable, mismatched or
omitted pack. A `generated_module` row is a planned app-owned module, whatever
its id: unless its own path or installed state resolves it, it selects no pack
and grants no layout extension, capability or output ownership. A row with no
registered source whose id names no shipped or installed pack names no pack to
omit, and selects nothing. A row that names its pack only under `capability_id`
or `module_id` is malformed: no producer of `capability_packs` rows (the
build-context projection, installed-component descriptors, AppBuildPlan and
ExistingAppDiscovery plan rows, the scanner's resolved rows) emits that shape,
so the resolver refuses it with a diagnostic naming the key, rather than
treating it as a non-selection.

Every consumer acts on a pack only through a verified entry. Planning
(`module_action_inventory`: pack-owned outputs, facade directories, template
module contracts and the managed contract inventory) reads a row's contents
only after `verified_pack_selection` compared its pins and recorded its
authority. A row the resolver refuses contributes no pack-owned output,
facade directory or template contract (assembly refuses it with the same
diagnostic). The managed contract inventory (`managed_pack_contracts`) feeds
what a managed pack owns (surface ownership, managed data owners and facade
actions), so a refused managed row raises there with the resolver's
diagnostic, and DesignDocs refuses the design rather than saving it without
the pack's ownership; a row's own inline `facades` are never read. Studio
refinement admission verifies the saved parent's rows once, as saved, and
reads declared outputs from those verified entries
(`verified_pack_output_paths`), so a pack selected with pins keeps them. When
the saved selection cannot be resolved or verified, ownership is unknown and
the candidate is refused with `PACK_OWNERSHIP_UNRESOLVED`; it is never
admitted unchecked.

The bundle scanner resolves the selection the same way (the build context's
selection when it has one, else the caller's rows) and passes only the resolved
rows to every pack-aware check: declared outputs and layout extensions,
managed-capability boundaries and `forbidden_outputs`, the managed setup lane,
the MozaiksPay and Mozaiks Cloud contracts, and the `entitlement_dispatch`
exemption. `forbidden_outputs` and `provides_capabilities` come only from the
verified contract; a caller row's inline values are never read. A caller row
for a pack the build context selected adds nothing but pins, which must match;
one for any other pack may not carry its own `pack_source_path`. The
validation facade and the app-bundle acceptance gate pass the same resolved
packs to the functional checks (`scan_functional_generated_app`), so a row
that selects no pack requires no pack facade either.

Plan coverage, the discard of worker copies of pack-owned files and assembly
read the same selection through the same resolver and the same verification
(`module_action_inventory._selected_packs` calls `verified_pack_selection`), so
an id-only row, such
as the default MozaiksPay row an OSS SaaS plan adds when no build context
selects MozaiksPay, owns exactly the outputs assembly materializes. While
`app_build_plan` builds a plan whose packs the build context does not select,
it releases the pack-owned paths of the plan's own rows.

The bundle's provenance cannot vouch for itself. For each resolved pack the
scanner requires that every `.mozaiks/pack_provenance.json` entry for that pack
records the resolved version (its recorded digest is not compared, because it
is a same-host identity), and that every template-owned output the pack ships
(the composed shared roots aside) is present. The output of each static
template, any template that needs no build-time context (every file but
`.j2`), must equal the verified template text with line endings normalized; a
template-owned migration is compared after the migration compilation assembly
applies. These output checks never render a template: a `.j2` template renders
from the build's template variables, which no validation caller has, so its
output is verified through the pack's declared outputs (present, inside the
declared output lanes, not forbidden) and its pinned identity. Its content is
not compared with anything; `operator_readiness` (a config YAML, a docs page
and a PowerShell script) is the only shipped pack with `.j2` templates. The
validation facade returns the resolved identities as
`GeneratedAppValidationResult.resolved_capability_packs`.

One check still renders templates, for data rather than output bytes: when
the caller passes the build's planned data contract (only AppGenerator's own
acceptance gate does, from its full build context), the planned-data check
recomposes it with the packs' data contributions through
`verified_pack_template_inventory`, which renders every selected pack's
templates with that context. The validation facade and Studio refinement
candidates pass no planned data contract, so they render nothing.

A pack version bump changes what the current pack ships. After an upgrade of
the installed `mozaiks` package (or any shipped pack version bump), an
unchanged bundle built from the previous version no longer matches the current
pack: its provenance records the old version, and its pack-owned files may
differ from the current templates. Validation then refuses it, and so do the
hosted gate and Studio refinement candidates of that bundle, whose build-record
rows resolve to the current pack. This is intended. For a shipped pack the
remedy is an AppGenerator revision of the build, accepted, which
re-materializes the pack at the current version (a regeneration only for an
app with no accepted build); the Community Component
lifecycle upgrade applies only to installed components.

### Hosted bundle gate authority (mozaiks-app)

mozaiks-app's `generated_bundle_gate` infers MozaiksPay from the presence of
`services/integrations/mozaikspay_client.py` and passes
`{"id": "mozaikspay", "capability_source": "managed_capability",
"provides_capabilities": ["subscription_write_path"]}` through
`validate_generated_app_bundle` with no build context. The authority chain for
that row is:

1. it names no `pack_source_path` and the facade has no `build_context_root`, so
   steps 1 and 2 do not apply;
2. `managed_capability` claims a pack, so the row must resolve or fail;
3. the installed `mozaiks` package's catalog,
   `resolve_factory_app_root()/build_context/mozaikspay`, resolves it
   (`resolution` and `authority`: `factory_build_context`), and
   `verify_pack_integrity` recomputes the version and digest from those
   shipped bytes;
4. its inline `provides_capabilities` is ignored: the `entitlement_dispatch`
   exemption and the forbidden outputs (such as `modules/wallet/`) come from the
   shipped `contract.yaml`;
5. the bundle's provenance version and template-owned files are compared with
   that pack, and the facade returns its identity.

A genuine MozaiksPay bundle built from the same package version passes
unchanged, whichever line endings the building or validating checkout uses. A
bundle built from another pack version fails until an AppGenerator revision
re-materializes the pack, as does a
tampered template-owned file or a forbidden output. A bundle of a pack whose
templates render from build context (`operator_readiness`) passes the facade:
those outputs are checked through the pack's declared outputs, never rendered.

Migration items for the mozaiks-app owner (not changed in this repository):

- select packs from the bundle provenance's pack ids instead of inferring
  MozaiksPay from a client file;
- drop the inline `provides_capabilities` claim, which grants nothing;
- remove the dead reads of `capability_packs` from the app record,
  `app_build_plan`, `build_plan` and `metadata`, which no hosted record fills;
- store the returned `resolved_capability_packs` with the hosting record, and
  compare a stored identity across hosts by `pack_id` and `version` only: the
  `digest` hashes the validating host's raw source bytes (see above);
- plan for version skew: after an OSS pin bump, a bundle built from the
  previous pack version fails the gate until an AppGenerator revision of the
  build (a regeneration only for an app with no accepted build)
  re-materializes the pack at the new version; a shipped pack
  has no lifecycle upgrade;
- bundles built before shared-root composition, whose provenance claims a
  shared root as an exclusive file, fail every scan (the hosted gate included)
  until they are reassembled (a regeneration only for an app with no accepted
  build).

## Resolution

At launch, the build-context provider discovers contexts under:

```text
{build_context_root}/*/context.yaml
```

For each context that applies to the target workflow:

1. `assets[]` is validated.
2. `catalog` assets are available to prompt projection middleware.
3. `contract` assets are loaded into `operator_contracts`.
4. A pack descriptor is created from `context.yaml` when `pack:` is present.
5. `projections.context_variables` projects selected values into workflow state.
6. `templates` assets are materialized only for selected packs.

Explicit launch context values take precedence over projected values.

Installed Community Components are consumed only through explicit selection.
An `AppBuildPlan` may select an installed `capability_pack_id`; the existing
materializer then resolves the pinned local source through
`build_context/.mozaiks` when launch context includes `build_context_root`.
Installed state alone is not a selected pack list, and selected pack
materialization still grants no provider or production authority.

## Build Context vs. Context Variables

Build context is authored outside the workflow and selected before the run
starts. Use it for reusable or operator-selected build inputs:

- prompt catalogs
- build packs
- typed generation contracts
- deterministic generated file templates
- provider-backed capability descriptors

`context_variables.yaml` is the workflow's runtime state contract. Use it for:

- user request and trigger payload fields
- current build task
- selected artifacts
- accumulated plans
- agent handoff state
- values tools and hooks read or update during the run

Do not put large static catalogs or pack context directly in
`context_variables.yaml`. Declare the key the workflow may receive, then project
that value from `context.yaml`.

Do not add a workflow-local `build_context/` subfolder to solve product or
operator specialization. Workflow directories own the workflow ABI:
`agents.yaml`, `context_variables.yaml`, `structured_outputs.yaml`, tools,
middleware, and transition contracts. Build contexts are intentionally outside
that directory so the same factory workflow can be reused by OSS, Studio,
hosted products, and customer workspaces without copying the workflow bundle.

The canonical split is:

- `workflows/{WorkflowName}/context_variables.yaml`: declares the keys a
  workflow can accept or mutate.
- `build_context/{context_name}/context.yaml`: declares selected build-time
  inputs and projects only declared keys.
- `build_context/{context_name}/assets`: catalog, contract, and template files
  declared by `assets[]`.
- `workflow middleware`: generic mechanics that render declared prompt
  projections; it should not contain pack-specific product logic.

For domain specialization such as software apps versus games, add named build
contexts rather than new workflow variants:

```text
build_context/
├── software_builder/
│   ├── context.yaml
│   ├── app_archetypes.yaml
│   └── contract.yaml
└── game_builder/
    ├── context.yaml
    ├── game_archetypes.yaml
    ├── engine_patterns.yaml
    └── contract.yaml
```

Both contexts may target the same `AppGenerator` and `AgentGenerator`
workflows. The workflow ABI stays stable; the selected context changes the
catalogs, contracts, templates, and projected small values available to those
workflows.

This only works when the shared workflow already has broad enough structured
outputs to express the target domain. If game generation needs different output
families such as scenes, assets, mechanics, engine configuration, physics
settings, or level data, then the production move is not just adding a
`game_builder` prompt pack. The workflow structured outputs and file contracts
must first add bounded fields or artifact families for those outputs. After
that, a `game_builder` build context can provide the domain catalogs,
contracts, templates, and selected defaults.

The same rule applies to `operator_readiness`: only add the pack when the
workflow already has a bounded `readiness_profile` or similar typed output to
drive it. If the product is not a host/operator platform, the pack should stay
absent. Do not infer it from prose alone, and do not make it the default for
customer-facing app generation.

For that reason, a reusable builder pack should be designed in this order:

1. Define the stable workflow outputs and artifact families the factory can
   validate.
2. Declare the workflow context variables that a specialization may seed.
3. Add build-context catalogs and contracts that choose among those declared
   lanes.
4. Add templates only for deterministic files the pack owns.
5. Use prompt projections to give each agent only the relevant catalog slice.

## Cross References

- [Workflow Architecture](workflow-architecture.md)
- [Workflow Authoring Contracts](workflow-authoring-contracts.md)
- [Domain-Agnostic Build Factory](domain-agnostic-build-factory.md)
