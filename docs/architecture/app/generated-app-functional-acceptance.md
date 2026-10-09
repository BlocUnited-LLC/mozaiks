# Generated-App Functional Acceptance

Mozaiks generated apps must be functionally coherent, not only schema-valid.
The acceptance boundary is:

```text
deterministic canonical plan
-> generated app bundle
-> static cross-artifact validation
-> runtime load smoke
-> representative routes/actions/facades resolve
```

This gate is intentionally independent of App Zero and BlocUnited hosted
services. Proprietary strategy may produce better app plans, but OSS baseline
output and proprietary-enhanced output must pass the same canonical acceptance
contract.

## Functional Completeness Definition

Generated-app acceptance is tracked in three levels:

- **Level 1 — Structural:** files, schemas, contracts, and cross-artifact
  references are valid.
- **Level 2 — Functional:** the app loads through the runtime, declared
  routes/actions/workflows/facades resolve, and expected surfaces do not return
  accidental 404, 501, missing-action, or placeholder responses.
- **Level 3 — User Journey:** representative multi-step user journeys work
  end to end through the UI and backend.

Normal OSS CI should enforce at least Level 2 for representative deterministic
generated archetypes: CRUD, monetized SaaS, workflow/agent, operations/admin,
and multi-module content/community applications. Level 3 coverage should grow
through golden journeys, but Mozaiks should not claim universal Level 3
coverage until tests prove it.

A generated app is functionally complete when:

- `ui/route_manifest.json` routes resolve to a built-in page, registered custom
  component, intentional redirect, or explicitly external route.
- `SchemaPage` routes point to a matching `ui/pages/*.yaml` or `*.yml` schema.
- Declared module actions in `modules/*/module.yaml` have an implemented module
  handler method.
- Page and custom UI module calls target declared `/api/modules/{module}/{action}`
  actions.
- Workflow YAML/JSON module-action references target declared module actions.
- Selected managed capabilities include the generated app-facing facade files
  and actions required by their public contract.
- Expected generated app surfaces do not contain accidental 501,
  `NotImplementedError`, or `*_NOT_IMPLEMENTED` placeholders.
- The app bundle loads through `AppLoader` without startup import errors.

The gate does not require real Stripe, Azure, GitHub, DNS, or BlocUnited hosted
credentials. External provider configuration may fail clearly at runtime, but an
internally declared app route/action/facade must not be missing.

## Existing Validation Matrix

| Invariant | Existing Validator | Static / Runtime | Coverage |
| --- | --- | --- | --- |
| Canonical path and secret boundaries | `generated_bundle_scanner.scan_generated_bundle` | Static | Existing, reused |
| Page schema shape and module endpoint syntax | `audit_page_schemas` | Static | Existing, reused |
| Page endpoint to module action wiring | `validate_wiring` | Static | Existing, reused in AppGenerator gate |
| Module action handler implementation | `validate_module_implementation_contract` | Static AST | Existing, reused in AppGenerator gate |
| Placeholder backend runtime data | `audit_module_runtime_quality` | Static AST/text | Existing, reused in AppGenerator gate |
| Workflow event/capability integration | `validate_workflow_integration_contract` | Static | Existing, reused when AgentGenerator metadata exists |
| App startup import/load | `AppLoader.load` via acceptance gate | Runtime smoke | Existing, reused |
| Boot with persistence, two-user CRUD isolation, entitlement enforcement | `app_runtime_smoke` via acceptance gate | Runtime / HTTP | Added |
| Route component/schema resolution | `scan_functional_generated_app` | Static | Added |
| Workflow module-action target resolution | `scan_functional_generated_app` | Static | Added |
| Managed capability facade completeness | `scan_functional_generated_app` | Static | Added |
| 501/not-implemented generated surfaces | `scan_functional_generated_app` | Static | Added to public facade |
| Generated app host boot and declared HTTP page/module surfaces | Platform `TestClient` over deterministic CRUD bundle | Runtime / HTTP | Added |
| Generated app JWT startup and request authentication | Platform `TestClient` over a materialized authenticated CRUD bundle with real signature and claim validation | Runtime / HTTP | Added |
| Monetized SaaS app host boot and declared MozaiksPay-compatible billing/facade surfaces | Platform `TestClient` over deterministic SaaS bundle plus in-process compatible provider fake | Runtime / HTTP | Added |
| Workflow/agent app catalog load, start, and module-action surfaces | Workflow manager plus platform `TestClient` over deterministic workflow bundle | Runtime / HTTP | Added |
| Cross-archetype post-plan replay | Captured `AppBuildPlan` fixtures through the real deterministic AppGenerator task-batch/materialization path, bundle validation, functional scanner, `AppLoader`, and platform `TestClient` | Static / Runtime / HTTP | Added |
| Brownfield post-discovery handoff | Captured `ExistingAppDiscovery` artifact plus module decomposition through deterministic AppBuildPlan projection, real AppGenerator materialization, validation, `AppLoader`, and platform `TestClient` | Static / Runtime / HTTP | Added |
| AgentGenerator-to-AppGenerator handoff | Captured `WorkflowBundleBuilderOutput` through AgentGenerator bundle materialization/promotion, workflow integration metadata, AppGenerator AppBuildPlan consumption, workflow registry loading, and platform `TestClient` | Static / Runtime / HTTP | Added |

### Generated App JWT Authentication

`tests/test_generated_app_jwt_auth.py` materializes an authenticated app and its
deployment files, loads it through `AppLoader`, and boots the platform host. It
proves missing `AUTH_AUDIENCE` prevents startup; a correctly signed access token
with the configured audience can call a module action; and missing bearer
tokens, missing or wrong audiences, ID tokens, and a request user ID that differs
from the token subject are rejected.

The test uses ephemeral signing keys, an in-process JWKS response, and fake
persistence. It exercises the real JWT verifier and HTTP authentication
dependencies. Browser sign-in and a live identity provider remain separate
acceptance checks.

## Page Wiring Input Authority

