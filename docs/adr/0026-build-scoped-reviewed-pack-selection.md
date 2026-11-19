# ADR 0026: Build-Scoped Reviewed Pack Selection

Date: 2026-11-18

Status: proposed; implementation and live acceptance are pending

## Context

The live Factory traversal on OSS `65c4c259` reached AppGenerator but reported
that the reviewed access policy did not match the selected pack identities.
The earlier DesignDocs launch had no registered packs; a downstream workflow
received an operator-enabled managed pack through a workflow-specific registry.
The application did not request that managed capability.

`load_trusted_build_context` admits registry declarations independently for each
workflow. `verified_pack_authorization` currently treats the resulting
`capability_packs` inventory as the application's selection. This conflates an
operator enabling a reusable offering with a particular build choosing it.
Giving both workflows every enabled pack would align their inventories but
would also introduce unwanted modules and permissions into unrelated apps.

ADR 0023 already supplies the correct durable authority: the bound immutable
DesignDocs artifact and its owner review, including exact pack identities.
The current source also owns verified pack resolution, dependency checks,
integrity digests, prompt projection and deterministic materialization. None
of those need a parallel registry, application model or decision ledger.

## Proposed decision

Select reusable packs inside the existing DesignDocs workflow before saving
and reviewing the design. Resolve every proposed reference through the existing
verified pack source resolver. Persist the selected identities in the existing
DesignDocs review receipt, and make downstream planning and materialization
consume that same selection.

1. **Availability remains registry-owned.** Build-context descriptors and
   operator capability bindings describe available offerings. They remain
   protected launch input. The catalog reads the target AppGenerator registry
   during DesignDocs as well, so workflow-specific availability cannot silently
   alter a later selection. A model, UI flag or stored context cannot overwrite
   registration or introduce a source directory. Already installed Community
   Components are discovered through the existing verified installed-state
   resolver under the canonical workspace build-context root. Installation is
   availability, not a build choice; no new lifecycle or installation authority
   is introduced.
2. **Selection is an explicit candidate.** At the start of DesignDocs,
   `DesignPackSelectionAgent` returns `pack_ids` from a compact verified
   catalog. An explicit empty list selects no packs. AG2 delivers that
   structured output to the automatic `select_capability_packs` tool before
   `DesignDocsAgent` receives the selected contracts and writes its bundle.
   The tool resolves the source, pins version and digest,
   validates dependency closure, and writes a tool-owned `pack_selection`
   candidate. It returns validation feedback for unknown or unusable references.
   It never chooses app roles, grants, provider activation or paid execution.
3. **Reasoning sees the selected contracts.** The current selected-pack prompt
   hook projects the chosen contracts and exact module actions. Descriptive
   ValueEngine pack-family hints remain advisory; they are not executable
   references or authority to register a pack. App-authored modules remain
   ordinary app work, not pretend reusable packs.
4. **Review remains the authority.** DesignDocs composes the verified selected
   pack data and validates the proposed policy through the existing save tool.
   Its existing review binds the app, build, author, policy, page roles and exact
   selected pack identities. New receipts always record `pack_identities`,
   including an explicit empty list. Identity validation is independent of
   whether the design grants roles or restricts pages. Any nonempty selection
   requires an authenticated affirmative owner review, including a managed
   capability with no role grants. A candidate is not approval. An empty selection
   selects no packs; an available billing offering does not silently become a
   selected billing pack. The existing subscription ownership/page completion
   reads selected managed contracts; monetization intent alone no longer inserts
   a default managed provider.
5. **Downstream selection comes from that artifact.** AppGenerator validates
   the bound review and freshly verifies the recorded source identities. Its
   plan may generate missing app-owned pieces, but cannot add, replace, drop or
   repin reusable packs without renewed design review. Late provider defaults
   must not introduce a new pack after the review. The existing AppGenerator
   prompt hook always projects the authorized identity list (including empty),
   making existing catalog/provider recommendations advisory to that decision.
   Changed, missing or
   unregistered sources fail closed. Factory passes these explicit selected rows
   to the existing physical pack APIs; their standalone/component lifecycle
   contract is unchanged. A read-only projection supplies the same selection
   where an existing scanner accepts context rather than explicit rows.
6. **Replay uses the same authority.** Reconnection, refinement and later
   workflow launches resolve the exact reviewed selection, not a newly expanded
   workflow inventory or a model-authored context copy. Existing context-graph
   and artifact lineage provide the reference. A refinement may inherit only the
   same app's trusted selected ancestor/version under existing draft/current
   precedence. A different app, unrelated build, stale candidate, changed source
   or changed selection requires a fresh review. Missing receipts cannot
   authorize materialized packs. Saved compiler inputs and bundle metadata
   record selected catalog packs by ID, version and digest. Replay discovers
   the current trusted catalog source and verifies its bytes, so moving an
   unchanged source between installs preserves approval. Explicit physical
   paths still require the source at the named location. Scoped implementation
   revisions preserve carried authored module contracts and still compile
   verified partial pack templates against the approved data contract.
   There is no new durable ledger.

