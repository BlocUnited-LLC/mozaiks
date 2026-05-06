# Mid-Flight Journeys

**Status:** Current reference  
**Last updated:** 2026-04-12
**Depends on:** [Orchestration and Decomposition](../../architecture/orchestration-and-decomposition.md), [Workflow Architecture](../../architecture/foundations/workflow-architecture.md), [Orchestration Control Loops](../../architecture/foundations/orchestration-control-loops.md), [MFJ Strict Resume Contract](mfj-strict-resume-contract.md)

**Validation status:** MFJ runtime semantics are implemented in the current codepath, but end-to-end confidence should come from the live smoke harness at `scripts/run_live_mfj_smoke.py` and `tests/test_mfj_live_smoke.py`, not from contract or mock coverage alone. The canonical no-HITL entry workflow for that harness is `factory_app/workflows/RuntimeSmoke`. The harness requires `OPENAI_API_KEY`, `MONGO_URI`, and a reachable MongoDB instance.

---

## Purpose

This document explains the current runtime meaning of a Mid-Flight Journey
(MFJ) in Mozaiks.

An MFJ is a workflow-local fork/join cycle:

1. a parent workflow reaches a decomposition point
2. runtime fans out into child runs
3. child results are aggregated
4. runtime resumes the parent deterministically

MFJ is not a global workflow journey.
It is not a vague planning concept.
It is executable runtime config under a workflow's own `extended_orchestration/mfj_extension.json`.

In the canonical orchestration model, MFJ belongs only to the workflow
execution loop. It is not the builder session loop and it is not the
refinement worker loop.

---

## Where MFJ Lives

Mozaiks has two separate graph layers.

### Global workflow graph

Path:

- `factory_app/workflows/extended_orchestration/extension_registry.json`

Owns:

- sequencing across workflows
- coarse journey order

Does not own:

- intra-workflow fan-out
- child run resume semantics

### Workflow-level MFJ graph

Path:

- `app/workflows/{workflow_name}/extended_orchestration/mfj_extension.json`

For builder workflows, the same file shape lives under `factory_app/workflows/{workflow_name}/extended_orchestration/mfj_extension.json`.

Owns:

- which `decomposition_agent` triggers an MFJ
- how child runs are spawned
- how child results are aggregated
- where the merged payload is injected
- how parent resume is routed

This is the layer that defines Mid-Flight Journeys.

---

## Current Mental Model

An MFJ should be understood as:

- decomposition inside one workflow
- deterministic runtime fan-out and fan-in
- persistent parent continuity
- strict resume routing through a router agent

The runtime does not read prose from the graph to decide what to do.
Reasoning belongs in workflow agents and structured outputs.
`mfj_extension.json` remains a compiled execution artifact.

---

## Relationship to Other Concepts

| Concept | Scope | Purpose |
|---|---|---|
| Global pack journey | Across workflows | Sequence independent workflows such as `GreenRoom -> WritersRoom -> MainStage` |
| Mid-Flight Journey | Inside one workflow | Fan out child work and merge results back into the parent run |
| Structured output | Inside one agent turn | Provide deterministic input the runtime can consume |
| DAG/task graph | Optional higher-order plan | Explicit dependency scheduling when a workflow needs more than simple fan-out |

The builder's `BuildGraph` and refinement routing are separate control-plane
concerns above MFJ.

MFJ is the runtime mechanism that sits between a decomposition agent and a
resumed host agent.

---

## Current Runtime Placement

At a high level:

```text
app/workflows/{workflow}/extended_orchestration/mfj_extension.json
  -> workflow loaders
  -> runtime orchestration support
  -> child runs + aggregation
  -> resume router handoff
```

Builder workflows use the same MFJ contract under `factory_app/workflows/{workflow}/extended_orchestration/mfj_extension.json`.

Relevant runtime areas are described in:

- [Workflow Architecture](../../architecture/foundations/workflow-architecture.md)
- [Orchestration and Decomposition](../../architecture/orchestration-and-decomposition.md)
- [MFJ Strict Resume Contract](mfj-strict-resume-contract.md)

---

## Minimal Authored Shape

The current authored experience should stay as small as possible.