`run_app_bundle_acceptance_gate` passes its assembled `generated_files` snapshot
directly to the existing `validate_wiring` check. The check detaches immutable
workflow-context values before inspecting mappings or lists. Saved page YAMLs
and module manifests in that snapshot take precedence over earlier `app_pages`,
planned actions, or an older generated directory. This also applies when the
gate is called with `files=` and no workflow context.

Missing validation input and unresolved page endpoints are blocking, including
when no module action registry is available. Static pages, navigation, custom
React routes without declarative API references, and the supported platform
read endpoints do not require app module actions. Page reads and nested action
hrefs (including form submit/cancel, buttons, and empty states) are checked.
An upstream experience specification's proposed URL is not a route declaration:
generated business operations must bind to actual module actions. Runtime page
schema parsing still permits safe host-owned/custom API paths; this check does
not replace the host's routing contract or introduce a second runtime validator.

`validation_strategy: skip` leaves build execution unverified; it does not
establish acceptance or bypass mandatory wiring checks. A zero-reference result only means
that inspected input contains no page API references, not that an absent input
was successfully validated.

## Diagnostics

Page task validation reuses the runtime page schema and reports its diagnostic
location. Builder retry feedback additionally preserves the known, input-free
action requirements (for example, `submit actions require href`); it does not
expose arbitrary validator messages or rejected values. A Form submit action
must carry its own fixed module endpoint. When create and update target different
actions, AppSchema generates separate forms/modals, not a null or conditional
`href`. The runtime schema and bounded task retry budget remain unchanged.

Functional failures are structured diagnostics. Examples:

- `MISSING_ROUTE_COMPONENT`
- `MISSING_SCHEMA_PAGE`
- `MISSING_MODULE_ACTION`
- `MISSING_MODULE_HANDLER`
- `CAPABILITY_FACADE_MISSING`
- `PLACEHOLDER_IMPLEMENTATION`

These diagnostics are exposed through
`mozaiksai.core.validation.validate_generated_app_bundle(...)` and through the
AppGenerator `functional_completeness` acceptance subgate.

## Runtime Smoke Gate

The other acceptance checks read files. A recorded app (AppGenerator chat
6ebcbc4b) passed every one of them while it could not start with persistence
enabled, crashed on every write and denied paying users. `app_runtime_smoke`
(`factory_app/workflows/AppGenerator/tools/app_runtime_smoke.py`) runs the
bundle instead.

**Process boundary.** Generated Python runs in disposable Docker containers,
never in Studio or a host Python child. Before staging any generated source,
the gate checks that local Docker can inspect the exact image ID in
`MOZAIKS_APP_RUNTIME_IMAGE_ID`. A missing or mismatched ID makes acceptance
`skipped` and blocks export. The AppLoader diagnostic probe runs as a non-root
user in a read-only container with no network, published ports, or Docker log
stream. Its only writable locations are size-limited temporary filesystems. Candidate Python can
write the probe's JSON, so `app_runtime_load_passed` and the loader check supply
repair diagnostics only; neither is promotion evidence. The host sets the
separate `app_runtime_load_worker` check only after the bounded worker returns
and its removal is confirmed. The runtime smoke uses a private MongoDB container
and a source-free observer; that smoke and the host-owned worker check govern
acceptance. Unconfirmed container cleanup blocks export.
Build the local validator with
`docker build -f infra/docker/Dockerfile.preview -t mozaiks-sandbox:local .`,
then set `MOZAIKS_APP_RUNTIME_IMAGE_ID` to the full value from
`docker image inspect --format '{{.Id}}' mozaiks-sandbox:local`. The gate never
pulls an image automatically.

**Boot.** The contained app loads the bundle with `AppLoader.load()` and applies the
declared indexes and data migrations to its private disposable database. It registers modules
with `ModuleExecutor.register_loaded_module`, the same call the platform host
makes, and mounts the `api_router` extensions. **Startup services are not
started.** They run outside module dispatch with their own clients and
credentials, so the smoke cannot keep them inside the disposable database. They
are reported `not_run`, and only the deployed app starts them. Requests go
through the real module router over an in-process ASGI client inside the contained app:
no port.

**Two-user CRUD.** For every collection whose canonical create action
(`create_<entity>`) is declared, signed-in users A and B, holding the same plan,
exercise the canonical actions that exist:

- A creates a record. The stored document must carry the returned id and A as
  owner.
- A lists and reads it, and a read of a missing id returns 404.
- B neither lists nor reads it, and B's update and delete leave the stored
  record unchanged.
- A's update changes the stored field, and A's delete removes it.

Payloads come from each action's declared input schema and the collection's
field types. `app_wide` collections get A's round trip only. Steps that need A's
record report `not_run` when the create produced none; they never pass
vacuously.

**Entitlements.** For every action with an `entitlement_gate`, a user on the
default plan without the capability must receive 402. A user with an active
assignment granting it must not be refused. The assignment is written through
`config/subscriptions.yaml` `assignment_store.data_alias`, resolved against
`data/contract.json` aliases exactly as `ConfiguredEntitlementAdapter` resolves
it. A store that cannot hold assignments is reported once: no store, no
`user_id_field`, no active status, or an undeclared alias. Without
`config/subscriptions.yaml` the host wires no entitlement adapter and every gate
allows, so the smoke expects exactly that.

**Permissions.** Users carry the token scopes `config/auth.yaml`
`frontend.default_scopes` grants. A module.yaml permission outside them denies
every signed-in user. That is reported once per action. The call is then
repeated for the same user with exactly the missing permissions, so the defects
behind it are reported in the same pass.

**Dispatch collaborators.** Inside the contained app, the composed app is dispatched in
enforce mode through a `ModuleDispatchEnvironment` dependency override:

- an empty `PlatformHookRegistry`, an in-memory audit log, local event
  recording and no usage metering;
- module persistence, entitlement reads and migration history in the disposable
  database.

The host's auth mode, platform hooks, audit log, usage metering and system
database are never used. A hosted factory may register module scope, permission,
policy and audit hooks. Generated-app smoke does not receive the host's Mongo
URI or write to the host database.

### Imported-source runtime smoke

