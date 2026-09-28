# Data Contract And Revision Contract

## Purpose

This document defines the canonical app data contract across:

- `ValueEngine`
- `DesignDocs`
- `AppGenerator`
- refinement Refinement Engine flows
- generated app artifacts
- runtime migration application

The current system persists useful build artifacts, but the database layer is
still only partially explicit. This document makes the intended contract clear.

## Core Decision

Mozaiks should treat app data development the same way it treats UI and module
generation:

- design intent is generated first
- intent is persisted as a typed artifact
- staged app output includes the current canonical database artifact
- refinement compares old intent to new intent
- migration application is explicit and safety-gated

The source of truth is not a sampled live collection and not a prompt-only
description.

The source of truth is a persisted **data contract artifact**.

In the canonical orchestration model, data contract revision is routed by the
builder session loop and executed by scoped refinement workers or targeted
workflow re-entry. It is not owned by ordinary workflow-local AG2 handoffs.

## Current Implementation Boundary

This contract is implemented today as generator guidance, staged app artifacts,
runtime `ctx.persistence` injection, data contract loading, index application,
and additive migration application.

Current truth:

- `data_contract` is the canonical generated database planning object.
- DesignDocs approves that object once. Task generation, repair, policy rendering,
  and assembly consume the same approved context artifact; AppBuildPlan does not
  carry another editable data contract.
- `AppGenerator` writes that object to `data/contract.json` when it is
  present.
- additive refinement plans may be staged under
  `data/migrations/{migration_id}.json`.
- generated modules use `backend/schemas.py` for typed document/request shapes;
  `backend/models.py` and `backend/models/*.py` are not canonical outputs.
- `backend/repo.py` owns persistence operations and should be derived from
  data contract where possible.
- the OSS runtime injects `ctx.persistence` into module actions when an `app_id`
  is available; generated repo code uses
  `ctx.persistence.collection(module_id, collection_name)`.
- the OSS runtime loads promoted `data/contract.json` as app metadata.
  Ownership fields are required for factory generation, but runtime loading
  validates them only when present. Existing collections without these fields
  load unchanged; they acquire no inferred reads, policies, or subscription gates.
  Present malformed fields still fail validation. Partial metadata does not opt
  a collection into deterministic generation.
  It applies declared collection indexes idempotently at platform startup.
  It loads `data/migrations/*.json` and applies only supported
  additive operations with migration history. Destructive migrations are not
  supported.
- the OSS runtime does not inject `ctx.db` into module actions.
- generated repo code must not require or emit `ctx.db`, import
  `get_mongo_client()`, or hardcode database names.
- historical project database managers are reference material only; do not port
  or copy them into generated apps.

## What This Contract Covers

This contract covers:

- initial app-build database design
- workflow-stage handoff of data contract
- staged app-bundle persistence of data contract
- refinement-time schema diffing
- migration-plan persistence
- runtime-safe application of additive changes

This contract does not assume:

- SQL databases
- ORM-managed schema
- destructive auto-migrations
- preservation of pre-production drift

## Canonical Ownership

Use these ownership rules.

| Concern | Owner |
| --- | --- |
| Runtime/session collections | `mozaiksai` runtime |
| Builder artifact collections | `factory_app` workflows persisted through `mozaiksai` |
| App business collections | generated module/Refinement Engine surfaces |
| Migration planning | `AppGenerator` + Refinement Engine |
| Migration application | platform/backend runtime |

## Canonical Persistence Namespaces

Collapse framework-owned metadata into one canonical Mongo namespace:

- `mozaiksai`

That namespace should own:

- runtime collections
- builder artifact collections
- refinement/migration metadata

Hardcoded framework database names outside `mozaiksai` should be treated as
drift and removed when encountered.

## Build Sequence Contract

### Phase 1: ValueEngine

`ValueEngine` owns concept intent and coarse planning hints.

It should persist:

- `ValueManifest`
- `BuildPlan`

It should not finalize database structure.

It may emit:

- domain/entity hints
- capability-pack hints
- surface candidate hints

But final collection ownership belongs downstream.

### Phase 2: DesignDocs

`DesignDocs` is the first workflow that should produce a canonical database
artifact.

It should emit two database outputs:

1. `database_markdown`
   - human-readable rationale and explanation