Canonical minimal example:

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "writers_room_cycle",
      "decomposition_agent": "DecompositionAgent",
      "fan_out": {
        "spawn_mode": "workflow",
        "child_initial_agent": "LaneWorkerAgent"
      },
      "fan_in": {
        "resume_agent": "HostAgent"
      }
    }
  ]
}
```

This is also the live pattern used in:

- `factory_app/workflows/AgentGenerator/extended_orchestration/mfj_extension.json`

Meaning:

- `decomposition_agent` emits the child work description in structured output
- runtime fans out deterministically
- runtime aggregates child results with `collect_all` by default
- runtime injects merged results under an auto key: `mfj_<journey_id>_results` (unless `inject_as` is explicitly set)
- runtime resumes at `resume_agent` by default (or `resume_entry_agent` if explicitly set)

---

## Schema Notes

### `mid_flight_journeys[]`

| Field | Required | Meaning |
|---|---|---|
| `id` | Yes | Unique MFJ identifier within the workflow |
| `decomposition_agent` | Yes | Agent whose structured output activates the MFJ |
| `fan_out` | Yes | Child spawn configuration |
| `fan_in` | Yes | Aggregation and resume configuration |
| `requires` | No | Dependency on earlier MFJ cycles within the same workflow |
| `stages` | No | Optional staged MFJ authoring (compiled to flat journeys at load time) |

### `fan_out`

| Field | Required | Meaning |
|---|---|---|
| `spawn_mode` | Yes | Child spawn strategy; `workflow` is the common current authored path |
| `child_initial_agent` | No | Override initial child agent |
| `max_children` | No | Concurrency cap for child runs |

### `fan_in`

| Field | Required | Meaning |
|---|---|---|
| `resume_agent` | Yes | Final presenter/target agent after fan-in |
| `resume_entry_agent` | No | First agent after resume; defaults to `resume_agent` |
| `aggregation_strategy` | No | Merge mode; defaults to `collect_all` |
| `inject_as` | No | Parent context key for merged output; defaults to `mfj_<journey_id>_results` |

Important:

- if you set `inject_as`, it is normalized to `mfj_*`
- keep authored MFJ config minimal; defaults are the baseline path

### Deferred Advanced Fields (Roadmap)

To keep authoring simple, these fields are intentionally not part of the baseline authored shape:

- `fan_out.input_contract`
- `fan_out.child_context_seed`
- `fan_out.timeout_seconds`
- `output_contract`
- `fan_in.on_partial_failure`
- `fan_in.timeout_seconds`

These remain runtime-capable internals and can be surfaced later as an advanced profile.

Roadmap home for advanced authoring/integration rollout:

- Internal MFJ authoring roadmap notes

---

## Resume Semantics

The most important behavior is that fan-in resume is entry-agent-first.

If `resume_entry_agent` is set, runtime resumes there first (router-first pattern).
If omitted, runtime defaults `resume_entry_agent` to `resume_agent`.

Instead it:

1. injects the aggregated payload into parent context under `inject_as`
2. writes runtime `_mfj_*` resume keys into parent session state
3. resumes the parent at `resume_entry_agent`
4. continues through normal declarative handoff rules

This is what makes fan-in deterministic.

Key runtime-injected fields include:

- `_mfj_resume_pending`
- `_mfj_resume_target_agent`
- `_mfj_resume_entry_agent`
- `_mfj_resume_nonce`
- `_mfj_resume_consumed_nonce`
- `_mfj_resume_trigger_id`
- `_mfj_resume_cycle`
- `_mfj_resume_inject_as`
- `_mfj_resume_succeeded_count`
- `_mfj_resume_failed_count`
- `_mfj_resume_timestamp`

These keys are validated in contract-focused runtime tests such as:

- `tests/test_mfj_resume_contract.py`

For the full handoff and nonce-consumption contract, see:

- [MFJ Strict Resume Contract](mfj-strict-resume-contract.md)

---

## Context Flow

### Parent to child

At fan-out, runtime may seed child context from:

- the decomposition agent's structured output
- parent context snapshot (default path)
- runtime metadata such as parent chat identity and MFJ identifiers

The exact reasoning about what children should do belongs in the decomposition
agent, not in the graph itself.

### Child to parent

At fan-in, runtime:

1. collects child outputs
2. aggregates them using the configured strategy (default `collect_all`)
3. injects the merged result into the parent under `inject_as`
4. resumes through `resume_entry_agent` (default `resume_agent`)

This means later agents in the same parent workflow can read the merged result
from a stable `mfj_*` context key.

---

## Aggregation Strategies

Set via `fan_in.aggregation_strategy` in `mfj_extension.json`. The strategy runs once after all children complete, before the merged result is injected into the parent.

### `collect_all`

Each child's final context is kept under its own workflow name key.

```json
{
  "BillingWorkflow":  { ...child A context... },
  "AuthWorkflow":     { ...child B context... },
  "_failed":          ["SomeWorkflow"]
}
```

**Best for:** planning phases where each child's output is reviewed independently — the parent can read `mfj_results["BillingWorkflow"]` and `mfj_results["AuthWorkflow"]` separately.

---

### `concatenate`

All child contexts are flat-merged into one dict. On key collisions, last-write-wins (sorted by workflow name for determinism).

```json
{
  "shared_key": "value from last child alphabetically",
  "child_a_key": "...",
  "child_b_key": "..."
}
```

**Best for:** children that produce disjoint keys — e.g. each child adds its own unique field to a shared result dict.

**Watch out for:** key collisions. If two children write the same key, only one survives.

---

### `merge_bundles`

Deep recursive merge — nested dicts are merged, lists are concatenated, scalars follow last-write-wins (sorted by workflow name).

```json
{
  "files": ["fileA.py", "fileB.py", "fileC.py"],  // lists concatenated
  "config": {
    "sectionA": { ... },   // merged from child A
    "sectionB": { ... }    // merged from child B
  }
}
```

**Best for:** file generation or bundle assembly where children each contribute a piece of the same output structure. The AgentGenerator pack generation loop uses this pattern.

---

### `first_success`

Returns the first child that completed successfully and discards the rest.

**Best for:** redundant parallel execution — spawn multiple children doing the same task, accept the fastest result. Useful for latency-sensitive operations or unreliable external APIs.

---

### `majority_vote`

Serializes each successful child's context to JSON, counts identical outputs, and returns the most common one.

**Best for:** evaluation or consensus workflows where multiple independent agents analyse the same problem — e.g. three analyst agents independently score a company acquisition, and the majority verdict wins.

---

### Custom strategies

Register a custom strategy at runtime with `register_merge_strategy()` and reference it in config as `custom:<name>`.

---

### Guidance

- Start with `collect_all` — it is the safest default and gives the resume agent full visibility into each child's work.
- Use `merge_bundles` when children each contribute a piece of one output artifact.
- Use `concatenate` only when you are certain children produce disjoint keys.
- Use `first_success` or `majority_vote` only for explicitly redundant or consensus patterns.

---

## Child-to-Child Context Sharing

Children cannot share context with each other while running. Each child is a fully isolated workflow run with its own `chat_id` and `context_variables`. They execute concurrently with no shared memory channel between them.

**What children do receive** is a snapshot of the parent's context at the moment of fan-out — every child starts with the same read-only copy of whatever the parent had.

**What gets shared via MongoDB at runtime** is technically possible — a child could write a value to the database and another child could read it. But this creates the coordination problems you'd expect:

- Race conditions: you cannot guarantee which child writes first
- Ordering assumptions: a child that reads a peer's value may execute before that peer has written it
- Non-determinism: the fan-in result depends on external timing, not the declared merge strategy
- Debugging difficulty: failures are invisible to the coordinator and to the merge step

The cleaner model is: children operate independently on their own slice of the problem, the merge strategy combines results at fan-in, and the resume agent reasons over the combined output. If one child's output genuinely needs to influence another child's work, that is a signal to use sequential MFJ cycles (`requires` gating) rather than concurrent fan-out — complete the first batch, resume the parent, let an agent process the intermediate results, then trigger a second fan-out with the enriched context.

---

## Current Showcase Pattern

The current repo showcase path is:

1. `GreenRoom`
2. `WritersRoom`
3. `MainStage`

In this sequence, the clearest MFJ example is `WritersRoom`.

### WritersRoom

Purpose:

- load the current set brief or creative frame
- decompose it into parallel lanes
- fan out child work inside the same workflow
- fan in to the host
- render the merged outcome back through the shared shell

Minimal runtime graph:

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "writers_room_cycle",
      "decomposition_agent": "DecompositionAgent",
      "fan_out": {
        "spawn_mode": "workflow",
        "child_initial_agent": "LaneWorkerAgent"
      },
      "fan_in": {
        "resume_agent": "HostAgent"
      }
    }
  ]
}
```

