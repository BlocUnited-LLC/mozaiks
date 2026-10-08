---
title: End-to-End App Editing Loop
status: Draft - Pre-Production Target Contract
created: 2026-07-31
depends_on:
  - refinement-engine.md
  - refinement-harness-architecture.md
  - orchestration-control-loops.md
  - ../foundations/app-intelligence-plane.md
  - ../foundations/app-context-and-brownfield-adoption.md
---

# End-to-End App Editing Loop

This document describes how Mozaiks should handle app edits after an app exists.
It is the product and architecture contract for the full edit loop: request,
classification, context, scoped execution, review, promotion, and App
Intelligence refresh.

It applies to every post-build editing path in Mozaiks: generated Mozaiks apps,
imported existing apps, Mozaiks-owned overlays/modules, migration artifacts,
and local developer workspaces. Brownfield import is one source mode, not the
definition of refinement.

The durable rule:

> The refinement harness decides what should be edited and why. A coding worker
> or workflow sequence performs the scoped work. Studio owns review and
> promotion. App Intelligence refreshes the context after accepted changes.

The refinement harness is not a standalone coding agent. It is the control
plane for app editing.

---

## Goals

- Let users edit apps naturally after generation.
- Avoid rerunning the full factory sequence for every small change.
- Keep generated app contracts, workflow contracts, and artifact lineage
  authoritative.
- Use App Intelligence to understand current app state before agents edit.
- Keep source/code mutation staged until the user accepts the change.
- Allow AG2-backed coding agents where they fit, without moving Mozaiks
  artifact policy into AG2.

## Non-Goals

- Do not introduce a dedicated `RefinementWorkflow`.
- Do not make an AG2 CLI coding agent the top-level editor.
- Do not let coding agents infer edit authority from file paths alone.
- Do not mutate live app source directly from chat.
- Do not use raw source dumps as default prompt context.
- Do not bypass artifact validation, review, promotion, or App Intelligence
  refresh.

---

## Current Layer Ownership

| Layer | Owns | Does not own |
| --- | --- | --- |
| Studio | edit UX, diff/review surfaces, accept/reject/retry/promote actions | code reasoning or workflow-local agent logic |
| Refinement Engine | classification, routing, checkpoint dispatch, scope policy, harness decisions | workflow execution order or artifact generation internals |
| `factory_app/refinement_harness` | first-party harness config, prompts, tools, builder-specific context tools | framework runtime primitives |
| App Intelligence | current app context, source refs, graph, snapshot, ownership boundaries, freshness state | edit permission by itself |
| Workflow sequences | regeneration/re-entry order for design, feature, core, and non-local patch routes | global refinement classification |
| Coding worker provider | scoped file edits against explicit inputs | route selection, scope widening, promotion |
| Artifact lifecycle | draft/staged/accepted/promoted versions, validation evidence, lineage | LLM behavior |

---

## Write-Back Modes

Generated apps and imported existing apps have different write-back targets. The
edit loop must choose the target explicitly before promotion.

| App/source mode | Accepted output should become | Must not do |
| --- | --- | --- |
| Generated Mozaiks app bundle | accepted/promoted artifact version and generated app workspace state | mutate staged output without artifact lineage |
| Existing app read-only import | patch bundle or pull request proposal against the user's repository | directly rewrite user source from chat |
| Existing app with Mozaiks-owned overlay | promoted Mozaiks overlay/module/workflow artifact plus optional PR for integration hooks | treat observed source files as Mozaiks-owned |
| Existing app full migration path | generated module/app artifacts staged for review, with carry-forward evidence | silently copy or rewrite whole-repo code |
| Local developer workspace | staged patch or branch controlled by the developer | bypass review and validation because files are local |

This distinction is critical across all app editing paths, especially
brownfield imports. App Intelligence may observe a file, but observation is not
ownership. The edit loop must know whether a path is read-only evidence, safe
overlay, Mozaiks-owned generated output, local developer workspace source, or an
external repo file that should receive a PR-style proposal.

Current implementation status:

