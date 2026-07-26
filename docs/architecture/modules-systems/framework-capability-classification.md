# Framework Capability Classification

This document defines one architectural distinction that must stay explicit in
Mozaiks:

- first-class in the framework distribution
- universal app-runtime substrate

Those are not the same thing.

## Core Decision

Some code in this repo is part of the universal substrate every downstream app
runtime depends on.

Some code is framework-owned and first-class in the Mozaiks distribution, but
optional from the perspective of a generated app runtime.

That means:

- Studio does not need to be workflow-agnostic substrate
- the factory layer does not need to be universal app-runtime content
- both may still be canonical parts of the framework repo

## The Four Classification Buckets

### 1. Universal substrate

This code is part of the minimal execution surface every downstream app runtime
depends on.

Typical characteristics:

- required to run app bundles
- workflow-agnostic at the substrate level
- not specific to building apps, hosting Mozaiks, or operating a Refinement Engine

Current examples:

- `mozaiksai/`
- `mozaiksai/hosts/runtime.py`
- `mozaiksai/hosts/platform.py`
- core `chat-ui/` shell primitives

Rule:

- universal substrate must not depend on Studio, `factory_app/`, hosted
  product paths, or repo-local app workspace shortcuts

### 2. Framework-owned optional capability

This code is first-class in the Mozaiks distribution, but not every generated
app runtime must carry it.

Typical characteristics:

- shipped and maintained by the framework
- consumes the substrate instead of defining it
- may know about build flows, authoring, review, or operational management
- may be enabled in OSS/CLI or hosted environments without becoming mandatory in
  every app bundle

Current examples:

- `factory_app/`
- `mozaiksai/hosts/studio.py`
- `factory_app/app/admin/pages/` and `factory_app/app/admin/index.js`
- `chat-ui/src/admin/`
- `mozaiks_cli/`

Important clarification:

- `factory_app/` is the first-party factory workspace, not universal substrate
- Studio is a Refinement Engine surface, not a universal app-runtime primitive
- CLI and Studio are framework interfaces over shared system capabilities, not
  substrate

### 3. Product-specific consumer

This code represents a first-party app or hosted product context that consumes
the lower layers.

Typical characteristics:

- Mozaiks-owned product behavior
- hosted-only extensions or product UX
- app/workspace-specific composition rather than shared framework ownership

Current examples:

- external hosted product workspaces consuming the `app/` contract

Rule:

- product consumers may use Studio and the factory layer
- product consumers must not become the canonical source of truth for shared
  framework capabilities

### 4. Generated or transient output

This code or content is emitted by the factory layer and awaits validation,
review, or promotion.

Current examples:

- `generated/`

Rule:

- generated output is not framework ownership
- generated output must not be treated as runtime core or canonical source code
  until explicitly promoted

## Repo Classification

| Repo area | Classification | Why |
|---|---|---|
| `mozaiksai/` | Universal substrate | workflow-agnostic execution engine |
| `mozaiksai/hosts/runtime.py` | Universal substrate | runtime host |
| `mozaiksai/hosts/platform.py` | Universal substrate | app host and shell/page/module composition |
| core `chat-ui/` primitives | Universal substrate | reusable shell/UI substrate |
| `factory_app/` | Framework-owned optional capability | first-party factory workspace |
| `mozaiksai/hosts/studio.py` | Framework-owned optional capability | Refinement Engine host |
| `factory_app/app/admin/pages/` | Framework-owned optional capability | Workspace Studio UI |
| `chat-ui/src/admin/` | Framework-owned optional capability | platform-management UI owned by Studio |
| `mozaiks_cli/` | Framework-owned optional capability | developer interface |
| external hosted product workspaces | Product-specific consumer | hosted workspace and product assets |
| `generated/` | Generated/transient output | staged build output |

## Decision Test

Ask these questions in order.

### Does every downstream app need this to run?

If yes, it belongs in universal substrate.

### Is this shipped by the framework but optional for downstream app runtimes?

If yes, it belongs in framework-owned optional capability.

### Does this exist mainly for the hosted product or one first-party workspace?

If yes, it belongs in product-specific consumer.

### Is this emitted by the builder and awaiting validation or promotion?

If yes, it belongs in generated/transient output.

## Dependency Direction

Allowed dependency direction should stay simple:

- substrate may be consumed by every higher layer
- framework-owned optional capabilities may depend on substrate
- product-specific consumers may depend on substrate and optional capabilities
- generated output may depend on promoted framework contracts, but framework
  source must not depend on generated output

Disallowed direction:

- substrate depending on Studio or `factory_app/`
- substrate depending on a repo-local hosted product workspace
- shared framework capability code depending on generated output as a canonical
  source of truth
- a hosted product consumer becoming the owner of shared generation logic

## Practical Consequence

Studio and `factory_app/` may both be first-class in this repo without being
equivalent to `chat-ui` core or the runtime substrate.

That is the intended model:

- runtime and app host stay workflow-agnostic
- shared factory workflows remain a builder/factory capability
- Studio remains a Refinement Engine capability
- generated apps may consume none, one, or both of those higher-layer
  capabilities depending on deployment and product needs

## Cross References

- [../foundations/distribution-and-workspace-model.md](../foundations/distribution-and-workspace-model.md)
- [../foundations/core-product-app-bundle-boundary.md](../foundations/core-product-app-bundle-boundary.md)
- [../workflows/workflow-architecture.md](../workflows/workflow-architecture.md)