An imported Genesis can contain arbitrary Python. Its proposed acceptance path uses
`run_contained_imported_app_runtime_smoke(app_root, expected_source_sha256=...)`
on the exact staged app bytes. The caller supplies every verified source-file
digest; the runner checks the copied mount bytes and rejects missing or extra
files. Generated-app acceptance now delegates to this contained runner after
its own image preflight; imported Genesis callers still opt in separately.

Before staging imported source, a caller can use
`preflight_contained_imported_smoke()` to require a reachable local Docker
daemon and an exact pinned validator image. It raises on missing capability and
returns the immutable image ID. The runner repeats the same preflight before
copying source and runs both containers by that ID; a successful early check
does not replace the execution-time check.

The imported backend copies up to 4,096 regular files and 64 MB into a private
temporary directory, rejecting links and special files. It runs two disposable
containers from the same pinned local image. Container A mounts the verified app
copy read-only, starts MongoDB and the app on its isolated loopback interface,
and has `--network=none`. Container B joins only A's network namespace. B mounts
only a read-only projection of `app.json`, the data/auth/subscriptions contracts,
and module manifests. It has no app Python, source repository, Docker socket, or
shared writable mount, and uses its own process and mount namespaces. Both
containers use a read-only root, a non-root user, dropped capabilities, no new
privileges, and CPU, memory, process, temporary storage and time limits.

The image-owned observer in B probes A over loopback HTTP and inspects the
disposable MongoDB directly. It runs the two-user CRUD and entitlement checks
from the declarative contracts. A's stdout is diagnostic only; the host parses
result events only from B, validates a per-run nonce and one HTTP boot/completion
receipt, and sets `observer_origin: trusted_external_probe_v1`, `observer_run_id`,
and `observed_boot` only on a passing result. The host retains the verified
source-content digest and exact validator image ID. Docker registers both names
before starting each container so cancellation can remove them. Both are forcibly
removed and their absence checked after success, failure, timeout, or cancellation.
A failed teardown or bounded-output violation fails the smoke. No host Mongo URI,
credential, provider key, source repository, Docker socket, or host workspace is
mounted or passed to either container.

The observer verifies externally visible HTTP and Mongo behavior. The runtime
drops a rejected `ctx.emit` before dispatch while preserving the action's write
and HTTP success; the rejection exists only in A's process. B therefore cannot
distinguish a rejected emit from no emit. Contained results declare
`observer_unverified_checks: [event_rejection]`, including when their observed
checks pass. A's reported rejection list is not trusted evidence. The ordinary
in-process smoke detects rejected events, but imported Genesis acceptance must
stay closed under its separate admission contract. `observer_origin` attests
the source of the bounded observations, not parity with every in-process smoke
check.

### Generated-app contained acceptance scope 2.0

[ADR 0020](../../adr/0020-generated-app-contained-runtime-acceptance-scope.md)
defines the narrower generated-app claim. Its only
`acceptance_scope.excluded_observer_checks` value is `event_rejection`.
The generated result retains `observer_unverified_checks: [event_rejection]`;
that field is never cleared or described as independently verified. The
platform-wired action emitter rejects invalid declared events before dispatch,
as proven by framework tests. The contained observer does not establish that a
particular candidate attempted no rejected `ctx.emit` or emitted every event
needed by its intended behavior. Required downstream event effects need their
own external acceptance checks.

The generated gate attaches `acceptance_scope: {version: "2.0",
excluded_observer_checks: [event_rejection]}` only after the host checks all
supported external outcomes, one matching boot and completion receipt, the
trusted observer origin and run ID, exact generated-source digest, locally
preflighted immutable validator image ID, and confirmed removal of both
containers. Its contained AppLoader worker must also have host-confirmed
cleanup and matching source/image identity. A missing, altered, or extra
unverified check leaves the generated gate pending and issues no snapshot
digest. Imported Genesis does not use this generated scope.

Build validation is a separate required gate. Generated apps can select only
`docker`, explicit `e2b`, or `skip`; `skip` blocks promotion. The Docker build
path inspects the same immutable local image ID before staging source and runs
offline as a non-root user with a read-only image and bounded writable space.
Candidate package scripts never run through a host npm subprocess. A passing
runtime scope cannot override a failed or skipped build.

The preview image must be rebuilt after changing this smoke module:
`docker build -f infra/docker/Dockerfile.preview -t mozaiks-sandbox:local .`.
An operator can select an equivalent trusted local image with
`DOCKER_SANDBOX_IMAGE`; imported source cannot select the image.
Set `MOZAIKS_IMPORTED_SMOKE_IMAGE_ID` to the trusted local image ID returned by
`docker image inspect --format '{{.Id}}' mozaiks-sandbox:local`. The gate checks
that ID and runs by ID, so a later tag change cannot swap the validator between
inspection and execution. It returns the image ID and copied-source digest as
candidate Genesis evidence.
Docker or image unavailability yields a skipped, blocking smoke result.
The image is never pulled automatically during this gate. Docker Engine and the
trusted preview image are local prerequisites; no paid service is required.
The observer separates the evidence channel from imported app Python, including
its stdout file descriptor and interpreter monkeypatches. A finite black-box
test cannot prove every future behavior of adversarial code: an app can still
choose behavior based on incoming requests. Retain exact-source review and
independent promotion approval for adversarial code.

**Results.** Each check passes, fails or did not run, with one message naming
the action, the user, the expected response and the actual one. A 5xx carries
the exception and the generated file and line it was raised from. The result is
persisted as `app_runtime_smoke_result` and inside
`app_bundle_acceptance_result`. Failures join the bundle repair diagnostics with
the file each one names:

| Failure | Repair path |
| --- | --- |
| Exception raised in a generated file | that file |
| Wrong id or owner, isolation breach | `repo.py` |
| Ungranted permission or unenforced gate | `module.yaml` |
| Index or alias defect | `data/contract.json` |
| Plan or assignment defect | `config/subscriptions.yaml` |

