# Mozaiks Control Plane

The Mozaiks control plane is the intelligence layer that sits between your
request and workflow execution.

When you say "fix this page," "add export controls," or "restart this from
concept," Mozaiks does not drop a generic coding agent into your repo and hope
for the best. The control plane interprets the request, checks the current
artifact state, decides the smallest valid next step, and only then routes into
scoped coding or workflow re-entry.

That is the umbrella. The harness is one part of it, not the whole thing.

## Current Implementation

Today, in the first-party builder experience, the control plane is made up of
three layers working together.

### 1. Runtime Layer

`mozaiksai/control_plane/` is the canonical runtime package. It owns the
control-plane runtime, contracts, loaders, ports, checkpoint dispatch, and the
first-party implementations that do the actual work.

Key runtime components:

- `LLMChangeClassifier` classifies requests as `patch`, `design`, `feature`, or
  `core`
- `RefinementTriggerRouteResolver` maps that classification to a workflow
  sequence such as `app_revision` or `full_rebuild`
- `ArtifactScopeProposer` scopes patch requests to the smallest relevant file set
- `ContractSurfacePlanner` builds ordered contract-surface plans for `feature`
  and `design` changes
- `SurfaceRegenerationWorker` executes those surfaces in dependency order
- `ScopedRefinementCodingWorker` handles patch-level coding against a scoped
  slice
- `FirstPartyHarnessDecisionPolicy` turns the result into a user-facing decision:
  auto-apply, confirm, clarify, or restart

### 2. Declarative Pack

`factory_app/control_plane/` is the first-party declarative pack that tells the
runtime how to behave.

Current anchors:

- `factory_app/app/config/ai.json` enables the control plane and selects LLM
  profiles such as `classifier`, `codegen`, and `reviewer_validator`
- `factory_app/control_plane/config/control_plane.yaml` declares routes,
  checkpoints, prompts, and tool bindings
- `factory_app/workflows/extended_orchestration/extension_registry.json`
  defines the workflow sequences that routing decisions can re-enter

This matters because the control plane is not hardcoded as one giant router. The
runtime executes a declarative pack.

### 3. Context Inputs

The control plane also depends on the current app context.

- the revision context and artifact summary tools provide persisted build state
- the Context Graph provides the live map of modules, pages, schemas,
  workflows, and bindings
- the artifact dependency graph in `extension_registry.json` tells the router
  what downstream artifact families are affected

This is why Mozaiks can make contract-level decisions instead of file-level
guesses.

## Where The Harness Fits

In the current implementation, the harness is the execution shell declared in
`control_plane.yaml`:

```yaml
harness:
  implementation: mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness
```

That harness coordinates the control-plane checkpoints and policies. It is the
runtime shell that runs the control-plane flow. It is not the same thing as the
entire control plane.

So the clean mental model is:

- **control plane** = the full intelligence layer
- **harness** = the runtime shell inside that layer
- **refinement control plane** = the post-generation change path the control
  plane owns

## How A Request Moves Through The Control Plane

```text
Your request
  → change classifier assigns patch / design / feature / core
  → route resolver selects a workflow sequence or scoped coding path
  → patch requests go to scope proposal + coding worker
  → feature/design requests go to contract-surface planning + regeneration
  → decision policy decides auto-apply, confirm, clarify, or restart
```

Two concrete examples:

**Patch request**

```text
"Fix the broken column header in the projects table"

→ classify: patch
→ query Context Graph for likely page/module scope
→ run scoped coding worker against only those files
→ auto-apply or ask to confirm depending on confidence
```

**Feature request**

```text
"Add export controls to the projects table"

→ classify: feature
→ route: app_revision
→ plan contract surfaces in dependency order
→ regenerate affected surfaces without rerunning the full build
```

## Why This Exists

Most AI tools help humans write software faster. The Mozaiks control plane helps
Mozaiks change generated software at the right level.

Because the app was generated from contracts in the first place, the control
plane can classify change intent, reason about artifact ownership, preserve the
current build state, and choose the smallest accurate next step.

See [Refinement Control Plane](./04-refinement-control-plane.md) for the
refinement-specific path inside this system.

---

**Architecture references**

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