- `RefinementReviewRecord` stores `write_back_mode` and `write_back_target`.
- Staged generated-app refinements default to `generated_artifact`.
- Direct source-bundle mutation requires `write_back_mode: local_workspace`.
- Draft app-bundle artifact metadata and Studio review payloads expose the
  selected write-back mode.
- Studio artifact review responses return a canonical
  `mozaiks.refinement.review_package.v1` package with route decision,
  write-back mode, affected paths, changed files, validation state, risk notes,
  and available actions.
- The App Build Review Studio page renders the canonical review package for
  the selected artifact and calls the existing accept, reject, and promote
  endpoints for enabled actions.
- External repo, overlay, and full-migration write-back modes are modeled but
  still need provider-specific execution and UX actions.
- The approved managed-execution context projection passes only editable source
  files to a coding request. Prohibited and read-only paths override allowed
  prefixes, and source contents outside the editable scope are excluded. This
  projection does not execute a repository change or connect the hosted handoff
  to the generated-app coding worker.

### Approved Repository Patch Candidate

`finalize_repository_patch` is the offline OSS bridge from an authenticated
`ApprovedExecutionContext` to a reviewable `external_patch` candidate for an
existing repository. It accepts a host-verified `RepositorySnapshotEvidence`,
exact selected baseline files, a disposable `StagedCodingWorkspace`, a
`StagedPatchProposal`, and the host's per-path `validate_path` policy. The host
must load the persisted approved snapshot, verify its digest and repository
identity and plan/request/app IDs against the approval, and fetch regular UTF-8
files at the immutable `baseline_commit_sha` before staging. The snapshot digest is opaque to OSS:
the hosted digest includes tenant and excluded-path evidence that is absent
from `ApprovedExecutionContext`.
The snapshot has no build-registry ID; the host verifies the approved context's
`build_registry_id` against its app registry when it retrieves the snapshot and
again before publishing a PR.

The finalizer checks every selected file against the approved `sha256:` file
manifest, harvests the stopped provider's staged tree, rejects symlinks and
unselected files, and requires the provider's proposal to match the observed
create, update, and delete operations and bytes. Creates and deletes require
exact `create_paths` and `delete_paths` in the approved plan digest; directory
scope alone never grants them. A create also requires a host callback that
proves absence and no path collision in the complete Git tree at the pinned
baseline, including excluded entries. It calls the host path policy for baseline,
selected, observed, and proposed paths; broad approved scopes do not bypass
host-specific protected descendants. The returned
`RepositoryPatchCandidate` contains the approval and provider identities,
nullable before/after hashes, updated content or a deletion, unified diffs, and required gate names.
Its `validation_state` is `pending` and `mutation_allowed` is `false`.
The candidate contract is `mozaiks.refinement.repository_patch.v2`.

This function reads the staged tree and does not invoke an agent, run validation
commands, persist a review record, publish a branch/PR, or mutate the source
repository. The host must isolate the coding provider to the disposable tree
and keep that tree stable while the finalizer harvests it. A host-owned
review/publisher must satisfy required gates and recheck the destination
baseline before publication.