2. `data_contract`
   - typed machine-readable contract

`data_contract` is the real handoff object.

### Phase 3: AppGenerator

`AppGenerator` consumes `data_contract` and compiles it into staged app
artifacts.

The canonical staged artifact path should be:

```text
generated/apps/{app_id}/{build_id}/app/data/contract.json
```

If the run is a refinement and a migration is needed, `AppGenerator` should
also stage:

```text
generated/apps/{app_id}/{build_id}/app/data/migrations/{migration_id}.json
```

This replaces the older idea of writing migrations under
`backend/database/migrations/`, which assumes a backend topology that is not the
canonical app-bundle contract.

### Phase 4: Promotion

Promotion copies the approved database artifacts along with the rest of the app
bundle.

The promoted app root should contain:

- `data/contract.json`
- optional `data/migrations/*.json`

## Canonical Data Contract Artifact

The canonical artifact is `data_contract`.

It should be stored in persistence and also written to the staged app bundle as
`data/contract.json`.

Hosted product/platform workspaces must keep product-owned collection metadata
outside `app/data/contract.json`. That path is reserved for generated
app persistence intent consumed by the OSS runtime. Do not place hosted
collection aliases, proprietary hosted collection names, or host-system
authority records in generated-app data contract.

Minimum shape:

```json
{
  "version": "1",
  "app_id": "app_123",
  "artifact_version_id": "art_456",
  "surfaces": [
    {
      "surface_id": "projects",
      "surface_kind": "module",
      "collections": [
        {
          "name": "projects",
          "scope": "app",
          "tenancy": "per_workspace",
          "owner_field": "workspace_id",
          "entity": "Project",
          "ownership": {
            "surface_id": "projects",
            "surface_kind": "module"
          },
          "fields": [
            {"name": "project_id", "type": "string", "required": true},
            {"name": "app_id", "type": "string", "required": true},
            {"name": "workspace_id", "type": "string", "required": true},
            {"name": "status", "type": "string", "required": true}
          ],
          "indexes": [
            {"keys": [["app_id", 1], ["project_id", 1]], "unique": true}
          ],
          "search_by": "project_id",
          "lifecycle": {
            "write_mode": "module_action",
            "migration_policy": "additive_only"
          }
        }
      ]
    }
  ],
  "shared_collections": []
}
```

## Required Fields In `data_contract`

At minimum, each collection intent must declare:

- `name`
- `scope` (`app`, `platform`, or `hosted`: the namespace owner)
- `tenancy` (`per_user`, `per_workspace`, or `app_wide`: row ownership)
- `owner_field` (a declared field receiving trusted `user_id` or `workspace_id`,
  respectively; null for `app_wide`)