This is the current flagship example to copy from when authoring MFJ behavior
in a live workflow.

---

## Authoring Guardrails

1. Do not put prose reasoning or freeform logic into `mfj_extension.json`.
2. Do not confuse global workflow journeys with workflow-local MFJ cycles.
3. Do not resume directly to the presenter when the strict resume contract
   expects a router agent first.
4. Do not use non-`mfj_` keys for fan-in payload injection.
5. Do not treat MFJ as a generic substitute for every planner or DAG problem.

---

## When to Use MFJ

Use MFJ when a workflow needs:

- a decomposition step followed by parallel child work
- persistent parent continuity
- deterministic fan-in before a host/presenter continues

Do not use MFJ when the workflow only needs:

- a normal multi-agent handoff chain
- a global cross-workflow sequence
- a one-off tool call
- a full dependency scheduler with explicit task edges

---

## Related Docs

- [Orchestration and Decomposition](../../architecture/orchestration-and-decomposition.md)
- [Workflow Architecture](../../architecture/foundations/workflow-architecture.md)
- [MFJ Strict Resume Contract](mfj-strict-resume-contract.md)
- [pack-graph-semantics.md](pack-graph-semantics.md)

---

## Example Workloads

### Research Synthesis

**Scenario**: User asks for a multi-source research brief with separate topical investigations.

