# ADR 0008: Deterministic Engineering Context for Software-Engineering Assignments

Date: 2026-08-31

Status: Proposed

## Context

Mozaiks assigns software-engineering work to AG2 agents. Today the context
those agents receive accumulated around older internal patterns rather than a
contract:

- `factory_app/workflows/AppGenerator/agents.yaml` carries roughly 266k
  characters of static prompt prose across 19 agents, concatenated with no
  templating by `mozaiksai/core/workflow/agents/factory.py`.
- Context variables are rendered by bare `str()` with no size cap
  (`mozaiksai/core/workflow/context/context_utils.py`) and frozen into system
  messages at agent construction. The whole generated-bundle file map
  (`generated_files`) is injected into four agents on repair re-entry.
- Output semantics are stated twice: prose `[OUTPUT FORMAT]` sections restate
  what `structured_outputs.yaml` (218 models) already enforces structurally.
- Catalog injections repeat: the same file-contract, language-profile, and
  subscription facts are delivered to up to seven agents, re-derived on every
  LLM call by prompt middleware.
- The code-writing agents have no bounded retrieval tools. That is a
  deliberate, test-locked consolidation
  (`tests/test_graph_authority_contract.py` asserts the removal of the old
  AppGenerator-local code-context machinery); code intelligence lives in
  `mozaiksai/core/app_context/` and is exposed today only through the
  refinement harness.
- The refinement harness demonstrates the intended pattern already: a
  33-line policy prompt plus bounded, checkpoint-gated retrieval tools
  (`factory_app/refinement_harness/config/harness.yaml`, `config/tools.yaml`)
  over the tree-sitter-backed context graph.
- `ARCHITECTURE.md` states the invariant — agents receive compact context
  first and retrieve exact sources through tools — but the only test of it is
  a string-presence check, not behavior.

AG2 1.0.3 (pinned) supplies the runtime primitives: fragment system prompts
and dynamic prompt hooks, `SkillPlugin`/`SkillsToolkit` progressive
disclosure, caller-supplied `AssemblyPolicy`, `TokenBudgetPolicy`,
`CompactStrategy`/`CompactTrigger`, deterministic `ViewPolicy` projections,
per-agent variables with call overrides, and network capability
advertisement via free-form `Resume.claimed_capabilities`. AG2 deliberately
has no serializable per-assignment statement of what an agent is entitled to
know: assembly is a runtime pipeline, not a data contract.

Mozaiks is pre-1.0. Unreleased internal prompt/context architecture carries
no compatibility obligation.

### Relationship to issue #411

Issue #411 is a design prompt, not architecture authority. This ADR does not
adopt a typed decision ledger as a second authority for application meaning:
the entitlement contract defined here carries references to canonical
contracts and never restates or re-decides semantic facts. To the extent
#411 gestures at deterministic, inspectable inputs to agent work, this ADR
adopts that direction; everything else in #411 is deferred.

## Decision

Mozaiks deterministically defines WHAT an engineering assignment is entitled
to know. AG2 owns HOW that entitlement becomes runtime model context.

Concretely:

1. One narrow, reference-oriented entitlement contract is introduced (final
   class name to be fixed at implementation; working name
   "assignment context manifest"). It is derived deterministically from the
   `CompilationPlan` unit, the layout registry, and the closed capability
   taxonomy. It is `extra="forbid"`, closed-domain, and self-digested —
   the same contract standard as `CompilationPlan`.
2. Mozaiks builds no general-purpose context engine and does not duplicate
   AG2 prompt assembly, Skills runtime, KnowledgeStore mechanics, Views,
   compaction, middleware, or network execution. Where an accepted AG2
   primitive exists, Mozaiks uses it.
3. Runtime context (retrieved bytes, history, KnowledgeStore state, provider
   serialization, compaction output) never becomes semantic authority.

### The entitlement contract

Only five concepts are genuinely new; everything else reuses existing
canonical contracts, and this ADR explicitly rejects duplicating those facts
in another metadata object:

New:

- `CompilationPlan` unit join/reference (plan digest + unit id);
- required local engineering Skill identifiers (closed set, selected by
  family + taxonomy);
- retrieval grants (see below);
- context budget class (symbolic identifier only);
- manifest content digest.

Reused, never duplicated:

- semantic source identities: the unit's `sources`
  (`mozaiksai/core/semantics/compilation_plan.py`);
- owned outputs: the unit's `outputs` plus existing owned-path enforcement
  in `mozaiksai/core/workflow/task_batches.py`;
