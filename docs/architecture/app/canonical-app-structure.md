# Canonical App Structure

Mozaiks apps use one workspace shape across generated apps, hosted product apps,
and first-party dogfood apps. The structure is app-agnostic: app behavior lives
in modules, app intent lives in config, implementation support lives in `app/services`,
and AI orchestration lives in workflows.

```text
workspace/
├── app/
│   ├── app.json
│   ├── config/
│   │   ├── ai.json
│   │   ├── shell.json
│   │   ├── data.json
│   │   ├── secrets.yaml
│   │   ├── integrations.yaml
│   │   └── targets.json
│   ├── modules/
│   │   └── {module_id}/
│   │       ├── module.yaml
│   │       ├── contracts/
│   │       ├── runtime_extensions.yaml
│   │       ├── backend/
│   │       │   ├── handler.py
│   │       │   ├── service.py
│   │       │   ├── repo.py
│   │       │   ├── policy.py
│   │       │   └── schemas.py
│   │       └── ui/
│   ├── ui/
│   │   ├── pages/
│   │   ├── pages/custom/
│   │   ├── route_manifest.json
│   │   └── index.js
│   ├── admin/
│   │   ├── admin_registry.yaml
│   │   ├── index.js          ← admin/index.js registers custom page components
│   │   └── pages/            ← admin/pages/ holds custom admin page files
│   ├── brand/
│   └── services/
│       ├── integrations/
│       ├── adapters/
│       ├── security/
│       ├── routes/
│       └── data/
├── workflows/
├── capability_packs/
├── generated/
├── tests/
├── docs/
└── scripts/
```

## Ownership Rules

- `app/modules/` owns deterministic app behavior: actions, state, permissions,
  emitted events, lifecycle transitions, and persistence authority.
- `app/services/` owns app service implementations: thin external clients,
  provider adapters, callback routes, provider-neutral security helpers,
  long-running workers, and data-contract helpers. Services do not create
  business actions or own durable app facts.
- `app/config/` declares app intent. `data.json` is the unified persistence/data
  contract. `secrets.yaml` is names-only. `integrations.yaml` declares external
  capability requirements. `targets.json` declares runtime/deployment/domain
  target intent.
- `workflows/` at the workspace root owns app-local AI workflows.
- `capability_packs/` contains reusable app overlays or hosted-pack client
  templates. Hosted proprietary engines are not copied into generated apps.
- `generated/` is staged output awaiting review or promotion.

## Module Contract

Modules are the only canonical place for app business behavior.

```text
app/modules/{module_id}/
├── module.yaml
├── contracts/
│   ├── events.yaml
│   ├── reactions.yaml
│   ├── notifications.yaml
│   ├── settings.yaml
│   ├── admin.yaml
│   ├── profile.yaml
│   └── entitlements.yaml
├── runtime_extensions.yaml
└── backend/
    ├── handler.py
    ├── service.py
    ├── repo.py
    ├── policy.py
    └── schemas.py
```

`handler.py` is thin dispatch. `service.py` owns business flow and emits events
after state commits. `repo.py` owns persistence operations through
`ctx.persistence.collection(module_id, entity_name)`. `policy.py` owns scope
query helpers. `schemas.py` owns typed request, response, and document shapes.

## Service Contract

`app/services/` is the canonical support lane:

- `app/services/integrations/` - thin clients for external or hosted APIs.
- `app/services/adapters/` - provider-specific mechanics for auth, source control,
  deployment, DNS, registrar, cloud, storage, secrets, payments, and similar
  implementation boundaries.
- `app/services/security/` - provider-neutral auth and secret resolution policy.
- `app/services/routes/` - app-level routes only when a module extension or host
  contract explicitly requires them.
- `app/services/data/` - optional helpers for `app/config/data.json`.

If a behavior has product state, permissions, user-facing actions, domain events,
or persistence authority, it belongs in a module. A module may call `app/services/`
code as an implementation detail.

## Config Contract

- `app/config/data.json` is the single data contract for module collections,
  cross-module aggregate ownership, external existing database mappings, and
  index/migration metadata.
- `app/config/data_migrations/{migration_id}.json` contains additive data
  migrations when refinement needs an explicit staged migration.
- `app/config/secrets.yaml` declares provider, vault policy, env handles, and
  secret names only. It must never contain raw secret values.
- `app/config/integrations.yaml` declares external services and hosted
  capability requirements.
- `app/config/targets.json` declares deployment, runtime, domain, DNS, and
  provider target intent. Provider mechanics live in `app/services/adapters/`.

## Workflow Contract

App-local workflows live beside `app/`:

```text
workflows/{workflow_name}/
├── orchestrator.yaml
├── agents.yaml
├── handoffs.yaml
├── context_variables.yaml
├── structured_outputs.yaml
├── tools.yaml
├── ui_config.yaml
├── hooks.yaml
├── tools/
└── ui/
```

Factory workflows live under `factory_app/workflows/`. Runtime workflow
resolution selects one root; generated and hosted product apps use
workspace-root `workflows/`.

## Generator Rule

AppGenerator emits this canonical structure only. It must not emit app-level
support code outside `app/services`, persistence helpers outside
`app/services/data`, or separate `data_contract` config files. Those concerns
are represented by `app/services/` and `app/config/data.json`.


