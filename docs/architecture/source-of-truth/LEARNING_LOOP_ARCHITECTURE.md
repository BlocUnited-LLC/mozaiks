# Learning Loop Architecture

**Status**: Planning (P5 execution, P0 hooks required now)  
**Date**: February 23, 2026  
**Goal**: Define the end-to-end learning system that makes Mozaiks workflows improve over time — and identify what every layer must expose to make it work.

---

## Why Plan This Now

The learning system is **not a layer** — it's a **loop that touches every layer**. If we don't identify the hooks now, we'll build P0-P2 without the ports that P5 needs, and retrofitting is always harder than designing in.

This document maps:
1. The full loop (what feeds what)
2. Every cross-cutting concern (what touches multiple layers)
3. The minimal hooks each priority phase must expose
4. What the learning system can actually improve
5. The recursive structure (nested optimization loops)
6. How to evaluate natural language output quality

Scope boundary: this document defines public `mozaiks` runtime hooks and learning contracts. Private automation prompt chains and proprietary tool internals are out of scope.

---

## The Full Loop (One Picture)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        THE LEARNING LOOP                                    │
│                                                                             │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐             │
│  │  1. GENERATE  │─────▶│  2. EXECUTE   │─────▶│  3. MEASURE   │            │
│  │  (Platform)   │      │  (Core)       │      │  (Core+Plat)  │            │
│  └──────────────┘      └──────────────┘      └──────┬───────┘             │
│         ▲                                           │                      │
│         │                                           ▼                      │
│  ┌──────┴───────┐      ┌──────────────┐      ┌──────────────┐             │
│  │  6. IMPROVE   │◀─────│  5. SCORE     │◀─────│  4. PERSIST   │            │
│  │  (Platform)   │      │  (Platform)   │      │  (Core+Graph) │            │
│  └──────────────┘      └──────────────┘      └──────────────┘             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Step by Step

| Step | Owner | What Happens | Output |
|------|-------|-------------|--------|
| **1. Generate** | External consumer layer | AI agents produce workflow bundles (YAML + stubs) | Workflow files on disk |
| **2. Execute** | Core | Runtime loads YAML, runs agents, streams events | Event stream (MEP) |
| **3. Measure** | Core emits, external consumer layer interprets | Runtime emits telemetry events; consumer layer collects generation-level metrics | Raw telemetry data |
| **4. Persist** | Core (event store) + FalkorDB | Telemetry stored in event store; scores written to graph | Durable metrics |
| **5. Score** | External consumer layer | Computes quality scores from raw telemetry | Quality ratings per pattern, per workflow, per agent config |
| **6. Improve** | External consumer layer | Injects scores into automation context for next generation | Better generation decisions |

---

## What the Learning System Can Improve

This is the part that spans everything. The learning system doesn't just improve one thing — it improves **six different concerns**, and each cuts across different files/layers.

### Improvement Targets

| # | What Improves | What Data Drives It | What Changes in Next Generation |
|---|--------------|--------------------|---------------------------------|
| 1 | **Pattern Selection** | Which patterns have high completion rates | PatternAgent picks patterns with proven track records |
| 2 | **Agent Design** | Turn counts, error rates per agent config | Fewer agents, better system prompts, tighter handoffs |
| 3 | **Tool Wiring** | Tool success/failure rates, tool call counts | Remove broken tools, fix argument schemas, add missing tools |
| 4 | **Workflow Decomposition** | Pack utilization (which workflows in a pack actually get used) | Stop over-decomposing; merge under-used workflows |
| 5 | **Graph Injection Rules** | Which injected context actually gets used by agents | Prune useless injection rules, add missing ones |
| 6 | **Pack Orchestration** | Journey completion rates, gate failure rates | Fix broken gates, reorder journeys, remove unnecessary prerequisites |

### Cross-Cut Map (Which Files Are Affected)

