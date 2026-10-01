# AppGenerator Output Assembly Contract

## Typed Page Data Sources

AppGenerator sections select `config.data_source: {module_id, action_id}` from
accepted module contracts. Planning hints carry the same typed `data_source`
separately from presentation-only `config_hint`. Submit/delete actions use their
own pair; navigation keeps its route `href`. Code emits the runtime
`/api/modules/{module_id}/{action_id}` into `api_endpoint` for reads and `href`
for mutations. Plan normalization removes `api_endpoint` and submit/delete
action `href` hints from section `config_hint` mappings, including nested
presentation hints. Navigation `href` values remain intact. DesignDocs supplies
presentation intent without endpoint URLs. Typed `data_source` references remain
authoritative; invalid references identify the page and section in feedback.
Generated files remain subject to the existing binding and wiring checks for
endpoint strings and unresolved references. No module is guessed from a URL or
page name.

Before module output is published to dependent workers, code closes missing
`list_{collection_name}` and `get_{collection_name}` actions for owned entities
shown by list/detail pages or explicitly selected canonical read references.
Collection ownership comes from `data_contract`; existing actions keep their
contracts. A module with protected actions (permissions, entitlement gates, or internal exposure) requires an explicit read contract before construction can widen its action inventory. Summary/metric semantics still require an explicitly declared action.
Service generation implements every accepted action, and page generation selects
from that closed inventory. The same compilation runs in standalone save and
detached task execution, including nested sections and admin panels. Module-owned admin panels select from their owning module contract; page bundles may reference any supplied dependency module. Deleted modules and internal-only actions are excluded from HTTP binding inventories.