- Decomposer: ResearchPlannerAgent splits the brief into parallel research lanes.
- Fan-out: Child runs gather evidence per lane.
- Fan-in: Runtime aggregates findings into a shared research bundle.
- Human checkpoint: SynthesisAgent presents the merged brief before any follow-up cycle.

### Document Batch Processing

**Scenario**: User uploads 50 contracts and says "Extract key terms and flag risks."

**MFJ-1 (Extraction)**:
- Decomposer: BatchPlannerAgent splits 50 documents into 5 batches of 10
- Fan-out: 5 child GroupChats each process 10 documents (OCR → extraction → risk scoring)
- Fan-in: Merge all extracted data into unified dataset
- Human checkpoint: ReviewAgent presents flagged risks for human review

### Multi-Platform Deployment

**Scenario**: User says "Deploy this app to AWS, GCP, and Azure."

**MFJ-1 (Deployment)**:
- Decomposer: DeployPlannerAgent identifies 3 target platforms
- Fan-out: 3 child GroupChats each handle one platform (Terraform generation, config, validation)
- Fan-in: Collect all deployment configs
- Human checkpoint: DeployReviewAgent presents configs for approval before applying

### Content Campaign

**Scenario**: User says "Create a product launch campaign with blog post, email sequence, social media posts, and landing page."

**MFJ-1 (Strategy)**:
- Decomposer: CampaignPlannerAgent identifies 4 content types
- Fan-out: 4 child GroupChats each draft one content type with tone/brand guidelines
- Fan-in: Collect all drafts
- Human checkpoint: CampaignReviewAgent presents all drafts for unified review

**MFJ-2 (Revision)**:
- Only triggers if user requests changes
- Fan-out: Only revised content types are re-generated
- Fan-in: Merge into final campaign package

### Multi-Agent Debate / Evaluation

**Scenario**: User says "Evaluate whether we should acquire Company X."

**MFJ-1 (Analysis)**:
- Decomposer: EvaluationPlannerAgent spawns 3 perspective analyses
- Fan-out: Bull case agent, Bear case agent, Neutral analyst — each builds independent arguments
- Fan-in: `majority_vote` or `collect_all` depending on use case
- Human checkpoint: Moderator agent synthesizes verdict from all three perspectives

### Testing & QA Pipeline

**Scenario**: User says "Run comprehensive tests on all modules."

**MFJ-1 (Testing)**:
- Decomposer: TestPlannerAgent identifies N modules to test
- Fan-out: N child GroupChats each run unit tests, integration tests, and generate coverage reports for one module
- Fan-in: Merge all test results + coverage into unified report
- Human checkpoint: QAReviewAgent presents pass/fail summary with drill-down links

---

## Human Checkpoints

Human checkpoints between MFJs are not a separate feature — they're the natural result of the handoff chain between the `resume_agent` of one MFJ and the `decomposition_agent` of the next.