```
                              WHAT GETS BETTER
                    ┌─────┬─────┬──────┬──────┬───────┬───────┐
                    │ Pat-│Agent│ Tool │Decom-│ Graph │ Pack  │
                    │tern │ De- │ Wir- │posi- │ Inj.  │ Orch. │
FILE                │ Sel │sign │ ing  │tion  │ Rules │ Graph │
────────────────────┼─────┼─────┼──────┼──────┼───────┼───────┤
orchestrator.yaml   │     │  ✓  │      │      │       │       │
agents.yaml         │     │  ✓  │      │      │       │       │
tools.yaml          │     │     │  ✓   │      │       │       │
stubs/tools/*.py    │     │     │  ✓   │      │       │       │
graph_injection.yaml│     │     │      │      │   ✓   │       │
workflow_graph.json │     │     │      │  ✓   │       │   ✓   │
PatternAgent prompt │  ✓  │     │      │  ✓   │       │       │
AgentRosterAgent    │     │  ✓  │      │      │       │       │
ToolPlanningAgent   │     │     │  ✓   │      │       │       │
────────────────────┴─────┴─────┴──────┴──────┴───────┴───────┘

✓ = This file changes based on learning from that improvement target
```

---

## The Three Telemetry Layers (Detailed)

### Layer A: Runtime Telemetry (Core Owns)

Core sees every workflow run. These are **standard MEP events** in the `telemetry.*` namespace.

#### Events Core Must Emit

| Event | When | Payload |
|-------|------|---------|
| `telemetry.run.started` | Run begins | `run_id`, `workflow_name`, `user_id`, `timestamp` |
| `telemetry.run.completed` | Run finishes successfully | `run_id`, `duration_ms`, `total_turns`, `agents_used[]` |
| `telemetry.run.failed` | Run errors out | `run_id`, `error_type`, `error_message`, `turn_number`, `agent_name` |
| `telemetry.run.abandoned` | No activity for N minutes, then session closes | `run_id`, `last_active_turn`, `idle_duration_ms` |
| `telemetry.agent.turn_summary` | After each agent turn | `run_id`, `agent_name`, `turn_number`, `duration_ms`, `tools_called[]`, `success` |
| `telemetry.tool.outcome` | After each tool execution | `run_id`, `tool_name`, `success`, `duration_ms`, `error_type?` |
| `telemetry.hitl.requested` | UI tool requests human input | `run_id`, `tool_name`, `agent_name` |
| `telemetry.hitl.resolved` | Human provides input | `run_id`, `tool_name`, `wait_duration_ms` |
| `telemetry.run.summary` | Post-run aggregation (single event) | See below |

#### `telemetry.run.summary` Event Shape

This is the **key event** — a single aggregated snapshot emitted after every run completes (or fails). It's the primary input for scoring.

```json
{
  "type": "telemetry.run.summary",
  "run_id": "run_abc123",
  "workflow_name": "ITSupportBot",
  "pattern_id": 6,
  "pattern_name": "context_aware_routing",
  "user_id": "user_456",
  "app_id": "app_789",

  "outcome": "completed",

  "timing": {
    "total_duration_ms": 45200,
    "first_response_ms": 1200,
    "avg_turn_duration_ms": 3400
  },

  "agents": {
    "count": 4,
    "turns_by_agent": {
      "Router": 3,
      "NetworkSpecialist": 5,
      "HardwareSpecialist": 0,
      "SoftwareSpecialist": 2
    }
  },

  "tools": {
    "total_calls": 12,
    "success_count": 11,
    "failure_count": 1,
    "failures": [
      { "tool": "lookup_inventory", "error": "timeout", "turn": 4 }
    ]
  },

  "hitl": {
    "requests": 1,
    "avg_wait_ms": 8500
  },

  "checkpoints": {
    "count": 3,
    "resumes": 0
  }
}
```

### Layer B: Generation Telemetry (External Consumer Layer)

The external consumer layer tracks what automation produces and what users do with it. **Core does not see this** — it's app-level.