For an isolated executor, `export_repository_workspace_archive` captures every
selected file from the stopped provider's disposable tree before that tree is
deleted. It refuses ungranted creates or deletes, links, non-exact UTF-8 content,
and archives outside explicit count and byte budgets. The exporter must run
inside an OS-isolated worker with filesystem and memory limits because the
canonical workspace harvester reads files before the archive budget is checked.
It uses the existing
deterministic archive envelope; it does not serialize the provider's own
changed-file claims as observation. The authenticated host caps the archive
before parsing and calls `stage_repository_workspace_archive` with the same
approved context, verified snapshot, exact selected baseline, and path policy.
The host checks the raw central-directory count and declared sizes before ZIP
parsing can allocate entry objects. It then verifies canonical archive metadata,
exact approved operation paths, hashes, and strict UTF-8, and writes output bytes only
into a fresh private staging tree first materialized from the approved baseline.
It passes that tree to `finalize_repository_patch` and reports any failure to
remove the tree after review candidate creation.
No ZIP entry is extracted by path. Deletion-only output uses a repository-specific
empty transport marker; the shared deterministic archive contract still rejects
empty ZIPs. The bridge accepts only portable UTF-8 files and remains inactive
until the hosted product binds a trusted executor and full-tree absence proof.
The host may pass selected tests or docs to an isolated ACP turn through
`select_repository_read_only_files`. Each file must be within both an approved
allowed path and an approved read-only path, outside prohibited paths, accepted
by the host's per-file policy, and hash-equal to the immutable snapshot. The
selection has explicit file and byte limits. A read-only path grants no new
read authority by itself, even under a broad read-only prefix. The executor
materializes these files in a separate read-only manifest. Post-turn harvest
rejects edits or deletion, including when create/delete flags are enabled,
and excludes those files from the archive and candidate's changed-file list.
The repository candidate uses host-derived summary and rationale, never the
agent's reply or ACP plan events. Raw provider results can still quote inspected
text; the Docker transport drops those fields and replaces proposal ID and
provider ID with host-owned metadata. The host must not persist or publish raw
provider text or events. Changed file content and diffs can also contain text
copied from inspection files, so the host must apply its content policy before
persisting or showing a candidate or publishing a PR.
The generated-app coding worker rejects requests carrying inspection files;
only the isolated repository provider proof uses this contract today.
Changed `app/security/secrets.yaml` content must pass the names-only secret
contract, while root `.env*.example` templates are excluded from this first
repository patch lane and read-only inspection until an equivalent content
gate is available.

`execute_repository_docker_turn` is the host-side transport for one scoped
`CodingWorkerRequest`. For exact create or delete grants, the trusted worker
passes the approved execution context, matching snapshot, path policy, and a
host callback that proves each create path absent from the complete typed tree
at the pinned commit. The transport checks selected baseline and read-only
inspection hashes before starting Docker. Only the selected file contents and
exact operation paths enter the container; the full snapshot and host callback
stay with the trusted worker. A separate trusted worker process or service with
local Docker socket access must call it; the App Zero web host and the agent
container must never mount or receive that socket. The trusted caller supplies
a prebuilt image reference and its separately approved immutable local image ID
outside the request. The transport resolves the reference locally and rejects a
different ID before creating a container or passing source. It creates by that
ID and verifies the created container's image before start. The approved ID
must come from trusted build or image-manifest evidence; resolving the tag during
the request cannot establish approval. The transport uses an empty Docker CLI
config and minimal CLI environment, passes only the task and selected
editable/read-only files on
stdin, and checks the created container before starting it. The container has
no network, bind or volume mounts, Docker log retention, or forwarded host
credentials; image environment keys outside the finite offline image contract
are rejected before start. The container runs as a nonroot user with a read-only
root, limited tmpfs, CPU, memory,
processes and wall time. Output is byte-capped before strict JSON parsing.
The worker archive must match the selected file set adjusted only for approved
creates and deletes, and use the canonical archive format. A deletion-only turn
uses the repository bridge's empty archive marker. Container removal targets a
random host-generated name even when the
create response is lost. A static label also lets the trusted worker inspect
and remove abandoned turns. A Docker daemon that finishes a timed-out create
after cleanup remains a residual daemon race for that worker to monitor.

The Docker result exposes only a strict, bounded token measurement in its
separate `usage` field. Provider model names, message text, and operational
events remain outside the sanitized proposal. A trusted host may record that
measurement through `record_isolated_acp_usage` using the original host-owned
request, a stable durable attempt ID, and a model identifier from trusted
configuration. A malformed or missing measurement cannot invalidate a valid
patch or create a usage receipt. These advisory ACP measurements enter the
existing runtime usage ledger directly and never trigger token-wallet debits.

The returned proposal and archive carry no approval authority. The host must
still call `stage_repository_workspace_archive` and `finalize_repository_patch`
with its verified snapshot, immutable baseline files, selected paths, and path
policy. `repository_patch_digest` gives the host one canonical digest over a
validated v2 candidate, including operation paths, hashes, content, review
text, and required gates. It sorts operation and gate lists before hashing;
validation and publication must use that same candidate digest and separately
verify the approved baseline. The opt-in fake-agent Docker test proves this offline transport; it
does not perform a live model turn. The executor is not wired into a hosted
route or enabled in refinement policy. Live ACP needs a dedicated minimal
image, controlled model credential delivery and egress, a trusted job worker,
and a binding to persisted approval and source snapshot before activation.
The proof worker fails the synthetic turn if its known ACP agent reports
outbound network access, host-secret visibility, or unexpected host files from
either its own process or an AG2 terminal request. Those agent-authored probe
values are useful only for this controlled offline fixture; a live agent's
reply can never attest to its own isolation.