```yaml
# handoffs.yaml — the handoff chain between MFJ-1 and MFJ-2

# MFJ-1 resumes at ProjectOverviewAgent, which presents results to user
- source_agent: ProjectOverviewAgent
  target_agent: user
  handoff_type: after_work
  transition_target: RevertToUserTarget

# User approves → proceed to next phase
- source_agent: user
  target_agent: ContextVariablesAgent
  handoff_type: condition
  condition_type: string_llm
  condition: When the user approves the proposed workflow, confirms they want to
    proceed, says the sequence diagram looks correct, or indicates there are no
    further changes needed.
  transition_target: AgentTarget

# User rejects → loop back to decomposer for revision
- source_agent: user
  target_agent: PatternAgent
  handoff_type: condition
  condition_type: string_llm
  condition: When the user provides feedback, suggestions, changes, or critique about
    the workflow, action plan, phases, agents, operations, integrations, or any aspect
    of the proposed solution.
  transition_target: AgentTarget
```

This means:
- Human approval is enforced by AG2's native handoff conditions — no custom orchestration logic
- Rejection routing is configurable per workflow — "loop back to decomposer" is just one option
- Additional checkpoint agents (APIKeyRequestAgent, FeedbackClassifierAgent) can be inserted between MFJs by adding them to the handoff chain
- The coordinator doesn't need to know about human checkpoints — it only knows about decomposition agents and resume agents

---

## Comparison to Alternatives

### vs. DAG Executor

| | Mid-Flight Journeys | DAGExecutor + AgentRunner |
|---|---|---|
| **Agent lifecycle** | Persistent agents in GroupChats with handoff rules | Disposable 2-agent pairs, fresh per task |
| **Context management** | AG2 context_variables with typed definitions, triggers, and hooks | Ad-hoc dict injection from ContextStore |
| **Orchestration** | Declarative YAML (handoffs, conditions) | Imperative Python (dependency graph walker) |
| **Hallucination control** | Agents are opinionated at config time; structured_outputs enforce schemas | TaskRoleConfig prompts; JSON parsing with fallbacks |
| **Human in the loop** | Native handoff conditions gate progression | None — pipeline runs to completion |
| **Parallel execution** | Pack coordinator spawns asyncio tasks per child GroupChat | DAGExecutor semaphore-bounded asyncio.gather |
| **Memory** | AG2 context_variables + update_agent_state hooks | None — agents are stateless |
| **Failure handling** | Runtime-capable per MFJ; baseline authoring keeps defaults and moves advanced knobs to roadmap profiles | Retry with backoff per task, deadlock detection |
| **Reusability** | Any workflow can declare MFJs in its mfj_extension.json | Tightly coupled to the build pipeline |

### vs. LangGraph / CrewAI

| | Mid-Flight Journeys | LangGraph | CrewAI |
|---|---|---|---|
| **Configuration** | YAML-first, declarative | Python code, imperative | Python code, imperative |
| **Parallel execution** | Fork-join via Pack coordinator | Parallel branches in graph | Async crew execution |
| **Human checkpoints** | Native handoff conditions | Interrupt nodes | Human tool |
| **State management** | AG2 context_variables + persistence | Graph state channels | Shared memory |
| **Multi-tenant** | Built-in (tenant_id, app_id scoping) | Manual | Manual |
| **Sub-workflow spawning** | First-class (spawn_mode, child GroupChats) | Subgraph invocation | Nested crews |
| **Result aggregation** | Configurable strategies (collect_all, merge_bundles, majority_vote) | Manual reducer | Manual |

---

## Runtime Capabilities

The current MFJ runtime includes these capability groups.

### Coordinator Lifecycle

- `WorkflowPackCoordinator` listens for `chat.agent_output_validated` and `chat.run_complete`.
- `handle_journey_triggered()` detects an MFJ trigger, pauses the parent flow, spawns children, and tracks active state.
- `handle_run_complete()` collects child completion, runs fan-in when all children finish, and resumes the parent.
- Parent resume writes the `_mfj_*` resume fields described earlier in this document and re-enters at `resume_entry_agent`.

### Typed Config and Pack Loading

- Workflow-local MFJ graphs use `version: 3` with `mid_flight_journeys`.
- Typed schema models cover fan-out, fan-in, trigger metadata, and staged expansion.
- Pack config loading is consolidated through the shared pack config path rather than ad hoc loaders.
- Unknown keys are rejected by schema validation.

### Sequencing, Persistence, and Recovery

- `requires` dependencies allow one MFJ cycle to wait on earlier cycles in the same parent workflow.
- Completion state is persisted so `requires` checks survive process restarts.
- The coordinator rebuilds completion cache state from persistence on recovery.
- Parent-scoped completion state prevents cross-chat bleed between unrelated runs.

