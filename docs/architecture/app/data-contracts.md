# Data Contracts

Data contracts are the app infrastructure lane for durable app business data.
They declare collection ownership, alias-to-collection mappings, indexes,
cross-module aggregate access, and additive migrations in a deterministic file
contract that agents and runtime loaders can validate.

The canonical app-bundle artifact is:

```text
app/data/contract.json
```

Older generator code and fixtures may still mention `app/config/data.json`; that
path is no longer canonical. Docs, prompts, generated bundles, and hosted product
workspaces must use `app/data/contract.json`.

The default module persistence model remains:

```python
ctx.persistence.collection(module_id, entity_name)
```

Use `app/data/contract.json` only when the app needs explicit durable data
intent beyond that default, such as stable existing collection names,
cross-module aggregate ownership, explicit authority records with stable
collection names, or indexed collections that must be applied at startup.

## Contract Shape

The contract has four distinct sections. They should not be collapsed into one
loose object.

```json
{
  "version": "1",
  "metadata_kind": "data_contract",
  "app_id": "my-app",
  "aliases": [
    {
      "alias": "orders.lifecycle",
      "collection": "orders",
      "owner_module": "orders"
    }
  ],
  "surfaces": [
    {
      "surface_id": "orders",
      "surface_kind": "module",
      "collections": [
        {
          "name": "order",
          "entity_name": "order",
          "mongo_collection": "orders",
          "data_alias": "orders.lifecycle",
          "scope": "app",
          "ownership": {
            "surface_id": "orders",
            "surface_kind": "module"
          },
          "fields": [
            {"name": "order_id", "type": "string", "required": true},
            {"name": "owner_id", "type": "string", "required": true},
            {"name": "status", "type": "string", "required": true}
          ],
          "indexes": [
            {
              "name": "order_owner_status",
              "keys": [
                {"field": "owner_id", "order": 1},
                {"field": "status", "order": 1}
              ]
            }
          ],
          "lifecycle": {
            "write_mode": "module_action",
            "migration_policy": "additive_only"
          }
        }
      ]
    }
  ],
  "shared_collections": [],
  "documented_alias_exclusions": [],
  "policies": {
    "default_scope_field": "owner_id",
    "allow_destructive_migrations": false
  }
}
```

### `aliases`

`aliases` is the global name map. It gives module code and app-data helpers a
stable logical alias such as `orders.lifecycle` while keeping the literal Mongo
collection name centralized.

Aliases are addressability metadata. An alias alone does not mean the runtime
will create indexes for the collection. Index application comes from surfaced
collections.

### `surfaces`

`surfaces` is the executable data ownership contract. Each surface groups the
collections owned by a module, workflow, Refinement Engine surface, external
integration, or UI-only surface.

For module-owned business data, each collection should declare:

- `name` or `entity_name`
- `mongo_collection` when the literal collection name must be stable
- `data_alias` when app-data helpers should resolve it by alias
- `ownership.surface_id` and `ownership.surface_kind`
- `fields`
- `indexes` when runtime startup should plan/apply indexes
- `lifecycle.write_mode`
- `lifecycle.migration_policy`

The index runner only applies indexes declared in
`surfaces[*].collections[*].indexes`. It validates index names, ordered keys,
and supported options, compares them with existing Mongo indexes, and creates
missing indexes idempotently. The canonical per-index options are `unique`,
`sparse`, `partialFilterExpression`, `collation`, `expireAfterSeconds`,
`hidden`, and `wildcardProjection`. Nested option documents are compared
semantically, so object key order does not matter; compound index key order
does matter. `background` is non-materialized compatibility metadata and does
not participate in readiness.

Index readiness is exact and fail closed:

- the same name with different keys or options is a conflict
- the same ordered keys under another name are a conflict, even when the other
  options match, because the declared name is part of the canonical identity
- a missing index is created with the complete declared option set, awaited,
  reread from Mongo, and accepted only when the materialized definition matches
- inspection, creation, and post-creation verification failures propagate
- the runtime never drops or rewrites an existing index