When Docker or the pinned image is unavailable, the check reports `skipped`
with the reason (`passed: null`, `status: "skipped"`). The generated smoke
starts its own disposable MongoDB; host `MONGO_URI` is not a prerequisite. Acceptance
lists it in `validation_evidence.skipped` and `skipped_checks` with that reason.
It is neither completed nor failed. The aggregate acceptance remains `pending`
with `passed: false` until required checks complete; an actual contract failure
still makes it `failed`. A skipped smoke contributes no app-code repair
diagnostic. Missing validation infrastructure must be restored before a build
can become ready. There is no persisted-draft revalidation endpoint yet: run
the build workflow to produce a new validated artifact; this may use model calls.
No accepted snapshot
digest is issued for an unverified candidate.

Source refinement follows the same distinction. Every selected, applicable
detected validation command must complete successfully. Unselected commands do
not block that source-validation result; rejected, unavailable or truncated
selected commands do. Static fallback checks retain their individual results,
but syntax-only success is an aggregate `warning`, not readiness. Empty or
all-skipped validation stays `skipped`. These outcomes preserve a reviewable
draft without declaring it validated or eligible for promotion.

**Not covered.** Module reactions, workflow triggers, pages and startup services
are not exercised. Custom actions are called only when they carry an entitlement
gate. Actions on the `internal` and `admin_internal` surfaces are out of scope:
signed-in users cannot call them over HTTP. `public` and `public_readonly`
actions are called like any other, as a signed-in user.

**Known follow-ups.** Pack-owned permissions outside `config/auth.yaml`
default scopes (commerce) are reported as ungrantable. Failures on code-rendered
files without an owning task remain blocked for repair. Only the exact
generated-app scope above permits the one known unverified observer check;
other unverified checks remain pending. The Docker validator image must be
built and pinned by the operator; the gate never downloads one on demand.

**Proof.** `tests/test_app_runtime_smoke.py` exercises the retained trusted-fixture
child path on real Mongo against two recorded bundles
(`tests/fixtures/runtime_smoke_*.json`):

- The 93a7 replay fails for its known reasons: null index name, migration
  without a version, undeclared assignment alias, ungrantable permissions, a
  crash on every create and on a missing id, and paying users denied. With the
  create crash repaired, the stored record shows the wrong id and owner fields.
- The fdfa818e run replayed at c8b9ea2e passes every check.
- Further tests cover the trusted fixture's hard timeout, child environment,
  startup services and the no-subscriptions case.

`tests/test_app_acceptance_isolation.py` verifies that generated acceptance
delegates to the contained loader and smoke runners, blocks when the image is
unavailable, and admits only the exact generated scope with trusted observer
evidence. Its opt-in real Docker test loads the recorded good bundle and
observes its HTTP boot with a pinned local image even when ambient Docker
host, context, and config settings
point elsewhere. The independently unverified rejected-event check remains
visible in the accepted scope; the contained invalid-emission fixture shows
why that check cannot be claimed as verified.

The suite-wide conftest gives every other test no smoke database, so acceptance
tests that do not opt in report `skipped`.

## Representative Archetypes

Current automated coverage includes:

- Basic authenticated CRUD route/action/schema coherence.
- Basic CRUD app runtime boot through the platform host plus declared
  `/api/pages/*` and `/api/modules/*` HTTP surfaces.
- Monetized SaaS facade expectations and runtime HTTP calls for the public
  MozaiksPay-compatible generated-app contract using an in-process compatible
  provider fake.
- Workflow/agent module-action references through declarative workflow YAML,
  workflow catalog loading, chat-session start, and the referenced module-action
  target through the platform host.
- Cross-archetype post-plan replay for authenticated CRUD, monetized SaaS,
  workflow/agent, admin/operations, and multi-module content/community apps.

The first gate uses deterministic fixtures rather than live LLM calls. This
tests the deterministic boundary after reasoning:

```text
captured/generated artifacts -> functional acceptance
```

## CI Meaning

The functional scanner is part of normal pytest coverage through
`tests/test_generated_app_functional_acceptance.py` and is invoked by the public
generated-app validation facade. A failure means the Factory can produce or
accept an app bundle whose declared app surfaces do not resolve internally.

The gate is deliberately not a broad static analyzer for arbitrary Python or
JavaScript. It validates canonical contracts and generated references where the
framework has deterministic knowledge.

## Post-Plan Deterministic Proof

Mozaiks now has a deterministic post-plan replay path for representative
generated apps:

```text
captured AppBuildPlan
-> real AppGenerator planning/batch execution
-> materialized canonical bundle
-> validation
-> runtime boot
-> declared routes/actions/facades resolve
```

This proves the deterministic boundary after reasoning. The AppPlan may still
be produced by dynamic intelligence, but once a canonical AppBuildPlan exists,
the OSS materialization path can replay it into a working generated app without
paid model calls.

Current coverage includes a representative SaaS AppBuildPlan fixture that
materializes, validates, and boots through the public platform host, then
proves entitlement denial and entitlement allowance on the declared module
surface.

## Archetype Regression Matrix

`tests/test_generated_app_archetype_matrix.py` keeps the breadth proof small and
offline. Each row uses a captured structured `AppBuildPlan` and passes it
through the same deterministic post-reasoning seam:

```text
AppBuildPlan
-> AppGenerator app_build_plan normalization
-> AppGenerator task-batch execution with deterministic AG2 runner output
-> assemble_app_tasks materialization
-> validate_generated_app_bundle
-> scan_functional_generated_app
-> run_app_bundle_acceptance_gate
-> AppLoader
-> platform TestClient
-> declared HTTP/module/workflow/capability surfaces
```

The current matrix covers:

- `authenticated_crud_projects`: auth plus persistent project/task CRUD pages
  and module actions.
- `monetized_saas_reports`: MozaiksPay-compatible billing facade, subscription
  status, checkout/portal actions, and entitlement denial/allowance through a
  local fake provider boundary.
- `workflow_agent_research`: generated app page/module wiring plus canonical
  workflow registry loading from deterministic workspace-level workflow files.
- `admin_operations_dashboard`: user route, admin registry route, protected
  operations action denial without auth, and successful dispatch with local
  auth.
- `community_content`: multi-module content/community pages with public page
  reads and authenticated module dispatch.

For every row, the test materializes the same plan twice and asserts exact
generated app file-map equality. This is a deterministic materialization proof,
not a prompt-to-app determinism claim. The model reasoning stage remains
dynamic.

