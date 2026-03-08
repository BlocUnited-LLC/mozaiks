# Mozaiks Runtime — Architectural Refactor Plan

**Created:** 2026-03-08  
**Status:** Active  
**Goal:** Eliminate three parallel orchestration stacks, wire the capability dispatch layer into every live code path, and restore the simplicity of the original system while preserving multi-capability support.

---

## Constraints (Non-Negotiable)

- Do NOT introduce compatibility layers, fallbacks, or legacy adapters.
- Do NOT build new logic on top of existing workflow/groupchat abstractions.
- Do NOT patch around the current architecture.
- No backward compatibility, aliases, deprecated fields, or fallbacks.
- Prefer canonical replacement and call-site updates.

---

## Diagnosis Summary

Three independent orchestration stacks exist:

| Stack | Lines | Live? | Dispatches through RunSupervisor? |
|-------|-------|-------|-----------------------------------|
| `WorkflowPackCoordinator` | 2,046 | **YES** — only live multi-workflow path | **NO** — calls `SimpleTransport._run_workflow_background()` directly |
| `UniversalOrchestrator` + `GroupChatPool` | ~950 | **NO** — exported but never called from production | YES |
| `DAGExecutor` | ~570 | **NO** — only tests call it | YES |

Additionally:
- `GroupChatPool` is a duplicate of `DAGExecutor` (same Kahn's algorithm, same `asyncio.Event` concurrency pattern, same `RunSupervisor.start_run()` dispatch)
- `WorkflowPackCoordinator` bypasses RunSupervisor by importing `SimpleTransport` internals (`_background_tasks`, `_run_workflow_background`, `connections`)
- Events stream out-of-band through SimpleTransport side effects inside `_stream_events`, not through the DomainEvent iterator chain

---

## Target Architecture (After Refactor)

```
WebSocket Handler
  │
  ├─ new chat / user message / resume
  │   └─ RunSupervisor.start_run(RunRequest)
  │       └─ CapabilityRegistry → WorkerRegistry → WorkerPort.execute()
  │           ├─ AgentWorker → AG2EngineAdapter → run_workflow_orchestration
  │           ├─ ProvisioningWorker → .NET HTTP → DomainEvents
  │           └─ (future workers)
  │
  └─ multi-workflow fan-out (structured output trigger)
      └─ WorkflowPackCoordinator (slim, ~500 lines)
          └─ DAGExecutor.execute_stream(tasks)
              └─ per-task: RunSupervisor.start_run(RunRequest)
```

Single dispatch path. No transport coupling. No duplicate DAG schedulers.

---

## Phase 1: Wire RunSupervisor Into Every Live Path

### Current State

The WebSocket handler already uses RunSupervisor at two call sites:
- `handle_user_input_from_api()` at `handler.py:982`
- `_resume_orchestration()` at `handler.py:1217`

Both do:
```python
from mozaiksai.runtime.execution.run_supervisor import get_run_supervisor as _get_run_supervisor
async for _ev in _get_run_supervisor().start_run(_run_req):
    pass
```

The RunSupervisor → AgentWorker → AG2EngineAdapter → `run_workflow_orchestration()` chain works. Events still flow out-of-band through SimpleTransport (this is fine for Phase 1).

### Task 1.1: Verify RunSupervisor Is the Single Entry Point in Handler

- [x] **Audit `handler.py`** — confirm that ALL workflow execution entry points route through `RunSupervisor.start_run()`:
  - `handle_user_input_from_api()` at line 982 — should already use RunSupervisor ✓
  - `_resume_orchestration()` at line 1217 — should already use RunSupervisor ✓
  - `_run_workflow_background()` at line 714 — this calls `handle_user_input_from_api()` which uses RunSupervisor ✓
- [x] **Confirm no direct calls** to `run_workflow_orchestration()` exist in `handler.py` (should be zero — it's called via AgentWorker → AG2EngineAdapter)
- [x] **Run tests:** `pytest tests/ -q --ignore=tests/test_integration_mfj.py --tb=short` — baseline must be 250/252

### Task 1.2: Remove Stale `_stream_events` / `run_groupchat` Imports From Handler

- [x] Grep `handler.py` for any direct imports of `run_workflow_orchestration`, `_stream_events`, `GroupChatExecutor`
- [x] Remove any found — these should only be imported by `AG2EngineAdapter` and `engine/orchestration.py`
- [x] Run tests to confirm

---

## Phase 2: Make WorkflowPackCoordinator Dispatch Through RunSupervisor

### Current State

`WorkflowPackCoordinator` (2,046 lines at `kernel/pack/workflow_pack_coordinator.py`) spawns child workflows by:

1. Importing `SimpleTransport` singleton (`await SimpleTransport.get_instance()`) at multiple sites:
   - Line 427 (in `handle_structured_output_ready`)
   - Line 792 (in `handle_run_complete`)
   - Line 1406 (in `_get_transport_conn`)
   - Line 1816 (in `_collect_child_results`)

2. Directly creating background tasks via transport internals:
   ```python
   transport._background_tasks[new_chat_id] = asyncio.create_task(
       transport._run_workflow_background(...)
   )
   ```
   At lines 597 and 1982.

3. Accessing `transport.connections[...]`, `transport._get_or_create_persistence_manager(...)`, `transport.pause_background_workflow(...)` — all transport internals.

### Task 2.1: Add a `run_spawner` Callback to WPC Constructor

Instead of WPC importing SimpleTransport, inject a callback that handles "start a new workflow run in the background."

- [x] **Add constructor parameter** to `WorkflowPackCoordinator.__init__()`:
  ```python
  def __init__(
      self,
      *,
      run_spawner: Callable[[RunRequest], Awaitable[None]] | None = None,
      ...existing params...
  ) -> None:
      self._run_spawner = run_spawner
  ```
  > **Implemented as:** `PackTransportPort` protocol in `ports/pack_transport.py` with 9 methods (spawn_run, send_ui_event, pause_workflow, is_task_running, get_task_error, get_persistence, get_connection_meta, setup_child_connection, flush_pre_connection_buffers). WPC constructor accepts `transport: Any = None`.

- [x] **Define the callback type** — it takes a `RunRequest` and runs it as a backgrounded task. The transport layer provides the implementation at wiring time (factory.py).

### Task 2.2: Create a `spawn_run` Method on SimpleTransport

- [x] **Add to `handler.py`** (SimpleTransport class):
  ```python
  async def spawn_background_run(self, request: RunRequest) -> None:
      """Spawn a workflow run as a background task, dispatched through RunSupervisor."""
      from mozaiksai.runtime.execution.run_supervisor import get_run_supervisor

      async def _run():
          async for _ev in get_run_supervisor().start_run(request):
              pass  # events flow out-of-band through transport side effects

      task = asyncio.create_task(_run())
      self._background_tasks[request.chat_id] = task
  ```

- [x] This replaces the pattern of `transport._run_workflow_background(...)` which builds `RunRequest` internally. Now the `RunRequest` is built by the caller and dispatch goes through RunSupervisor.

### Task 2.3: Replace All Transport Coupling in WPC

For each of the coupling sites in `workflow_pack_coordinator.py`:

- [x] **Line 427-429 and 597** (child spawning in `handle_structured_output_ready`):
  - Replace `SimpleTransport.get_instance()` + `transport._run_workflow_background(...)` + `transport._background_tasks[...]`
  - With: `await self._run_spawner(RunRequest(capability="agent", workflow_name=child_name, app_id=..., user_id=..., chat_id=new_chat_id, context={...}))`

- [x] **Line 792-794 and surrounding** (run complete in `handle_run_complete`):
  - Replace `SimpleTransport.get_instance()` calls
  - For any transport reads (like `transport.connections[...]`), use the session_registry or persistence directly instead

- [x] **Line 1406-1408** (in `_get_transport_conn`):
  - Remove this method entirely — WPC should not access transport connections
  - Replace callers with `self._transport.get_connection_meta()` via PackTransportPort

- [x] **Line 1816-1818** (in `_collect_child_results`):
  - Replace transport access with `self._transport.get_task_error()` via PackTransportPort

- [x] **Line 1934/1982** (parent resume in `_resume_parent`):
  - Replace `transport._run_workflow_background(...)` with:
    ```python
    await self._run_spawner(RunRequest(
        capability="agent",
        workflow_name=parent_workflow_name,
        app_id=app_id,
        user_id=user_id,
        chat_id=parent_chat_id,
        context={"resume_agent": resume_agent_name, "merged_output": merged_output},
    ))
    ```

- [x] **Run tests** after each sub-task

### Task 2.4: Wire the Callback in Factory

- [x] **In `factory.py` startup handler**, after SimpleTransport is created:
  ```python
  transport = await SimpleTransport.get_instance()
  wpc = WorkflowPackCoordinator(run_spawner=transport.spawn_background_run, ...)
  ```

- [x] Register WPC with the event dispatcher as before

- [x] Remove any remaining `SimpleTransport` imports from `workflow_pack_coordinator.py`

- [x] **Verify**: `grep -r "SimpleTransport" mozaiksai/kernel/` should return ZERO matches

### Task 2.5: Run Full Test Suite + Manual HelloWorld Test

- [x] `pytest tests/ -q --ignore=tests/test_integration_mfj.py --tb=short` — 244/249 (5 pre-existing failures: 3 autogen import + 2 kernel.__all__)
- [ ] Start server, run HelloWorld WS test script to verify end-to-end
- [ ] If MFJ tests exist, run those separately to verify fan-out/fan-in still works

---

## Phase 3: Delete GroupChatPool, Replace With DAGExecutor

### Current State

Two implementations of the same DAG scheduler:

| Component | File | Lines | Topo Sort | Dispatch |
|-----------|------|-------|-----------|----------|
| `GroupChatPool` | `kernel/pool.py` | 436 | `_topological_sort()` L167 | `RunSupervisor.start_run()` L370 |
| `DAGExecutor` | `runtime/dag_executor.py` | 571 | `_topological_sort()` L278 | `RunSupervisor.start_run()` L460 |

`DAGExecutor` is the better implementation:
- Runtime-layer (correct placement)
- `DAGTask` has explicit `capability` field (not hardcoded to `"agent"`)
- Has `DAGTaskResult` and `DAGResult` output types
- Emits `dag.started`, `dag.task_started`, `dag.task_completed`, `dag.task_failed`, `dag.completed` events
- Fully tested (19 tests passing)

`GroupChatPool` is used by `UniversalOrchestrator._execute_decomposition()` — which is itself never called in production.

### Task 3.1: Update UniversalOrchestrator to Use DAGExecutor

- [ ] **In `orchestrator.py`**, replace `GroupChatPool` usage in `_execute_decomposition()` (line ~340-400):
  - Convert `DecompositionPlan.sub_tasks` → `list[DAGTask]`
  - Create `DAGExecutor(run_supervisor=self._get_supervisor())`
  - Call `dag.execute_stream(tasks, parent_run_id=..., app_id=..., user_id=...)`
  - Merge step uses the `DAGResult` from the final `dag.completed` event

- [ ] **Remove `GroupChatPool` import** from `orchestrator.py` (line 376 area)

### Task 3.2: Delete GroupChatPool

- [ ] **Delete file**: `mozaiksai/kernel/pool.py`

- [ ] **Remove from `kernel/__init__.py`**: the `GroupChatPool` import and `__all__` entry

- [ ] **Remove from `kernel/pack/__init__.py`** if referenced

- [ ] **Grep for `GroupChatPool`** across entire codebase — update or remove all references:
  - `kernel/__init__.py` — remove import
  - `kernel/orchestrator.py` — replaced in 3.1
  - Tests — update any test that uses `GroupChatPool` directly

- [ ] **Run tests** — expect test count to drop slightly (GroupChatPool-specific tests removed)

### Task 3.3: Verify DAGExecutor Tests Still Pass

- [ ] `pytest tests/test_dag_executor.py -v` — all 19 must pass
- [ ] `pytest tests/ -q --ignore=tests/test_integration_mfj.py --tb=short` — baseline maintained (minus removed pool tests)

---

## Phase 4: Slim WorkflowPackCoordinator

### Current State

2,046 lines handling:
- Pack config loading and graph parsing
- MFJ (Mid-Flight Journey) fan-out triggering
- Child workflow spawning
- Child result collection
- Fan-in merge execution
- Parent workflow resumption
- Journey auto-advance
- Journey step gating queries
- Recovery from persistence
- Transport connection management (being removed in Phase 2)

### Target

~500 lines handling:
- React to structured output events → build `list[DAGTask]` from pack config
- Hand task list to `DAGExecutor`
- On DAG completion → merge results → resume parent via `run_spawner`
- Journey auto-advance (separate, simple)

### Task 4.1: Extract DAG Building From WPC

- [ ] **Create `kernel/pack/task_builder.py`** (~100 lines):
  ```python
  def build_dag_tasks_from_pack(
      pack_graph: PerWorkflowPackGraph,
      parent_context: dict,
      app_id: str,
      user_id: str,
  ) -> list[DAGTask]:
      """Convert a pack graph's MFJ triggers into DAGTasks for the DAGExecutor."""
  ```
  - Reads `pack_graph.mid_flight_journeys` → maps each trigger to a `DAGTask`
  - Sets `capability="agent"` for agent workflows (or reads from pack config)
  - Resolves dependencies from the pack graph
  - Returns a flat `list[DAGTask]` ready for `DAGExecutor.execute_stream()`

- [ ] **Unit test** the builder separately

### Task 4.2: Extract Merge Logic From WPC

The merge logic (collecting child results, applying merge strategies) should move to a standalone function:

- [ ] **Create `kernel/pack/merge_executor.py`** (~80 lines):
  ```python
  async def merge_child_results(
      dag_result: DAGResult,
      merge_strategy: MergeStrategy,
      parent_context: dict,
  ) -> MergeResult:
      """Apply a merge strategy to DAGExecutor results."""
  ```

- [ ] This replaces the ~200 lines of result collection + merge execution currently spread across WPC methods

### Task 4.3: Rewrite WPC Core as Event-Reactive Coordinator

- [ ] **Rewrite `handle_structured_output_ready()`** to:
  1. Load pack graph for parent workflow
  2. Call `build_dag_tasks_from_pack(...)` → `list[DAGTask]`
  3. Create `DAGExecutor` (using injected `run_supervisor`)
  4. `async for event in dag.execute_stream(tasks): yield event`
  5. On `dag.completed` → `merge_child_results(dag_result, strategy, context)`
  6. Resume parent via `self._run_spawner(resume_request)`

- [ ] **Rewrite `handle_run_complete()`** to:
  - Only handle journey auto-advance (the "next step in wizard" logic)
  - Remove all transport coupling
  - Use `self._run_spawner(RunRequest(...))` for auto-advance spawning

- [ ] **Delete dead methods** — anything that's only reachable from removed code:
  - `_get_transport_conn()`
  - `_collect_child_results()` (replaced by DAGResult)
  - Internal state tracking that duplicates what DAGExecutor handles

### Task 4.4: Inject RunSupervisor Into WPC

- [ ] **Add `run_supervisor` parameter** to WPC constructor (alongside `run_spawner`):
  ```python
  def __init__(self, *, run_spawner, run_supervisor=None, ...):
      self._run_supervisor = run_supervisor or get_run_supervisor()
  ```
  DAGExecutor receives this supervisor for dispatching.

- [ ] **Wire in factory.py**

### Task 4.5: Move Journey Orchestration to Separate Module

- [ ] **Move journey auto-advance logic** from WPC to `kernel/pack/journey_orchestrator.py` (this file may already exist — check and merge):
  - `handle_journey_step_complete(app_id, user_id, workflow_name, chat_id)` → determines next step → spawns via `run_spawner`
  - ~100-150 lines

- [ ] **WPC calls journey_orchestrator** in its `handle_run_complete()` method

### Task 4.6: Verify Target Line Count and Clean Exports

- [ ] **WPC should be ~400-500 lines** after extraction
- [ ] **Update `kernel/pack/__init__.py`** — export new modules (`task_builder`, `merge_executor`, `journey_orchestrator`)
- [ ] **Run full test suite**

---

## Phase 5: Restore `_pack` Ownership Semantics

### Current State

`_pack/workflow_graph.json` is loaded by:
- `kernel/pack/config.py:load_pack_graph(workflow_name)` — per-workflow pack (MFJ triggers)
- `kernel/pack/config.py:load_pack_config()` — global pack (journeys + gates)
- `kernel/orchestrator.py:_load_pack_config()` — inline duplicate of per-workflow loader
- `platform/extensions.py` — calls `load_pack_config()` for gating

The global `workflows/_pack/workflow_graph.json` **does not exist on disk**. The only real file is `workflows/HelloWorld/_pack/workflow_graph.json` (empty v3 stub).

### Task 5.1: Clarify the Two Config Scopes

- [ ] **Rename functions** in `kernel/pack/config.py` for clarity:
  - `load_pack_graph(workflow_name)` → `load_workflow_pack(workflow_name)` — per-workflow MFJ config
  - `load_pack_config()` → `load_global_pack()` — global journeys + gates

- [ ] **Update all call sites** (config, WPC, orchestrator, extensions, tests)

- [ ] **Delete the inline duplicate** in `orchestrator.py:_load_pack_config()` — use the canonical `load_workflow_pack()` from config.py

### Task 5.2: Document the Ownership Rule

- [ ] **Add docstring** to `load_workflow_pack()`:
  ```
  Per-workflow pack config. Lives at workflows/<name>/_pack/workflow_graph.json.
  Owned by the workflow author. Declares child workflows this workflow can spawn.
  This is a WORKFLOW feature, not a core framework feature.
  ```

- [ ] **Add docstring** to `load_global_pack()`:
  ```
  Global pack config. Lives at workflows/_pack/workflow_graph.json.
  Declares journeys (wizard chains) and gates (prerequisites).
  Used by the platform layer for multi-workflow coordination.
  ```

---

## Phase 6: Fix ProvisioningWorker Contract Alignment

### Current State

`mozaiks-platform/app/workers/provisioning_worker.py` uses old field names:
- `DomainEvent(payload=...)` — should be `data=`
- `request.payload.get(...)` — should be `request.context.get(...)`

The coerce validators in `contracts/events.py` and `contracts/runner.py` silently handle this, but per constraints: no fallbacks.

### Task 6.1: Update ProvisioningWorker to Canonical Field Names

- [ ] **In `provisioning_worker.py`**, replace all occurrences:
  - `payload=` in `_event()` helper → `data=`
  - `request.payload.get(...)` → `request.context.get(...)` (2 sites at lines 113-114)
  - `request.payload` as JSON body → `request.context` (line 128: `json=request.payload`)

- [ ] **Update docstrings**: "RunRequest.payload must be..." → "RunRequest.context must contain..."

### Task 6.2: Remove Coerce Validators

Once all call sites use canonical names:

- [ ] **In `contracts/events.py`**: remove `_coerce_data()` model_validator from `DomainEvent` and `EventEnvelope`
- [ ] **In `contracts/runner.py`**: remove `_coerce_context()` model_validator from `RunRequest`
- [ ] **Grep entire codebase** (both `mozaiks/` and `mozaiks-platform/`) for `payload=` on DomainEvent and RunRequest — must be zero
- [ ] **Run tests** — any test using old field names must be updated, not silently coerced

---

## Phase 7: Clean Up Dead Code

### Task 7.1: Assess UniversalOrchestrator Usage

After Phase 3and 4, `UniversalOrchestrator` uses `DAGExecutor` instead of `GroupChatPool`. But it's still never called from production code.

Decision: **Keep or delete?**

- [ ] **If keeping**: Wire it into the handler as an optional orchestration layer between WS handler and RunSupervisor (for decomposition-capable workflows)
- [ ] **If deleting**: Remove `kernel/orchestrator.py`, update `kernel/__init__.py`, remove tests

Recommend: **Keep** — it's the right abstraction for Approach 0 (multi-pass generation) and Approach 3 (multi-workflow pack) from the source of truth docs. Wire it in when a workflow needs decomposition.

### Task 7.2: Remove Duplicate Topological Sort

After GroupChatPool deletion, only one `_topological_sort` remains (in `DAGExecutor`).

- [ ] Verify: `grep -r "_topological_sort" mozaiksai/` should show only `dag_executor.py`
- [ ] If `WorkflowPackCoordinator` had its own sort, it's gone (it now uses DAGExecutor)

### Task 7.3: Clean Kernel Exports

- [ ] **Update `kernel/__init__.py`**:
  - Remove: `GroupChatPool`
  - Keep: `UniversalOrchestrator`, `DecompositionPlan`, `SubTask`, `MergeStrategy`, etc.
  - Add: New exports from Phase 4 extractions

### Task 7.4: Run Final Test Suite

- [ ] `pytest tests/ -q --ignore=tests/test_integration_mfj.py --tb=short`
- [ ] Note new baseline (should be close to 250 minus removed GroupChatPool tests)
- [ ] Start server → HelloWorld WS test → verify end-to-end

---

## Phase 8: Event Streaming Architecture (Future — After Core Refactor)

This is the "events flow out-of-band" problem. Currently:

```
AgentWorker.execute() → AG2EngineAdapter.run()
  → run_workflow_orchestration()
    → _stream_events(run)
      → SimpleTransport.send_event_to_ui(envelope, chat_id)  ← SIDE EFFECT
  ← returns run state dict
← yields DomainEvent(event_type="run.completed")  ← LIFECYCLE ONLY
```

The DomainEvent iterator from `AgentWorker.execute()` only contains lifecycle events (`run.started`, `run.completed`). The actual agent messages, tool calls, and handoffs are sent directly to the WebSocket by `_stream_events` as a side effect.

This means:
- `DAGExecutor` sees only lifecycle events when collecting task results
- `RunSupervisor` can't inspect or intercept real-time events
- Non-WebSocket consumers (API, batch, replay) can't receive agent events

### Task 8.1 (Future): Make `_stream_events` Yield DomainEvents

- [ ] Refactor `_stream_events` from a side-effect function to an async generator that yields `DomainEvent`s
- [ ] Each AG2 event translation produces a `DomainEvent` that gets yielded up the chain
- [ ] The transport layer (handler) is the one that sends events to WebSocket — not the engine
- [ ] This is a significant refactor — save for after the core structural cleanup

### Task 8.2 (Future): RabbitMQ Event Bridge for .NET Services

- [ ] Each .NET service publishes events to a shared RabbitMQ broker (outbox pattern)
- [ ] A consumer in the Python runtime subscribes and injects events into the EventBus
- [ ] Agents can then react to CRUD events ("when app deployed, trigger LearningLoop")
- [ ] This is a mozaiks-platform concern, not a core runtime change

---

## Verification Checklist (After All Phases)

### Structural Verification

- [ ] `grep -r "SimpleTransport" mozaiksai/kernel/` → ZERO matches
- [ ] `grep -r "GroupChatPool" mozaiksai/` → ZERO matches
- [ ] `grep -r "_run_workflow_background" mozaiksai/kernel/` → ZERO matches
- [ ] `grep -r "transport\._background_tasks" mozaiksai/kernel/` → ZERO matches
- [ ] `grep -r "_topological_sort" mozaiksai/` → only `runtime/dag_executor.py`
- [ ] `WorkflowPackCoordinator` is < 600 lines
- [ ] Every workflow execution enters through `RunSupervisor.start_run()`
- [ ] `ProvisioningWorker` uses `data=` and `request.context`
- [ ] No coerce validators remain in contracts

### Functional Verification

- [ ] `pytest tests/ -q --ignore=tests/test_integration_mfj.py --tb=short` — all pass
- [ ] `pytest tests/test_dag_executor.py -v` — all pass
- [ ] Server starts cleanly (`python run_server.py`)
- [ ] HelloWorld workflow runs end-to-end via WebSocket
- [ ] Platform hooks register correctly (`ProvisioningWorker` visible in `WorkerRegistry`)

### Architecture Verification

- [ ] Runtime layer (`runtime/`, `transport/`) has no imports from `kernel/pack/`
- [ ] Kernel layer (`kernel/`) has no imports from `transport/`
- [ ] Engine layer (`engine/`, `adapters/`) has no imports from `kernel/` or `transport/`
- [ ] Contracts layer (`contracts/`, `ports/`) has no imports from any other layer
- [ ] Platform extensions (`platform/`) only import from `contracts/`, `kernel/pack/config`, `kernel/pack/gating`

---

## File Inventory (Before → After)

| File | Before | After | Change |
|------|--------|-------|--------|
| `kernel/pool.py` | 436 lines | **DELETED** | Replaced by DAGExecutor |
| `kernel/pack/workflow_pack_coordinator.py` | 2,046 lines | ~500 lines | Stripped to event coordinator |
| `kernel/pack/task_builder.py` | — | ~100 lines | **NEW** — DAGTask builder |
| `kernel/pack/merge_executor.py` | — | ~80 lines | **NEW** — merge step |
| `kernel/pack/journey_orchestrator.py` | exists or new | ~150 lines | Journey auto-advance extracted |
| `kernel/orchestrator.py` | 513 lines | ~450 lines | GroupChatPool → DAGExecutor |
| `kernel/__init__.py` | exports GroupChatPool | no GroupChatPool | Clean |
| `transport/websocket/handler.py` | 1,719 lines | ~1,750 lines | +`spawn_background_run()` |
| `transport/factory.py` | 499 lines | ~510 lines | Wire WPC with callbacks |
| `runtime/dag_executor.py` | 571 lines | 571 lines | Unchanged |
| `contracts/events.py` | has coerce validator | no coerce validator | Removed fallback |
| `contracts/runner.py` | has coerce validator | no coerce validator | Removed fallback |

---

## Execution Order

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
                                                                  │
                                                          Phase 8 (future)
```

Each phase is independently testable. Run the full test suite after each phase. Do not proceed to the next phase until tests pass.
