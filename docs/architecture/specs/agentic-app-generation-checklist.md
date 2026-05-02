# Agentic App Generation Checklist

**Status:** Active alignment checklist
**Purpose:** Keep implementation work aligned to the canonical app-generation strategy and prevent drift.

**Primary reference:** [agentic-app-generation-strategy.md](./agentic-app-generation-strategy.md)
**Existing-app reference:** [existing-app-augmentation-strategy.md](./existing-app-augmentation-strategy.md)

---

## Usage Rule

Do not skip phases.

Each phase has:

- required deliverables
- acceptance checks
- explicit non-goals

If a phase is not complete, do not pretend later phases are solved by adding one-off workflow logic.

---

## Phase 0: Strategy Alignment

- [ ] Strategy doc is canonical and referenced from the docs index
- [ ] Existing-app augmentation doc is canonical and referenced from the docs index
- [ ] Existing architecture docs do not teach a conflicting workflow-first story
- [ ] Temporary placeholders are explicitly documented as transitional
- [ ] Coding-agent guidance points to the strategy + this checklist for builder work

**Acceptance**

- A new coding agent can read the docs and describe the artifact model:
  `RequestIntent -> ProductSpec -> CapabilitySpec[] -> ExperienceSpec -> AgentAugmentationPlan -> BuildGraph`

---

## Phase 1: Artifact Contracts

- [ ] Define `RequestIntent`
- [ ] Define `ProductSpec`
- [ ] Define `CapabilitySpec`
- [ ] Define `ExperienceSpec`
- [ ] Define `AgentAugmentationPlan`
- [ ] Define `BuildGraph`
- [ ] Define refinement-layer mapping across those artifacts

**Acceptance**

- Every planning-stage output has one canonical artifact contract
- No duplicate artifact names compete for the same role

**Non-goals**

- Do not implement runtime compilers yet
- Do not solve page rendering yet

---

## Phase 2: Builder Workflow Role Alignment

- [ ] `ValueEngine` is reframed as concept/ProductSpec compiler
- [ ] `ExistingAppDiscovery` is reframed as existing-product discovery into the same planning path
- [ ] `ExistingAppDiscovery` outputs `ExistingProductSpec`, `CapabilitySpec[]`, and `AgentAugmentationPlan`
- [ ] `ExistingAppDiscovery` supports guided plain-language intake by default and advanced operator inputs second
- [ ] `ExistingAppDiscovery` includes a first-class workspace-app preset for local brownfield onboarding such as `mozaiks-app`
- [ ] `DesignDocs` is reframed as internal design elaboration for `ExperienceSpec`
- [ ] `AppGenerator` is reframed as deterministic product bundle compiler
- [ ] `AgentGenerator` is reframed as agent augmentation compiler
- [ ] User-facing docs describe one build journey, not multiple unrelated workflows

**Acceptance**

- A user can ask for “create app”, “connect existing app”, or “refine app” without needing to choose internal compiler stages
- Existing-app onboarding defaults to augmentation first, not full migration first

---

## Phase 3: Persistent App UI Contract

- [ ] Define page archetype catalog
- [ ] Define archetype-to-primitive compiler contract
- [ ] Define custom-slot contract
- [ ] Decide which surfaces remain fully declarative
- [ ] Decide where bespoke React is allowed
- [ ] Update docs so page generation is neither “raw React by default” nor “only low-level primitives”

**Acceptance**

- Persistent pages have a deterministic higher-level IR
- Customization path is clear and reusable

**Non-goals**

- Do not let arbitrary per-app page React become the default

---

## Phase 4: Deterministic Product Bundle Compiler

- [ ] Compile `CapabilitySpec[]` into deterministic modules/services/entities
- [ ] Compile `ExperienceSpec` into persistent page schemas/navigation/theme/shell artifacts
- [ ] Compile auth/integrations from the product model
- [ ] Produce `BuildGraph` with owned paths and validation rules
- [ ] Ensure deterministic validation exists for generated outputs

**Acceptance**

- A non-agentic app can be generated and validated without `AgentGenerator`

---

## Phase 5: Agent Augmentation Compiler

- [ ] Compile `AgentAugmentationPlan` into workflows/tools/handoffs
- [ ] Define app-to-workflow invocation bindings
- [ ] Define workflow access to app entities/services
- [ ] Define agent UI and transition UI bindings
- [ ] Keep agent augmentation optional and composable

**Acceptance**

- A deterministic app can attach one agentic feature without changing the product compiler model

---

## Phase 6: Refinement and Persistence

- [ ] Route refinements by artifact layer, not by whole-workflow rerun
- [ ] Persist artifact versions for concept, experience, product bundle, and agent bundle
- [ ] Define “patch”, “design”, “feature”, and “core” against artifact boundaries
- [ ] Ensure `SessionRouter` can re-enter the correct compiler stage

**Acceptance**

- A user can request a targeted change and only the affected artifact layer is rebuilt

---

## Phase 7: Functional First Milestone

- [ ] Choose one canonical proof scenario
- [ ] Generate a deterministic app from scratch
- [ ] Attach one agentic augmentation
- [ ] Validate the resulting bundle end to end
- [ ] Prove one refinement path without rerunning everything

**Recommended proof scenario**

- auth/admin CRUD
- messaging
- one agentic augmentation such as thread summary

**Acceptance**

- The system is demonstrably useful for one realistic app category
- The artifact model holds under both initial generation and refinement

---

## Phase 8: Production Readiness Gate

- [ ] Docs match runtime behavior
- [ ] Prompts match artifact contracts
- [ ] Validators reject drift
- [ ] Runtime and chat UI honor the same artifact boundaries
- [ ] Live end-to-end smoke exists for the canonical build journey
- [ ] Ops concerns are defined for approvals, retries, observability, and failures

**Acceptance**

- The build journey is deterministic, modular, and supportable

---

## Anti-Drift Rules

- [ ] Do not add a new workflow just to patch over missing artifact contracts
- [ ] Do not turn normal product functionality into workflows by default
- [ ] Do not let page generation drift into arbitrary raw React by accident
- [ ] Do not add duplicate planning objects with overlapping meaning
- [ ] Do not hide missing product decisions inside runtime heuristics

If one of those becomes necessary, stop and update the strategy first.