| Metric | How Consumer Layer Collects It | What It Reveals |
|--------|--------------------------|-----------------|
| **Generated file hash** | Hash each YAML file at generation time | Baseline for diff detection |
| **Post-edit diff** | Diff user's version against generated version | What the generator got wrong |
| **Most-edited files** | Which files have largest diffs | Where generation is weakest |
| **Time to first run** | Generation timestamp → first `telemetry.run.started` | Did the user struggle to get it running? |
| **Abandoned generations** | Generated but never executed (no matching run events) | Was the output useless? |
| **Regeneration requests** | User explicitly asks to regenerate | Generator failed badly enough to restart |
| **Pattern selection frequency** | Count of `pattern_id` across generations | Which patterns are popular |
| **Pack utilization** | In multi-workflow packs: which workflows have runs? | Over-decomposition detection |

### Layer C: Quality Scoring (External Consumer Layer Computes, Persists to Graph)

The external consumer layer combines Layer A + Layer B into composite scores.

#### Per-Pattern Score

```
pattern_quality = weighted_average(
    completion_rate         × 0.25,   # Core: does it finish?
    low_error_rate          × 0.20,   # Core: does it break?
    low_post_edit_rate      × 0.20,   # Platform: was generation accurate?
    efficient_turn_count    × 0.15,   # Core: is it efficient?
    low_hitl_rate           × 0.10,   # Core: is it autonomous?
    low_abandon_rate        × 0.10,   # Platform: do users give up?
)
```

#### Per-Workflow Score

```
workflow_quality = weighted_average(
    run_completion_rate     × 0.30,   # Core: runs that finish
    tool_success_rate       × 0.25,   # Core: tools that work
    efficient_turns         × 0.20,   # Core: agent efficiency
    user_satisfaction       × 0.15,   # Platform: low edit rate + no regeneration
    responsiveness          × 0.10,   # Core: time to completion
)
```

#### Per-Agent-Config Score

```
agent_quality = weighted_average(
    low_error_rate          × 0.30,   # Core: agent doesn't cause errors
    efficient_turns         × 0.25,   # Core: completes task in few turns
    tool_usage_accuracy     × 0.25,   # Core: calls right tools, they succeed
    low_handoff_bounce      × 0.20,   # Core: doesn't bounce between agents
)
```

---

## Persistence Architecture (Where Scores Live)

Scores need to be **durable** (survive restarts) and **queryable** (injection rules need to read them). Two persistence paths:

### Path 1: Event Store (Core Owns)

Raw telemetry events go into core's existing event persistence. This is append-only, sequenced, and already built.

- Every `telemetry.*` event gets persisted like any other `EventEnvelope`
- Platform can query historical telemetry via core's API
- No new infrastructure needed — uses existing `EventStore`

### Path 2: Knowledge Graph (FalkorDB — Core Provides, Platform Populates)

Computed scores go into FalkorDB. This is the **queryable** persistence that injection rules read from.

```cypher
// Pattern quality node
(:PatternScore {
  pattern_id: int,
  pattern_name: string,
  quality_score: float,       // 0.0 - 1.0
  sample_size: int,           // How many runs contributed
  completion_rate: float,
  error_rate: float,
  avg_turns: float,
  post_edit_rate: float,
  last_computed: datetime
})

// Workflow quality node
(:WorkflowScore {
  workflow_name: string,
  app_id: string,
  quality_score: float,
  run_count: int,
  tool_success_rate: float,
  avg_duration_ms: int,
  last_computed: datetime
})

// Agent config quality node
(:AgentConfigScore {
  agent_name: string,
  model: string,
  pattern_id: int,
  quality_score: float,
  avg_turns: float,
  error_rate: float,
  sample_size: int,
  last_computed: datetime
})

// Relationships
(:PatternScore)-[:USED_IN]->(:WorkflowScore)
(:AgentConfigScore)-[:PART_OF]->(:WorkflowScore)
(:WorkflowScore)-[:GENERATED_BY]->(:GenerationRun {timestamp, generator_version})
```

