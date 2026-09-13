# Runtime Failed-Run Durability Handoff

## Scope and State

Implemented in the operator-owned integration worktree:
`C:/Repos/BlocUnitedRepo/mozaiks/.local/worktrees/factory-repeatable-integration`.
Last supplied checkpoint: `5639842f`, plus the parent's ongoing integration work.
No git operations were used to recheck or alter its branch, base, or PR state.
No commits, pushes, dependency installs, pins, or provenance changes were made.

The eight affected runtime modules passed an offline import check. The parent
subsequently started and health-checked `serve11` on port 8020. A later bounded
follow-up adds same-outcome CAS-race acknowledgement, described below. This work did not operate
the server or browser, call model providers, or modify live session records.

## Ownership and Behavior

This changes the universal runtime substrate's Run/Event boundary, not AG2's
agent scheduling or a Factory-specific retry policy. No architecture
contradiction or new state owner was required.

- `ChatSessions.status`: `0` in progress (including paused), `1` completed,
  `2` failed. Failure records `failed_at`, duration, and session version before
  failure hooks and terminal UI delivery. Pending input is cleared; artifacts
  and immutable run/build identity remain intact.
- The existing persistence owner performs a scoped, lease-checked transition
  from `0`. Repeating the same terminal outcome is idempotent; changing one
  terminal outcome into another is rejected. Storage errors are not silently
  acknowledged as successful terminal writes.
- After an acknowledged CAS miss, one app-scoped status re-read acknowledges
  an already-persisted identical terminal outcome. An opposite terminal outcome
  raises `ChatSessionTerminalError`; a missing or still-running row remains a
  failed write. There is no second write, status override, or version increment.
  An unacknowledged write is rejected without reading its result.
- HTTP input, live continuation, callback dispatch, injected resume context,
  and direct orchestration refuse terminal sessions before mutable execution.
  Failed websocket reconnects close with policy violation `1008` before
  autostart. Completed sessions cannot autostart from an empty transcript.
- Cancellation, lease loss, admission denial, and ordinary AG2 human pauses
  remain nonterminal. Failed sessions never satisfy completed prerequisites.
- A closed chat-scoped AG2 WAL blocks opening a replacement channel even when
  an old session's status stayed at `0`. Tests prove both run and resume paths
  leave the deterministic agent's call count unchanged after failure.
- The parent's `workflow_bridge.py` failure hook, target-scoped revision
  settlement, invalid-output suppression, and live-handle cleanup remain in
  place. Failure persistence now precedes that existing settlement path.

## Files

Runtime owners:

- `mozaiksai/core/data/models.py`
- `mozaiksai/core/data/persistence/persistence_manager.py`
- `mozaiksai/core/workflow/orchestration_patterns.py`
- `mozaiksai/core/transport/workflow_bridge.py`
- `mozaiksai/core/adapters/ag2_orchestration.py`
- `mozaiksai/core/adapters/ag2_network_runner.py`
- `mozaiksai/hosts/runtime.py`
- `mozaiksai/hosts/routers/sessions.py`

Status mappings:

- `factory_app/app/admin/pages/AppOverviewPage.jsx`
- `chat-ui/src/admin/pages/ActivitySection.jsx`

The App Zero overview was inspected read-only: its app-local implementation
uses commercial string statuses and does not contain the obsolete numeric
run-status helpers. It was neither copied from OSS nor modified.

Regression coverage and aligned test doubles:

- `tests/test_failed_run_durability.py`
- `tests/test_workflow_bridge.py`
- `tests/test_workflow_bridge_failure_settlement.py`
- `tests/test_workflow_bridge_failure_registry_mongo.py`
- `tests/test_ag2_network_execution_alignment.py`
- `tests/test_runtime_websocket_contract.py`
- `tests/test_lifecycle_identity_guard.py`
- `tests/test_chat_execution_lease.py`
- `tests/failed-run-status.test.mjs`

Contract documentation:

- `docs/architecture/foundations/events-and-data/persistence-and-artifact-storage.md`
- `docs/architecture/mozaiksai/pack-graph-semantics.md`
- This handoff.

## Verification

All validation used the current integration checkout, not an installed OSS
revision or an immutable dependency pin. The supplied model-call-admission
Python lacked FastAPI, so the existing App validation interpreter was used:
`C:/Repos/BlocUnitedRepo/mozaiks-app/.local/worktrees/factory-contract-consumption/.venv/Scripts/python.exe`.