### Aggregation and Failure Handling

- Built-in strategies cover `collect_all`, concatenation, deep merge, first success, and majority vote.
- Custom aggregation strategies can be registered and referenced from config.
- Input and output contracts can validate required parent context and merged child outputs when enabled.
- Partial failure and timeout behavior are runtime-capable (`resume_with_available`, `fail_all`, `retry_failed`, `prompt_user`) but intentionally deferred from baseline authoring.
- Advanced overrides are tracked in internal roadmap notes.

### Events and UI Feedback

- MFJ lifecycle events feed both typed orchestration events and user-facing transport events.
- UI-facing progress includes batch start, child completion, fan-in start, and workflow resume events.
- Resume and batch events can carry `trigger_id`, cycle, and success/failure counts.
- Decomposition and merge lifecycle events are emitted into the runtime event pipeline for observability.

### Observability

- MFJ logs use structured fields such as `trigger_id`, `parent_chat_id`, `mfj_trace_id`, and lifecycle event names.
- OpenTelemetry spans cover full-cycle, fan-out, child execution, and fan-in timing.
- Metrics track fan-out counts, fan-in counts, timeouts, partial failures, and duration histograms.
- Observability integrations degrade safely when optional telemetry SDKs are absent.

---

## Current Constraints

The current implementation still has a few explicit limits:

- `RETRY_FAILED` does not yet re-spawn failed children with a dedicated retry scheduler.
- Automatic detection and resume of paused parents after all children complete is not performed as a separate background recovery action.
- `chat.mfj_fan_in_progress` is not emitted during long-running merges.
- Some typed orchestration events still carry summary fields without the full trigger enrichment desired for every callback.

These are implementation limits, not authoring-model changes.

---

## Validation Coverage

MFJ behavior is covered by several layers of tests.

### Unit Coverage

- Coordinator behavior covers fan-out, fan-in, merge resolution, sequencing, contracts, duplicate suppression, and partial-failure handling.
- Schema coverage validates typed `mid_flight_journeys` input and staged-to-flat expansion behavior.
- Persistence coverage validates write-through, read-through, recovery, TTL/index behavior, and graceful degradation.
- Observability coverage validates structured logging, tracing fallbacks, metrics fallbacks, and coordinator wiring.
- Merge-strategy coverage validates built-in strategies plus registry-based custom lookup.

### Integration Coverage

- Full-cycle tests verify trigger → fan-out → child completion → merge → resume.
- Multi-MFJ tests verify `requires` gating and cycle progression.
- Timeout and partial-failure tests verify degraded-but-deterministic resume paths.
- Persistence-backed tests verify completion records are written and can satisfy later requires checks.
- Event-sequence tests verify emitted UI/runtime event order alongside parent context updates.

### Representative Test Files

- `tests/test_workflow_pack_coordinator_mfj.py`
- `tests/test_pack_schema_models.py`
- `tests/test_mfj_persistence_recovery.py`
- `tests/test_mfj_observability.py`
- `tests/test_merge_strategies.py`
- `tests/test_mfj_resume_contract.py`
- `tests/test_mfj_live_smoke.py`

---

## Pack Graph Keys

Workflow-local MFJ files use:

- **Version 3** (`mid_flight_journeys`): typed MFJ config for fan-out/fan-in inside one workflow.

Global cross-workflow journeys are a separate config (`extension_registry.json`), not workflow-local MFJ.

---

## Design Principles

1. **Declarative over imperative**: MFJs are defined in YAML/JSON config, not in Python code. Workflow authors describe *what* should happen; the runtime handles *how*.

2. **AG2-native**: MFJs leverage AG2's existing primitives (context_variables, update_agent_state hooks, handoffs, structured_outputs) rather than building parallel mechanisms. This keeps the framework opinionated and avoids drift.

3. **Parent continuity**: The parent GroupChat is the user's single point of interaction. From the user's perspective, they're having one conversation. The fork-join mechanics are transparent.

4. **Contract-driven**: Input and output contracts make MFJs self-documenting and checked at fan-in. They reduce misconfiguration risk, but they do not replace live AG2 acceptance coverage.

5. **Failure is a first-class concept**: Not all children will succeed. The system has explicit strategies for partial failure rather than silently dropping results or crashing.

6. **Composable**: MFJs are building blocks. A simple workflow might have zero MFJs. A complex build pipeline might have three. The same primitives work for both.