### How Injection Reads These Scores

In a generator workflow's `graph_injection.yaml`:

```yaml
injection_rules:
  - name: "inject_pattern_quality"
    agents: ["PatternAgent"]
    queries:
      - id: "top_patterns"
        cypher: |
          MATCH (ps:PatternScore)
          WHERE ps.sample_size >= 10
          RETURN ps.pattern_name, ps.quality_score,
                 ps.completion_rate, ps.avg_turns, ps.post_edit_rate
          ORDER BY ps.quality_score DESC
          LIMIT 10
        inject_as: "proven_patterns"
        format: "markdown"

  - name: "inject_agent_configs"
    agents: ["AgentRosterAgent"]
    queries:
      - id: "best_agent_configs"
        cypher: |
          MATCH (acs:AgentConfigScore)-[:PART_OF]->(ws:WorkflowScore)
          WHERE acs.quality_score > 0.7 AND acs.sample_size >= 5
          RETURN acs.agent_name, acs.model, acs.avg_turns,
                 ws.workflow_name, acs.quality_score
          ORDER BY acs.quality_score DESC
          LIMIT 20
        inject_as: "proven_agent_configs"
        format: "json"

  - name: "inject_tool_reliability"
    agents: ["ToolPlanningAgent"]
    queries:
      - id: "reliable_tools"
        cypher: |
          MATCH (ws:WorkflowScore)
          WHERE ws.tool_success_rate > 0.9
          RETURN ws.workflow_name, ws.tool_success_rate
          ORDER BY ws.tool_success_rate DESC
        inject_as: "reliable_tool_configs"
        format: "list"
```

---

## Cross-Cutting Hooks Required Per Phase

This is the critical section. What must each priority phase **expose** so the learning loop isn't blocked later?

### P0: Core Runtime (Must Expose)

| Hook | Why P5 Needs It | Cost to Add Now | Cost to Retrofit |
|------|-----------------|-----------------|------------------|
| `telemetry.*` event namespace reserved | Scores need raw data | Zero (just reserve the namespace) | Breaking change to event taxonomy |
| `telemetry.run.summary` emitted post-run | Primary input for all scoring | Small (aggregate existing events) | Medium (must audit all run-end paths) |
| `telemetry.tool.outcome` emitted per tool | Tool reliability scoring | Small (already have tool execution) | Medium (must instrument all tool adapters) |
| Event store persists `telemetry.*` | Historical queries for scoring | Zero (already persists all events) | Zero |
| FalkorDB connection in docker-compose | Graph injection reads scores | Small (add service definition) | Small |

### P1: Docs + Examples (Must Expose)

| Hook | Why P5 Needs It | Cost to Add Now | Cost to Retrofit |
|------|-----------------|-----------------|------------------|
| Document `telemetry.*` events in taxonomy | OSS users understand what's measured | Small (add to EVENT_TAXONOMY.md) | Small |
| Example `graph_injection.yaml` with score queries | Shows the pattern | Small (example file) | Small |

### P2: Platform Generation (Must Expose)

| Hook | Why P5 Needs It | Cost to Add Now | Cost to Retrofit |
|------|-----------------|-----------------|------------------|
| Hash generated files at generation time | Diff detection for post-edit rate | Small (hash before write) | Medium (must intercept all generation writes) |
| Log `pattern_id` with every generation | Pattern frequency tracking | Small (already in PatternSelection) | Small |
| Store generation metadata | Links a run back to its generator | Small (one DB row per generation) | Hard (no way to correlate runs to generations after the fact) |

### P3: Pack Orchestration (Must Expose)

| Hook | Why P5 Needs It | Cost to Add Now | Cost to Retrofit |
|------|-----------------|-----------------|------------------|
| `telemetry.journey.step_completed` | Journey completion rate | Small | Small |
| `telemetry.gate.evaluated` (pass/fail) | Gate failure rate | Small | Small |

