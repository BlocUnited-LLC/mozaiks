# Shared Persistence Contracts

Shared persistence contracts are an opt-in app infrastructure lane for generated
apps that need stable shared collections, cross-module aggregate ownership, or
integration with an existing database.

Most generated apps should not use this lane. The default persistence model is:

```python
ctx.persistence.collection(module_id, entity_name)
```

That default is declared by `database_intent_bundle` and exported as
`config/database_intent.json`.

## When To Use

Use `config/shared_persistence.json` only when the app plan explicitly needs one
of these:

- an existing database with stable collection names
- one aggregate collection intentionally shared by multiple modules
- cross-module lifecycle updates that must be declared before repo code uses them
- import or migration alignment where deterministic generated collection
  names would hide existing records

Do not use shared persistence for ordinary module-local CRUD records. Those
belong in `config/database_intent.json` and module repos that call
`ctx.persistence.collection(module_id, entity_name)`.

## Canonical Artifacts

Generated app shared persistence uses generic app names:

```text
app/
├── config/
│   └── shared_persistence.json
└── shared_persistence/
    ├── contracts.py
    ├── persistence.py
    ├── indexes.py
    └── proposals.py
```

`shared_persistence/` helper code is optional. Generate it only when the app
needs code beyond declarative metadata.
Common helper paths are `shared_persistence/contracts.py`,
`shared_persistence/persistence.py`, `shared_persistence/indexes.py`, and
`shared_persistence/proposals.py`.

Do not generate hosted-product-specific names for normal apps:

- `shared`
- `platform_persistence`
- `host_system`
- `hosted_database_metadata.json`
- `HostSystemPersistence`

Those names are not generic app output contracts.

## Contract Shape

The minimal contract is:

```json
{
  "version": "1",
  "mode": "app_shared_contracts",
  "aliases": [
    {
      "alias": "orders.lifecycle",
      "collection": "orders",
      "owner_module": "orders",
      "access": "lifecycle_update",
      "description": "Shipping may advance fulfillment fields on order records."
    }
  ],
  "shared_collections": []
}
```

Supported modes:

- `app_shared_contracts`: stable shared collections inside the generated app
- `external_existing_db`: a declared adapter to existing collections

The default generated-scoped mode does not need this file.

## Boundaries

- Shared persistence is infrastructure, not a runtime module.
- Module repos may use shared persistence helpers only when the module and access
  path are declared in `config/shared_persistence.json`.
- Shared helpers must not bypass app authorization, tenant scoping, or repo
  serialization allowlists.
- Shared helpers must not use raw database clients unless the explicit contract
  is `external_existing_db` and the app plan requires it.
- Shared persistence does not replace `database_intent_bundle`; it narrows the
  exceptional cases where deterministic module-scoped collections are not enough.
