# Runtime Orchestration Gap Closure Plan

> Goal: close the runtime gaps between current behavior and the architecture described in `docs/1`, while staying runtime-only, engine-agnostic at the runtime boundary, event-driven, and multi-tenant safe.

**Audit date: 2026-03-09** — reflects current codebase state.

---

## 1. Scope

This plan covers runtime-layer changes only:

- transport
- orchestration
- workflow pack loading
- persistence + event wiring
- observability

It does not introduce product/UI feature logic.

---

## 2. Gap Status

### ✅ CLOSED

| Gap | Resolution |
|---|---|
| 2.2 MFJ schema mismatch | `schema.py` defines canonical `MidFlightJourney`, `MFJFanOutConfig`, `MFJFanInConfig`, `MFJContract`. `extra="forbid"` actively rejects `nested_chats` and workflow-level `journeys`. |
| 2.3 Workflow trigger model | `WorkflowPackCoordinator` reads `mid_flight_journeys` exclusively — no `nested_chats`. |
| 2.4 Universal orchestrator | `universal_orchestrator.py` and `change_classifier.py` exist with `EVENT_ROUTE_MAP`, `CHANGE_TYPE_ROUTE_MAP`, `ChangeClassifierAdapter`. |
| 2.6 MFJ persistence + observability | `mfj_persistence.py` and `mfj_observability.py` implemented in `pack/`. |
| Coordinator parsing | Fan-out/fan-in wired to `mid_flight_journeys`, `requires` chain, partial failure policies. |
| Journey orchestrator | `journey_orchestrator.py` reads `GlobalPackGraph` (version 2), clean of legacy keys. |
| Gating | `gating.py` resolves `dependencies[].gating == "required"` from global graph, queries MongoDB. |
| Workflow-level graphs | All `platform/workflows/<name>/_pack/workflow_graph.json` files are version 3, `mid_flight_journeys: []`, no legacy keys. |

### ❌ OPEN

#### Gap A — `pack/config.py` path resolver cannot find `platform/workflows/`

`_repo_root()` searches parent directories for `workflows/` + `mozaiksai/` siblings. No root-level `workflows/` directory exists — workflows live at `platform/workflows/`. The search always falls back to `cwd`, so `get_global_pack_graph_path()` resolves to `<cwd>/workflows/_pack/workflow_graph.json` which does not exist. Pack graph loading returns `None` at runtime.

Also missing: per-workflow override env var (`PACK_WORKFLOW_GRAPH_PATH` / dir override) — only `PACK_GLOBAL_GRAPH_PATH` exists.

Fix target: `mozaiksai/core/workflow/pack/config.py`

#### Gap B — `execution/lifecycle.py` double-wrong path and format

- Path: `base_dir = Path('workflows') / self.workflow_name` → hardcodes non-existent root `workflows/`.
- Format: reads `tools.json` with a `lifecycle_tools` key. Canonical workflow dirs only contain `tools.yaml`. No `tools.json` exists in `platform/workflows/`.
- Result: no lifecycle hooks can load in production for any `platform/workflows/` workflow.

Fix target: `mozaiksai/core/workflow/execution/lifecycle.py`

#### Gap C — `agents/tools.py` uses root `workflows/` path

`Path('workflows') / workflow_name` appears in agent tool discovery — same root path assumption as Gap B.

Fix target: `mozaiksai/core/workflow/agents/tools.py`

#### Gap D — `universal_orchestrator.py` embeds application workflow names

`EVENT_ROUTE_MAP` and `CHANGE_TYPE_ROUTE_MAP` are hardcoded with workflow names (`DecompositionGroupChat`, `ValueEngineGroupChat`, etc.) that do not exist in `platform/workflows/`. This is application-specific logic embedded in the runtime — violates the engine-agnostic, declarative-first constraint (AGENTS.md).

The routing table must be loaded declaratively (e.g. from `platform/app.json` or a `routes.json` config), not hardcoded.

Fix target: `mozaiksai/core/orchestration/universal_orchestrator.py`

#### Gap E — 3 test files missing

- `tests/test_workflow_pack_coordinator_mfj.py`
- `tests/test_journey_orchestrator_global.py`
- `tests/test_mfj_persistence_recovery.py`

#### Gap F — `merge.py` missing `majority_vote` strategy implementation

Schema allows `aggregation_strategy: "majority_vote"` but the merge registry only has `CollectAllMerge`, `MergeBundlesMerge`, `ConcatenateMerge`. No `MajorityVoteMerge` or `custom:<name>` dispatch.

---

## 3. Canonical Target (No Compatibility Layer)

Adopt one canonical config model and remove legacy runtime parsing paths.

## 3.1 Global Pack Graph (Registry + Across-Workflow Journeys)

Canonical file:

- `workflows/_pack/workflow_graph.json`

Canonical keys:

- `version`
- `workflows`
- `journeys` (global workflow sequencing groups)

This is for cross-workflow orchestration only.

## 3.2 Per-Workflow Pack Graph (Mid-Flight Journeys)

Canonical file:

- `workflows/<WorkflowName>/_pack/workflow_graph.json`

Canonical keys:

- `version`
- `mid_flight_journeys`

Only this key is used for fan-out/fan-in within a single workflow run.

## 3.3 Remove Legacy Runtime Keys

Remove runtime support for:

- `nested_chats`
- workflow-level `journeys` as MFJ triggers

If needed, migration is done via one-time converter script and config updates, not runtime fallback.

---

## 4. Canonical Schemas

## 4.1 Global Graph Schema

```json
{
  "version": 2,
  "workflows": [
    { "id": "ValueEngine", "description": "..." },
    { "id": "DesignDocs", "description": "..." }
  ],
  "journeys": [
    {
      "id": "build",
      "steps": [
        "ValueEngine",
        ["AgentGenerator", "DesignDocs"],
        "AppGenerator"
      ]
    }
  ]
}
```

## 4.2 Per-Workflow MFJ Schema

```json
{
  "version": 3,
  "mid_flight_journeys": [
    {
      "id": "planning",
      "trigger_agent": "PatternAgent",
      "trigger_on": "structured_output",
      "requires": [],
      "fan_out": {
        "spawn_mode": "generator_subrun",
        "generator_workflow": "AgentGenerator",
        "child_initial_agent": "WorkflowStrategyAgent",
        "max_children": 10,
        "timeout_seconds": 600,
        "input_contract": {
          "required": ["PatternSelection"], #what is PatternSelection
          "optional": ["InterviewTranscript"]
        },
        "child_context_seed": {
          "is_child_workflow": true
        }
      },
      "fan_in": {
        "resume_agent": "ProjectOverviewAgent",
        "aggregation_strategy": "collect_all",
        "inject_as": "mfj_planning_outputs",
        "on_partial_failure": "resume_with_available",
        "timeout_seconds": 60
      },
      "output_contract": {
        "required": ["WorkflowStrategy"],
        "optional": ["AgentRoster"]
      }
    }
  ]
}
```

---

## 5. How User-Provided Examples Map

## 5.1 Global Example

`.../workflows/_pack/workflow_graph.json` is valid as global pack config with minor normalization:

- keep `workflows`
- keep `journeys.steps` with parallel arrays
- optional: enforce explicit `version`

## 5.2 AgentGenerator Workflow-Level Example

Current sample:

- uses `journeys` with `trigger_agent` + `logic`

Canonical replacement:

- convert to `mid_flight_journeys`
- move trigger behavior into explicit fields (`trigger_agent`, `fan_out`, `fan_in`, contracts)
- remove free-text `logic` as executable config; keep it only as `description` if needed

---

## 6. Remaining Runtime Work (Open Gaps Only)

All items below correspond directly to the open gaps in Section 2.

## Phase A: Fix `pack/config.py` Path Resolver (Gap A)

**File:** `mozaiksai/core/workflow/pack/config.py`

- Change `_repo_root()` to look for `platform/workflows` (or `MOZAIKS_WORKFLOWS_PATH` env) instead of bare `workflows/`.
- Add `MOZAIKS_WORKFLOWS_PATH` (already used by `workflow_manager.py`) as the canonical env override — remove `PACK_GLOBAL_GRAPH_PATH` and use the same env var family.
- `get_global_pack_graph_path()` → `<workflows_root>/_pack/workflow_graph.json`
- `get_workflow_pack_graph_path(name)` → `<workflows_root>/<name>/_pack/workflow_graph.json`
- No fallback to alternative paths.

## Phase B: Fix Lifecycle + Agent Tool Paths (Gaps B + C)

**Files:**
- `mozaiksai/core/workflow/execution/lifecycle.py`
- `mozaiksai/core/workflow/agents/tools.py`

Lifecycle changes:
- Replace `Path('workflows') / self.workflow_name` with resolved `MOZAIKS_WORKFLOWS_PATH / self.workflow_name`.
- Switch from `tools.json` / `lifecycle_tools` key to `tools.yaml` (canonical format).
- Parse lifecycle hooks from the same `tools.yaml` structure used by agent tools — define the hooks section format explicitly (e.g. `lifecycle:` top-level key in `tools.yaml`).
- Sequential execution order per trigger remains the contract.

Agent tools:
- Replace `Path('workflows') / workflow_name` with same `MOZAIKS_WORKFLOWS_PATH` resolver.

## Phase C: Decouple Routing Table from Runtime (Gap D)

**File:** `mozaiksai/core/orchestration/universal_orchestrator.py`

- Remove hardcoded `EVENT_ROUTE_MAP` and `CHANGE_TYPE_ROUTE_MAP` constants.
- Load routing table at startup from a declarative config source (e.g. `platform/app.json` `routing` key, or a dedicated `platform/config/routes.json`).
- `UniversalOrchestrator.__init__` accepts the routing table as a constructor argument (or reads from config loader).
- No workflow names in runtime source code.

## Phase D: Add Missing Merge Strategy (Gap F)

**File:** `mozaiksai/core/workflow/pack/merge.py`