### P4: Graph Injection (Must Expose)

| Hook | Why P5 Needs It | Cost to Add Now | Cost to Retrofit |
|------|-----------------|-----------------|------------------|
| `telemetry.injection.executed` (rule name, result count) | Know which injections are useful | Small | Medium |
| `telemetry.mutation.executed` (rule name) | Know which mutations fire | Small | Small |

---

## The "Intelligence Stack" (How It All Connects)

Reading bottom-to-top, each layer feeds the one above:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  LAYER 6: SELF-IMPROVEMENT (P5)                                         │
│  Automated A/B testing of generation strategies                         │
│  "Try Pattern A vs Pattern B for this request type, measure, pick best" │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│  Needs: scoring, historical telemetry, injection                        │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ reads scores
┌──────────────────────────────────────▼──────────────────────────────────┐
│  LAYER 5: SCORING (P5)                                                  │
│  Compute quality scores per pattern / workflow / agent config           │
│  Write scores to FalkorDB                                               │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│  Needs: runtime telemetry + generation telemetry                        │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ reads raw telemetry
┌──────────────────────────────────────▼──────────────────────────────────┐
│  LAYER 4: PERSISTENCE (P0 event store + P4 FalkorDB)                   │
│  Raw telemetry → Event Store (append-only, core)                        │
│  Computed scores → FalkorDB (queryable, graph injection reads)          │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│  Needs: telemetry events to persist, graph DB to write scores           │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ receives events
┌──────────────────────────────────────▼──────────────────────────────────┐
│  LAYER 3: TELEMETRY (P0 runtime + P2 generation)                       │
│  Core emits: run summaries, tool outcomes, HITL events                  │
│  Platform emits: generation diffs, pattern selections, abandonment      │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│  Needs: hook points in runtime + generation pipeline                    │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ observes
┌──────────────────────────────────────▼──────────────────────────────────┐
│  LAYER 2: EXECUTION (P0)                                                │
│  Core runs workflows from YAML + stubs                                  │
│  Every run produces event stream (MEP)                                  │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│  Needs: working runtime, event streaming, persistence                   │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ runs output of
┌──────────────────────────────────────▼──────────────────────────────────┐
│  LAYER 1: GENERATION (P2)                                               │
│  Consumer-side automation produces workflow bundles                     │
│  PatternAgent picks patterns, build agents produce YAML + stubs         │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│  Needs: automation agents, pattern library, target runtime contract     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Nested Optimization Loops (Recursive Structure)

The system has a recursive structure that makes it feel like infinite complexity. It's not infinite — there are exactly **three nesting levels** (dolls). Each doll has the same shape: agents + prompts + tools → output → measure → improve. But each operates on a different target.

### The Three Dolls

