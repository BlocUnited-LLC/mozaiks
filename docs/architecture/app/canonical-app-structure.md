# Canonical App Structure

Mozaiks apps use one workspace shape across generated apps, hosted product apps,
and first-party dogfood apps. The structure is app-agnostic: app behavior lives
in modules, app runtime preferences live in config, data and security are
first-class app planes, implementation support lives in `app/services`, and AI
orchestration lives in workflows.

```text
workspace/
├── app/
│   ├── app.json
│   ├── config/
│   │   ├── ai.json
│   │   ├── shell.json
│   │   ├── integrations.yaml
│   │   ├── targets.json
│   │   └── subscriptions.yaml   ← SaaS apps only
│   ├── data/
│   │   ├── contract.json
│   │   └── migrations/
│   ├── security/
│   │   └── secrets.yaml
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
│       └── routes/
├── workflows/
├── build_context/
│   └── {context_name}/
│       ├── context.yaml
│       └── files declared by context.yaml assets[]
├── generated/
├── tests/
├── docs/
└── scripts/
```

## Ownership Rules

- `app/modules/` owns deterministic app behavior: actions, state, permissions,
  emitted events, lifecycle transitions, and persistence authority.
- `app/services/` owns app service implementations: thin external clients,
  provider adapters, callback routes, and long-running workers. Services do
  not create business actions, own durable app facts, or hold first-class data
  or security contracts.
- `app/data/` declares data ownership, indexes, and additive migrations.
- `app/security/` declares names-only secret requirements and vault/provider
  policy.
- `app/config/` declares runtime preferences such as AI, shell, integrations,
  targets, and deployment/domain target intent.
- `workflows/` at the workspace root owns app-local AI workflows.
- `build_context/` is workspace-level build-time context when the workspace has
    prompt overlays, pack descriptors, service templates, or reusable build-time
    contracts consumed by shared factory workflows. Proprietary or operator
    engines are referenced through named build contexts, declared assets, and generated app facades;
    they are not copied into generated apps.
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
│   └── profile.yaml
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
  deployment, DNS, registrar, cloud, storage, secrets, database, payments, and similar
  implementation boundaries.
- `app/services/routes/` - app-level routes only when a module extension or host
  contract explicitly requires them.

If a behavior has product state, permissions, user-facing actions, domain events,
or persistence authority, it belongs in a module. A module may call `app/services/`
code as an implementation detail.

## Data And Security Contracts

- `app/data/contract.json` is the single data contract for module collections,
  cross-module aggregate ownership, external existing database mappings, and
  index/migration metadata.
- `app/data/migrations/{migration_id}.json` contains additive data
  migrations when refinement needs an explicit staged migration.
- `app/security/secrets.yaml` declares provider, vault policy, env handles, and
  secret names only. It must never contain raw secret values.

## Config Contract

- `app/config/integrations.yaml` declares external services and hosted
  capability requirements.
- `app/config/targets.json` declares deployment, runtime, domain, DNS, and
  provider target intent. Provider mechanics live in `app/services/adapters/`.
- `app/config/subscriptions.yaml` (SaaS apps only) — the canonical generated-app
  plan catalog. Declares plan_ids and the capability_ids each plan grants.
  When `assignment_store` is declared, the OSS `ConfiguredEntitlementAdapter`
  reads that app data alias for active subscription assignment state and is
  wired into `ModuleExecutor`. Non-SaaS apps omit this file;
  `NoOpEntitlementAdapter` grants all entitlement gates unconditionally.
  Schema: `mozaiks.subscriptions.v1`. This controls the generated app's own
  end-user feature gates, not hosted product pack access.

## Workflow Contract

App-local workflows live beside `app/`:

```text
workflows/{workflow_name}/
├── orchestrator.yaml
├── agents.yaml
├── transition_graph.yaml
├── context_variables.yaml
├── structured_outputs.yaml
├── tools.yaml
├── ui_config.yaml
├── middleware.yaml
├── tools/
└── ui/
```

Factory workflows live under `factory_app/workflows/`. Runtime workflow
resolution selects one root; generated and hosted product apps use
workspace-root `workflows/`.

## Workspace Build Context Contract

Workspace build contexts live beside `app/`, not inside it:

```text
build_context/
└── {context_name}/
    ├── context.yaml
    └── files declared by context.yaml assets[]
```

This root is launch-time input used by the factory layer. A context file
declares which workflows the context affects and which files are consumed as
assets. It must not contain generated app runtime code, raw secrets,
module business logic, Python resolver files, or workflow routing rules.

The context-level `templates/` lane mirrors the generated app shape under
`app/`: module templates live under `templates/modules/`, service templates
under `templates/services/`, page templates under `templates/ui/`, and config
templates under `templates/config/`.

## Generator Rule

AppGenerator emits this canonical structure only. It must not emit app-level
support code outside `app/services`, data contracts outside `app/data`, or
secret policy outside `app/security`. `app/services/data/` and
`app/services/security/` are not canonical app planes.

AppGenerator also must not emit `build_context/` into the app bundle. Build
context is a workspace-level input surface owned by the workspace or factory,
not an app artifact family.



