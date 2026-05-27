# Distribution And Workspace Model

This document defines the canonical target architecture for how Mozaiks is
distributed, how apps are created, and what shape an app workspace should take.

It is intentionally more decisive than the current repo layout. The repo is
still in transition; this document defines the end state the code should move
toward.

## Core Decision

Mozaiks is not one giant app repo that permanently contains every generated
app.

Mozaiks should produce and host **app workspaces** that are separate from the
core runtime/framework repo.

That means the canonical model is:

1. Mozaiks ships reusable runtime and shell dependencies
2. the factory layer produces app artifacts
3. each generated app becomes its own workspace/repository
4. hosted product workspaces use the same app-workspace contract from their own
   repos

## The Five Artifact Types

Mozaiks should be understood as five distinct artifact families.

### 1. Runtime package

Owns:

- execution substrate
- sessions
- transport
- persistence
- workflow execution
- platform host composition primitives

Current seed in this repo:

- `pyproject.toml`
- `mozaiksai/`
- `mozaiksai/hosts/runtime.py`
- `mozaiksai/hosts/platform.py`
- `mozaiksai/hosts/studio.py`

Long-term use:

- installed as a Python dependency in generated app workspaces

### 2. Shared web shell package

Owns:

- React shell
- page renderer
- chat UI
- theme runtime
- admin and workspace/build shell surfaces

Current source in this repo:

- `chat-ui/`
- `web_shell/` (local Vite shell host)

Long-term use:

- consumed as a reusable frontend dependency by generated app workspaces and by
  hosted deployments

### 3. Factory layer / shared builder workflows

Owns:

- shared builder workflows
- prompts
- structured outputs
- assembly rules
- validators
- artifact generation contracts

Canonical target:

- the factory layer must live outside any individual app workspace
- builder workflows are infrastructure, not app-owned runtime content

Current implementation state:

- `factory_app/app/` is the current first-party Studio app bundle served by the Studio host
- shared builder workflows live under `factory_app/workflows/`
- the Studio host binds to the shared builder workflow root at
  `factory_app/workflows/`
- app/product hosts bind to the active app root's `workflows/` directory; any
   product-owned overlay registry lives in the product workspace, not in this repo

### 4. App workspace

An app workspace is the unit a user owns, versions, runs locally, and deploys.

It is not the runtime repo.

Canonical target shape:

```text
my-app/
└── app/
    ├── app.json
    ├── config/
    ├── ui/
   │   ├── pages/
   │   ├── route_manifest.json
   │   └── index.js
    ├── workflows/
    ├── modules/
    └── brand/
```

Rules:

- `ui/` and `brand/` belong inside the active app root
- the workspace is self-contained
- app behavior is declared and extended inside that workspace
- promotion lands validated artifacts into that workspace

### 5. Hosted product workspace

A hosted Mozaiks product is a real app workspace built on the same substrate.

It may carry hosted-only product capabilities, but it should still follow the
same app-workspace contract as generated apps.

Canonical target:

```text
hosted-product/
└── app/
    ├── app.json
    ├── config/
    ├── ui/
   │   ├── pages/
   │   ├── route_manifest.json
   │   └── index.js
    ├── workflows/
    ├── modules/
    └── brand/
```

Current implemented state in this repo:

- `factory_app/app/` is the first-party Studio app bundle served by the Studio host
- hosted product workspaces are expected to live outside this repo

## How Apps Should Be Created

The canonical creation flow is:

1. Studio or the CLI creates a build request
2. shared generation core runs the builder workflows
3. artifacts are written to a generated/staging area
4. validation and review happen
5. promotion copies validated artifacts into an app workspace

This is the important boundary:

- the factory layer produces artifacts
- app workspaces consume promoted artifacts
- runtime hosts execute app workspaces

Current composition rule in this repo:

- the active app workspace is resolved first
- the Studio host uses `factory_app/workflows/` as its builder workflow root
- app/product hosts use the active app root's `workflows/` directory
- product-owned workflows stay under the active app root's `workflows/`
   directory

That is the same composition model that should survive when a hosted product
workspace lives in its own repository.

## OSS Versus Hosted

The bundle contract should stay the same across OSS and hosted use.

### OSS / local

The user should end up with their own app workspace repository and install:

- the Mozaiks Python runtime package
- the shared web shell package

They then run the workspace locally through the Mozaiks host/CLI.

### Hosted

The hosted product runs the same app-workspace contract, but Mozaiks owns:

- hosted infra
- hosted Mozaiks product surfaces built on the Studio host
- product capabilities
- billing/collaboration/deployment surfaces

Hosted is a deployment and product context difference, not a different app
bundle contract.

## Deployment Contract

Generated app deployment artifacts are standardized by a provider-neutral
contract in OSS.

See:

- [../deployment/generated-app-deployment-contract.md](../deployment/generated-app-deployment-contract.md)

## What This Means For The Current Repo

The current repo contains the canonical runtime, shell, CLI, and first-party
builder sources in one repository.

### Canonical ownership

- `mozaiksai/` - runtime package source
- `chat-ui/` - shared shell/UI source
- `mozaiks_cli/` - developer interface
- factory layer / shared builder workflows - should remain first-class and stay
   outside customer app workspaces

## Canonical Decisions

These are the decisions the rest of the docs should follow.

1. Generated apps should become standalone workspaces/repositories.
2. The factory layer must not permanently live inside an app workspace.
3. Shared generation workflows must not live inside a hosted product workspace
   or any customer app workspace.
4. App workspaces should be self-contained: `config/`, `ui/pages/`, `workflows/`,
   `modules/`, `ui/`, and `brand/` belong together.
5. Hosted product workspaces should use the same workspace contract as
   generated apps, with hosted-only capabilities layered above it.
6. OSS and hosted should differ primarily in deployment/product capabilities,
   not in the app artifact contract.

## Cross References

- [../app/canonical-app-structure.md](../app/canonical-app-structure.md)
- [workflow-architecture.md](../workflows/workflow-architecture.md)
- [../deployment/generated-app-deployment-contract.md](../deployment/generated-app-deployment-contract.md)
- [Architecture Overview](../mozaiksai/index.md)
- repo-root `ARCHITECTURE.md`
