# Builder Execution Model

This document defines how the first-party Mozaiks NL app generator should turn
user intent into a production-ready app bundle.

The builder is not a one-shot coding chat. It is a guided wizard backed by
typed contracts and hidden authoring workflows.

## Non-Negotiable Rules

- the builder writes app-bundle assets, not core runtime internals
- the builder must prefer core configuration over bespoke code generation
- decomposition happens before file generation
- app substrate, automation, and workflows stay separate models
- AG2 workflows are implementation workers, not the source of truth
- build sequencing and refinement routing belong to the builder session loop,
  not to workflow-local AG2 handoffs

In the first-party builder, that loop is sequence-driven: `workflow_sequences`
in `factory_app/workflows/extended_orchestration/extension_registry.json`
coordinate the build, while `AgentGenerator` and `AppGenerator` are individual
workflows inside that broader sequence.

## The Pipeline

```text
user intent
  -> ConceptBlueprint
  -> IntentBrief
  -> CapabilityMap
  -> PlatformProvisionPlan
  -> AppModel
  -> AutomationModel
  -> WorkflowModel
  -> BundlePlan
  -> BuildGraph
  -> compile
  -> validate
  -> preview and iterate
```

For change flows, `ImpactSet` is inserted before compile so the builder knows
how much of the graph must be replanned or rebuilt.

## The Four Builder Layers

### 1. Value layer

Produces:

- `ConceptBlueprint`

This is where the system decides whether the app or requested change is worth
building and how to explain that value to the user.

This is the home of the builder's "ValueEngine" behavior.

### 2. Modeling layer

Produces:

- `IntentBrief`
- `CapabilityMap`
- `PlatformProvisionPlan`
- `AppModel`
- `AutomationModel`
- `WorkflowModel`

This is where architectural meaning is created.

Important rule:

- the modeling layer should first ask what the platform core already provides
- only then should it ask what app-specific declaratives or stubs still need to
  be authored

### 3. Planning layer

Produces:

- `BundlePlan`
- `BuildGraph`
- `BuilderBlueprint`
- optional `ImpactSet`

This is where the builder decides:

- what work must happen
- what can happen in parallel
- which builder workflow owns which paths
- which work is just core configuration
- which work is app-authored stub creation

### 4. Execution layer

Produces:

- bundle declaratives
- generated UI files
- workflow files
- validation reports

This is where actual file writing happens.

## AG2 Implementation Note

The builder may be implemented as a set of AG2 beta workflow runs.

That is compatible with this model only if:

- AG2 workflows consume typed contracts
- AG2 workflows emit bounded authoring results
- AG2 workflows do not improvise the architecture after approval

The builder therefore needs three separate execution concerns:

- workflow execution loop: one AG2 workflow run such as `ValueEngine` or `AppGenerator`
- builder session loop: the control plane that sequences workflows, validates staged artifacts, and decides re-entry
- refinement worker loop: scoped repair or regeneration after the first pass

Do not collapse those concerns into one global handoff mesh.

Mozaiks NL app generator can absolutely be "an AG2 workflow that writes code,"
but only after the system has already decided what that workflow is allowed to
write.

## What `Task` Means

Inside the builder, `task` means `BuildTask`.

A `BuildTask` is:

- a bounded authoring unit
- owned by one builder workflow role
- tied to capability refs or provision refs
- explicit about bundle path ownership

It is not:

- a feature request
- a product capability
- a vague spec fragment
- a single file edit

This distinction is what makes the builder parallelizable without becoming
chaotic.

## Core-Provision Classification

Before the builder creates app-authored tasks, it should classify each concern
as one of:

- `core_provided`
- `core_configured`
- `app_stub`
- `external_integration`
- `disabled`

Examples:

- auth is usually `core_configured`
- websocket delivery is usually `core_provided`
- shell layout is usually `core_configured`
- a custom ERP adapter may be `external_integration`
- a domain-specific admin helper may be an `app_stub`

The builder should prefer:

1. core-provided behavior
2. core configuration
3. thin stubs on top of core
4. only then bespoke app code

## Universal Orchestrator's Role

The global orchestrator should stay coarse even inside the builder.

Use it for phase changes such as:

- concept review to architecture
- architecture to planning
- planning to compilation
- compilation to validation
- validation to preview

Do not use it as the place where every file-generation unit is scheduled.

That finer-grained work belongs to the builder's own `BuildGraph`.

## Parallelism

Parallel build work should be based on bounded ownership:

- one task owns specific declaratives or paths
- shared foundations are configured first
- parallel dispatch happens only after dependencies are explicit
- task result merging happens before integration and validation

The builder should never let multiple AG2 authoring workers improvise over the
same paths.

## Write Boundary

The builder must keep path ownership explicit.

- `IntentModeler` may write no bundle paths
- `ArchitecturePlanner` may write no bundle paths
- `AutomationPlanner` may write no bundle paths
- `WorkflowAuthor` may write only workflow-owned bundle paths under
  `generated/workflows/{app_id}/{build_id}/{workflow_name}/`
- `BundleCompiler` may write only app manifest, shell, substrate, module, and
  automation bundle families
- `Validator` may write reports, but no bundle declaratives

This boundary should be enforced in the typed `BuildGraph`, not left to prompt
discipline.

## Why The Current App Bundle Matters

The greenfield target is the canonical app-root contract, not the retired
`platform/` shell shape.

The builder should reason toward:

- generated app bundles under `generated/apps/{app_id}/{build_id}/app/`
- generated workflow bundles under
  `generated/workflows/{app_id}/{build_id}/{workflow_name}/`
- the canonical promoted app-root families:
  - `app/app.json`
  - `app/config/*`
  - `app/ui/pages/*`
  - `app/modules/*`
  - `workflows/*`
  - `app/brand/*`

At runtime, the same contract is consumed from the active app root, such as
`factory_app/app/` for the first-party builder/reference app workspace or
`app/` inside a generated workspace.

## Outputs

Before or during build, the builder should leave behind reviewable artifacts:

- concept blueprint
- architecture summary
- provision plan summary
- automation route summary
- workflow inventory
- build graph summary
- validation report

Without these artifacts, the system cannot explain what it is about to build or
change.

## Cross References

- [app-planning-contracts.md](app-planning-contracts.md)
- [app-builder-architecture.md](app-builder-architecture.md)
- [orchestration-control-loops.md](../workflows/orchestration-control-loops.md)


