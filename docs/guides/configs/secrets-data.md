# Secrets And Data

`app/security/secrets.yaml` and `app/data/contract.json` are separate app
planes. Keep them separate even when the same feature needs both.

Secrets describe how the app names sensitive values. Data contracts describe
durable app records.

## `app/security/secrets.yaml`

This file is names-only. It declares secret names, env handles, providers, and
vault policy. It must never contain raw values.

Use it for:

- required secret names
- environment variable handles
- secret provider or vault policy
- setup notes that are safe to store in source

Do not put raw API keys, OAuth tokens, passwords, connection strings, private
keys, webhook secrets, provider tenant ids, or generated credentials in this
file.

Starter:

```yaml
schema_version: mozaiks.secrets.v1
provider: env
secrets:
  - name: OPENAI_API_KEY
    backend: env
    required: true
    used_by:
      - kind: workflow
        id: SupportIntake
  - name: RESEND_API_KEY
    backend: env
    required: false
    used_by:
      - kind: integration
        id: email
```

Environment templates such as `.env.example`, `.env.staging.example`, and
`.env.production.example` may repeat these names with placeholder values only.

## `app/data/contract.json`

This file declares durable data intent when default module persistence is not
enough.

Use it for:

- stable collection aliases
- explicit module collection ownership
- cross-module aggregate ownership
- mappings to existing external database collections
- indexes that should be validated or applied at startup
- additive data migrations

Do not put credentials, connection strings, runtime record values, fixture
data, Python helpers, or destructive migrations in this file.

Starter:

```json
{
  "version": "1",
  "metadata_kind": "data_contract",
  "app_id": "support-desk",
  "aliases": [
    {
      "alias": "tickets.lifecycle",
      "collection": "tickets",
      "owner_module": "tickets"
    }
  ],
  "surfaces": [
    {
      "surface_id": "tickets",
      "surface_kind": "module",
      "collections": [
        {
          "name": "ticket",
          "entity_name": "ticket",
          "mongo_collection": "tickets",
          "data_alias": "tickets.lifecycle",
          "scope": "app",
          "ownership": {
            "surface_id": "tickets",
            "surface_kind": "module"
          },
          "fields": [
            {"name": "ticket_id", "type": "string", "required": true},
            {"name": "owner_id", "type": "string", "required": true},
            {"name": "status", "type": "string", "required": true}
          ],
          "indexes": [
            {
              "name": "ticket_owner_status",
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

## Default Persistence

Most generated modules do not need `app/data/contract.json`. They can use the
default module persistence contract:

```python
ctx.persistence.collection(module_id, entity_name)
```

Add `app/data/contract.json` when the app needs stable aliases, explicit
indexes, cross-module aggregate records, or existing database mappings.

See also [Data Contracts](../../architecture/app/data-contracts.md).