`infra/docker/Dockerfile.acp-worker` combines the already built offline proof
worker image and pinned real ACP adapter image. Its one-shot entrypoint still
selects the synthetic agent and runs through `execute_repository_docker_turn`
with `--network none`; the real Codex and Claude adapters only initialize in
separate credential-free checks. Build those two parent images first, then run:

```powershell
docker build -f infra/docker/Dockerfile.acp-worker -t mozaiks-acp-worker:local .
$env:MOZAIKS_ACP_WORKER_PROOF_IMAGE_ID = (docker image inspect --format '{{.Id}}' mozaiks-acp-worker:local).Trim()
$env:MOZAIKS_RUN_ACP_WORKER_IMAGE_PROOF = '1'
python -m pytest -q --no-cov tests/test_acp_worker_image.py
```

This checks that the selected-file worker and both adapter binaries coexist in
one disposable image. It does not exercise model-backed coding or authorize
network access, a credential, or PR publication. The image inherits the proof
base's Factory and synthetic-agent files; a live worker needs a separate minimal
image after its isolation decision and acceptance gates are satisfied.

The image ID lookup above is test setup for this offline proof. A trusted
production caller must use an independently approved ID, not accept whichever
ID a mutable tag resolves to when the request arrives. The separate opt-in
repository proof test uses `MOZAIKS_REPOSITORY_DOCKER_PROOF_IMAGE_ID` for the
approved local ID of `mozaiks-acp-proof:local`. The required CI isolation job
binds that variable to the ID of the proof image it just built and checks that
all three offline Docker cases ran. This CI test setup grants no production
image approval.

The offline ACP proof image consumes the host's exact `create_paths` and
`delete_paths` alongside selected files. Its coding provider remains
update-only by default; only that isolated worker instance receives operation
grants. It tells the agent the granted paths, then checks the observed
workspace against those exact grants before returning a proposal. The archive
exporter repeats the operation check, including a canonical empty archive for
deletion-only turns. Host snapshot and finalizer checks remain authoritative.

---

## User-Facing Mental Model

Users should experience app editing as one continuous loop:

1. "Change this app."
2. Mozaiks decides whether the request is a small patch, a design change, a
   feature, or a concept-level change.
3. Mozaiks shows the proposed route or patch scope when review is needed.
4. Mozaiks stages the change.
5. Mozaiks validates it.
6. The user reviews and accepts or rejects it.
7. Accepted changes become the new app state.
8. App Intelligence refreshes so the next edit starts from the new truth.

Internally this is not one workflow. It is a coordinated loop across the
Refinement Engine, factory workflows, staged artifacts, validation, and App
Intelligence.

---

## Canonical Flow

### 1. App Context Is Current

Before editing, Mozaiks should have a current `AppContextVersion` for the app or
artifact being edited.

For generated apps, this context comes from the accepted/generated app bundle.
For existing apps, it comes from ExistingAppDiscovery or a later source refresh.

The edit loop reads:

- current `AppContextVersion`
- `AppIntelligenceSnapshot`
- `AppContextGraph`
- source refs and selected source catalog
- artifact lineage
- validation evidence
- ownership boundaries

If the context is missing, stale, unsafe, or partial in a way that affects the
requested edit, the harness must block or ask for refresh before allowing a
scoped edit.

### 2. User Submits An Edit Request

The request may come from:

- Ask mode
- Workflow mode
- artifact review UI
- a validation failure recovery action
- an explicit "edit this surface" action in Studio

Every request should be normalized into the same refinement request shape:

- `app_id`
- `user_id`
- `artifact_kind`
- `artifact_version_id`
- `raw_user_request`
- `source_surface`
- optional explicit selected paths
- optional selected contract surfaces
- current AppContext refs

### 3. Request Is Classified