The Support pack must declare DesignDocs applicability so that an explicitly
selected Support/Messaging combination can expose its stock widget, inbox,
module actions and data boundaries before review. This does not make Support
selected by default or change its runtime permissions.

Hosted products retain ownership of their offering catalogs and monetization
policy. They expose managed capability bindings through the existing public
registry seam, without unconditionally selecting them for every app. A provider
choice must be settled before pack-sensitive design review; later subscription
planning must preserve it or request renewed design review. The review names
the selected provider explicitly and records affirmative owner choice; an
incidental model suggestion or catalog entry is not managed-provider intent.
Existing product eligibility requirements remain in force. A later approved
subscription contract and its explicit provider must agree with that recorded
choice. A false/non-required contract cannot materialize managed billing, and
the existing self-managed assignment provider remains distinct. Selection
implies no provider activation, provisioning, payment or entitlement grant.

Assignment writers are classified from verified contract declarations of
`subscription_write_path`, including custom managed packs, rather than inline
plan labels or a list of built-in IDs. `entitlement_dispatch` 0.1.12 declares
that same existing capability and applies to DesignDocs. A pack receipt alone
cannot authorize assignment files: planning, assembly and the durable compiler
input boundary require a later approved contract with `contract_required: true`.
The shared subscription renderer names the reviewed writer, requests renewed
design review when none exists, and assigns no model work to template-owned
outputs. Model-authored implementation notes are not projected as compiler
instructions; typed subscription facts remain in the prompt.

AG2 continues to own model calls, tools, agent handoffs and structured-output
transport. The existing prompted structured-output mode can address a provider's
native schema-size limit while preserving the canonical local validation model.
It does not repair selection provenance or grant permission.

## Alternatives considered

- Disable or loosen the pack-review identity comparison: rejected; unreviewed
  permissions could acquire authority.
- Enable all operator offerings in every workflow: rejected; availability would
  still silently introduce unwanted modules into a build.
- Remove one hosted selector only: insufficient; explicit later provider
  selection would still miss earlier pack-sensitive review.
- Infer executable packs from free-form concept hints: rejected; those hints
  are advisory and may name logical capability families.
- Add a new selection service, workflow sequence or decision ledger: rejected;
  the existing workflow, verified resolver and DesignDocs artifact own this work.
- Change model providers or weaken local output validation: unnecessary; AG2
  already supplies the explicit alternate schema transport.

## Relationship to issue 411