```
┌─────────────────────────────────────────────────────────────────────────┐
│  DOLL 3: Meta-Learning (Outermost)                                      │
│  "Is the generation workflow itself any good?"                          │
│                                                                         │
│  Agents: Human engineers + eventually automated prompt tuners           │
│  Input:  Doll 2 scores (how good are generated workflows?)              │
│  Output: Improved generation workflow                                   │
│  Measure: Are Doll 2 scores trending up over time?                      │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  DOLL 2: Generation Quality (Middle)                              │  │
│  │  "Is the generated output any good?"                              │  │
│  │                                                                   │  │
│  │  Agents: PatternAgent, AgentRosterAgent, ToolPlanningAgent, etc.  │  │
│  │  Input:  User request ("build me an IT support bot")              │  │
│  │  Output: Workflow bundle (YAML + stubs)                           │  │
│  │  Measure: Does the generated workflow run well? (Doll 1 scores)   │  │
│  │                                                                   │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  DOLL 1: Runtime Quality (Innermost)                        │  │  │
│  │  │  "Does this workflow perform well for end users?"            │  │  │
│  │  │                                                             │  │  │
│  │  │  Agents: The generated agents (e.g., Router, Specialist)    │  │  │
│  │  │  Input:  End user messages                                  │  │  │
│  │  │  Output: Agent responses, tool calls, task completion       │  │  │
│  │  │  Measure: Completion rate, error rate, turns, HITL, etc.    │  │  │
│  │  └─────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Each Doll's Learning Loop

The same loop shape repeats at every level:

| | Doll 1: Runtime | Doll 2: Generation | Doll 3: Meta-Learning |
|---|---|---|---|
| **Who are the agents?** | Generated agents (Router, Specialist, etc.) | Automation agents (PatternAgent, AgentRoster, etc.) | Engineers + automated tuners |
| **What are the prompts?** | `agents.yaml` system prompts | Automation agent system prompts (consumer code) | Prompt optimization strategies |
| **What are the tools?** | `tools.yaml` + `stubs/tools/*.py` | Generation tools (file writers, validators) | Scoring pipelines, A/B frameworks |
| **What is the output?** | Agent responses to end users | Workflow bundles (YAML + stubs) | Improved automation prompts |
| **What gets measured?** | Completion rate, errors, turns, HITL | First-run success, post-edit rate, user satisfaction | Trend of Doll 2 scores over time |
| **What improves?** | Nothing (this is what Doll 2 improves) | Generated workflow quality | Generation quality itself |
| **Loop speed** | Real-time (every run) | Per-generation (minutes-hours) | Per-sprint (weeks) |
| **Who owns it?** | Core (runtime) | Consumer automation layer | Consumer engineering layer |

### Why This Is NOT Infinite Recursion

The dolls stop at 3 because:

- **Doll 1** (runtime) doesn't improve itself — it just executes. Improvement comes from Doll 2.
- **Doll 2** (generation) is improved by Doll 3, but Doll 3 is initially **human-driven** (engineers tuning prompts).
- **Doll 3** (meta-learning) eventually becomes partially automated, but it has a **human gatekeeper**. Nobody tunes the meta-learning automatically.

The recursion terminates because **Doll 3's learning loop includes a human**. There is no Doll 4.

### The Data Flow Between Dolls

```
Doll 1 (runtime)                 Doll 2 (generation)           Doll 3 (meta)
─────────────────                ──────────────────            ─────────────────

End user uses workflow     ──→   telemetry.run.summary    ──→   "Doll 2 scores
  └─ completion?                   └─ per-pattern scores         are trending
  └─ errors?                       └─ per-agent scores           down for
  └─ turns?                        └─ post-edit rates            Pattern X"
  └─ tools work?                                                    │
                                        │                           │
                                        ▼                           ▼
                                 graph_injection.yaml        Engineer examines
                                   injects scores into       PatternAgent prompt
                                   PatternAgent context      and rewrites it
                                        │                           │
                                        ▼                           ▼
                                 Next generation picks       Improved generator
                                 better patterns,            produces better
                                 better agent configs        patterns + configs
                                        │
                                        ▼
                                 Generated workflow
                                 runs better (Doll 1)
```

---

## Prompt Quality: The Hard Evaluation Problem

The agents in all three dolls are fundamentally **prompts + tools**. Measuring "did the prompt work?" in natural language is the hardest sub-problem in the loop. There is no single metric — you need a structured approach.

### What "Prompt Quality" Actually Means Per Doll

| Doll | Prompt Lives In | "Good Prompt" Means | Measurable Proxy |
|------|----------------|---------------------|------------------|
| **1 (Runtime)** | `agents.yaml` → `system_prompt` | Agent solves user's problem efficiently | Completion rate, low turns, low errors, low HITL |
| **2 (Generation)** | Automation agent code (consumer layer) | Agent produces correct YAML + stubs that run well | First-run success rate, low post-edit rate, Doll 1 scores |
| **3 (Meta)** | Prompt tuning strategy | Prompt change improves Doll 2 scores | Before/after comparison of Doll 2 scores |

### Evaluating Generated Prompts (Doll 2 → Doll 1)

When an automation workflow produces `agents.yaml`, it's writing prompts for Doll 1 agents. How do you know if those prompts are any good?

#### Structural Evaluation (Cheap, Immediate)

Can be checked at generation time, before runtime:

| Check | What It Tests | How |
|-------|--------------|-----|
| **Syntax valid** | Is the YAML parseable? | Schema validation |
| **System prompt present** | Does every agent have a system prompt? | Field check |
| **Prompt references tools** | Does the prompt mention the tools available to it? | String matching |
| **Prompt has clear role** | Does it specify what the agent does vs. doesn't do? | Structural rubric |
| **Handoff instructions present** | Does the prompt explain when to hand off? | Keyword detection |
| **No contradictions** | Does the prompt contradict the orchestrator config? | Cross-file validation |

These can be automated at P2 as a **generation validator**. No LLM needed.

#### Behavioral Evaluation (Medium, At Runtime)

Observed during Doll 1 execution:

| Signal | What It Reveals | Source |
|--------|----------------|--------|
| **Agent never gets called** | Prompt or handoff config makes agent unreachable | `telemetry.agent.turn_summary` (zero turns) |
| **Agent always errors** | Prompt gives bad instructions | `telemetry.run.failed` + `agent_name` |
| **Agent calls wrong tools** | Prompt doesn't guide tool selection well | `telemetry.tool.outcome` mismatches |
| **Excessive handoff bouncing** | Agents hand off back and forth without progress | Turn sequence analysis |
| **Agent asks user to repeat** | Prompt doesn't preserve context or instructions are vague | Chat content analysis (expensive) |
| **Task completed in 2 turns vs 10** | Prompt is efficient or wasteful | Turn count per task type |

These are available from Doll 1 telemetry. No LLM-as-judge needed — just statistical analysis of telemetry events.

#### Semantic Evaluation (Expensive, Periodic)

For deeper quality signals that can't be derived from telemetry alone:

| Method | What It Does | Cost | When to Use |
|--------|-------------|------|-------------|
| **LLM-as-Judge** | A separate LLM rates the generated prompt against a rubric | Medium (API call per evaluation) | Periodic sampling, not every generation |
| **A/B Testing** | Generate two variants, run both, compare Doll 1 scores | High (double the runs) | Only for significant prompt changes |
| **User Feedback** | Thumbs up/down on workflow quality | Free (but sparse and biased) | Always collect, low weight in scoring |
| **Expert Review** | Human reviews generated prompts against best practices | Expensive (human time) | Calibration — use to validate automated metrics |

### The Prompt as a Structured Object

The key insight: **don't treat prompts as opaque strings**. Treat them as structured objects with evaluable components.

A system prompt for a generated agent has these components:

```yaml
# Conceptual structure (not literal YAML — this is how we think about it)
prompt_structure:
  role_definition:        # "You are a network specialist..."
    clarity: evaluable    # Is the role specific enough?
    scope: evaluable      # Are boundaries defined?

  context_instructions:   # "You have access to the following context..."
    completeness: evaluable  # Does it mention all injected context?

  tool_instructions:      # "Use the diagnostic_tool when..."
    coverage: evaluable   # Does it mention all available tools?
    guidance: evaluable   # Does it say WHEN to use each tool?

  handoff_instructions:   # "Hand off to SecuritySpecialist when..."
    specificity: evaluable  # Are handoff conditions clear?
    completeness: evaluable # Are all handoff targets mentioned?

  output_format:          # "Respond in this format..."
    present: evaluable    # Is output format specified?
    matches_schema: evaluable  # Does it match structured_outputs.yaml?

  constraints:            # "Do not..."
    safety: evaluable     # Are safety boundaries set?
    scope_limiting: evaluable  # Does it prevent scope creep?
```

If the generator produces prompts as structured components, each component can be independently scored, and the learning loop can identify **which part** of a prompt is weak, not just "the prompt is bad."

### Evaluation Tiers (What to Build When)

| Tier | Type | When to Build | Blocks |
|------|------|--------------|--------|
| **Tier 0** | Structural validation (schema, field presence) | P2 (generation) | Nothing — do it at generation time |
| **Tier 1** | Behavioral analysis (telemetry statistics) | P5 (needs runtime data) | Needs Doll 1 telemetry flowing |
| **Tier 2** | LLM-as-Judge (periodic sampling) | P5+ (needs significant data) | Needs Tier 1 as baseline to validate against |
| **Tier 3** | Automated prompt tuning (Doll 3 automation) | P5++ (research territory) | Needs Tier 2 + human-gated deployment |

---

## Concrete P0 Deliverables (What to Build Now for P5 Later)

These are cheap to add at P0 and expensive to retrofit:

### 1. Reserve `telemetry.*` Event Namespace

Add to the standard event catalog in `EVENT_TAXONOMY.md`:

```
telemetry.run.started
telemetry.run.completed
telemetry.run.failed
telemetry.run.abandoned
telemetry.run.summary          ← the key event
telemetry.agent.turn_summary
telemetry.tool.outcome
telemetry.hitl.requested
telemetry.hitl.resolved
telemetry.journey.step_completed
telemetry.gate.evaluated
telemetry.injection.executed
telemetry.mutation.executed
```

### 2. Emit `telemetry.run.summary` Post-Run

One function call at the end of every run lifecycle. Aggregates what already happened during the run into a single event. ~50 lines of code.

### 3. Store `generation_id` on Every Run

When platform creates a run, it passes `generation_id` (or null for manual workflows). Core stores it. This is the **only** link between "what was generated" and "how it performed." If this link doesn't exist, no amount of P5 work can correlate generation quality to runtime outcomes.

### 4. FalkorDB in docker-compose

Even if graph injection isn't built yet, having FalkorDB in the dev stack means P4/P5 isn't blocked on infra decisions.

---

## Open Questions (Must Resolve Before P5 Execution)

| # | Question | Impact | When to Decide |
|---|----------|--------|----------------|
| 1 | Who computes scores — a background job, or on-demand at generation time? | Latency of feedback loop | P3 |
| 2 | Should OSS users see quality scores, or only platform? | If OSS gets scores, core must compute them. If platform-only, core just emits raw telemetry. | P2 |
| 3 | How large must `sample_size` be before a score is trusted? | Affects cold-start behavior — new patterns have no scores | P5 |
| 4 | Should the learning loop be per-tenant or global? | Security + data isolation vs. learning speed | P3 |
| 5 | Can a pattern's quality score go *down*? | Prevents lock-in to early winners; requires score decay | P5 |
| 6 | Does the `telemetry.run.summary` include user satisfaction signals (thumbs up/down)? | Explicit feedback vs. implicit signals only | P1 |
| 7 | Where does generation telemetry live? Platform DB? Core event store? Both? | Determines whether scoring requires cross-system queries | P2 |
| 8 | How is prompt quality evaluated — LLM-as-judge, structured rubric, user signals, or all three? | Determines the Doll 2 scoring mechanism | P2 |
| 9 | Do generator agent prompt improvements require human approval, or can the system self-modify? | Safety boundary for the meta-learning loop | P5 |
| 10 | Should prompt version history be stored in FalkorDB or a separate versioning system? | Affects rollback capability for Doll 2 | P3 |

---

## Relationship to Other Architecture Docs

| Doc | Relationship |
|-----|-------------|
| [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md) | Defines the YAML files and runtime dispatchers this system measures |
| [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md) | Shows how telemetry events physically flow between processes (Trace 5) |
| [Graph Injection Contract](GRAPH_INJECTION_CONTRACT.md) | Defines how scores get injected into automation agents |
| [Orchestration & Decomposition](../orchestration-and-decomposition.md) | Defines pack structure and decomposition logic that scoring evaluates |
| EVENT_TAXONOMY.md | Must include `telemetry.*` namespace |

