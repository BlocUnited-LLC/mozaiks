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
- `values`: static provider values used by projection rules.

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
digest; material pack-content changes produce a different digest.

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
  ]
}
```

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
removed, and changed owned files, and potential conflicts. Applying an upgrade
is allowed only when the operation can compare the workspace files against the
old pack's rendered content. It refuses to overwrite locally modified owned
files, files owned by another installed pack, or workspace-owned/custom files.
It does not execute migration scripts.

Mozaiks does not perform remote downloads, registry lookup, marketplace ranking,
trust scoring, cryptographic signing, or automatic semver range upgrades for
this local lifecycle layer.

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
