# Mozaiks Control Plane

Mozaiks does not just generate apps. It knows how to change them intelligently
after generation.

When you ask for a change, Mozaiks does not treat every request like a blind
code edit. It understands whether you are asking for a tiny patch, a
design adjustment, a new capability, or a concept-level pivot. It routes to the
smallest accurate next step, preserves everything above the change, and only
regenerates what actually needs to move.

## What The Control Plane Actually Does

When a change request comes in, the control plane:

- classifies the request as `patch`, `design`, `feature`, or `core`
- checks which artifact family is affected and what downstream work that implies
- routes into the smallest valid workflow sequence or coding path
- scopes the change to the relevant contracts and files
- decides whether to auto-apply, ask for confirmation, or clarify first

## Context Graph

The control plane depends on persisted revision context, artifact summaries, and
the Context Graph.

The Context Graph tells Mozaiks what exists and how it is connected. The control
plane uses that map to decide what happens next.

## Declarative Pack

The runtime is driven by a first-party declarative pack. Its job is to describe
what the control plane is allowed to do, what runtime implementation should do
it, and which workflow sequences can be re-entered.

| File | Shape | What it controls | Derived from |
|---|---|---|---|
| `factory_app/app/config/ai.json` | app config JSON | Enables the control plane and selects model profiles | The app's chosen control-plane policy and model budget |
| `factory_app/control_plane/config/control_plane.yaml` | control-plane manifest YAML | Declares checkpoints, prompts, routes, tools, and the harness implementation | The first-party control-plane pack for this app workspace |
| `factory_app/workflows/extended_orchestration/extension_registry.json` | workflow registry JSON | Defines the workflow sequences the router can re-enter | The cross-workflow build and revision graph |

See [Control Plane Schemas](./02-schema-shapes.md) for the current schema
shapes and their runtime-derived definitions.

## Harness

The harness is the runtime shell inside the broader control plane. It is the
implementation named in `control_plane.yaml`, and it coordinates checkpoints,
tool calls, and routing decisions.

If you want the short version:

- control plane = the full intelligence layer
- harness = the runtime shell that runs the checkpoint flow
- declarative pack = the files that configure that flow

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