The `request_submitted` checkpoint classifies the request:

| Class | Meaning | Typical result |
| --- | --- | --- |
| `patch` | local, bounded correction | scoped patch or local worker |
| `design` | experience, layout, brand, or UI schema change | workflow sequence re-entry |
| `feature` | new or changed capability within the same app direction | workflow sequence re-entry or targeted regeneration |
| `core` | product premise, audience, model, or value change | concept-level restart/replan |

Classification reads App Intelligence and artifact state. It must account for
stale upstream artifact families. A small app-bundle patch can become `design`
if design docs or experience intent are stale.

### 4. Route Is Resolved

The `route_requested` checkpoint maps:

```text
artifact_kind + change_class -> workflow_sequence
```

The route comes from the loaded refinement harness. The sequence itself is
declared in `extension_registry.json`; the harness names the sequence but does
not duplicate the workflow list.

### 5. Harness Decides Execution Mode

The `decision_requested` checkpoint chooses one of:

| Decision | Meaning |
| --- | --- |
| `auto_patch` | safe scoped edit can proceed |
| `clarify_scope` | user must narrow or confirm scope |
| `workflow_reentry` | launch the selected workflow sequence |
| `targeted_regeneration` | update selected contract surfaces in dependency order |
| `fallback_workflow` | patch path is unsafe; use workflow sequence |
| `core_restart` | restart/replan from concept |

This is the point where the product chooses the lowest safe amount of work.

---

## Patch Path

Patch is the only class eligible for direct coding.

### Scope Selection

If the UI provided explicit paths, the harness validates them against the
artifact workspace, current source context, and ownership boundaries.

If no explicit paths are provided, `scope_requested` proposes a narrow scope
using:

- artifact workspace catalog
- App Intelligence context
- Context Graph catalog
- source search
- current request and artifact lineage

The scope proposer may return:

- `scoped_files`
- `clarify`
- `workflow`

The system must never treat related graph files as editable unless they are
explicitly selected and validated as in-scope.

### Coding Execution

The current first-party path is `ScopedRefinementCodingWorker`. The worker owns
eligibility, validation, artifact persistence, and the checkpoint result shape;
patch production is delegated through the `CodingExecutionProvider` protocol
(`mozaiksai/control_plane/ports.py`), whose durable output is the
provider-neutral `StagedPatchProposal` contract. The first implementation is
`StructuredOutputCodingProvider`, which uses `AG2StructuredAgentRunner` to
produce a typed `CodingWorkerPlan` in a single structured-output turn.

The provider boundary `implemented`:

```text
CodingExecutionProvider
  input: scoped CodingWorkerRequest (explicit files, validation policy hints)
  output: StagedPatchProposal — staged file changes, validation hints,
          provider identity, review notes
```

Provider implementations may include:

- first-party structured-output worker
- AG2 CLI-agent/ACP-backed coding worker
- deterministic fixture worker for tests
- future hosted coding worker

The provider is not allowed to choose wider scope, promote artifacts, or mutate
live source. It receives scope; it does not own scope.

### Staged Patch Contract

The preferred patch output should evolve from simple full-file replacements into
a richer staged patch contract:

```yaml
schema_version: mozaiks.refinement.patch.v1
request_id: string
artifact_kind: app_bundle
base_artifact_version_id: string
app_context_version_id: string
changed_files:
  - path: app/ui/pages/dashboard.yaml
    operation: update
    previous_hash: string
    new_hash: string
    rationale: string
validation_plan:
  commands: []
  required_checks: []
review_notes:
  summary: string
  risks: []
app_intelligence_refresh:
  required: true
  reason: changed_app_surface
```

Full updated file content can still be stored in the staged workspace or content
store, but the review contract should describe the change, not just contain it.

---

## Targeted Regeneration Path

Some edits are not safe as raw code patches but do not need a full factory
rerun. These should use contract-surface planning.

The `contract_surface_requested` checkpoint maps the request to canonical
surfaces such as:

- module action
- module contract
- page binding
- data schema
- workflow tool
- workflow agent
- UI component
- app config

Targeted regeneration then updates surfaces in dependency order. For example:

```text
data_schema -> module_contract -> module_action -> page_binding
```

This path is for controlled artifact regeneration, not freeform coding.

---

## Workflow Re-Entry Path

For design, feature, core, unsafe patch, or broad changes, the harness launches
the selected workflow sequence.

Workflow re-entry must carry explicit context:

- original user request
- change class and refinement lane
- source artifact version
- current AppContext refs
- affected artifact families
- selected contract surfaces when available
- ownership boundaries
- validation evidence
- carry-forward candidates when relevant

Downstream workflows should not rediscover this from chat prose. They should
receive it as context variables or launch context.

---

## Review, Validation, And Promotion

All edit paths produce staged output first.

Studio should expose:

- route decision
- scope decision
- files or surfaces affected
- staged diff or artifact preview
- validation status
- warnings and ownership-boundary notes
- accept, reject, retry, or reroute actions

Promotion flow:

```text
staged output
  -> validation evidence
  -> user review
  -> accepted artifact version or PR/patch bundle
  -> promoted/current app state or external write-back proposal
  -> artifact invalidation
  -> App Intelligence refresh
```

Accepted changes must update artifact lineage. Promotion should invalidate
downstream artifact families when required by the workflow sequence or
artifact-family graph.

For existing-app imports, acceptance does not automatically mean source mutation.
It may mean Mozaiks stores an accepted patch proposal, opens a pull request, or
promotes a Mozaiks-owned overlay while leaving the original repository as the
external source of truth.

---

## App Intelligence Refresh

After accepted changes, Mozaiks should refresh App Intelligence for the changed
app or artifact workspace.

Refresh creates a new `AppContextVersion` when source refs, accepted artifact
versions, or validation evidence change.

The next edit must use the new context version, not the stale pre-edit context.

Refresh output should be visible enough for the user to trust that Mozaiks is
editing the current app:

- current context version
- indexed source/artifact refs
- skipped or partial context warnings
- ownership boundaries
- validation evidence attached to context

---

## AG2 Placement

AG2 should own model execution and agent mechanics. Mozaiks should own product
policy, artifact lifecycle, routing, validation, and context authority.

Recommended placement:

| Need | Owner |
| --- | --- |
| LLM-backed checkpoint call | AG2 via `AG2StructuredAgentRunner` or a successor |
| CLI coding-agent subprocess execution | AG2 CLI-agent/ACP provider behind Mozaiks coding provider boundary |
| request classification schema | Mozaiks contract, executed through AG2 |
| scope policy and path validation | Mozaiks Refinement Engine |
| artifact promotion and invalidation | Mozaiks artifact lifecycle |
| context refresh | Mozaiks App Intelligence |

AG2 CLI agents should be executor plugins, not routing authority. They receive a
scoped task and produce staged output.

---

## Critical Gaps To Close

The architecture is intentionally next-generation: it treats app editing as a
stateful lifecycle, not a one-off code-generation prompt. The system is only as
strong as the product loop around it. These gaps must be closed before the model
can feel reliable end to end.

### Write-Back Target Selection

The edit loop must choose whether the accepted output updates a generated app
artifact, a Mozaiks-owned overlay, a local workspace branch, or an external repo
patch/PR. That choice must be visible in the route decision and stored with the
staged result.

### Studio Review UX

Studio is the make-or-break layer. The review surface must show:

- what Mozaiks decided
- why that route was selected
- which paths or contract surfaces are affected
- whether the app context is current
- what changed
- validation status
- risks and ownership-boundary notes
- accept, reject, retry, reroute, and refresh actions

If this surface is weak, the system will feel like another black-box agent even
if the underlying routing is correct.

### Workflow Re-Entry Context Contract

Workflow re-entry must be explicit launch context, not inferred prompt prose.
ValueEngine, DesignDocs, AgentGenerator, and AppGenerator should receive the
same canonical refinement envelope:

- raw request
- change class and refinement lane
- route decision
- selected `workflow_sequence`
- target artifact and source artifact version
- AppContext refs
- affected surfaces and paths
- ownership boundaries
- validation evidence
- carry-forward candidates

### Context Freshness Enforcement