Platform startup awaits this check whenever persistence is enabled and a loaded
data contract declares indexes. Persistence is enabled by a configured Mongo
connection, `MOZAIKS_DATABASE_STARTUP_POLICY=required`, or a production
environment. A conflict or backend error then aborts startup even when the
general database startup policy is `best_effort`; that policy continues to
govern additive migration failure handling. Local best-effort workflows with no
Mongo connection skip index readiness, preserving intentionally non-persistent
operation even when a generated fixture contains a data contract.

### Changing an existing index

Index option changes are compatibility migrations, not in-place runtime
repairs. Before changing a contract, inspect the live collection and validate
that existing data satisfies the new constraint (especially uniqueness and
partial-filter changes). Create an additive replacement under a new name when
Mongo permits both definitions. If Mongo rejects coexistence, schedule an
operator-controlled maintenance step to remove the obsolete index, then deploy
the new contract and let startup create and verify its replacement. Remove old
contract declarations only after the replacement is verified. The runtime does
not automatically drop a conflicting index because that could change query or
write correctness without operator review.

### `shared_collections`

`shared_collections` declares intentional cross-module access to one literal
collection. Use it when one module owns the aggregate but another module may
read it or update a narrow lifecycle slice.

This section is a guardrail, not an index source. If the shared collection also
needs runtime-managed indexes, surface the collection under its owner in
`surfaces`.

### `documented_alias_exclusions`

`documented_alias_exclusions` is an explicit non-executable review ledger. It
answers: "this alias exists, so why is it not surfaced with index specs or
shared-access guardrails in this contract pass?"

Use it for aliases that are intentionally out of scope, usually because they
are single-owner collections, require a separate persistence review, or use a
temporary/manual indexing path.

Example:

```json
{
  "module": "billing",
  "aliases": ["billing.processed_webhook_events"],
  "reason": "single-owner billing collection; no cross-module access guardrail is declared in this phase"
}
```

The index planner reports these entries as `skipped`. It does not create,
delete, or compare indexes from them. This makes omissions auditable without
turning every known alias into a runtime indexing command.

## Deterministic Agent Output

Agents should produce data contracts as strict structured outputs, then
materialize the approved object to `app/data/contract.json`.

Generation should follow this order:

1. DesignDocs emits a typed `data_contract` object with finite fields, explicit
   surface ownership, declared fields, declared indexes, shared access, and
   migration policy.
2. AppGenerator carries that object into the staged app bundle without inventing
   extra helper code.
3. Module generation derives `backend/repo.py`, `backend/policy.py`, and
   `backend/schemas.py` from the same collection/entity contract.
4. Runtime app loading validates the JSON shape and indexes collections by
   `(module_id, entity_name)`.
5. Startup index application plans or applies only declared surfaced indexes.
6. Additive migrations live under `app/data/migrations/{migration_id}.json` and
   are applied in deterministic filename order.

At scale, the important rule is that agents never infer database authority from
prose, module code, or sampled live collections. They emit structured contract
objects. Runtime code then validates and applies the small supported subset:
alias resolution, entity indexing, idempotent index creation, and additive
migration operations.

## Boundaries

- `app/data/contract.json` is declarative metadata, not Python code.
- Do not put helper Python files under `app/data/`.
- Module repos may use data contract aliases only when the module and access
  path are declared in the contract.
- `handler.py` and `service.py` must not access persistence directly.
- `repo.py` owns persistence calls through `ctx.persistence`.
- Shared access must be declared before another module reads or mutates a
  collection it does not own.
- Destructive migrations, document rewrites, collection drops, and arbitrary
  migration code are not supported by the generated app migration contract.

## Complex Workspace Example

`C:\Repos\BlocUnitedRepo\mozaiks-app\app\data\contract.json` is explicit because
that workspace has existing literal Mongo collection names, shared aggregates,
cross-module reads, and many index expectations. Those requirements are within
the generic app data contract boundary. A generated app with the same
requirements should use the same explicit contract shape.

- `aliases` lists stable app aliases such as `hosting.apps` and maps them to
  literal Mongo collections.
- `surfaces` declares the module-owned collections whose fields and indexes are
  runtime-auditable.
- `shared_collections` records the few aggregates intentionally shared across
  modules, such as an app registry collection extended by hosting lifecycle
  state.
- `documented_alias_exclusions` records aliases reviewed but intentionally not
  surfaced in this pass.

That file should be read as a declarative authority map, not as special hosted
runtime code.