- `entity` (an exact entry in the owning surface's `primary_entities`)
- `ownership.surface_id`
- `ownership.surface_kind`
- `fields`
- `indexes`
- `search_by` when updates are supported
- `lifecycle.write_mode`
- `lifecycle.migration_policy`

Field entries should include:

- `name`
- `type`
- `required`
- optional `default`
- optional `enum`
- optional `nullable`

## Module-Level Collection Ownership

Module ownership does not need a separate top-level canonical `database.yaml`
file yet.

Instead, module-level collections should be declared inside
`data_contract.surfaces[*].collections[*]` with:

- `surface_kind=module`
- `surface_id=<module_id>`

That keeps one canonical database source of truth while still expressing module
ownership clearly.

The same factory collection contract applies to `shared_collections`. Each generated
shared entry declares `ownership.surface_id` and `ownership.surface_kind`; its placement
does not erase module ownership or row tenancy. Runtime loading preserves entries
without declared ownership as metadata only: they receive no entity or database indexes,
generated reads, or policies. Malformed present ownership still fails. Runtime metadata,
read inventories, and policy compilation use the same owner resolver for both locations.
An owner/entity pair and an owner/collection-name pair must each be unique across
the entire contract. An entry inside a surface cannot override that surface's
owner or kind.

Generated module files such as:

- `backend/repo.py`
- `backend/policy.py`
- `backend/schemas.py`

should be derived from this artifact, not act as the schema source of truth.

Only modules owning collections receive a generated `policy.py`. Entity mapping
uses the declared `entity`; it never guesses singular/plural names or assumes the
only collection is the right one. Runtime metadata indexes the declared entity;
repository code resolves that entry's collection `name` before calling
`ctx.persistence.collection(module_id, collection_name)`.

Design surfaces declare `custom_reads` beside `owned_mutations` for actions such
as dashboard summaries. Code supplies canonical entity list/get reads and, for
every collection whose `lifecycle.write_mode` is `module_action`, the canonical
`create_<entity>`, `update_<entity>` and `delete_<entity>` writes. Their ids
derive from the declared `entity`, so DesignDocs may list them in
`owned_mutations` or omit them; custom mutations and custom reads remain declared
design work implemented by ServiceAgent. Canonical create assigns the generated
record id, which is the declared `<entity>_id` field, else `id`, else Mongo
`_id`; clients never supply it. `search_by` is the canonical get lookup and may
be a user-entered natural key, which is never overwritten with a generated id.
Create stamps declared `created_at`/`updated_at` fields; update and delete
address records by the generated id and answer 404 outside the caller's scope. The
closed create/update request schemas carry only writable scalar fields: ids,
owner fields, timestamps and scope metadata are never client input, and
structured (array/object) fields reach records only through defaults, hooks or
custom mutations. Per-user and per-workspace canonical actions require login and
owner filtering, without a role permission. A permission on a canonical action
survives only when `config/auth.yaml` `frontend.default_scopes` declares it;
other permissions are removed with a logged normalization on owner-scoped
collections (and their unreferenced declarations dropped) and rejected on
app_wide collections. An authored `api_surface` of `internal` or
`admin_internal` on a canonical write is kept; `public` is rejected. Canonical
list/get actions never carry subscription gates; canonical writes may. A paid
view must use a declared custom read. An app_wide collection beside actions
with permissions, subscription gates or internal surfaces receives no open
canonical writes or reads: ConfigMiddlewareAgent declares each of them
explicitly with its access policy and ServiceAgent implements them. Ordinary
canonical implementations remain code-owned.

`backend/schemas.py` is rendered from the contract for every module owning
collections: record and input `TypedDict`s, field constants, a
`serialize_<entity>` allowlist, `<entity>_create_values`,
`<entity>_update_changes` and `new_<entity>_id`. Records are plain dicts.
ServiceAgent writes business logic only as module-level hooks in `service.py`
(`before_create_<entity>(ctx, values)`, `after_create_<entity>(ctx, record)`,
`before_update_<entity>(ctx, record, changes)`, `after_update_<entity>(ctx,
record, changes)`, `before_delete_<entity>(ctx, record)`,
`after_delete_<entity>(ctx, record)`), which the rendered service calls when
present, and as custom mutations or reads written against the rendered repo API.
Task admission parses hooks: they must be module-level `async def` functions
with those signatures and an entity the module owns. A before hook may return
None (unchanged); ids and managed timestamps in its result are stripped with a
logged warning. An update hook cannot change the owner field either; a create
hook's owner value reaches the runtime, which rejects a foreign owner (403).
Field types come from the canonical list
(string, boolean, integer, number, date, datetime, object, array) and defaults
must decode to the declared type; DesignDocs validates both at save time and
AppGenerator repeats the check only as a backstop. A required array or object
field without a default is saved with `"[]"` or `"{}"` and a logged
`DATA_CONTRACT_FIELD_NORMALIZED` notice; unknown types are still rejected with
the valid choices. A model-authored
`schemas.py` for a persistent module is overwritten by the rendered file with a
logged warning, and a `data_models` task with nothing left to author completes
without a worker turn.
Unique indexes on per-user and per-workspace collections compile to compound
keys led by the owner field, so keys are unique within one owner's rows.
Page bindings use the full approved action inventory. Managed facade entries use
the pack contract's full action set even when DesignDocs names a subset.
Subscription gate targets are restricted to approved writes and custom reads;
canonical collection reads and managed facade actions are excluded.

The DesignDocs save boundary first removes contract-identified platform state.
When identity data contains app fields with one determined app owner, its retained
collection is explicitly per-user with `owner_field: user_id` and a derived
`*_app_data` entity registered on that surface. Other missing ownership decisions
are rejected with valid choices before any artifact is saved.

## Deterministic Runtime Artifacts

DesignDocs may leave an index `name` null. Code derives a stable name from the
ordered keys and index options before saving the design and when materializing
recorded designs. Explicit names are preserved. Runtime index validation still
requires a name; it does not repair generated artifacts during startup.

AppGenerator stamps missing migration `version` and `schema_version` fields,
names migration indexes, and excludes operations belonging to selected managed
pack owners. A migration containing only managed-facade state keeps its
`migration_id` with an empty operation list, so runtime history can record its
application without creating app-owned provider collections. App-owned migration
operations must use the runtime's supported additive contract.

Subscription assignment stores are local entitlement policy data. For each
`assignment_store.data_alias` declared by the approved subscription contract,
materialization declares the alias and its literal Mongo collection in
`data/contract.json`. Missing stores become an `app_policy` surface named
`subscription_assignments`, with fields derived from the configured assignment
field mappings and a unique index over the configured subject identity fields.
Existing explicit collection mappings remain authoritative.
This applies to managed billing and self-hosted `entitlement_dispatch`; neither
path requires app-owned managed billing plans, invoices, or provider records.
Assignment storage does not generate public CRUD actions or grant capabilities
by itself. The configured entitlement adapter still checks the subject and plan.

## Persistence Collections For Database Contracts

Add canonical builder metadata collections under `mozaiksai`:

- `DataContracts`
- `DatabaseMigrations`

### `DataContracts`

Stores the latest and historical typed data contract artifacts.

Suggested keys:

- `app_id`
- `artifact_version_id`
- `build_id`
- `change_class`
- `data_contract`
- `created_at`
- `updated_at`

### `DatabaseMigrations`

Stores generated migration plans and application status.

Suggested keys:

- `migration_id`
- `app_id`
- `base_artifact_version_id`
- `target_artifact_version_id`
- `change_class`
- `diff_summary`
- `migration_document`
- `status`
- `applied_at`
- `warnings`

## Revision And Refinement Contract

Every refinement that can affect business data must compare:

- previous `data.json`
- new `data.json`

The diff output is the basis for the migration plan.

The current helper in
`factory_app/workflows/AppGenerator/tools/schema_migration.py` is the right
starting point, but it should be treated as part of this contract rather than a
standalone helper.

## Change-Class Rules

### `patch`

Default rule:

- data contract should not change

If DB changes appear in a `patch` refinement:

- route must escalate scope
- do not auto-apply

### `design`

Default rule:

- data contract is frozen

Visual or layout refinements should not mutate collection intent.

### `feature`

Default rule:

- additive changes only

Allowed:

- new collection
- new optional field
- new field with safe default
- new non-destructive index

Blocked by default:

- field removal
- collection removal
- type narrowing
- unique constraint that would invalidate existing data

### `core`

Default rule:

- create a new upstream concept revision
- mark downstream data contracts stale

`core` is not an in-place destructive migration flow.

## Safe Migration Categories

Safe to auto-apply:

- create collection
- add nullable field
- add field with deterministic backfill/default
- add non-conflicting index

Needs explicit review:

- rename field
- make optional field required
- add unique index on existing dirty data
- change field type

Blocked by default:

- drop collection
- drop field
- destructive data rewrite

## Runtime Application Contract

The runtime/platform layer should apply migrations only from the staged/promoted
data migration artifact.

It should:

- load `data/contract.json`
- ensure declared indexes exist
- load any pending `data/migrations/*.json`
- record applied migration ids
- reject blocked/destructive operations unless explicitly approved by policy

Current implementation status: runtime loads `data/contract.json`,
verifies declared indexes exactly, creates missing definitions with all
declared options and rereads them, loads `data/migrations/*.json`, and
records migration state in `mozaiksai.AppDatabaseMigrations`. Supported
migration operations are limited to `ensure_collection` and `ensure_index`.
Runtime does not mutate existing documents, apply destructive changes, execute
arbitrary migration code, or support operator-approved destructive migrations
yet.

Database startup policy is controlled by `MOZAIKS_DATABASE_STARTUP_POLICY`:

- `best_effort` is the default for generated apps and existing app setups.
  Migration failures are logged and platform startup continues. Declared index
  readiness failures always abort startup because an incompatible uniqueness,
  partial-filter, collation, TTL, sparse, hidden, wildcard, or ordered-key
  definition can change application correctness.
- `required` is recommended for production persistent generated apps. Migration
  failures also fail startup with app id, app root, and original error context.

The index readiness comparison covers the name, ordered keys, `unique`,
`sparse`, `partialFilterExpression`, `collation`, `expireAfterSeconds`,
`hidden`, and `wildcardProjection`. A same-name mismatch or any same-key
definition under another name is a startup conflict. Runtime index
application is additive only: it never drops or rewrites an existing index.
Operators changing a definition must validate existing data, create an
additive replacement where Mongo permits coexistence, or perform an explicit
maintenance-window removal of the obsolete index before startup creates and
verifies the replacement.

App business data is stored in the generated-app database selected by:

1. an injected database name when the runtime adapter is constructed
2. `MOZAIKS_APP_DATABASE_NAME`
3. `MOZAIKS_APPS_DATABASE`
4. fallback `mozaiks_apps`

Migration history and locking:

- `mozaiksai.AppDatabaseMigrations` doubles as the migration lock collection.
- the runtime atomically claims a migration by inserting an `in_progress` record
  for `(app_id, migration_id)` before operations begin.
- the history collection has a unique `(app_id, migration_id)` index, so two
  platform/runtime instances cannot both claim the same app migration.
- `in_progress`: written before migration operations begin, with `claimed_at`
  and `lock_owner`.
- `applied`: written after all operations succeed.
- `failed`: written when an operation fails, including `error_type`,
  `error_message`, `failed_operation_index`, and
  `failed_operation_summary`.

Retry policy is conservative: an existing `applied` record with the same hash is
skipped; an existing `applied` record with a different hash errors; existing
`in_progress` or `failed` records error until an operator clears or repairs the
history record. There is no automatic lock takeover in the first pass.
`in_progress` means another instance is applying the migration or a prior
instance crashed after claim. This avoids silently reapplying ambiguous migration
state.

Operator health inspection is read-only. The runtime helper
`get_migration_health_report()` returns:

```json
{
  "summary": {"total": 12, "applied": 10, "in_progress": 1, "failed": 1, "unknown": 0},
  "items": [
    {
      "app_id": "app_123",
      "migration_id": "001_projects",
      "status": "failed",
      "migration_hash": "...",
      "failed_at": "...",
      "error_message": "...",
      "failed_operation_index": 1,
      "is_blocker": true,
      "unknown_status": false
    }
  ],
  "has_blockers": true,
  "has_unknown_statuses": false
}
```

The helper may filter by `app_id` and `status`, and it enforces a result limit.
It does not mutate history, clear failed records, repair stuck `in_progress`
records, retry migrations, or take over locks. Repair/clear workflows remain
future operator tooling.

Operators can inspect the same report from the CLI:

```text
mozaiks migrations status --app-id app_123
mozaiks migrations status --status failed --json
```

The command returns `0` when there are no blockers or unknown statuses, `1` when
failed/in-progress blockers or unknown statuses exist, and `2` for configuration
or Mongo/report loading errors. It is read-only and does not print Mongo
credentials.

## Generated App Persistence Runbook

Generated app persistence is now supported end to end for module-owned business
data. The canonical generated artifacts are:

```text
data/contract.json
data/migrations/{migration_id}.json
modules/{module_id}/backend/repo.py
modules/{module_id}/backend/policy.py
modules/{module_id}/backend/schemas.py
```

Generated apps must not use:

```text
backend/models.py
backend/models/*.py
backend/database/schema.json
backend/database/seed.json
```

At runtime, `ModuleContext` exposes `ctx.persistence` when the module request has
an `app_id`. `ctx.db` is not injected and is not canonical. Generated
`backend/repo.py` is the only generated backend layer that should touch
persistence, and it should use `ctx.persistence.collection(module_id,
collection_name)` with values that match `data/contract.json`. Generated
module code must not call `get_mongo_client()` or hardcode database names.

Layer responsibilities:

- `handler.py` dispatches action calls to service methods only.
- `service.py` owns orchestration, validation, and event emission after state is
  committed; it calls repo methods for data access.
- `repo.py` owns persistence access through `ctx.persistence`.
- `policy.py` is rendered deterministically as optional ownership preflight. Runtime persistence enforces collection tenancy and owner_field independently; domain authorization stays in service.py.
- `schemas.py` owns typed document shapes and pure normalization helpers.

Runtime app loading behavior:

- missing `data/contract.json` is allowed for non-persistent apps.
- valid `data/contract.json` is loaded and indexed by
  `(module_id, entity_name)`.
- invalid JSON or invalid shape fails app load.
- declared indexes are applied idempotently and verified against materialized
  Mongo metadata before startup reports readiness.
- additive migration files are loaded from `data/migrations/*.json`.
- migration states are recorded in `mozaiksai.AppDatabaseMigrations`.
- supported migration operations are `ensure_collection` and `ensure_index`.
- destructive migrations and arbitrary migration code are not supported.
- index readiness failures always fail startup; production persistent apps
  should also set `MOZAIKS_DATABASE_STARTUP_POLICY=required` so migration
  failures fail closed.

Compact neutral example:

```json
{
  "version": "1",
  "surfaces": [
    {
      "surface_id": "projects",
      "surface_kind": "module",
      "collections": [
        {
          "name": "projects",
          "entity": "Project",
          "scope": "app",
          "tenancy": "per_user",
          "owner_field": "owner_id",
          "ownership": {"surface_id": "projects", "surface_kind": "module"},
          "fields": [{"name": "owner_id", "type": "string", "required": true}],
          "indexes": [
            {
              "name": "project_owner_created_at",
              "keys": [
                {"field": "owner_id", "order": 1},
                {"field": "created_at", "order": -1}
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

```python
class ProjectsRepo:
    async def _collection(self, ctx):
        persistence = getattr(ctx, "persistence", None)
        if persistence is None:
            raise RuntimeError("Persistence is not available for this app context.")
        return persistence.collection("projects", "projects")
```

```json
{
  "migration_id": "001_projects_tasks_indexes",
  "version": "1",
  "operations": [
    {"type": "ensure_collection", "module_id": "projects", "entity_name": "projects"},
    {
      "type": "ensure_index",
      "module_id": "projects",
      "entity_name": "projects",
      "index": {
        "name": "project_owner_created_at",
        "keys": [{"field": "owner_id", "order": 1}, {"field": "created_at", "order": -1}]
      }
    }
  ]
}
```

Current coverage includes runtime persistence tests, generated app persistence
smokes, downstream persistent projects generation replay, and live AppPlanAgent
fixture replay.

## `data_entity` Contract Upgrade

The existing `data_entity` runtime path is directionally correct but separate
from generated module repo persistence.

Today it accepts:

- `schema`
- `indexes`
- `write_strategy`

Current support can validate required fields, create declared indexes, and
enforce basic types/enums in the workflow data-entity lane. That does not mean
generated module repos should use `ctx.db`; generated module repos use
`ctx.persistence` instead.

To fully match this contract, runtime/platform persistence still needs:

- support safe deferred flush semantics
- record applied collection setup state

## Context Loading Contract

Workflows should continue to read builder artifacts through
`context_variables.yaml` `data_reference` sources.

Add canonical context variables such as:

- `data_contract`
- `database_migration_plan`
- `database_migration_status`

Do not make downstream workflows depend on ad hoc collection names that drift
from the persisted source-of-truth artifact.

## Current Drift To Remove

These are known inconsistencies in the current system:

1. `ValueEngine` writes `ValueManifests`, while downstream contexts still read
   from `Concepts`.
2. Builder metadata must be consolidated under the canonical `mozaiksai`
   database namespace.
3. prompts and tests must not fall back to old `ctx.db`,
   `backend/database/*`, or `backend/models/*` artifacts.
4. generated repo guidance must stay aligned with `ctx.persistence` as the
   runtime-supported persistence boundary.

## Recommended Implementation Order

1. Normalize concept persistence naming.
   - unify `ValueManifests` vs `Concepts`
2. Introduce `data_contract` as a typed `DesignDocs` artifact.
3. Persist it to `mozaiksai.DataContracts`.
4. Write `data/contract.json` during `AppGenerator`.
5. Move migration output to `data/migrations/`.
6. Persist migration docs to `mozaiksai.DatabaseMigrations`.
7. Keep generated `repo.py` guidance aligned with `ctx.persistence`.
8. Exercise a persistent generated app smoke test.
9. Teach refinement routing to apply the change-class DB rules in this doc.

## Relationship To Other Docs

- [end-to-end-build-lifecycle.md](end-to-end-build-lifecycle.md)
  - overall builder lifecycle
- [generated-frontend-surface-contract.md](../frontend/ui-system/generated-frontend-surface-contract.md)
  - persistent frontend surface ownership and realization boundaries
- [refinement-engine.md](../workflows/refinement-engine.md)
  - refinement routing and artifact-version Refinement Engine

This document defines the missing database layer that those docs assume.




## Runtime Ownership and Generated Policy Preflight

Persistent generated modules use the DesignDocs-approved collection `tenancy`,
`owner_field`, and `entity` from `data_contract`. `per_user` maps to authenticated
`ctx.persistence.principal.user_id`; `per_workspace` maps to authenticated
`ctx.persistence.principal.workspace_id`; `app_wide`
has no additional owner field. The runtime's app partition remains enforced
independently. `scope` describes namespace ownership (`app`, `platform`, or
`hosted`) and never determines tenancy. Generated contracts require complete
metadata and missing scoped identity fails closed; field names and ownership
are never guessed. Existing collections without ownership metadata receive no
additional row filter. Apps without scoped ownership retain their existing
persistence behavior. AppBuildPlan has no
second editable copy of this contract.

Repo calls use `ctx.persistence.collection(module_id, collection_name)` with the
declared collection `name`. Its declared `entity` is also resolved to that same
physical collection; unknown names fail closed. Owned collections force
`app.json.authRequired=true` during materialization and trigger the canonical
auth scaffold. Model-written public intent cannot disable that requirement.

Code renders `modules/{module_id}/backend/policy.py` and
`modules/{module_id}/backend/schemas.py` before worker output acceptance.
ServiceAgent owns the policy path and ModelAgent the schema path, but neither
emits source for a module that owns collections.
These helpers are optional preflight: `scoped_query(context, filters,
entity_name=collection_name)` and `scope_record(context, record,
entity_name=collection_name)` use the immutable persistence principal.
Multiple collections require an explicit `entity_name`. Runtime persistence
enforces ownership on every collection operation whether generated code uses
these helpers or passes its domain filters and records directly. Canonical
reads use the collection directly. Business authorization and lifecycle checks
remain in `service.py`.

Queries omit `app_id` because `ctx.persistence` injects that mandatory partition.
Owned filters are conjoined with it; existing app-wide operations reject extra
`app_id` filters. App-wide policies add no other row filter.
Persistence filters reads, counts, updates, deletes, and aggregations by the
declared owner field. Inserts stamp it from the authenticated principal and
reject conflicting supplied values; updates cannot transfer ownership. The
optional record preflight follows the same conflict rule. Request-bound
authority is checked on every operation, including cached collection handles.
Action inputs and requested workspace IDs are never authority. A registered
host hook may assert `verified_workspace_id` only after authenticated membership
verification; omitted assertions preserve token scope and explicit `None`
revokes it. Auth-disabled development uses an explicit logged development
principal rather than treating request scope as authenticated claims.
Aggregations cannot introduce
foreign collection rows or write output, and admin permissions do not disable
ownership. Cross-owner functionality is unavailable through module persistence
until an explicit declared and auditable runtime access contract is implemented.
When an app declares scoped collections, literal access to those physical
targets is denied. Unowned aliases use a bounded facade retaining normal
operations and safe aggregation, without raw database introspection or
foreign-collection stages. Apps without owned collections retain raw aliases.
Generated-module scanning rejects private backing attributes, raw-client and
context factories, and principal-binding internals even through ordinary aliases.
Separate declared startup-service workers retain their explicit driver contract;
request code cannot import those storage implementations. Scoped shared
collections require an explicit owning surface; missing ownership fails loading.
The runtime-owned fields `user_id` and `workspace_id` can only serve as the owner
field for their corresponding tenancy; `app_id` and `tenant_id` are not valid
per-user or per-workspace owner fields.

A refinement that changes collection tenancy, ownership, or `owner_field` must
also include a `business_services` task owning that module's `backend/policy.py`.
Changing only the persistence contract leaves the old policy inconsistent and
assembly rejects it. Deterministic rendering does not expand task ownership.
Existing policy artifacts also require their matching data contract at assembly;
missing scope metadata cannot turn a policy into unconstrained custom code.