App Intelligence freshness must be a real gate. If the current context is
missing, stale, unsafe, partial, or outside ownership boundaries for the
requested edit, the harness must return a refresh/clarification decision unless
the user explicitly overrides with recorded risk.

### Coding Provider Boundary

AG2 CLI-agent/ACP support should be added behind a provider boundary, not wired
directly into the harness. Providers receive scoped files and policy; they do
not choose route, widen scope, promote artifacts, or mutate live source.

### First-Class Patch Artifacts

Patch outputs should become versioned review artifacts with hashes, file
operations, rationale, validation plans, risk notes, and refresh hints. Full
file replacements are acceptable execution payloads, but not enough as the
durable review contract.

### Brownfield Ownership Boundaries

Existing-app adoption requires stricter ownership modeling than greenfield
generation. The harness must distinguish:

- observed source
- user-owned external source
- Mozaiks-owned overlay
- generated module/app output
- integration hook proposal

That distinction determines whether the output is a promoted artifact, overlay,
branch, PR, or patch bundle.

---

## Product UX Contract

The user should not need to know about checkpoints.

Customer-facing language should be:

- "Small fix"
- "Design update"
- "New feature"
- "Rethink the app"
- "Review patch"
- "Run update"
- "Refresh app context"

Internal terms such as `Refinement Engine`, `workflow_sequence`, `AppContext`,
and `artifact_kind` may appear in logs and contributor docs, not primary UX.

---

## Implementation Roadmap

The next production move is the durable user loop:

```text
edit request
  -> route decision
  -> staged diff or regeneration preview
  -> validation
  -> review
  -> accept/promote/PR
  -> App Intelligence refresh
```

### Phase 1: Durable Review Loop

- Make route decisions durable session state.
- Persist scope proposals and selected paths.
- Return a canonical Studio review package for staged diffs and artifact
  previews.
- Render the canonical review package in Studio. `implemented`
- Support accept/reject/retry/reroute actions.
- Attach validation evidence to staged output.
- Store the selected write-back mode with the staged result.

### Phase 2: Context Safety Gates

- Block scoped edits when AppContext is missing, stale, unsafe, or violates
  ownership boundaries.
- Add explicit refresh actions to unblock edits.
- Carry AppContext refs through workflow re-entry context.
- Record explicit user overrides when edits proceed with stale or partial
  context.

### Phase 3: Coding Provider Boundary

- Introduce a narrow `CodingExecutionProvider` contract.
- Keep the current structured-output coding worker as the first provider.
- Add an AG2 CLI-agent/ACP provider behind the same boundary.
- Ensure every provider writes only to staging.

### Phase 4: Rich Patch Artifacts

- Replace ad hoc changed-file metadata with a versioned staged patch contract.
- Include per-file rationale, hashes, validation plan, review notes, and refresh
  hints.
- Make patch artifacts first-class review inputs without making them live app
  state until accepted.
- Model existing-app PR/patch-bundle outputs separately from generated app
  artifact promotion.

### Phase 5: Promotion And Refresh Automation

- On accept, promote the child artifact version.
- Invalidate dependent artifact families.
- Refresh App Intelligence.
- Resume the user in the same conversation with the new context version.
- For external repos, create or export the accepted write-back proposal before
  refreshing source context.

---

## Canonical Invariants

- No edit without a current context decision: current, refresh required, or
  explicit user override.
- No scoped edit without validated scope.
- No coding provider can widen scope.
- No live source mutation before review.
- No promotion without validation evidence or explicit override.
- No downstream workflow re-entry without explicit refinement context.
- No App Intelligence refresh means the next edit is not allowed to assume the
  staged change is current truth.

---

## Related Docs

- [Refinement Engine](refinement-engine.md)
- [Refinement Harness Architecture](refinement-harness-architecture.md)
- [Orchestration Control Loops](orchestration-control-loops.md)
- [App-Local Refinement Harness](../app/refinement-harness.md)
- [App Intelligence Plane](../foundations/app-intelligence-plane.md)
- [App Context and Brownfield Adoption](../foundations/app-context-and-brownfield-adoption.md)
- [Context Graph and Code Intelligence](../foundations/context-graph-and-code-intelligence.md)
