# Data Contracts

Data contracts are the app infrastructure lane for generated apps that need
durable module collections, cross-module aggregate ownership, or integration
with an existing database.

The default persistence model is:

```python
ctx.persistence.collection(module_id, entity_name)
```

That default is declared by `data_contract` and exported as `config/data.json`
when the app owns durable data.

## When To Use

Use `config/data.json` when the app plan explicitly needs one of these:

- module-local durable records
- an existing database with stable collection names
- one aggregate collection intentionally shared by multiple modules
- cross-module lifecycle updates that must be declared before repo code uses them
- import or migration alignment where deterministic generated collection
  names would hide existing records

## Canonical Artifacts

Generated app data contracts use generic app names:

```text
app/
├── config/
│   └── data.json
└── services/data/
    ├── contracts.py
    ├── persistence.py
    ├── indexes.py
    └── proposals.py
```

`app/services/data/` helper code is optional. Generate it only when the app
needs code beyond declarative metadata.
Common helper paths are `app/services/data/contracts.py`,
`app/services/data/persistence.py`, `app/services/data/indexes.py`, and
`app/services/data/proposals.py`.

Do not generate hosted-product-specific or host-internal helper names for
normal apps. Those names are not generic app output contracts.

## Contract Shape

The minimal contract is:

```json
{
  "version": "1",
  "mode": "app_data_contract",
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

- `app_data_contract`: app-owned collections and cross-module ownership inside the generated app
- `external_existing_db`: a declared adapter to existing collections

The default generated-scoped mode does not need this file.

## Boundaries

- Data contract is infrastructure, not a runtime module.
- Module repos may use data contract helpers only when the module and access
  path are declared in `config/data.json`.
- Shared helpers must not bypass app authorization, tenant scoping, or repo
  serialization allowlists.
- Shared helpers must not use raw database clients unless the explicit contract
  is `external_existing_db` and the app plan requires it.
- Data contract helpers do not replace module repos; they narrow the
  exceptional cases where deterministic module-scoped collections are not enough.

