# Database Intent And Revision Contract

## Purpose

This document defines the canonical database contract across:

- `ValueEngine`
- `DesignDocs`
- `AppGenerator`
- refinement control-plane flows
- generated app artifacts
- runtime migration application

The current system persists useful build artifacts, but the database layer is
still only partially explicit. This document makes the intended contract clear.

## Core Decision

Mozaiks should treat database development the same way it treats UI and module
generation:

- design intent is generated first
- intent is persisted as a typed artifact
- staged app output includes the current canonical database artifact
- refinement compares old intent to new intent
- migration application is explicit and safety-gated

The source of truth is not a sampled live collection and not a prompt-only
description.

The source of truth is a persisted **database intent artifact**.

In the canonical orchestration model, database intent revision is routed by the
builder session loop and executed by scoped refinement workers or targeted
workflow re-entry. It is not owned by ordinary workflow-local AG2 handoffs.

## What This Contract Covers

This contract covers:

- initial app-build database design
- workflow-stage handoff of database intent
- staged app-bundle persistence of database intent
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
| App business collections | generated module/control-plane surfaces |
| Migration planning | `AppGenerator` + refinement control plane |
| Migration application | platform/backend runtime |

## Canonical Persistence Namespaces

Collapse framework-owned metadata into one canonical Mongo namespace:

- `mozaiksai`

That namespace should own:

- runtime collections
- builder artifact collections
- refinement/migration metadata

The current mixed names:

- `autogen_ai_agents`
- `MozaiksAI`
- `mozaiks`

should be treated as drift to remove over time.

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
2. `database_intent_bundle`
   - typed machine-readable contract

`database_intent_bundle` is the real handoff object.

### Phase 3: AppGenerator

`AppGenerator` consumes `database_intent_bundle` and compiles it into staged app
artifacts.

The canonical staged artifact path should be:

```text
generated/apps/{app_id}/{build_id}/app/config/database_intent.json
```

If the run is a refinement and a migration is needed, `AppGenerator` should
also stage:

```text
generated/apps/{app_id}/{build_id}/app/config/database_migrations/{migration_id}.json
```

This replaces the older idea of writing migrations under
`backend/database/migrations/`, which assumes a backend topology that is not the
canonical app-bundle contract.

### Phase 4: Promotion

Promotion copies the approved database artifacts along with the rest of the app
bundle.

The promoted app root should contain:

- `config/database_intent.json`
- optional `config/database_migrations/*.json`

## Canonical Database Intent Artifact

The canonical artifact is `database_intent_bundle`.

It should be stored in persistence and also written to the staged app bundle as
`config/database_intent.json`.

Minimum shape:

```json
{
  "version": "1",
  "app_id": "app_123",
  "artifact_version_id": "art_456",
  "surfaces": [
    {
      "surface_id": "campaigns",
      "surface_kind": "module",
      "collections": [
        {
          "name": "campaigns",
          "scope": "app",
          "ownership": {
            "surface_id": "campaigns",
            "surface_kind": "module"
          },
          "fields": [
            {"name": "campaign_id", "type": "string", "required": true},
            {"name": "app_id", "type": "string", "required": true},
            {"name": "status", "type": "string", "required": true}
          ],
          "indexes": [
            {"keys": [["app_id", 1], ["campaign_id", 1]], "unique": true}
          ],
          "search_by": "campaign_id",
          "lifecycle": {
            "write_mode": "module_action",
            "migration_policy": "additive_only"
          }
        }
      ]
    }
  ],
  "shared_collections": [],
  "policies": {
    "default_scope_field": "app_id",
    "allow_destructive_migrations": false
  }
}
```

## Required Fields In `database_intent_bundle`

At minimum, each collection intent must declare:

- `name`
- `scope`
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
`database_intent_bundle.surfaces[*].collections[*]` with:

- `surface_kind=module`
- `surface_id=<module_id>`

That keeps one canonical database source of truth while still expressing module
ownership clearly.

Generated module files such as:

- `backend/repo.py`
- `backend/policy.py`
- `backend/schemas.py`

should be derived from this artifact, not act as the schema source of truth.

## Persistence Collections For Database Contracts

Add canonical builder metadata collections under `mozaiksai`:

- `DatabaseIntents`
- `DatabaseMigrations`

### `DatabaseIntents`

Stores the latest and historical typed database intent artifacts.

Suggested keys:

- `app_id`
- `artifact_version_id`
- `build_id`
- `change_class`
- `database_intent_bundle`
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

- previous `database_intent.json`
- new `database_intent.json`

The diff output is the basis for the migration plan.

The current helper in
`factory_app/workflows/AppGenerator/tools/schema_migration.py` is the right
starting point, but it should be treated as part of this contract rather than a
standalone helper.

## Change-Class Rules

### `patch`

Default rule:

- database intent should not change

If DB changes appear in a `patch` refinement:

- route must escalate scope
- do not auto-apply

### `design`

Default rule:

- database intent is frozen

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
- mark downstream database intents stale

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
database migration artifact.

It should:

- load `config/database_intent.json`
- load any pending `config/database_migrations/*.json`
- ensure declared indexes exist
- record applied migration ids
- reject blocked/destructive operations unless explicitly approved by policy

## `data_entity` Contract Upgrade

The existing `data_entity` runtime path is directionally correct but incomplete.

Today it accepts:

- `schema`
- `indexes`
- `write_strategy`

But it only enforces required fields on insert.

To match this contract, `DataEntityManager` should be upgraded to:

- create declared indexes
- enforce basic field typing
- enforce `enum` when declared
- honor `search_by`
- support safe deferred flush semantics
- record applied collection setup state

## Context Loading Contract

Workflows should continue to read builder artifacts through
`context_variables.yaml` `data_reference` sources.

Add canonical context variables such as:

- `database_intent_bundle`
- `database_migration_plan`
- `database_migration_status`

Do not make downstream workflows depend on ad hoc collection names that drift
from the persisted source-of-truth artifact.

## Current Drift To Remove

These are known inconsistencies in the current system:

1. `ValueEngine` writes `ValueManifests`, while downstream contexts still read
   from `Concepts`.
2. Builder metadata is split across `autogen_ai_agents`, `MozaiksAI`, and
   `mozaiks`.
3. `data_entity` advertises indexes/schema/write strategy more strongly than it
   currently enforces.
4. migration file placement still reflects an older backend topology idea.

## Recommended Implementation Order

1. Normalize concept persistence naming.
   - unify `ValueManifests` vs `Concepts`
2. Introduce `database_intent_bundle` as a typed `DesignDocs` artifact.
3. Persist it to `mozaiksai.DatabaseIntents`.
4. Write `config/database_intent.json` during `AppGenerator`.
5. Move migration output to `config/database_migrations/`.
6. Persist migration docs to `mozaiksai.DatabaseMigrations`.
7. Upgrade `DataEntityManager` to enforce indexes/basic schema.
8. Teach refinement routing to apply the change-class DB rules in this doc.

## Relationship To Other Docs

- [end-to-end-build-lifecycle.md](./end-to-end-build-lifecycle.md)
  - overall builder lifecycle
- [surface-realization-refactor.md](./surface-realization-refactor.md)
  - surface ownership and module/workflow decomposition
- [REFINEMENT_CONTROL_PLANE_SPEC.md](../../docs/architecture/specs/REFINEMENT_CONTROL_PLANE_SPEC.md)
  - refinement routing and artifact-version control plane

This document defines the missing database layer that those docs assume.
