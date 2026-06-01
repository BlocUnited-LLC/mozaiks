# Mozaiks Control Plane

Mozaiks does not just generate apps. It knows how to change them intelligently
after generation.

That is the product promise behind the control plane.

When you ask for a change, Mozaiks should not treat every request like a blind
code edit. It should understand whether you are asking for a tiny patch, a
design adjustment, a new capability, or a concept-level pivot. It should route
to the smallest accurate next step, preserve everything above the change, and
only regenerate what actually needs to move.

That is what the Mozaiks control plane does. It is the layer that turns the app
into a self-improving system instead of a one-time generation output.

## Why This Matters

This is the part of Mozaiks that aligns most directly with the YC "AI Operating
System for Companies" thesis.

The app is already legible to AI because it was generated from contracts. The
control plane is what uses that legibility to compare the current build state to
your requested outcome and choose the right path forward.

Without this layer, Mozaiks would just be a generator. With it, Mozaiks can
keep evolving the app after the first build.

## What The Control Plane Actually Does

When a change request comes in, the control plane:

- classifies the request as `patch`, `design`, `feature`, or `core`
- checks which artifact family is affected and what downstream work that implies
- routes into the smallest valid workflow sequence or coding path
- scopes the change to the relevant contracts and files
- decides whether to auto-apply, ask for confirmation, or clarify first

That is the user-facing behavior. Under the hood, the current implementation is
split across three layers.

### 1. Runtime Layer

`mozaiksai/control_plane/` is the canonical runtime package. It owns the
control-plane runtime, checkpoint dispatch, contracts, loaders, and the
first-party implementations that execute the current flow.

Key components include:

- `LLMChangeClassifier`
- `RefinementTriggerRouteResolver`
- `ArtifactScopeProposer`
- `ContractSurfacePlanner`
- `SurfaceRegenerationWorker`
- `ScopedRefinementCodingWorker`
- `FirstPartyHarnessDecisionPolicy`

### 2. Declarative Pack

The runtime is driven by a first-party declarative pack:

- `factory_app/app/config/ai.json` enables the control plane and selects model
  profiles
- `factory_app/control_plane/config/control_plane.yaml` declares checkpoints,
  prompts, routes, tools, and the harness implementation
- `factory_app/workflows/extended_orchestration/extension_registry.json`
  defines the workflow sequences the router can re-enter

So the current system is not one monolithic router hardcoded in Python. It is a
runtime executing a declarative control-plane pack.

### 3. Context Inputs

The control plane depends on persisted revision context, artifact summaries, and
the Context Graph.

The Context Graph tells Mozaiks what exists and how it is connected. The control
plane uses that map to decide what should happen next.

## How This Differs From AG2's Harness

AG2's harness and the Mozaiks control plane solve different problems.

AG2 describes its harness as the set of opt-in primitives you compose onto a
single agent loop: context assembly policies, persistent knowledge, sub-task
spawning, and the middleware those features inject. In other words, AG2's
harness is agent-local. It makes one agent richer.

Mozaiks uses the word differently.

In Mozaiks, the harness is a runtime shell inside the broader control plane.
It coordinates checkpoints and decisions, but it is not the whole intelligence
layer. The broader control plane sits above workflow execution and answers a
different question: given this user request and this app state, what should the
system do next?

The clean comparison is:

- AG2 harness: enrich one agent's turn lifecycle
- Mozaiks harness: runtime shell for checkpoint orchestration inside the control plane
- Mozaiks control plane: app-level routing and refinement system above workflows

So if someone already knows AG2, the easiest way to explain the difference is:
AG2's harness is about composing capabilities onto an agent. Mozaiks' control
plane is about governing how a generated app changes over time.

## How A Request Moves Through The System

```text
Your request
  → change classifier assigns patch / design / feature / core
  → route resolver selects a workflow sequence or scoped coding path
  → patch requests go to scope proposal + coding worker
  → feature/design requests go to contract-surface planning + regeneration
  → decision policy decides auto-apply, confirm, clarify, or restart
```

Two examples make that concrete.

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

See [Refinement Control Plane](./04-refinement-control-plane.md) for the
refinement-specific path inside this system.

---

**Architecture references**

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