The workflow/agent row intentionally keeps workflow files at the workspace
workflow root because workflow bundles are AgentGenerator-owned artifacts, not
files inside the generated app bundle. The matrix proves the AppGenerator app
bundle and canonical workflow registry boundary load together.

## Brownfield Post-Discovery Proof

`tests/test_brownfield_agentgenerator_acceptance.py` covers the deterministic
boundary after ExistingAppDiscovery reasoning:

```text
local brownfield source fixture
-> ExistingAppDiscovery source preload evidence
-> captured ExistingAppDiscovery structured artifact
-> captured module_decomposition_plan
-> canonical AppContext adoption artifacts
-> deterministic AppBuildPlan handoff
-> AppGenerator app_build_plan normalization
-> AppGenerator task-batch materialization
-> validate_generated_app_bundle
-> scan_functional_generated_app
-> run_app_bundle_acceptance_gate
-> AppLoader
-> platform TestClient
```

This proves post-discovery correctness for a representative authenticated
FastAPI/React CRUD adoption fixture. It asserts source intent survival such as
`/projects`, `Project`, and `update_project` reaching generated page, module,
persistence, and handler surfaces. It also mutates the generated handler to
prove a dropped discovery-required action fails functional acceptance.

This is not a raw source plus LLM determinism claim. Source discovery and
reasoning can still be dynamic. The deterministic proof begins at the captured
structured discovery/adoption boundary.

## AgentGenerator Handoff Proof

The same acceptance file covers the deterministic boundary after AgentGenerator
reasoning:

```text
captured WorkflowBundleBuilderOutput
-> AgentGenerator bundle file materialization
-> workflow promotion into workspace workflow root
-> workflow_integration_metadata extraction
-> AppBuildPlan workflow touchpoint/module action alignment
-> AppGenerator app materialization
-> validation and functional scanner
-> AppLoader
-> workflow manager registry load
-> platform TestClient
```

This proves that a representative AgentGenerator workflow bundle and the
AppGenerator-produced app harness load together without paid model calls. The
test asserts that the declared workflow, agents, context variables, structured
outputs, tool function, page route, and referenced generated module action all
survive the handoff.

## Factory Save Boundaries

Live generation must prove saved artifacts, not only successful model responses
or workflow-completion messages. Runtime `structured_output` is a read-only,
turn-local projection. Factory tools use `detach()` before dictionary validation,
serialization, or persistence; they do not mutate the runtime projection.

ThemeCapture declares one save attempt and `saved`/`blocked` outcomes. DesignDocs
declares `saved`, `revise`, and `blocked` over three attempts, retrying only after
`revise`: a refused surface map returns to the agent with the reason in
`design_docs_save_feedback`, while `blocked` stays terminal for what a retry
cannot change. Retrying on the error value is not permitted, so a retryable
rejection needs its own outcome word. SubscriptionContractDesigner declares three review attempts, retries
only after `changes_requested`, and distinguishes `confirmed`,
`not_requested_headless`, and `blocked`. A connected review that becomes
unavailable is not headless approval. Existing tool-outcome validation and AG2
transition graphs enforce these contracts; failed saves terminate with
`workflow_failed` rather than starting the next workflow.

Before persistence, DesignDocs normalizes duplicate platform authentication/session
and selected managed-pack state only when the declared contract determines both
the canonical owner and the state to remove. Authentication surfaces become
`owner: platform` references. Managed subscription surfaces become the app-owned
`billing_portal` facade, merging into an existing facade when present. Approved
app-owned pages and routes are preserved. A page a normalizing surface owns moves
to the facade only when the facade serves it: one of the facade's pages by name or
route, or a page whose every section is billing. A section is billing when its
typed binding (`data_source` or a module action URL) targets only the facade's own
actions, whatever fields it shows; when it binds only the surface's aliases of
them and shows only the rule's state fields; or, on a page named exactly for the
rule's reserved state (`Subscription`, `Subscription Management`), when it binds
nothing and shows only state fields. Wording alone never moves a page (`My Plans`,
`Usage Alerts`), nor does a reserved name over app columns. Any other page
(`Alerts`) stays with an app surface that also owns it; with none left, one
rejection names every such page, and other pages' bindings the facade will not
serve, with each change that saves: bind every section to a facade action through
`data_source`, or remove the page while the design keeps another, if it shows
billing; otherwise move it to an app surface's `owned_pages`, or keep the surface
app-owned under entity and collection names of its own when the rule declares its
entity a homonym (`homonym_entity_names`: `Subscription`, not token wallets). Pages
the design itself puts on `billing_portal` are not moved and are not checked. Only identified duplicate collections
and entities are removed; a duplicate collection on an otherwise valid app domain
surface does not change that surface's owner.

Two further corrections are determined by the same contracts. Events declared on
a surface that normalizes to platform or managed-provider ownership are that
owner's lifecycle events, which the app cannot emit; they are removed and
recorded as `removed_events`. Sign-in pages owned by a surface whose ownership
rule declares `platform_capability: authentication` duplicate what the generated
auth contract already serves; they are removed from the approved
`experience_spec` inventory (recorded as `removed_pages`), and typed navigation
references (`href`, `route`, `path`, `fallbackPath`) to their route point at
`routes.login` (recorded as `redirected_navigation`; a page designed at the
login route itself records no redirect). Sign-in checks, removal, and redirects
compare routes under one identity that ignores a trailing slash. A page is a sign-in page when its route
is one of the auth contract's `login`, `callback`, or `logout` routes or a route
one of them nests under (such as `/auth`), or when a `Form` section collects a
field the rule lists as identity evidence (`password`, tokens, credentials).
Any other page filed under an auth surface, apart from the user-administration
pages described below, is a user-designed app page. It is never dropped, and the
save rejects it naming the app-owned surface it must move to. Sibling surfaces that normalize to the same platform capability are not
co-owners; the removal is constructed once and recorded on each. Facade pages
are not removed: the pack renders them as app pages. AppGenerator plan review
therefore never receives a sign-in page to build.