List actions use `page`, `page_size`, and `search`, returning `items` and `total`.
Repositories use the deterministically rendered collection policy described in
[the data contract](data-contract-and-revision-contract.md#deterministic-generated-module-policies).
Wiring and runtime-quality validators remain the acceptance backstop.

## Page Output and Action Binding Acceptance

AppSchemaAgent receives accepted module actions with their declared input and
output schemas and entitlement gates. A metric's `value_key` selects a declared
output field. A table's `data_key` selects a declared array, and its columns
select fields declared on that array's items. These checks apply to client and
server pagination. Compilation fills canonical `items`/`total` list bindings
when the action contract determines them; it does not guess semantic metric
renames or invent row fields. Invalid selections identify the bound action and
list the valid fields so the page worker can revise them.

### Constructed bindings

The compiler rejects only genuine judgment gaps. Where the accepted contracts
determine the binding, `page_binding_construction` writes it and logs one
`[pages] <path>: constructed ...` line per change, in the task worker, the
standalone save tool and assembly alike (assembly re-materializes typed page
output, so it constructs from the same approved inputs):

- a metric `detail_key`/`trend_key` the bound action does not return is set to
  null; a metric whose own `id` exactly names a returned field reads that field;
- a create/edit `workflow` action that names no generated workflow is replaced
  by a modal form submitting the collection's one create-shaped or
  update-shaped mutation, when the bundle declares no workflows at all;
- the canonical update (`update_<entity>`) of a collection that a list table
  shows gets an Edit row action (with `selection: single`) and a modal form
  that submits the typed action with `{selected_row.<identifier>}` and
  `{form.<field>}` payload entries, gated or not. A single gated
  update-shaped mutation beside it gets its own row action and modal form,
  labelled with the mutation's name when the canonical Edit is also built
  (`open-<action>` and `<action>-modal` keep the two apart); two gated
  update-shaped mutations are the author's choice;
- the canonical create (`create_<entity>`) of that collection gets a
  `New <Entity>` toolbar action and a modal form submitting the create's
  fields, and the canonical delete (`delete_<entity>`) gets a Delete row
  action opening a confirmation dialog whose Delete button posts
  `{selected_row.<identifier>}` (Cancel closes it), gated or not. Only the
  canonical ids qualify, so a custom delete-shaped mutation such as an archive
  action is never presented as Delete. Internal and `admin_internal` actions
  get no entry point.

A write every plan includes is ungated and still needs its entry point, so
these constructions do not depend on `entitlement_gate`; notes name an
ungated write `shared action <module>/<action>` and a gated one
`gated action <module>/<action> (<gate>)`. When the page already holds a Modal
(at any depth, under any id) whose enabled Form submits the write, or whose
actions delete through it, the constructed opener opens that Modal instead of
adding a second one. A Modal that submits the write only from its footer
actions is not reused: footer actions carry no form state, so it collects no
input. Code builds its own modal form and the note names the footer-only
modal that stays unopened.

The collection is the approved data-contract collection whose canonical list
read the table binds; its identifier is the `search_by` field the canonical
get read looks up, else a declared `id` field, else the field of a unique
single-field index. Mutation shape candidates are the surface map's
`owned_mutations` together with the collection's canonical writes
(`create_<entity>`, `update_<entity>`, `delete_<entity>`, which code compiles
for a module-written collection whether or not the design repeats them), and
each action's declared input decides the shape: only a mutation whose inputs
are all declared collection fields is a record write, a create takes no
identifier, an update requires it plus other fields, a delete requires it
alone. A collection with no record identity at all still gets the create
replacement when exactly one record write exists; edit constructions need the
identifier and are refused. Two candidates of one shape, an input the form
primitive cannot render, an authored section already holding the constructed
modal id, an edit button in a table's empty state, or a missing data contract
leave the author's choice alone; every refusal is logged as
`[pages] <path>: not constructed ...` with its reason. Constructed openers
carry their own ids (`open-<action>`), so they never collide with an authored
action id. A constructed form's explicit `{form.<field>}` payload entries are
served by the page renderer with the coerced field value's own type, so a
number field reaches the action as a JSON number.

### Rejection and pack-owned outputs

A rejection closes every page in the task before it is raised: unresolved
references, binding errors, plan identity and page-schema errors of every page
travel in one message, so the worker's one bounded correction (and the
standalone save tool's caller) sees them all. A page whose bindings were
rejected keeps its compiled form and still gets the structural corrections and
the page-schema check, so its schema errors join the same rejection. A
page-schema diagnostic names the location (including `.config` for a
section's configuration), the section's registered primitive and plain id, the
offending field, and what the contract allows: the accepted fields for an
unknown key, the allowed values for a literal, the rule text for a contract
rule. It never echoes a rejected value.

`config/subscriptions.yaml` is never model work either. Assembly writes it
from the approved subscription contract (`materialize_app_config_contracts`,
reading `subscription_contract` or its artifact fallback) and omits it when no
contract is required. There is no `subscription_config` task type and no
ConfigMiddleware mode for it; plan review and the plan cache release the path
from any task that lists it (`release_subscriptions_config`) and drop a task left
empty. `AppBuildPlan.monetization_provider` is validated against the approved
contract's `contract_required`, not against a planned task. The file's
`assignment_store` is constructed by code (`subscription_assignment_store`): the
runtime `SubscriptionAssignmentStoreDef` defaults, including `active_statuses`
`[active, pending, trialing]`, with `data_alias: billing.subscriptions` and
`user_id_field: user_id`, which both assignment writers key on. The designer does
not author it.

Pack-owned outputs are never model work. Every path a selected pack declares in
`required_outputs` with `owner: templates`, and every `owner: workspace` path
the pack ships a template for in a genesis build, is written from the pack's
templates (`pack_owned_outputs`, resolved from the pack source or the projected
operator contracts). Plan review releases those paths from every task and drops
a task left with nothing to build, so it never synthesizes a facade task trio
for a template module; `page_bundle` owns only the pages no pack ships, and its
message names the pack-provided pages. A worker's, repairer's or earlier
assembly's copy of a pack-owned path is discarded with a
`PACK_OWNED_OUTPUT_DISCARDED` log line instead of validated. Pages bind facade
actions against the template module contract, the one assembly applies.
Assembly takes pack-owned files only from the templates and never re-derives a
template page; it runs `pack_template_page_errors` on each template page (page
schema and action closure, the approved route, metric and table bindings
against the assembled module contracts, workflow references), and a failure
names the pack and the directory its templates came from, because no task can
repair it. CI runs every shipped pack's templates through the same function, and
every `owner: templates` output must ship a template. An acceptance diagnostic
for an unreachable gated action is attributed to the authored page that reads
the action's module, never to `ui/route_manifest.json` or a template page, so
the page owner can repair it; with no authored page at all it is recorded
unowned rather than dropped. An unreachable canonical write
(`wiring_unreachable_canonical_write`) is attributed to the authored page that
lists its collection. When only a template page lists it, the diagnostic is
attributed the way a gated action's is.

Canonical list/get response schemas declare the collection fields their existing
read implementations project. This enriches the response declaration without
changing persistence or the data contract. Managed pack templates replace their
page and module artifacts together before final page compilation, so acceptance
checks the actual emitted contracts. Worker inventories still come from their
accepted prerequisite outputs.

Page workflow actions must resolve to workflows in the supplied workflow bundle
or its artifact-backed integration metadata. A proposed workflow name alone
cannot authorize a button. CRUD actions use typed module references, including
submit actions in create/edit forms, instead of invented workflow names.

Every gated HTTP action must be reachable from a page
(`wiring_unreachable_gated_action`). So must the canonical create, update and
delete of every collection a page's DataTable or ResourceTable lists through
its canonical list read, gated or not (`wiring_unreachable_canonical_write`,
read against the bundle's `data/contract.json`). Each such failure is one
message naming the action, the page or pages that list the collection, and
the entry point to add. An action the gated check already reports is not
repeated. Internal and `admin_internal` actions remain outside both
requirements, and other ungated unused actions remain advisory.
These checks cover declarative app pages under `ui/pages/` and run through the
existing wiring acceptance gate and page repair
path; they do not introduce a new runtime dispatcher or authorization policy.

### Server Table Query Acceptance

Opt-in server-paged DataTable sections must close against the actual generated
module action contract during the existing `validate_wiring` acceptance gate.
Action names in a plan alone are insufficient. The action declares integer
`page` and `page_size`, string `search`, and accepts the table's initial and
next-page query, including empty search and a representative nonempty query when
search is enabled. Extra required inputs cannot be supplied
by this fixed query contract. Internal-only actions are not browser endpoints.

`data_key` and `total_key` resolve through required inline object properties to
an array of explicitly typed objects and an integer respectively. Schema references are not supported in this
bounded binding contract and are never fetched. Missing or incompatible bindings
fail the existing wiring check; they do not create another routing or retry
system. Client-paged tables retain their current input contract and also check
declared output fields. At runtime,
the data-fetch owner separately validates actual rows and counts; declaration
closure does not prove that generated backend behavior implements the query.

**Status:** Canonical contract
**Purpose:** Define exactly how AppGenerator turns persistent UI intent into bundle artifacts.

---

## File Creation and Formats

DesignDocs completes the monetization page inventory before subscription design.
For a greenfield build with `monetization_enabled: true` and an approved concept
recording both `monetized: true` and `subscription_contract_likely: true`, its
save boundary materializes the MozaiksPay pack's facade pages from
`factory_app/build_context/mozaikspay/contract.yaml`: Pricing at `/pricing`,
Billing at `/billing`, and Usage at `/usage`. Each has the app-owned
`billing_portal` facade as its primary `surface_map` owner. A design already on
the same route keeps its name and contents without duplication. App-owned
product pages still require design judgment; the model need not reproduce
pack-declared pages before saving.

`ExperienceSpec` requires a layout, intent, and nonempty primitive sections, so
name and route alone are insufficient. Materialized pages use the pack's
full-width layout, intent derived from the facade's declared actions, and one
`PageHeader` section whose title is the declared page name. This is the minimum
valid design scaffold for the page-bundle/AppSchema path. Final assembly applies
the pack's existing page templates, which own the complete facade UI.

Other enabled monetization still requires DesignDocs to author `/pricing` with
one primary surface owner. Disabled monetization adds no facade pages, and
brownfield/revision scope remains authoritative.

Subscription page requirements refine the approved inventory rather than add
routes. AppGenerator's exact name/route equality check remains unchanged.
Pricing reads the local `config/subscriptions.yaml` catalog through the existing
public-readonly `billing_portal.list_plans` facade action. The facade delegates
checkout to the managed provider; `/api/me/usage` and token endpoints supply
runtime usage and balances, not the app's public plan catalog. No additional
billing module or platform catalog endpoint is needed.

Subscription action gates follow a closed feature inventory. Before subscription
design, the factory injects feature IDs for approved app-owned mutations, custom
reads, and canonical writes. Feature IDs are
`module.<module_id>.<action_id>`. Workflow features are excluded until workflow
launch enforces plan grants (#770). The designer chooses each plan's `included_features` from
that inventory and may set `usage_limits[].feature_id` to a selected feature.
It does not author capability IDs or action gates. The compiler derives stable
`feature.<feature_id>` capability IDs (normalizing each ID segment to lowercase
snake case when needed), runtime `plans[].capabilities`, and
`module_contract_updates` only for features absent from at least one plan. An
action shared by every plan has no entitlement gate, including after a plan is
cancelled. It records `selected_features_by_plan` in the
persisted contract. Unknown selections return one revision message with the
valid IDs and the option to remove the feature from the plan.
The designer still declares metering intent in
`metering_declarations` when applicable. Generated add-ons may select an
inventory `required_feature`; code derives the runtime `required_capability`.

Canonical collection list/get reads and managed-pack facade actions are never
selectable gate targets. A limited Free plan that promises core task management
selects approved create/update/delete features and sets a limit on task creation;
a Pro plan may add an approved custom read such as `summarize_tasks`.
Usage limits are display-only until quota enforcement is implemented (#770).

AppGenerator preserves approved module surface IDs as module IDs and stamps `module.id`
from the approved module path when the file writer drifts. After merging generated
files and managed templates, assembly writes the approved `entitlement_gate`
values into `actions[]`, replacing conflicting model-written values and clearing
unmapped gates on approved modules. Missing module/action implementations require
repair; assembly does not invent their behavior. Subscription configuration and
derived gate mappings remain authoritative across real immutable context reads. The
bundle scanner also verifies that every derived `feature.module.*` grant resolves
to the matching module action entitlement gate.

Agents return structured JSON responses. This transport format does not determine
the format of generated files. App schemas, module contracts, and build plans
feed the existing deterministic materializers; bounded implementation tasks
provide Python/React source through `CodeFile` entries. Assembly combines those
outputs with explicitly declared capability-pack templates.

The existing task-batch attempt budget includes deterministic materialization and
file-ownership validation. Invalid worker output is not merged. AppGenerator
retains the first output rejection for one Factory-authorized correction through
the same AG2 batch, within the original four-call ceiling. Dependencies continue
to drain when a prerequisite fails. Successful outputs and original failure
history remain authoritative; assembly writes a separate summary. Eligible
prerequisites and blocked descendants resume without replaying successful tasks.
Missing evidence, stale inputs, and uncertain interrupted attempts remain blocked.
See [ADR 0011](../../adr/0011-factory-bounded-task-recovery.md).

AppPlanAgent clears stale plan/task state before validating a replacement.
Completed or partial batches advance to assembly; successfully assembled partial
batches go directly to validation for completeness diagnostics. A materializer
or assembly contract failure stops the pipeline and records its exception type
and message in the tool outcome and `app_assembly_error`. The previous
`generated_files` snapshot remains intact. Failed assembly cannot validate or
export that stale snapshot. Attributable failures use the owning task's existing
bounded repair policy, and partial batches retain their bounded task recovery.
After a repair changes the inputs, assembly must succeed before validation.
Unattributable failures and exhausted repairs terminate with `workflow_failed`
and preserve the real cause; user replies cannot retry unchanged assembly inputs.
Auth scaffolding uses an assembled
`app.json` when present. A rejected or empty plan cannot fall through to standalone
page generation.

The workflow-owned `review_app_build_plan` tool validates the strict AppBuildPlan
model, registered pack origins, approved app-owned module identities, page
inventory, and complete genesis task ownership. Capability sources use the
build-context registry vocabulary plus `host_universal` for built-in host
surfaces. A product category is not a registered managed service.
Before repairs or capability-ID checks, every capability and task must reference
an approved surface. The inventory comes from `design_surface_map` and trusted
selected `capability_packs` provider/facade descriptors, never the proposed plan
or a descriptive catalog. Selected managed providers retain the surface IDs used
by existing materializers: the pack ID, its declared `surface_id`, and
`{pack_id}_managed`. Facades use their declared module IDs. An aggregate
page task may use the structural scope `page_bundle` with type `page_bundle`,
kind `ui_only`, and a null capability ID when approved ExperienceSpec pages
exist. This scope never authorizes a capability or module task.
What a page task owns determines that scope. Before the surface check, a
`page_bundle` task with a null capability ID that owns at least one path, every
one an approved page artifact (an approved page file or `app.json`), is labelled
`page_bundle` with kind `ui_only` when its surface_id is not an approved surface,
or when it is `page_bundle` with another kind. A live planner named one task per
page after the page and resubmitted the rejected plan unchanged until its
attempts ran out. A task under an approved surface keeps its label; a task that
owns nothing, owns anything else, or carries a capability ID keeps the check.
Any other unapproved surface rejects the plan without dropping or relabeling work. The
normal `app_plan_feedback` revision loop names each surface and instructs the
planner to remove its capability and tasks. Authentication feedback also states
that auth is platform-provided and needs no generated module.
The planner receives exact case-sensitive page paths projected from the approved
ExperienceSpec through the materializer's existing page-stem helper. Display
names do not become filenames: `Books` at `/books` owns `ui/pages/books.yaml`.
Review constructs omitted genesis page and module tasks even when the proposed
task list is empty. Construction and validation share required page/module path
computations, including route-derived casing. Existing correct ownership is
preserved; missing required paths are filled and conflicting non-page owners
still fail. Tasks of the same type for the same capability and surface that
share an owned file are one unit of work split by the planner (for example one
business_services task per action, each owning the module's service.py): review
merges them into the first one in plan order, unions their owned paths,
criteria and instructions (the first task wins any conflicting field), and
renames absorbed ids in depends_on, generation_order,
carry_forward_decisions and integration_needs. Ownership another step settles
is not a reason to merge: app.json, config/subscriptions.yaml, a selected pack's
outputs and facade module, and page_bundle work. Tasks of different types
sharing a file still fail, and a split that repeats or reuses a task_id, has a
blank id, or would close a dependency cycle is left to those checks.
Selected pack inventory is resolved before coverage construction.
Canonical worker mapping and selected subscription, refinement, and split-admin
task file requirements are also shared with validation. Explicit approved action
names reach module workers. Module materialization constructs canonical reads and, for module-written collections, canonical create/update/delete actions with their implementations and schemas from declared collection ownership and typed list/detail intent. The generated record id is the declared `<entity>_id` field (else `id`, else `_id`); `search_by` is only the get lookup and a natural key there is never replaced by a generated id.
Subscription providers must be explicit or already selected, and facade/client
dependencies follow registered bindings rather than task prose.
Section hints bind only to actions that exist. A `sections_hint[].data_source`
outside the approved action inventory (`all_module_actions`: approved
mutations, custom reads, code-built canonical reads and writes, selected facade
actions) is removed by code rather than rejected; the section keeps its
primitive, intent and title, and the page_bundle task owning the page is told
which binding was removed and which actions its module declares. No action is
constructed to satisfy a hint. A provider pair is checked at the facade its
registered route rebinds it to, and a page a selected pack writes keeps the
bindings its template fixes. Without an approved `design_surface_map` the
inventory is open and nothing is removed. Page compilation enforces the same
inventory; its unknown module/action error lists the valid action ids, or the
valid module ids when the module itself is unknown.
See the [construction requirement inventory](app-build-plan-construction.md) for
the construction/judgment boundary and downstream requirements.
The existing plan cache preserves all typed plan fields. Frozen context values
are detached before catalog lookup and validation.

Plan review follows the Factory quality-gate pattern: deterministic tool state
routes an invalid plan back to AppPlanAgent, with at most three total attempts.
Exhaustion leaves the plan unready and the worker queue empty, then terminates
as a workflow failure. Graph outcome operations remain separate from task-batch
triggers; this gate does not change that runtime contract. Scoped revision and
brownfield plans do not have to regenerate the entire genesis inventory.

UI review follows the same authorized tool boundary. After validated
`AppUIQualityReviewRequest` output, the `review_ui_quality` auto tool audits
the artifacts persisted by `save_app_schema` and commits `app_ui_quality_status`
before the AG2 packet selects the next speaker. One invocation spends at most
one revision attempt. `passed` routes to AdminRegistryAgent, `needs_revision`
to AppSchemaAgent, and `blocked` to the user. An unset status still fails with
`no_transition_matched`; prompt middleware does not write this closed-writer
quality state or recover schema artifacts from chat history.

Partial revisions preload the selected target-owned app-bundle archive into
`generated_files`. The Factory reader verifies the committed archive digest;
foreign ownership, retired records, missing content, and incomplete text-file
loading fail rather than producing a partial baseline. Assembly rechecks the
baseline, then applies schema/task outputs, accumulated repairs, and explicit
deletions in that order. Unchanged files remain intact. A stale selected version
is allowed as revision input because invalidation marks the old version stale;
it is not thereby accepted or promoted. Explicit conceptual replans and full
rebuilds retain their separate carry-forward policy and do not copy the old
implementation wholesale. Binary-containing bundles currently require a
binary-capable refinement path; this text-file path refuses to silently omit them.
Auth scaffolding remains deterministic Factory materialization outside build tasks.
`validate_app_bundle_from_request` invokes `save_auth_scaffold` before every
acceptance check, including user-reply and repair re-entry. It uses the admitted
`app.json` and route manifest, including accepted repairs, and idempotently adds
`config/auth.yaml`, the shared OIDC facade, and public auth routes when
`authRequired` is true. The standalone bundle acceptance gate still rejects a
missing or invalid required auth contract. Only a passed complete-bundle
validation advances to DownloadAgent.
The validation request retains the materialized snapshot through asynchronous
checks and commits it before routing, so the next turn receives the same files
that passed acceptance.

Integration readiness blocks only on required `build_time` or `validation_time`
needs. Runtime-only needs remain in integration declarations and names-only
secret contracts as deployment requirements; missing runtime credentials do not
block app generation. The checkpoint automatically runs `check_integration_readiness`
followed by `save_integration_manifest`, so declarations persist without a model
tool call. Manifest persistence remains best-effort.

Persistent entities are planned through the canonical data contract and
`ctx.persistence`, not a generated replacement database service.

Prompt-time catalog injection preserves the complete authored instructions and
live repair feedback. Catalog names mentioned inline are not section boundaries;
the shared section updater replaces only standalone bracketed headings. Planning
describes the approved scope and task contracts without copying implementation
source or entire catalogs into task messages. Category defaults do not expand
explicitly approved surfaces or override interview exclusions.

Revision planning preserves explicit behavioral qualifiers in each relevant
owned task's `initial_message` and `acceptance_criteria`, including cross-layer
requirements. Generic criteria such as "works as required" do not replace
literal-versus-regex intent, later-page reachability, defaults/edit behavior,
or mutation outcome requirements. The existing `refinement_request` is also
projected to AppSchemaAgent, ServiceAgent, ConfigMiddlewareAgent, and ModelAgent
so those workers can compare their scoped task with the original request.
It conveys requested behavior, not permission to expand file ownership,
override runtime/operator contracts, or change authorization boundaries.
Workers report conflicting contracts or missing prerequisite ownership instead
of silently weakening the request. This adds no context field, decision ledger,
runtime routing policy, or alternate validator.

The existing module-contract persistence guidance supplies ServiceAgent with
the supported offset-pagination path: an ownership-filtered aggregate with
stable ordering and a unique tie-breaker, offset, bounded limit, and a matching
count query. Literal search escapes user text only when literal semantics are
requested. Mutation-success events require an actual owned mutation; missing,
denied, and idempotent no-op outcomes follow the declared action contract, not
a universal HTTP response policy. These are implementation instructions, not
new persistence or page schema APIs.

`tests/test_appgenerator_refinement_behavior_guidance.py` exercises the actual
prompt middleware and contract renderer, including request refresh, explicit
regex intent, and unexposed-field isolation. It does not prove that generated
code implements the instructions. Generated-app acceptance must still probe
later-page records, literal matching, and foreign/no-op mutations with event
observation.

Each normalized `module_contract` task reserves its module's optional
`contracts/events.yaml` alongside `module.yaml`. This reserves write authority,
not a required output: a module with no events still emits no manifest, as with
the other typed optional companions.

Canonical writes own their events. One naming rule
(`canonical_write_event_type`) names them: the canonical create, update and
delete of entity `Task` emit `domain.task.created`, `domain.task.updated` and
`domain.task.deleted`. When the approved design's `events_emitted`, an action's
`emits` or an events.yaml declaration names one of them under any spelling
(`task.created`, `domain.tasks.task_created`, `task_management.created`, ...),
`close_module_events` puts it on the canonical action's `emits`, renders its
events.yaml entry (version 1, producer the module, payload schema the stored
record's declared fields) and the rendered service emits it after the
`after_*` hook with `serialize_<entity>(record)`. A spelling two collections
share maps to neither. Nothing is emitted when nothing names the event.

A custom event keeps its own name. An emit and a declaration that differ only
by the required `domain.` prefix are reconciled to the prefixed type, on both
sides and in reactions/notifications. Otherwise action `emits` names alone do
not determine event versions or payload schemas, so code does not invent the
declaration: closure rejects the output with one message naming the action, the
emitted type, the declared types and the declaration to add. In model service
code, a `ctx.emit` literal naming a declared event under another spelling is
rewritten to the declared type; a write hook re-emitting the event its
canonical write already emits is removed; emitting a canonical write event the
module does not declare is rejected with the site. Every normalization is
logged as `CANONICAL_EVENTS_NORMALIZED` or `EMIT_LITERAL_NORMALIZED`.
Runtime undeclared-event diagnostics identify `contracts/events.yaml`, allowing
the contract worker to author the missing typed custom declarations. For an already
accepted plan without that explicit path, repair uses the task's existing
optional companion authority and adds only the diagnosed companion to its
allowed paths. An explicit foreign owner always takes precedence; the approved
inventory and its execution evidence are not rewritten.

Module action/capability schemas, event payload schemas, and policy-hook schemas
are compiled from `JsonSchemaContract` lists into runtime JSON Schema maps. Null annotations are
omitted; enums and array item types are rendered under their JSON Schema keys.
Each `JsonSchemaProperty.required` boolean is the sole authored source of
requiredness. `JsonSchemaContract` has no top-level `required` field: the renderer
derives the runtime required-name list from the flags, in property order, and
omits it when no properties are required. The retired typed required-name list
is rejected, including when it agrees with the flags; there is no precedence
rule or compatibility normalization. Runtime JSON Schema maps retain their
standard `required` lists, including schemas shipped in pack templates.
Action/capability requests use the existing closed-contract importer: unknown
keys are rejected, and
unrepresentable nested/open request objects fail instead of being weakened.

Workflow/module/page contracts use `.yaml`. Browser manifests, app identity,
data contracts, migrations, and tooling manifests retain their canonical `.json`
paths. Jinja templates are build inputs: `name.yaml.j2` renders to `name.yaml`,
and the `.j2` suffix is removed. Required missing template values or invalid
rendered YAML/JSON stop materialization with the pack and template path in the
error. Optional values must declare their defaults in the template. String
substitutions in YAML must be serialized, for example with Jinja's `tojson`
filter, so punctuation does not change the YAML structure or scalar type.

`generate_and_download` runs `run_app_bundle_acceptance_gate` on the assembled
files before packaging. That gate is the deterministic check of generated
contracts and their connections; it does not substitute for exercising an app's
business behavior and configured integrations.

## Owned Artifacts

AppGenerator emits deterministic app-bundle artifacts for persistent app UI:

- `app.json`
- `admin/admin_registry.yaml`
- `ui/pages/*.yaml`
- `brand/theme_config.json`
- `config/shell.json`
- `config/asset_manifest.json`

The artifact split is strict:

- `app.json` defines app identity, targets, auth intent, and startup behavior such as `startup.landing_spot`.
- `admin/admin_registry.yaml` declares all AdminPortal pages for the generated app's admin surface. Module panels reference these page ids via the `page` field in `modules/{module}/contracts/admin.yaml`. Standard generated-app admin paths use `/admin`; `/apps/:appId/...` belongs to first-party Studio or explicit hosted-operator surfaces.
- `ui/pages/*.yaml` define persistent page structure and route ownership.
- `brand/theme_config.json` defines visual tokens, shared primitives, and semantic `ui.chat` / `ui.shell` / `ui.page` styling.
- `config/shell.json` defines compact app-wide shell behavior: header logo/actions, canonical shortcuts, navigation policy, non-page-owned navigation items, and chrome mode defaults.
- `config/asset_manifest.json` defines reusable media inventory metadata for non-token assets (icons/images/video), including source/provenance and usage hints.

AppGenerator does not own the full build lifecycle, agent workflows, agent UI
tools, or workflow transition surfaces. AgentGenerator produces workflow/agent
artifacts, and the Refinement Engine governs AppContextVersion selection,
validation, review, and ArtifactVersion acceptance/promotion. Generated bundle
files become source of truth only through artifact acceptance and promotion.

When AppGenerator assembles module artifacts into the app workspace, companion
manifests stay under `modules/{module}/contracts/`. Canonical event files are
`contracts/events.yaml`, `contracts/reactions.yaml`, and
`contracts/notifications.yaml`; do not flatten them to the module root or emit
`contracts/subscriptions.yaml`. Persistent module backends use
`backend/schemas.py`, not `backend/models.py`.

---

## Upstream Inputs

AppGenerator should compile these inputs in priority order:

1. `captured_theme_config`
   - optional canonical ThemeCapture artifact
   - strongest visual source when present

2. `app_build_plan`
   - carries `theme_preferences`, `brand_intent`, optional `shell_preset_hint`, pages, entities, capability packs, auth, and integrations

3. `experience_spec_document` / `ui_design_document`
   - persistent page intent and layout guidance

4. `concept_blueprint` and related design docs
   - fallback context when no stronger artifact exists

---

## Compilation Flow

### 1. ThemeCapture

`ThemeCapture` produces canonical visual evidence only.

It emits:

- `theme`
- `identity`
- `assets`
- `primitives`
- `fonts`
- `colors`
- `shadows`
- `ui.chat`
- `ui.shell`
- `ui.page`

It does **not** emit shell content such as header actions, profile menu items, notification copy, or footer links.

### 2. AppSchemaAgent

`app_build_plan.pages` owns the approved page name/route inventory, while
`AppSchemaOutput` supplies complete runtime page contracts. Task acceptance and
assembly validate those schemas without replacing their sections, forms, or
bindings with planner hints. Invalid schemas receive bounded task feedback.
Missing worker pages fail instead of being synthesized from incomplete hints.
Generated-module capability plans declare `user_data_scope`, matching the
runtime module field. True requires a planned `backend/account_data_handler.py`
and its module stub; bundle validation rejects scope drift or a missing handler.
The data contract decides it for a module owning `per_user` collections: plan
review sets the pack's `user_data_scope` and assigns the handler path to the
module's `business_services` task, module closure sets `module.user_data_scope:
true`, and code renders the handler (`module_account_data`): it exports the
owner's rows of each such collection (declared fields, datetimes as ISO 8601,
paged past the persistence page size) and deletes them through the account's
runtime-scoped persistence. A model-authored copy is replaced with an
`ACCOUNT_DATA_HANDLER_OVERWRITTEN` warning. `per_workspace` rows belong to a
workspace, not one member, so account deletion never removes them. The account
lifecycle protocol remains owned by the runtime.
The plan's page name is a display label; the runtime page `name` must match its
owned filename, with the display label in `title`. The approved route remains
unchanged. Page URL validation supplies input-free corrective diagnostics, and
task materialization forwards the build timestamp used for provenance replay.

`AppSchemaAgent` compiles persistent UI into one `AppSchemaOutput` with six payloads:

- `manifest`
- `pages`
- `custom_route_bundle`
- `theme_config_patch`
- `shell_config`
- `asset_manifest`

`manifest` is typed `AppManifest | null`. A detached `page_bundle` worker emits
only its owned pages and supplies a manifest only when it owns `app.json`.
Null materializes neither `app.json` nor `provenance.yaml`; it does not remove
existing root metadata. Other unowned optional output fields remain null.
The existing file-ownership gate still rejects an emitted manifest outside
`owned_paths` and a missing manifest when the task owns `app.json`.

New-app plan coverage requires a manifest owner and ownership of every planned
page. Scoped revision assembly hydrates the verified archive and overlays task
outputs, preserving the baseline manifest and unchanged pages. Nullable worker
output does not make the final app manifest optional: full bundle validation,
runtime loading, and export acceptance still require a complete valid app.
Standalone `save_app_schema` continues to reject a null manifest before writes.
Provider response schemas require an explicit object or null; local acceptance
models retain the existing optional-field default semantics.

### Theme vs Shell Ownership

Two artifacts handle visual and behavioral customization:

- `theme_config_patch` → visual tokens only: colors, typography, spacing scale, shadows, density, `ui.chat` / `ui.shell` / `ui.page` semantic styling
- `shell_config` → shell behavior only: header logo/actions, canonical `shortcuts`, `navigation.policy`, non-page-owned `navigation.items`, and `chrome` mode overrides

`shell_preset_hint` is not an artifact. It is prompt-time AppGenerator guidance
from `factory_app/build_context/AppGenerator/shell_presets.yaml`. AppSchemaAgent
uses it to choose page `navigation`, page `shell_mode`, and whether a compact
`shell_config` override is necessary. The preset id is never written into the
generated app bundle.

Do not mix them. Raw spacing/width/density tokens belong in `theme_config_patch`.
Header action labels and app-wide shell placement rules belong in `shell_config`.

Rules:

- `manifest.default_route` is persisted to `app.json -> startup.landing_spot`
- `custom_route_bundle` is a rare bounded escape hatch for persistent routes that cannot be expressed cleanly through shipped primitives
- `theme_config_patch` is a partial patch for `brand/theme_config.json`
- `shell_config` is a partial patch for `config/shell.json`
- `asset_manifest` is a partial patch for `config/asset_manifest.json`
- raw spacing, width, density, and sizing tokens belong in `theme_config_patch`, not `shell_config`
- generated `shell_config.shortcuts` may only contain `header`, `profile`, `mobile`, `footer`, and `footerHideOnMobile`
- do not emit custom shortcut catalogs or shell fields outside `AppShellConfigPatch`
- shell actions may use semantic `variants[].when` for context-aware label/target changes; do not emit path-prefix, wildcard, or query-param override rules
- page-owned shell navigation belongs on `ui/pages/*.yaml -> navigation`; `shell_config.navigation.policy` owns app-wide placement rules
- page-owned header/footer/bottom-bar intent belongs on `ui/pages/*.yaml -> shell_mode`; `shell_config.chrome` owns only app-wide mode-policy overrides
- reusable media inventory belongs in `asset_manifest`, not in `theme_config_patch` or `shell_config`
- custom routes must be owned exclusively by `custom_route_bundle` (`ui/route_manifest.json` + `ui/pages/custom/*.jsx`) and must not duplicate any `ui/pages/*.yaml` route
- every custom route manifest entry must have exactly one `page_files` entry whose `registry_key` matches the route `component`; `save_app_schema` synthesizes `ui/index.js` from that matched pair
- `app/ui/index.js` must register every component referenced by `ui/route_manifest.json`; missing registrations are export/download blockers, not runtime surprises
- scoped private routes should declare route `meta.routeAuth` when route path or query params identify a resource whose visibility depends on membership, ownership, tenant/workspace access, or an invitation/access state. Declarative pages use `ui/pages/*.yaml -> meta.routeAuth`; custom routes use `ui/route_manifest.json -> pages[].meta.routeAuth`.
- `admin/admin_registry.yaml` declares admin page and panel metadata; it is not a route registry and must not own full-page custom route components
- managed-capability pages must bind through an app-owned facade module endpoint such as `/api/modules/analytics_dashboard/get_metrics`, never directly to managed-capability internals
- declarative pages may launch workflow sessions through typed page actions (`action_type: workflow`), but workflow-local React still belongs to AgentGenerator and `chat.tool_call`

### Managed-capability facade binding

When a build has a selected managed capability pack, AppGenerator must keep the generated app
boundary explicit:

```text
managed_capability
  -> app/services/integrations/{pack_id}_client.py
  -> app-owned facade module
  -> ui/pages bind to the facade module
```

Provider-neutral example:

```text
managed_analytics
  -> app/services/integrations/managed_analytics_client.py
  -> modules/analytics_dashboard/
  -> ui/pages/analytics.yaml
  -> /api/modules/analytics_dashboard/get_metrics
```

The pack descriptor may provide `surfaces`, `supported_domains`, `branding`,
`generation_rules`, `adapter_template`, and `capability_source`.
Those fields are planning metadata. They do not authorize generated pages to
call managed provider internals directly.

For SaaS apps that select the `mozaikspay` managed capability, the generated
app bundle must include the portable SaaS contract, not managed provider
internals:

- `config/subscriptions.yaml` with plans, gates, and only the token wallets,
  token allowances, top-up products, and usage limits required by the app's AI
  usage, credit, or quota model
- `services/integrations/mozaikspay_client.py` as the app-side connector client
- `modules/billing_portal/` as the app-owned facade module
- billing and usage pages bound only to `/api/modules/billing_portal/*`
- no `modules/mozaikspay/`, managed billing module, wallet module, or direct
  provider SDK ownership in the generated app

Managed packs that own subscription assignment writes must declare that through
`provides_capabilities: [subscription_write_path]` in the pack `contract.yaml`.
The scanner uses that provider-neutral capability flag to skip
`entitlement_dispatch`; otherwise any generated app with
`config/subscriptions.yaml -> assignment_store` must include the
`entitlement_dispatch` generated module so self-hosted and custom-provider apps
still have a deterministic assignment writer. This rule is pack-driven and must
not special-case MozaiksPay in scanner logic.

The deterministic app-bundle acceptance gate enforces this boundary on the
assembled bundle, not on isolated task output. A selected managed capability
must pass the generated bundle scanner and the `app_runtime_load` check, which
materializes the same file map that `generate_and_download` would package and
loads it through `AppLoader.load()`. This catches semantic drift such as pages
binding to provider internals, facade handlers accepting synthetic payload
wrappers, missing app-level service packages, invalid companion manifests, or
template files that cannot be imported by the runtime loader.

When `config/integrations.yaml` declares a managed setup lane, the same scanner
also rejects raw payment-provider env handles, provider webhook/checkout routes,
and direct provider SDK mechanics anywhere in the generated bundle. Managed
setup is a contract boundary: provider callbacks and processor mechanics stay
with the managed/hosted product, while the generated app uses the managed
capability client and app-owned facade.

### Route/component registration drift

For custom full-page React routes, the route contract is complete only when all
of these are true:

- `ui/route_manifest.json -> pages[].component` names the runtime component key
- `ui/pages/custom/*.jsx` contains exactly one default-exported page for that key
- `ui/index.js` registers the same key through `registerComponent`
- no declarative `ui/pages/*.yaml` route owns the same path
- no implicit file discovery or admin-registry fallback may substitute for the three files above

Generation should catch missing or mismatched component registrations before
download/export.

### Scoped route authorization

`routeAuth` is the first-class route metadata contract for declarative pages
and custom full-page routes that need a pre-render authorization check. It is
not app-specific and it does not encode policy in the shell. Route metadata
names an app-owned module action, and the module action makes the authorization
decision using the normal service/policy layer.

In AppSchemaAgent structured output, `routeAuth.params` is emitted as strict
`{key, value}` entries so provider response-format validation remains enabled.
`save_app_schema` normalizes those entries into the runtime object shape written
to generated YAML/JSON.

Example:

```json
{
  "name": "ProjectSettings",
  "route": "/projects/:projectId/settings",
  "title": "Project Settings",
  "page_type": "settings",
  "layout": "grid",
  "meta": {
    "routeAuth": {
      "module": "project_access",
      "action": "authorize_project_route",
      "params": [{ "key": "project_id", "value": "$route.projectId" }]
    }
  },
  "sections": [
    {
      "id": "project-settings-header",
      "primitive": "PageHeader",
      "config": { "title": "Project Settings" }
    }
  ]
}
```

The target module action must return `{ "allowed": true }` to render the
route. Returning `{ "allowed": false, "reason": "..." }` denies the route and
lets the shell show the denial reason. This gate protects deep links and avoids
rendering pages before scope is known, but backend module actions must
still perform their own authorization checks.

### 2b. AdminRegistryAgent

`AdminRegistryAgent` runs after `AppUIQualityAgent` passes and before `AssemblyAgent`.

It produces `AdminRegistryOutput` with two payloads:

- `admin_registry` — typed `AdminRegistry` object (page list with ids, labels, paths, icons, scope, order)
- `code_files` — serialized `admin/admin_registry.yaml`

Rules:

- Always emits `overview` and `settings` app-scope pages
- Includes additional **standard** pages (`access`, `billing`, `usage`, `activity`, `operations`, `integrations`, `support`) based on `app_build_plan.capability_packs` entity domains and `auth_strategy`
- Standard pages are modular, not a "full app" bundle preset. A generated app
  should only receive pages whose source capability, entity domain, integration
  need, or access model creates a real operator task for that page.
- Includes **operator-only** pages (e.g. `hosting`) only when `capability_packs` is non-empty and explicitly targets hosted/operator app management — never in plain OSS contexts without workspace build context
- Uses `scope: app` for all generated app pages; workspace-scope pages only for hosted operator contexts
- Hosted global operator registries belong to hosted product workspaces and must not be emitted into standard generated app bundles
- Page ids must cover every entity domain that `ConfigMiddlewareAgent` assigns module admin panels to
- The module contract quality gate validates at generation time that every `admin.yaml` panel `page` field resolves to a declared page id

**Operator-only pages:**

| Page id | Inclusion condition | Path |
|---|---|---|
| `hosting` | `capability_packs` explicitly targets hosted/operator app management such as `hosting` or hosted deployment records; portable deployment artifacts alone do not qualify | `/apps/:appId/hosting` |

Add new operator-only pages here when a managed capability context introduces a new operator surface. Do not add them to the standard inclusion rules.

`AssemblyAgent` must include `admin/admin_registry.yaml` in the final bundle output.

### 3. save_app_schema

`save_app_schema` is the persistence tool for schema-driven app bundles.

It must:

- write generated artifacts under
  `$MOZAIKS_GENERATED_ARTIFACTS_PATH/apps/{app_id}/{build_id}/app/`
- write `app.json`
- write `ui/pages/{name}.yaml`
- write `ui/route_manifest.json` when `custom_route_bundle` exists
- write `ui/pages/custom/*.jsx` when `custom_route_bundle` exists
- synthesize `ui/index.js` from `custom_route_bundle.page_files` when custom routes exist
- reject or warn on custom route registry drift before assembly: missing page files, duplicate registry keys, `.js` route files, non-default-exported React, or route components that no page file registers
- preserve `captured_theme_config` as the theme base and deep-merge non-null
  `theme_config_patch` deltas into `brand/theme_config.json`
- deep-merge `shell_config` into `config/shell.json`
- deep-merge `asset_manifest` into `config/asset_manifest.json`
- store `app_manifest`, `app_pages`, `app_custom_route_bundle`, `app_theme_config_patch`, `app_shell_config`, `app_asset_manifest`, and `app_schema_ready` in workflow context

It must not write directly into an active runtime-loaded app root such as a
workspace's `app/` bundle or `factory_app/app`. Activation requires an
explicit promotion step.

### 4. AssemblyAgent

When `app_schema_ready == true`, `AssemblyAgent` must emit those artifacts back out as `code_files` so downstream download/export tools can bundle them.

Required schema-driven outputs:

- `app.json`
- `admin/admin_registry.yaml`
- `ui/pages/{name}.yaml`
- `ui/route_manifest.json` when `app_custom_route_bundle` exists
- `ui/pages/custom/*.jsx` when `app_custom_route_bundle` exists
- `ui/index.js` when `app_custom_route_bundle` exists
- `brand/theme_config.json` when a captured theme or `app_theme_config_patch` exists
- `config/shell.json` when `app_shell_config` exists
- `config/asset_manifest.json` when `app_asset_manifest` exists

When `app_schema_ready == false`, `AssemblyAgent` should use task batch outputs
via `assemble_app_tasks` and must still preserve the page contract.
Both assembly paths retain the captured theme's identity, assets, and visual
tokens. A partial patch is not a replacement theme document. Explicit deltas
override the corresponding base fields; null patch fields mean no change.

Task worker prompt views come directly from the workflow's declared
`context_variables.yaml` agent views, just like network agents. Detached task
snapshots must not silently lose these declarations or expose undeclared values.

### 4b. Raw Frontend Path Removed

AppGenerator no longer carries a secondary raw frontend page/component generation lane.

Rules:

- ordinary persistent pages still compile through `AppSchemaAgent`
- raw React page/component tasks do not belong in AppGenerator build plans outside the explicit `custom_route_bundle` contract
- shell content compiles through `shell_config`, not through a separate frontend shell agent
- if the primitive system is insufficient, the platform should add a primitive, pattern, or page capability rather than reviving a second frontend codegen path

### 5. IntegrationReadinessAgent

`IntegrationReadinessAgent` runs after `AssemblyAgent` and before validation or
download. It is not a manual preflight step. It is the agentic aggregation point for
third-party connector needs discovered while planning or executing decomposed
build tasks.

Inputs:

- `app_build_plan.external_integrations`
- `capability_packs[].required_integrations`
- `build_tasks[].integration_needs`
- `app_task_batch_results` from task batch execution
- `integration_needs` recorded by task agents with `record_integration_need`

Rules:

- Reuse ready app-scoped connectors from the platform connector store.
- Prompt inline only for unresolved required credentials or required non-secret
  connector configuration.
- Emit structured `integration.required` requests with `integration_id`,
  provider/service id, `purpose`, `required_fields`, `secret_fields`,
  `non_secret_fields`, `permissions_required`, and resume context.
- Persist newly supplied credentials through the platform connector service so
  `/apps/{appId}/integrations` reflects the result.
- Treat the workspace integration catalog as inventory only. The app's
  integration page is driven by `integration_needs` and managed capability
  requirements, not by every available catalog entry.
- For monetized apps, `save_integration_manifest` records `mozaikspay` as a
  removable default app integration when the build did not explicitly declare a
  monetization connector. This default is optional and operator-removable; an
  explicit `capability_packs[].required_integrations` entry remains the source
  of truth for blocking credential requirements.
- Persist only frontend-safe non-secret config in connector metadata. Secret
  fields are write-only and must not be returned to chat, generated code, or
  frontend read APIs.
- Do not generate app-owned API-key tables, credential collections, or custom
  credential forms.
- If required credentials are declined or unavailable, block validation/download
  and return to the user with the unresolved service list.

### 6. generate_and_download

`generate_and_download` is the bundling tool.

It does not reason about artifact ownership. It packages the materialized file set
into the downloadable app bundle after the deterministic app-bundle acceptance
gate passes.

Validation and download use `admitted_app_file_map`: the current `generated_files`
map, admitted `code_files` repairs, and admitted `deleted_files` tombstones.
Historical raw worker responses and prior generated directories cannot overwrite,
refill, or delete that snapshot. An older foreign readback filtered by a save tool
cannot reappear during packaging. Empty current artifacts remain empty.
Authorized deterministic scaffold additions run before acceptance; migration
history registration records metadata without rewriting accepted file bytes.
The archive and export gate consume that same final accepted snapshot.

Download archives may include a single top-level folder named for the bundle
such as `GeneratedApp/`. Studio artifact promotion treats that folder as a zip
transport wrapper only when stripping it reveals an app-root bundle containing
`app.json`. The promoted active app root must contain `app.json`, `config/`,
`modules/`, `ui/`, and other app-bundle families directly; it must not require
the platform host to look under `GeneratedApp/`.

When AgentGenerator workflow metadata is present, the acceptance gate is
export-blocking. AppGenerator must wire each generated workflow through:

- `modules/{module}/module.yaml` `capabilities[]` with `kind: workflow`,
  the AgentGenerator `capability_id`, and the AgentGenerator workflow name as
  `target`
- `modules/{module}/contracts/events.yaml` declaring every workflow trigger event
- `modules/{module}/module.yaml` action `emits[]` for the trigger event that
  starts the workflow
- `modules/{module}/contracts/reactions.yaml` routing each trigger event to the
  AgentGenerator workflow capability id

The gate also blocks semantic drift: trigger-event `capability_id` values must
match the workflow capability id from AgentGenerator metadata, generated app
modules must not invent workflow capabilities absent from that metadata, and a
workflow trigger event must not also route to a different generated workflow
capability unless that route is declared by the same metadata.

Workflow integration, generated-bundle, module, runtime, functional, and planned
completeness diagnostics share one artifact-repair policy. The approved task
inventory determines each exact path's owner. The gate persists:

- `bundle_repair_status`, `bundle_repair_target`, and `bundle_repair_request`
- `bundle_repair_attempt_count` and `bundle_repair_max_attempts`
- `bundle_repair_errors`, failure fingerprint, and no-progress projection
- `bundle_repair_result`, including the active task, allowed paths, request ID,
  settled response state, history, and every deferred diagnostic

A failed or unstarted prerequisite uses `app_task_recovery_request` on the
existing batch rather than artifact repair. `app_task_recovery_result` retains
the outcome; `app_task_recovery_status` projects completed/partial progress back
to assembly. Unknown root diagnostics are never reconstructed.

Accepted tasks may receive a scoped artifact correction. The same two-proposal
budget covers all artifact diagnostics. A repeated, rejected, or interrupted
lane stays blocked while an independent eligible owner can use a remaining
proposal. Each save validates the entire candidate before applying any change;
foreign writes/deletions are rejected, and unrelated accepted output is preserved.
Canonical optional-path rules cannot override another explicit task owner.

The graph routes schema, module-contract, and service repairs through their
quality gates and back to complete-bundle acceptance. It also supports ModelAgent,
DatabaseAgent, ControllerAgent, RefinementHarnessAgent, and FrontendStubAgent as
explicit owners. It never selects ServiceAgent merely because a diagnostic
contains `backend/`; `schemas.py` belongs to its approved ModelAgent task.

Acceptance requires all planned required files and accepted task evidence in the
final snapshot, including revision baseline preservation. A class rename cannot
complete an absent action method. A successful import cannot waive the workspace
subclass contract. Missing files, changed plan/evidence, and unresolved quality
findings block export. Passing evidence binds the plan, inventory/results, build
binding, and exact file contents; GitHub export checks the actual archive against
that digest. Final snapshot validation cannot refill omissions from historical
worker output.

Offline regressions live in `tests/test_appgenerator_bounded_recovery.py`,
`tests/test_appgenerator_task_integrity.py`, and
`tests/test_appgenerator_recovery_routing.py`. They prove original rejection
retention, bounded AG2 correction, released descendants, complete acceptance,
unauthorized-write rejection, and blocked exhausted/interrupted recovery.

Browser interaction evidence remains a separate validation-environment contract.
E2B workspace failures do not authorize worker changes or an app-owned npm project.
Live installed-package acceptance is coordinated separately after the OSS change.

When a build/export context requests deployment output, or the generated files
already contain `deployment.manifest.json`, `Dockerfile`, `docker-compose.yml`,
`.github/workflows/readiness.yml`, or `.github/workflows/deploy.yml`, the acceptance gate runs the provider-neutral
deployment artifact validator. Deployment-ready bundles must include valid
deployment artifacts such as `Dockerfile`, `.env.example`,
`.env.staging.example`, `.env.production.example`, and `deployment.manifest.json`;
missing deployment artifacts block export/promotion
instead of being discovered by a later hosting adapter.

The acceptance gate also runs `app_runtime_load`, a no-live-call runtime loader
check. It writes the assembled file map to a temporary `app/` root and calls
`AppLoader.load()`. Export and promotion are blocked when the app manifest,
module contracts, companion manifests, handler entrypoints, runtime extension
contracts, data/subscription config, or app-level `services.*` imports cannot
load. The check persists `app_runtime_load_passed` and
`app_runtime_load_result` into workflow context and includes `app_runtime_load`
in `app_bundle_acceptance_result.validation_evidence`.

After it, the gate runs `app_runtime_smoke`: in a child process with a scrubbed
environment and a hard time limit, the bundle is booted on a disposable
database and its module actions are called as two signed-in users (see
[Runtime Smoke Gate](../app/generated-app-functional-acceptance.md#runtime-smoke-gate)).
Generated code does not run in the factory process. The result is persisted as
`app_runtime_smoke_result` and inside `app_bundle_acceptance_result`, which the
downloaded bundle's metadata carries. Failures join the bundle repair
diagnostics with the file each one names. A check with no database reports
`skipped`: `validation_evidence.skipped` and `skipped_checks` list it with the
reason, and it is neither completed nor failed.

Modules declaring `user_data_scope` must provide a loadable account-data class
whose constructor takes `db` or `persistence` (the account routes inject what it
declares) and asynchronous, keyword-callable `delete_user_data` and
`export_user_data` methods accepting `app_id` and `user_id`. The loader
imports it in the same module namespace as the action handler and fails the
module when the contract cannot be registered. Account-data load failures and
repository API quality failures join the existing bounded `ServiceAgent`
repair path; they do not become successful loads with warnings.

File-contract prompt hooks preserve all hard constraints. Service workers see
the callable signatures from the runtime's `PersistenceCollection` protocol,
not a Motor collection API. The repository quality gate rejects unsupported
cursor and find-and-modify calls. A persistent module's repository is
module-level functions, and code renders the canonical ones. At task time and
assembly, a model-authored undecorated top-level function or class in
`repo.py` that neither business logic nor any import-time statement reaches (a
leftover repository class, a duplicate CRUD helper) is removed with a
`REPO_CODE_DISCARDED` warning. Every name a non-definition top-level statement
uses (an assignment, a `HANDLERS["x"] = f` registration, a module-level `if`)
and every decorated definition is live. A repository class the handler or
service uses, or a referenced repo function calling a Motor-only method, is
rejected with the use site and the replacement. An opaque use of the
repository module (a star import, the module passed or reflected on,
`importlib`, `__import__` or `sys.modules` anywhere in the module's code)
proves nothing dead, so nothing is removed. Bundle acceptance also checks that generated
data contracts preserve the approved plan's field types and required flags.
These checks do not prove arbitrary business logic correct; live authenticated
CRUD and ownership tests remain necessary for end-to-end acceptance.

### 7. AppValidation Strategy

`AppValidationAgent` must use an explicit validation strategy contract instead of
implicit E2B-only behavior.

Canonical strategy values:

- `e2b`
- `docker`
- `local`
- `skip`

Canonical status values:

- `passed`
- `failed`
- `skipped`

Rules:

- Studio/hosted environments may prefer `e2b` when sandbox credentials are available.
- Local environments with a running Docker daemon resolve to `docker`, which also
  exposes a preview URL (see
  [app-validation-sandboxes.md](app-validation-sandboxes.md)).
- CLI/local environments may resolve to `local` or explicit `skip`.
- generation/export must not be blocked solely because E2B is unavailable.
- `skip` is explicit and deterministic; it is not a hidden fallback and it is not
  reported as `passed`.
- export gating must allow only `passed` or explicit `skipped`, and still requires
  integration readiness and wiring checks to pass.

Materialization rule:

- `ModuleContractBundle` keeps `module_yaml` required. Companion manifests are
  nullable: null means no file. Tasks must explicitly own any companion they
  generate; ordinary CRUD does not automatically create admin/settings/events
  manifests. Do not serialize null manifests as empty YAML or raw file mirrors.

- typed agent outputs such as `app_backend_admin_config`, `python_files`, and
  `js_files` are the source of truth for their owned lanes
- the same applies to `database_files`, `model_files`, and `service_foundation_bundle.files`
- extraction materializes canonical file content from those typed fields before
  task/save admission; packaging consumes the admitted serialization
- raw `code_files` are the serialized mirror, not the authority, when a typed
  lane exists

---

## Bundle Rules

Partial AppSchema repairs overlay the existing page inventory by canonical page
name, preserve unchanged routes, and publish the rendered file overlay back to
the same validation bundle. A corrected page on disk is not sufficient if the
export gate still sees stale context.

Every task writer that edits an existing artifact receives `generated_files`
through its declared agent context view, including ModelAgent. Schema patches
preserve exports consumed by unchanged files. Runtime module-import failures
enter the existing bounded ServiceAgent repair lane; that lane returns directly
to AppValidationAgent instead of regenerating unrelated UI or adapter files.

Page HTTP bindings, including nested form and modal actions, must resolve to
HTTP-visible module actions. Omitted/null `api_surface` means authenticated API
access; `internal` and `admin_internal` are not browser endpoints. Keep declared
permissions and user ownership when correcting exposure; do not make the action
public to bypass authentication.

Optional event manifests remain explicit. When a task owns `contracts/events.yaml`,
ConfigMiddlewareAgent receives the exact event/action bindings from the approved
plan's `event_flows`, even if the task's prose does not repeat them.

Service implementations emit declared events with `await ctx.emit(event_type,
payload)` after persistence, not `ctx.events.publish(...)`. The module quality
gate rejects access to that nonexistent event bus. Input constraints belong in
the closed action request contract where supported. Additional business checks
such as nonblank names run in service code before mutations and raise
`mozaiksai.core.runtime.ModuleInputValidationError` for `INVALID_PARAMS`/HTTP 400.
The executor does not expose the exception message or classify arbitrary
`ValueError` bugs as client mistakes. Expected ownership denials use `PermissionError` so
the module API returns a permission response instead of an internal error.

`api_surface` is a finite runtime contract. Omitted/null is distinct from the
invalid string `"null"`. Explicit form submit payloads must bind every declared
field that the form submits. `SummaryStrip` and `MetricCard` may declare
`api_endpoint` for live module-backed values through the same authenticated
data loader as tables.

Scoped coding repair validates the full staged baseline plus changed files,
not just a patch or an unrelated indexed workspace. When using fallback checks,
Mozaiks page validation also resolves module action references and rejects
unsupported primitive configuration. A source validation failure blocks artifact
creation. Explicit validation skips remain skips, not proof of functionality.
The result message distinguishes a staged patch from a failed, ineligible, or
planned repair; staging never implies promotion or successful live acceptance.

Do:

- keep persistent pages declarative
- stack primary record tables below page headers with `layout: full-width`;
  `grid` means peer top-level columns, not full-width rows
- keep shell content separate from shell styling
- reuse ThemeCapture output when available
- deep-merge generated theme/shell patches into canonical runtime files

Do not:

- generate raw React files for persistent pages by default
- generate any AppGenerator-managed raw React page/component files for persistent pages
- place header/footer action content in `theme_config.json`
- place spacing/padding/density tokens in `shell.json`
- place reusable media inventory in `theme_config.json` or `shell.json`
- route visual shell concerns through AgentGenerator

---

## Why This Exists

Without this split, AppGenerator either under-specifies visual/media control or mixes styling, shell behavior, and asset inventory.
The contract above keeps bundle generation deterministic, keeps ThemeCapture reusable, and gives the runtime a stable set of artifacts to consume.