The process-local `.local/failed-run-validation/run.py` runner clears operator
configuration, disables dotenv and telemetry, uses dummy provider keys, blocks
nonlocal sockets, and keeps logs/temp files inside this worktree. Real-Mongo
tests use their existing uniquely named databases and cleanup fixtures. It
does not install packages or start servers. Pytest addopts are overridden
because this interpreter lacks the configured rerun plugin; no assertions or
tests are weakened, xfailed, or retried.

Final consolidated result: **377 passed, 0 skipped, 0 failed in 55.68 seconds**.
Report: `.local/failed-run-validation/consolidated-final-mongo.xml`.

```powershell
& 'C:/Repos/BlocUnitedRepo/mozaiks-app/.local/worktrees/factory-contract-consumption/.venv/Scripts/python.exe' -I .local/failed-run-validation/run.py `
  tests/test_failed_run_durability.py tests/test_workflow_bridge.py `
  tests/test_workflow_bridge_failure_settlement.py tests/test_workflow_bridge_failure_registry_mongo.py `
  tests/test_ag2_network_execution_alignment.py tests/test_runtime_websocket_contract.py `
  tests/test_lifecycle_identity_guard.py tests/test_chat_execution_lease.py `
  tests/test_data_models.py tests/test_run_replay.py tests/test_session_router.py `
  tests/test_orchestration_port.py tests/test_persistence_manager_helpers.py `
  tests/test_persistence_initial_messages.py tests/test_orchestration_seed_persistence.py `
  tests/test_platform_chat_meta_contract.py tests/test_general_chat_persistence_contracts.py `
  tests/test_admin_router_helpers.py tests/test_transport_input_handlers.py `
  tests/test_transport_ui_tools.py tests/test_server_owned_session_fields_real_mongo.py `
  -q -o addopts='' --no-cov -p no:cacheprovider --tb=short --show-capture=no `
  '--basetemp=\\?\C:\Repos\BlocUnitedRepo\mozaiks\.local\worktrees\factory-repeatable-integration\.local\failed-run-validation\temp-final-2' `
  --junitxml=.local/failed-run-validation/consolidated-final-mongo.xml
node --test tests/failed-run-status.test.mjs
```

JS: **4 passed**, using existing read-only esbuild/React packages to evaluate
overview helpers and render all three activity labels. Whole-tree Ruff passes
with the App interpreter's `python -m ruff check . --output-format concise`.

Earlier runs: 8 initial passes; 148 focused passes; 351 passes with three
new-test-double failures (missing cursor `limit`, fixed); 366 passes with 11
opt-in Mongo skips. The final run above enables and passes those Mongo gates.

CAS-race follow-up: **103 passed, 0 skipped, 0 failed in 10.46 seconds**.
Report: `.local/failed-run-validation/cas-focused.xml`. The same offline runner
and interpreter executed `tests/test_failed_run_durability.py`,
`tests/test_workflow_bridge_failure_settlement.py`,
`tests/test_chat_execution_lease.py`, `tests/test_persistence_manager_helpers.py`,
and `tests/test_workflow_bridge_failure_registry_mongo.py`, with a fresh
Windows extended-length basetemp at `.local/failed-run-validation/temp-cas-1`.
New tests force both terminal callbacks to read status 0 before either CAS and
prove one durable mutation, two successful same-outcome acknowledgements, and
preserved artifacts. Opposite outcomes, vanished/unsettled rows, and
unacknowledged writes remain rejected. No live session rows were modified.

## Residuals and Integration

- No generic runtime interpretation of Factory `build_terminal_receipt` was
  introduced. A historical receipt-only failure with no terminal AG2 state is
  not backfilled or automatically inferred. Historical affected records need
  operator review; this work made no live-data migration.
- Control-plane classification, artifact routing, workflow sequences, and
  checkpoint policy are unchanged. Failed-session re-entry is blocked; a new
  explicit run remains the retry mechanism.
- UI status labels changed, but parent-owned `ChatPage.js`, generation prompts,
  app validation, and generated artifacts were not edited.
- No full OSS suite or new paid/live generation was run for this bounded change.
  The parent's live acceptance and full integration verification remain theirs.
- The focused change is ready for parent review/integration. No claim is made
  that the entire concurrently edited branch is merge-ready. No PR was opened.
