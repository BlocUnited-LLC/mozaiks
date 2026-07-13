# Domain-Agnostic Build Factory

The build factory is the stable execution system behind Studio app creation.
It should not be rewritten for every app domain. Domain-specific behavior
belongs in declared workflow contracts and build-context packs.

## Contract

The factory has three layers:

| Layer | Owns |
| --- | --- |
| Factory harness | Session routing, checkpoints, task execution, artifact staging, validation gates, and promotion |
| Workflow contracts | Structured outputs, task models, transition graph, context variables, and tool bindings |
| Build context packs | Domain catalogs, contracts, templates, examples, and small projected defaults |

The harness stays domain-agnostic. A domain such as web apps, games, mobile
apps, or enterprise workflows can change what agents know and which declared
lanes they choose, but it must not create undeclared artifact shapes at prompt
time.

## Rules

- Define structured outputs before agents produce artifacts.
- Add task types and artifact families to the workflow contract before build
  context packs reference them.
- Keep domain knowledge in `build_context/{context_name}/` assets declared by
  `context.yaml`.
- Project only relevant catalog slices to each agent.
- Keep tools deterministic: they validate, assemble, save, or inspect declared
  artifacts.
- Do not fork the factory harness to support a new domain unless the shared
  contract is missing a required primitive.

## Current Shape

The first-party factory currently uses `factory_app/workflows/` for shared
builder workflows and `factory_app/build_context/` for targeted build-time
input. `AppGenerator` remains the primary app-bundle workflow, while build
context packs supply reusable domain and capability guidance.

The target direction is to make web-app specifics an explicit pack rather than
implicit prompt knowledge. The shared workflow and harness contracts should be
general enough that future domains add build-context input and structured
contract extensions, not a separate generation runtime.

## Validation

A domain-aware factory change is ready only when:

- The new artifact or task shape is represented in strict structured outputs.
- Build-context assets are declared in `context.yaml`.
- Runtime loaders and validators accept the declared shape.
- Agent prompts reference contract-owned fields instead of inventing paths,
  schemas, or file ownership rules.
- Tests cover the contract, generated bundle shape, and any build-context
  projection behavior.

## Cross References

- [Build Context Packs](build-context-packs.md)
- [Workflow Authoring Contracts](workflow-authoring-contracts.md)
- [AppGenerator Capability Planning](../modules-systems/appgenerator-capability-planning.md)
- [Factory Build Workflow System](../builder/builder-execution-model.md)