- Implement `MajorityVoteMerge` strategy.
- Implement `custom:<name>` dispatch via the existing registry (call `get_merge_strategy(name)` for names prefixed `custom:`).
- Ensure all `aggregation_strategy` values allowed by `schema.py` have a registered implementation.

## Phase E: Add 3 Missing Test Files (Gap E)

1. `tests/test_workflow_pack_coordinator_mfj.py`
   - MFJ happy path (single trigger → fan-out → fan-in → resume)
   - Multi-MFJ `requires` chain
   - Timeout + partial failure policies (`resume_with_available`, `fail_all`)
   - Output contract validation

2. `tests/test_journey_orchestrator_global.py`
   - Sequential `journeys.steps` advancement
   - Parallel step group dispatch
   - Gating integration (prerequisite not met → blocked)

3. `tests/test_mfj_persistence_recovery.py`
   - `requires` record persists and survives process restart
   - TTL behavior
   - Compound index correctness

---

## 7. Migration Actions (Config + Call Sites)

> Most config migration is complete. Remaining items:

1. ~~Convert workflow-level pack configs from `nested_chats` / `journeys` triggers~~  — **Done**: all `platform/workflows/<name>/_pack/workflow_graph.json` are version 3, `mid_flight_journeys`.
2. ~~Add/normalize global pack config at `workflows/_pack/workflow_graph.json`~~ — **Done**: `platform/workflows/_pack/workflow_graph.json` version 2 exists.
3. Add `lifecycle:` hooks section to `tools.yaml` in any workflow that needs lifecycle hooks, once Phase B lands.
4. Define routing table config structure (Phase C) and populate `platform/app.json` or `platform/config/routes.json` with app-level event routes.
5. Add `routing` section to docs once canonical source is decided.

---

## 8. Acceptance Criteria

## 8.1 Global-Level

- ✅ `GlobalPackGraph` version 2 schema enforced
- ✅ `gating.py` blocks workflows on unmet `dependencies`
- ✅ `journey_orchestrator.py` advances `journeys.steps` (sequential + parallel)
- ❌ Runtime discovers `platform/workflows/_pack/workflow_graph.json` without env override (requires Phase A)

## 8.2 Workflow-Level MFJ

- ✅ `mid_flight_journeys` schema enforced (version 3, `extra="forbid"`)
- ✅ Fan-out spawns children, fan-in merges, parent resumes at configured agent
- ✅ Partial failure policies wired (`resume_with_available`, `fail_all`, `prompt_user`, `retry_failed`)
- ✅ `mfj_persistence.py` persists completion records
- ❌ `requires` chain coverage in tests (requires Phase E)

## 8.3 Universal Routing

- ✅ Structured events route via `EVENT_ROUTE_MAP`
- ✅ Free-text events route through `ChangeClassifier`
- ✅ Dispatch is observable and tenant-safe
- ❌ Routing table loaded from config, not hardcoded (requires Phase C)

## 8.4 Lifecycle

- ❌ Lifecycle hooks load from `platform/workflows/<name>/tools.yaml` (requires Phase B)
- ❌ Sequential execution order enforced (requires Phase B)

---

## 9. Test Matrix

| Test File | Status |
|---|---|
| `tests/test_pack_config_paths.py` | ✅ EXISTS — will pass once Phase A lands |
| `tests/test_pack_schema_models.py` | ✅ EXISTS |
| `tests/test_lifecycle_manager_contract.py` | ✅ EXISTS — uses `tmp_path` synthetic `tools.json`; update for `tools.yaml` in Phase B |
| `tests/test_universal_orchestrator_routing.py` | ✅ EXISTS — add declarative routing table test in Phase C |
| `tests/test_workflow_pack_coordinator_mfj.py` | ❌ **MISSING** — Phase E |
| `tests/test_journey_orchestrator_global.py` | ❌ **MISSING** — Phase E |
| `tests/test_mfj_persistence_recovery.py` | ❌ **MISSING** — Phase E |

---

## 10. Rollout Sequence

1. **Phase A** — Fix `pack/config.py` path resolver → unblocks pack graph loading at runtime
2. **Phase B** — Fix lifecycle + agent tool paths → all `platform/workflows/` hooks become executable
3. **Phase C** — Decouple routing table → removes app logic from runtime
4. **Phase D** — Add `majority_vote` + `custom:<name>` merge strategies
5. **Phase E** — Implement 3 missing test files
6. Update `test_lifecycle_manager_contract.py` for canonical `tools.yaml` format (alongside Phase B)

---

## 11. Definition Of Done

Done means:

- runtime resolves `platform/workflows/` without env overrides for both global and per-workflow pack graphs
- lifecycle hooks load from `tools.yaml` using canonical format; execution order is deterministic
- routing table is declarative — no workflow names embedded in runtime source
- all `aggregation_strategy` values in schema have a registered merge strategy implementation
- all 7 test files in the test matrix exist and pass
- multi-tenant boundaries (`app_id`, `user_id`, `chat_id`, `run_id`) enforced at each orchestration boundary (unchanged)