Platform identity is recognized by what a surface declares, not by its
`surface_id`. The authentication rule's `entity_names` and
`surface_entity_names` (`User`, `UserCredential`, `Session`, `AuthIdentity`,
..., compared case-insensitively and across camelCase/snake_case) are the platform
identity entities; a `credential_entity_names` entity (`Account`) is one only
where its records carry one of the rule's `sign_in_credential_fields` (a password
hash), so a CRM, bank, or linked social account, or a password manager's saved
logins, stays app data. Recognition needs evidence: a catalog
identifier the surface declares (a reserved surface id, entity, or action); a
user-account collection of one of its platform identity entities; or a sign-in
action (one of the rule's `sign_in_verbs` with a platform identity entity, such
as `login_user`) over identity records of an entity it declares, such as a
session store. A user-account collection is identity state (every field a
declared identity claim in its bounded type, beyond bookkeeping keys and the
collection's own `<entity>_id` record id unless that name is itself a declared
claim, as `session_id` is) and either carries a credential field
or has a unique index on one of the rule's `account_key_fields` (email
identity), alone or beside bookkeeping keys such as `app_id` or the owner field
the save path prefixes to unique indexes on owned collections. An identity
entity is the platform's on a surface when none of that surface's own records of
it (singular or plural) hold app data, and either the surface stores identity
records of it or no collection anywhere in the design holds app data for it. App
fields beside a password make it app data, and so do its actions. That data
stays with its module and splits as described below. A users collection of an
account entity (`User`, `AuthIdentity`, or an `Account` holding a password hash) that
no surface declares, filed under an app module or under a data group with no
surface at all, is judged by its fields, but only when its records are one per user (identity with
one of the rule's `account_credential_fields`, unique on an account key, or unique
on `user_id`; never kept per workspace): identity state is
removed, and app fields beside identity split to its app module as below. A
device, ledger, integration, membership, or history collection is left to the
design. Such a surface normalizes to the
platform whatever it is called (`user_management`, `auth_module`). Its identity
collections of identity entities are removed regardless of collection name. An
action that is exactly one of the rule's `identity_lifecycle_verbs` and a
platform identity entity (`create_user`, `list_users`, `user_login`,
`logout_user`) is the platform's user lifecycle. It is removed and recorded as
`removed_mutations`/`removed_reads`; actions the catalog already reserves are not
re-recorded. Any other action naming a user (`follow_user`, `update_user_theme`)
is app behavior. Its events and sign-in pages are removed as above. Sign-in is
the platform's even where the identity entity carries app data: on an app-owned
surface that declares a platform identity entity, or whose identity records were
removed or split out, sign-in actions (one of the rule's `sign_in_verbs` with an
account entity, such as `login_user` or `register_user`) and sign-in events
(`<entity>_<fact>` with one of the rule's `sign_in_facts`, such as
`domain.users.user_logged_in`) are removed and recorded, and so is a page section
whose typed binding targets a removed action (`removed_sections`) and a sign-in
page it owns (`removed_pages`, with typed navigation pointed at the login route);
its app data and other actions stay. A workflow trigger on a removed sign-in event, on any
surface, is a design decision and is rejected. On every other app-owned surface,
whatever it declares, a page at an auth contract route (`login`, `callback`, or
`logout`, or a route one nests under such as `/auth`) that holds nothing but
authentication is removed the same way and recorded in `removed_pages` on that
surface, which keeps its other pages, actions, and data. The platform owns those
routes, so an app page there can never be served. The page holds nothing but
authentication when no section has a typed binding (`data_source` or a module
action URL) to an app-owned module and every section is a credential form or
names no typed record field beyond the rule's identity claims and credentials. A
credential form is a `Form` whose typed fields are a secret (a `password`-type
field or an identity evidence field) beside one of the rule's
`account_key_fields` (`email`, `username`), every other field a secret or an
identity claim. A page at those routes with app content stays approved. Away
from those routes nothing is recognized: an email and password form there cannot
be told apart from an app's own "connect your account" form (a Jira API token,
an IMAP mailbox). A sign-in page designed at another route (`/signin`) on an app
surface that neither declares nor stored platform identity is therefore a known
false negative and stays approved. Field types
are bounded when they are any canonical scalar type (`CANONICAL_FIELD_TYPES`
minus `STRUCTURED_FIELD_TYPES`, so `date` counts), and structured only where the
rule declares that shape for the claim.

The platform administers users itself. The rule's typed `user_administration`
names the admin portal's built-in `users` panel, the `access` admin page, and the
entities it administers. The platform's user accounts are the declared fields of
the administered entities' identity records, on any surface normalizing to the
platform, wherever the design files them. A page such a surface owns lists
the accounts when a `DataTable` or `ResourceTable` has a typed column among those
fields beyond bookkeeping keys. That page is never a sign-in page, even with a
create-user form collecting a password. It is user administration when every
listed column is an account field or a bookkeeping key, and every other typed
record field on the page is an identity claim or a credential input. It is then
removed from the inventory and recorded in `removed_pages` with `builtin_panel`
and `admin_page`. Otherwise it is rejected, naming the fields the accounts do
not declare. The panel is not yet served end to end (no user-list API and no
working Access route in generated apps), so no route is declared or linked.
Typed navigation to the page (a `?query` or `#fragment` does not matter) is
removed rather than redirected, and recorded as `removed_navigation`. The link
or action object goes with its reference, and so does what that leaves without
a purpose:
- a list or mapping all of whose entries went;
- a child section whose config went;
- a `Grid` whose children all went (a `Modal` keeps its own content);
- an `actions` entry whose action went;
- an `ActionButton` or `Button` with no action left.

Any other action keeps its control. Each removal is recorded at the outermost
object that went. A section left without a purpose is removed. A page left with
no sections is a design decision and is rejected. A form alone, such as a profile form, lists nobody. Neither does a
list of sessions or teams. These stay user-designed pages that must move to an
app owner. A page listing the accounts beside columns they do not declare is
never kept app-owned: it is rejected naming those columns, whatever the
surface is called. An app entity that merely
references users (`TeamMember`, or `Task` with a `user_id`) is not an identity
entity and stays app-owned. So do identity-named entities with no account
evidence (an app's own `Session` of focus time, unless the surface pairs its
identity records with sign-in actions) and those with app data behind them.