- base artifacts: `ChildContractRef`;
- dependency references: `ApprovedAssignmentSpec.dependency_context_refs`
  (`mozaiksai/core/workflow/plan_assignment_compiler.py`) — populated with a
  defined reference grammar rather than free-form strings;
- validation: `required_validators` and `required_structured_output_id` on
  the same spec;
- capability requirements: the closed capability taxonomy
  (`mozaiksai/core/taxonomy.py`).

The contract carries references, not content. It contains no prompt prose,
no model names, no AG2 channel/envelope identifiers, no runtime history, no
free-form metadata, and no inlined file contents.

### Retrieval grants (the novel concept)

An assignment receives a deterministic authorization describing which
reference and scope classes it MAY retrieve just in time. Candidate grant
surfaces include semantic payload references, dependency artifact
references, assigned base artifacts, approved app-context graph
neighborhoods, required contract documents, and permitted source paths.
Grants are part of the manifest and therefore digested and auditable. The
manifest does not eagerly contain retrieved content, and an agent asking for
broader filesystem or repository access is not a reason to grant it.
Retrieval flows through the consolidated `mozaiksai/core/app_context/`
authority using bounded, harness-shaped tools; the graph-authority contract
tests remain in force.

### Context budget class

The manifest names a symbolic budget class. Numeric token limits and
provider-specific context parameters are runtime policy, set from evaluation
evidence, and are out of scope for canonical contracts. This ADR fixes no
numbers.

### Prompt / schema / Skills policy

- Structured-output schemas own machine-enforced output shape, required
  fields, and types.
- Prompts own semantic responsibility, reasoning constraints, invariants,
  authority boundaries, and failure behavior — at the altitude the
  refinement harness's coding prompt already demonstrates.
- Skills own reusable specialized engineering methodology, one per artifact
  family, progressively disclosed via AG2's skills runtime.
- Examples are justified only to explain semantic ambiguity, ownership
  boundaries, edge cases, or reasoning patterns. Examples that merely
  demonstrate JSON/YAML/Pydantic shape duplicate the schema and are
  migration debt, not a pattern to perpetuate.

### The context pattern

Small high-signal policy context, plus deterministically authorized
references, plus bounded just-in-time retrieval. Eager: what is
authoritative and small. Referenced: what is bulky but verifiable by
digest. Retrieved: what is exploratory, through granted bounded tools. The
refinement harness is the internal evidence that this pattern already works
in production.

### AG2 runtime mapping

| Entitlement element | AG2 primitive |
|---|---|
| Role + policy prompt | `Agent(prompt=[fragments])` |
| Manifest reference rendering | dynamic prompt hooks (once per turn) |
| Family methodology | `SkillPlugin` / `SkillsToolkit` |
| Volatile small facts | per-agent variables + call overrides |
| Manifest and tool handles | `Inject` / `Context` dependency injection |
| Granted retrieval | bounded tools over `core/app_context` |
| Run state | `KnowledgeStore` (unchanged boundary) |
| Ordering + budget | `AssemblyPolicy`, `TokenBudgetPolicy` |
| History reduction | `CompactStrategy` / `CompactTrigger` per budget class |
| Network history scope | deterministic `ViewPolicy` per checkpoint class |
| Peer capability | `Resume.claimed_capabilities`, taxonomy-validated |

Runtime identifiers and state never enter the deterministic contract.

### Local Skills vs network skills

These are distinct AG2 concepts and stay distinct in Mozaiks:

- Local Skills: reusable, progressively disclosed task methodology and
  content. The Mozaiks engineering-skill requirement (closed identifiers in
  the manifest) selects validated local skill activation.
- Network skills / claimed capabilities: peer capability discovery and
  advertisement. Mozaiks' closed capability taxonomy validates what an agent
  advertises; free-form AG2 network capability strings are never canonical
  Mozaiks authority and are never consumed for routing unvalidated.

Two mappings, two directions, never merged.

### Determinism boundary

Deterministic (digestable, auditable): the entitlement manifest, semantic
and artifact references, allowed retrieval surfaces, Skill requirements,
validation requirements, owned outputs, budget policy class, view class.

Runtime/dynamic: retrieved bytes, conversation history, KnowledgeStore
results, provider serialization, model-specific prompt structure, and
compaction output where LLM summarization is used. Dynamic context informs a
run; it never becomes semantic authority.

### AppGenerator evolution and product states