[Issue 411](https://github.com/BlocUnited-LLC/mozaiks/issues/411) is a design
prompt, not implementation authority. This decision adopts its requirement that
typed references cross a deterministic boundary and that a derived plan must
not invent application meaning. It defers a generalized semantic compiler and
typed decision ledger. The existing approved DesignDocs artifact remains the
single source for reviewed pack composition and access policy.

## Consequences and reversibility

Pack composition becomes reviewable before generation and remains stable
through replay. Builds that previously relied on late implicit pack additions
must renew design review. Old failed builds must not be silently backfilled with
invented selections. Standalone authored apps retain their existing contracts;
this changes Factory build input and acceptance, not runtime role membership.

Risk is medium: the context and planning consumers must cut over together.
Do not introduce a dual-authority fallback to the registry inventory. Preserve
existing artifact history and use the normal refinement/review path for a new
selection. No role, grant, persistence ownership or resource authorization is
widened by this decision.

## Validation before acceptance

- Real prompt/tool path: choose verified stock packs; expose exact actions;
  reject unknown IDs, unresolved dependencies, changed pins and arbitrary paths.
- A non-monetized app with an available managed offering selects none of it.
- An explicitly chosen managed provider is reviewed before generation;
  unapproved late provider insertion is denied.
- Role review, generated module permissions and app auth compilation consume
  the same selected sources. Non-holders and wrong-app actors remain denied.
- Registry expansion, reconnect and replay cannot change an approved selection.
- Explicit empty-selection replay; previously saved receipts without identities; a managed selection
  with no grants; wrong app/build/draft/author; stale candidate approval; registry
  removal and digest drift; false/non-required or conflicting later provider.
- Stock Support and Messaging design/rendering regression, including app-local
  owner reply and customer readback without a hosted support-operator role.
- Relevant generator, context-authority, artifact/review and materializer suites,
  then normal full CI and independent exact-head review.
- A real final-pair Factory chat build, artifact open/use/refine, owner/member
  login and generated Admin inside the preview. A harness or partial traversal
  is not this acceptance proof.

This document records a proposal, not completed implementation, accepted
architecture or a successful live build.

## Planner read lifetime

The synchronous plan-review tool may reuse successful source resolution and
pack-authorization reads within its invocation. The private operation memo is
not context state, model input, persisted evidence or a process-wide cache.
Readers receive defensive copies; errors are not cached, and `finally` removes
the memo on success, rejection and exceptions. Nested operations own separate
memos. No scope spans an await or background task.

Receipt digest, author, app/build lineage and supplied design checks remain
live on each read. Plan rendering/caching starts with a fresh read scope, and
the final ready gate re-admits the complete expected selection in another fresh
scope. A failed final gate clears the cached plan through the existing rejection
path. Assembly, saved compilation replay and publication do not inherit the
planner memo and retain their independent fresh verification boundaries.

After the owner review card returns approval, saving DesignDocs rechecks that
the candidate selection is unchanged and re-admits its captured source pins.
A change while the card was open refuses persistence and requires renewed
review; the earlier display-time check is not a post-approval source receipt.

The synchronous template, workspace-path and file-merge assembly phases each
own a separate read operation with the same successful-read and defensive-copy
rules. A phase never inherits another phase's memo. All scopes end before the
async caller can yield; acceptance, saved input capture and publication still
reverify current sources. This avoids reparsing identical admitted inputs for
each consumer inside one phase without treating an earlier phase as authority.

The physical-source projection retains the complete verified plan, including
its approved customization inputs, after checking its source selection. Catalog
descriptors do not replace those compiler inputs. Public replay validators return
their native failed result for changed/removed sources; they do not execute or
approve an invalid snapshot. Async acceptance input errors use a generic failure
category rather than attributing every deterministic input error to pack drift.

Standalone physical bundle validation carries the scanner's resolved, verified
sources through the same read-only physical projection. That projection is not
a DesignDocs receipt and never selects launch inventory. Ownership and planned
data checks still read those verified contracts, including managed collection
exclusions. Task workers derive template-owned paths from their authoritative
parent context; task-local design changes remain subject to the existing bounded
rejection and repair path before output compilation.

The repository pack-proof fixture declares its test roles, permissions and
action-to-collection grants before materialization. Collection-backed packs use
the same reviewed Factory input contract as builds. Cloud and Pay facades have
no collection-backed target for those custom test-role grants: their Factory
case proves that a candidate auth role cannot become an unreviewed grant.
Separate authored test apps pass canonical authored-app static validation and
retain the same verified source identities, implementation and provenance bytes
for the existing trusted runtime/provider probes. Receipts distinguish Factory
authorization from runtime authorization, origin and bundle hashes. These
authored probes establish facade wiring and existing role enforcement; they do
not establish Factory-generated facade grants, containment, or hosted-provider
acceptance.

## Archived corpus expectation migration

The 91 recorded bodies, original context, hashes and labelled defaults are
unchanged. Replay explicitly selects no reusable packs because the historical
outputs contain no explicit provider choice/review. Thirty-one expectations
change from saved to a named judgment rather than backfilling owner consent.
The remaining 58 saved cases and two existing judgment cases retain their
expected results. These are fixture contract migrations, not live-build proof.

These 29 outputs relied on implicit managed facade pages. With no selected provider, their monetized design must supply Pricing or deliberately select and review a provider before save.

Affected fixture IDs: `85fb9714-1a8618cc`, `d190027d-0647769c`, `68008d69-460938f2`, `0057c1df-b19f1997`, `20caa37c-791d0aa1`, `e35d02ca-69a38618`, `323ec8c1-c11e8295`, `99554650-bc41e824`, `892386a9-d3dc37ec`, `9340d094-bd1d80ef`, `6302e34e-7d9bf3b4`, `e4f99ee9-e8ba430c`, `2731f856-2311f3f1`, `247c4c3e-1acd0c9a`, `ace46fc2-a72250ca`, `8f28a79a-2c527c2a`, `6957f7f2-da5aac3b`, `e9eb1ebd-9c9ef1e5`, `156dec1c-7c95024c`, `d8c0ed75-1156fd38`, `988f8840-90ae07d4`, `988f8840-bc95cf30`, `dec33c80-19a69503`, `f2504efe-6a12637c`, `f2504efe-b3495060`, `c0d1e58d-ff3da5ef`, `c0d1e58d-f9118265`, `c65f5d0f-7616efbd`, `4da11a24-b55aca6e`.

These two outputs relied on implicit provider ownership of subscription_status. With no selected provider, separating platform identity leaves ambiguous app ownership that requires an explicit design decision.

Affected fixture IDs: `b964a0a1-be75b249`, `b964a0a1-ad81fc1a`.