Each correction appears in the saved design's `ownership_normalizations` and
human-readable documents, and produces a `DESIGN_OWNERSHIP_NORMALIZED` log entry
with the source surface, canonical owner, removed collections, and, when
present, removed mutations and reads, removed events, removed pages (a
user-administration page names the admin panel that administers users),
redirected navigation, and removed navigation. A managed facade correction also
records `mapped_actions` (a provider action the facade serves, such as
`subscribe_user` -> `start_subscription_checkout`, declared by the rule's
`action_aliases`; an alias never makes a surface billing by itself),
`rebound_sections` (typed page bindings moved to the facade and its action), and
`released_pages` (a page an app-owned surface also owns, which stays app-owned). Design completions share the record: an AI
`workflow` surface the approved concept never asked for is saved as `module`
when it declares entities, mutations, custom reads, or collections and as
`ui_only` otherwise (`realized_surface_kind`; with no AI in the app, every
module-owned collection declared `workflow_write` becomes module-written,
`module_written_collections`), a module
owning a collection whose entity it does not list gains it (`added_entities`)
unless another surface declares it or another module's collections hold it too,
the module lists it under another spelling, or it is a platform identity or
selected-provider entity, a collection's `ownership.surface_kind` follows its
owner's kind (`mirrored_collection_kinds`), and a custom read equal to a
code-owned canonical read (`list_tasks`, `get_tasks` for a module's `tasks`
collection, after ownership normalization) is dropped from `custom_reads`
(`removed_reads`, on the surface's own record). These recorded
corrections and normalized typed contracts govern any conflicting original prose.
Normalization works on detached data and does not modify the model's turn-local
structured output.

Ambiguous ownership still returns `revise` with feedback before any design is
saved, and the feedback names the exact behavior to split out and where it
belongs. This includes a provider-state collection with app fields (a managed
facade owns no collections, so the feedback names the collection to remove, the
facade read that serves its state per the rule's `state_readers`, and the app
fields to keep under an app module), a collection on app surfaces named for the
provider only by its entity or by a reserved collection name beside an app entity
its surface declares (remove it, or name the app's own records for what they
are), unknown state under reserved surfaces,
app-specific entities or actions that cannot be assigned to the canonical owner,
`workflow_triggers` on a platform or facade surface, unrelated facade
integrations, a grouped collection whose declared app owner disagrees with its
group (a collection holding only the rule's state is removed wherever it is
filed when its declared owner is the rule's own surface), competing owner
declarations, a platform auth event that another
surface's `workflow_triggers` consume, a sign-in page that an app-owned surface
also owns or whose sections bind app-owned modules through typed
`data_source`/module-action references, and a non-sign-in page filed under an
auth surface. An app-named surface (one matched only through a reserved entity
or action, not by its surface id) that also owns app entities, actions,
collections, workflow triggers, or non-sign-in pages is told to drop the
reserved claim and stay app-owned rather than to become a platform reference;
this is decided for every surface before any page is removed, so the outcome
does not depend on surface order. An app-named surface whose every claim is
reserved is determined and normalizes to the canonical owner. A surface
recognized as platform identity that also declares app-owned entities, actions,
collections, or pages is told the same thing. The rejection names its identity
claims to remove (entities, actions, the identity collections the save removes,
and the platform lifecycle events it declares), and the entities, actions,
collections, pages, and triggers that stay app-owned on it. Removing platform pages can never empty the approved
inventory.
Selected managed ownership rules without a declared `facade_module` also reject:
the tool cannot infer a provider's canonical app boundary from its display name.
For mixed auth collections, the tool strips declared identity fields and moves
the residual fields to the collection's existing app-module owner when declared,
otherwise to the sole eligible app module. Platform surfaces, all facade modules
declared by selected pack contracts (even without a `surface_ownership` rule),
and other reserved surfaces are not eligible. Zero or multiple candidates require
revision, with candidate IDs in feedback; no app module is invented. The retained
collection has a required string `user_id`, `search_by: user_id`, and a unique
user index (including `app_id` when declared). Indexes referencing removed fields
are removed. A globally reserved collection name gains `_app_data`; a conflicting
destination collection requires revision instead of merging unrelated data.
Splits are recorded with destination, removed fields, and retained fields in
`ownership_normalizations` and the saved design prose.

This is deterministic coverage, not a measured production rejection rate:
auth-only tables do not need an app owner; mixed tables with one determined app
owner normalize; mixed tables with zero or multiple unresolved owners revise.
There is no traversal telemetry establishing how frequently the last case occurs.
AppGenerator's `validate_surface_ownership` check remains
strict before module repairs: an invalid already-approved design must be revised,
and cannot be silently dropped or converted into generated app code.

Exact ownership identifiers live in `surface_ownership` rule lists under
`capability_routing.yaml`'s `layers.runtime_provided` and selected packs' declared
contract assets. `SurfaceOwnershipRule` validates `owner`, optional
`facade_module`, and lists of `surface_ids`, `entity_names`, `action_ids`, and
`collection_names`. Bounded normalization also declares `state_field_names`,
`surface_collection_names`, `surface_entity_names`, and `surface_action_ids`;
the authentication rule adds `identity_lifecycle_verbs`, `account_key_fields`,
and `user_administration`. A managed facade rule may declare
`account_state_field_names`, the names designs give its state on the platform
account record (MozaiksPay: `subscription_status`, `subscription_type`). They join
the authentication rule's account state only while that pack is active, because
the facade serves them; in an app without the pack nothing serves them and they
stay app data. Its `homonym_entity_names` are the provider entities whose names
also name app records.
The auth field inventory cites [OIDC Core standard claims](https://openid.net/specs/openid-connect-core-1_0.html#StandardClaims),
the platform account profile, `UserClaims`, provider claim mapping, and input
aliases in the catalog itself. `role`/`roles` on an identified auth table are
platform identity: the Keycloak adapter maps realm/client roles into
`UserClaims.roles`, persisted by the platform account profile. This does not
classify resource-scoped memberships or unrelated app profile collections as
identity; see [user classes and resource relationships](user-classes-and-resource-relationships.md).

The three surface-scoped lists identify state and behavior only within a surface
that is platform identity: one that declares a reserved surface id, entity, or
action, or one recognized by its user-account collection as described above. So `users` and `create_session` do
not globally classify app identity or appointment behavior as platform auth. The
one exception is credential evidence: a `users` or `sessions` collection carrying
a credential field is identity storage on any surface. A removable collection must
contain only declared state fields, including at least one field beyond identity
keys and timestamps; a collection with no such evidence requires revision.
Identity fields use scalar types or the exact composite types declared in
`structured_state_field_types` (for example OIDC `address: [object]` and token
`roles: [array]`). An object named `email` remains invalid; arbitrary nested
app fields in a mixed auth table are retained in the residual collection.
The tool compares exact identifiers without classifying free-form
labels or prose. Facade actions come from the contract's page `primary_actions`;
facade modules own no local entities or collections. Grouped and shared
data-contract collections are both checked. Explicit platform owner hints also
constrain the corresponding surface. A `user_id` key alone never makes an app
collection identity state. Domain-specific profiles, preferences, newsletter
subscriptions, and appointment sessions remain app concerns.

DesignDocs receives selected pack descriptors through its protected build-context
projection. Available but unselected packs impose no ownership restrictions.
The existing enabled greenfield subscription intent also activates the default
MozaiksPay contract, using the same resolver as facade page completion. Explicitly
selected pack rules still apply to refinements and brownfield designs.
AppGenerator enforces actual selected packs; it does not infer provider selection
from monetization intent during repair.

MozaiksPay declares `/pricing`, `/billing`, and `/usage`. It does not require a
separate `/subscription` page. An approved additional subscription presentation
can belong to `billing_portal` and use `list_plans`, `get_subscription_status`,
`start_subscription_checkout`, and `open_billing_portal`; it cannot introduce
`subscription_management`, `update_subscription`, or a local `subscriptions`
collection. Changing only a surface's owner label cannot authorize that storage.

The AG2 adapter also treats `no_transition_matched` and `max_turns` as failures.
Persisted channel closure takes precedence over a simultaneous user-pause
observation. ThemeCapture explicitly routes user replies back to the active
interview or analysis stage. AgentGenerator's `NEXT` trigger uses exact matching,
which the context-authority policy distinguishes from freeform regex extraction.

DesignDocs and AppGenerator represent index keys as ordered objects with `field`
and `order`, not untyped nested arrays. Runtime strict-response preparation
rejects untyped values before provider dispatch.

The focused regression suites are `test_factory_auto_tool_acceptance.py`,
`test_design_docs_bundle_persistence.py`, `test_subscription_contract_designer.py`,
and `test_structured_output_runtime_contracts.py`. They complement, but do not
replace, an authenticated live generated-app CRUD/refinement/export proof.

## Scoped refinement candidates

`validate_generated_app_candidate` in `mozaiksai.core.validation` connects the
existing Factory acceptance and build validators for a complete, explicit file
snapshot. The coding worker merges only approved changes into the saved baseline,
validates that candidate, and persists a canonical bundle archive with its own
acceptance and build results. Both must pass before the candidate is validated.
Parent validation and source-index checks cannot certify a new candidate.

Execution strategy comes from operator policy and the request, never a model's
suggestion. `skip` runs neither acceptance nor build execution and cannot activate
the candidate. Acceptance uses the pinned local Docker image for AppLoader
diagnostics and the contained runtime smoke; Docker or E2B can separately isolate
the compilation stage. This entrypoint proves explicit bundle correctness. It
does not replay Genesis task execution or manufacture task evidence from a
refinement request.

Saved archives use the same canonical identity and digest checks as normal builds.
The existing Studio lifecycle registers the exact candidate for review; explicit
acceptance and promotion remain separate from generating or validating edits.

## App root route acceptance

When `app.json` declares `startup.landing_spot: "/"`, a page declared at `/`
renders through the shared component registry and route authentication wrapper.
Authentication is required by default; only `meta.requiresAuth: false` makes a
page public. A missing registered component reports the binding error instead of
silently opening Chat.

Chat remains the root fallback when the app declares no root page. The explicit
`/chat/*` and `/app/*` routes retain their core chat owner. An explicit non-root
landing spot retains its redirect from `/`.

The browser regressions in `appJourneyStart.browser.test.js` exercise the actual
renderer with declared routes, authentication states, and landing redirects.
These isolated component tests do not qualify a generated app's preview,
interactions, or persistence; those still require the generated bundle's live
acceptance journey.

## Saved build review

Studio's app Build Review page loads the selected saved app bundle through the
authenticated artifact bundle endpoint. It verifies the returned app, registry,
and version binding before rendering the endpoint's registered Workbench through
the shared UI-tool renderer. Opening or changing a selection does not resume a
workflow or start a preview. Preview remains an explicit Workbench action.

The existing Workbench owns scoped refinement and server-gated acceptance and
activation. Saved review has no workflow export-confirmation response. Build
history and preservation reports remain available in a disclosure; failed bundle
loads offer a retry, and cancelled or stale selections cannot display another
version's payload. Responsive browser fixtures exercise this composition with
mocked HTTP boundaries; live preview and generated app behavior require their
own acceptance evidence.

Within the Workbench, a completed refinement retains its own files, validation
and review identity when the original saved-version payload refreshes or a later
request fails. Responses from a previously opened version cannot replace the
current selection. Server-confirmed acceptance and activation show separate
receipts; activation reports when an app restart is required.

## Remaining Gaps

P0: none identified by this pass in the covered deterministic fixtures.

P1:

- Broaden the deterministic post-plan replay to additional representative
  captured AppPlan archetypes beyond the current small CI matrix when new
  canonical product categories are added.
- Add deterministic fake AG2 turn completion for generated workflow/agent
  bundles. Current Level 2 coverage proves registry/config/start/module-action
  runtime wiring without executing a model-backed websocket turn.
- Add browser-level route rendering smoke for generated bundles if CI can run it
  without making ordinary unit feedback slow.

P2:

- Expand workflow reference checks as workflow packs add more declarative target
  shapes.
- Add heavier archetype rows outside ordinary CI if they materially slow normal
  test feedback.