AppGenerator is not to be split merely because individual prompts are large;
oversized prompts are debt under this ADR's prompt policy, not workflow
shape. Future decomposition occurs only at meaningful boundaries: user
interaction, persistence, authority, context regime, recovery/retry
isolation, or durable milestone. The pre-Slice-5 planning path is not split
before its authority cutover (ADR 0007 Slice 5); splitting a path scheduled
for replacement is wasted motion.

Users see meaningful product states, not internal workflow topology. A
transition is user-facing only when user input is needed, ambiguity must be
resolved, approval or confirmation is required, a meaningful decision
exists, a consequential action requires permission, a durable result is
ready, or a recoverable failure requires user action. Projection,
`CompilationPlan` derivation, assignment compilation, materialization,
validation, repair loops, and persistence internals stay behind the UI.

A non-normative example progression — product copy, not architecture
identifiers:

Understanding → Designing → Building → Verifying → Ready.

### Superseded-mechanism cleanup discipline

No direct residue of older AG2 group-chat APIs exists in this repository
(verified by search). The debt is Mozaiks-built parallel mechanisms that
accepted AG2 primitives now supersede. The migration discipline:

When the accepted AG2 primitive (or a contract from this ADR) supersedes a
Mozaiks-built mechanism, the superseded path is deleted atomically in the
same change that lands the replacement — every in-repo caller, test, and doc
updated; no compatibility flags; no dual old/new execution paths; no
shim-by-default. Evidence is required before deletion: a mechanism is
removed because its replacement is accepted and its callers are migrated,
never merely because it looks old.

Debt categories to audit in future slices: bespoke prompt concatenation
superseded by accepted assembly policy; construction-time bulk
stringification superseded by bounded retrieval; per-call catalog
re-rendering superseded by Skills progressive disclosure;
schema-duplicating prose; parallel local context systems; watch-only budget
logic once an active AG2 budget/compaction policy is authoritative; older
routing/history assumptions superseded by Network Views; compatibility
flags selecting between internal execution paths.

The companion inventory
[appgenerator-context-debt](../architecture/workflows/appgenerator-context-debt.md)
records the concrete, evidence-backed findings.

### Slice placement

- ADR 0007 Slice 4C: no engineering-context implementation.
- Slice 5: assignment projection must populate the existing landing zones
  (`dependency_context_refs`, `required_validators`,
  `required_structured_output_id`, `allowed_agent_ids`) with
  manifest-compatible grammars. Slice 5 must not introduce free-form
  reference or validator identifiers that would later require migration.
- Post-Slice-5 bounded slice: the likely implementation point for the
  entitlement contract, skills, grants, and budget classes.
- Slice 6: plan/refinement closure becomes the context-scope authority for
  Refinement Runs; manifests carry that swap.
- AG2 distributed-runtime track: network Views, reconnect, delivery, and
  runtime identity remain separate concerns.

### Evaluation

Behavioral evaluation is required before numeric thresholds are fixed:
eager-context size per budget class; irrelevant and duplicate context;
retrieval count and volume; owned-path violations; first-pass validation
rate; hallucinated-dependency rate; repair retry count; token usage; and
output-quality regression against the acceptance gate. Thresholds come from
corpus evidence, not this document. These evaluations become the behavioral
enforcement of the `ARCHITECTURE.md` compact-context invariant.

## Consequences

- One narrow contract makes each assignment's knowledge entitlement
  auditable, cacheable, and reproducible without duplicating any canonical
  fact.
- AG2 adoption replaces several Mozaiks-built mechanisms; each replacement
  deletes its predecessor, shrinking surface rather than growing it.
- Coding agents gain bounded retrieval through the consolidated
  app-context authority rather than eager whole-bundle exposure, aligning
  AppGenerator with the already-proven harness pattern and the documented
  architecture invariant.
- No work is added to Slice 4C; Slice 5 gains one seam obligation it is
  already positioned to satisfy.

## Alternatives considered and rejected

1. Prompt refactor without a contract — leaves entitlement unauditable and
   refinement caching impossible.
2. A Mozaiks context engine over AG2 — duplicates AG2's deliberately
   runtime assembly and violates the ownership boundary.
3. Folding context facts into `CompilationPlan` — contaminates a semantic
   authority with execution ergonomics, rejected by the Slice 4B boundary
   corrections.
4. Per-agent local retrieval tools inside AppGenerator — reinstates a
   deliberately removed subsystem against a test-enforced consolidation.
5. Treating network capability strings as skills or as routing authority —
   merges distinct AG2 concepts and launders free-form strings into
   canonical authority.
