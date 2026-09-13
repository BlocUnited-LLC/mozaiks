# Scoped AppSchema Manifest Handoff

## Integration State

Done for the bounded page-worker contract and terminal-settlement CAS follow-up.
Worktree: `C:/Repos/BlocUnitedRepo/mozaiks/.local/worktrees/factory-repeatable-integration`.
Last parent-supplied base: `5639842f`, plus ongoing parent integration edits.
Branch and PR state were not rechecked: no git operations were authorized or used.
No commits, pushes, dependency installs, pins, or provenance pins changed.

Parent ServiceAgent, UI, newline-writer, and live acceptance changes were
preserved. This work did not restart servers, use a browser, invoke model
providers, modify live session records, or implement preview hosting.

## Decisions

- `AppSchemaOutput.manifest` is typed `AppManifest | null`. A page-only task
  emits null; a task owning `app.json` emits its manifest. Null materializes
  neither `app.json` nor `provenance.yaml`, so partial workers cannot overwrite
  app identity or provenance merely by returning their required output shape.
- Provider response models require an explicit object or null. Local acceptance
  models retain the existing nullable-field default. The global structured
  output builder was not changed.
- The task ownership gate is unchanged. An emitted manifest without app.json
  ownership fails; null when app.json is owned fails for missing required output.
  Extra unowned page files are rejected, not silently filtered. No new optional
  path exemptions, ownership widening, or alternate page output schema was added.
- Genesis plan coverage still requires a manifest owner and every planned page.
  Revision assembly reuses verified archive hydration and overlays only emitted
  files. Full bundle acceptance/export and runtime loading still require a
  complete valid app. Standalone `save_app_schema` still rejects null before writes.
- Terminal settlement now re-reads app-scoped status once after an acknowledged
  CAS miss. The same terminal outcome succeeds without another mutation; an
  opposite outcome is rejected. Missing/still-running rows and unacknowledged
  writes remain failures. Lease checks and the status-0 CAS are unchanged.

No new architecture or state owner was needed. Forced app.json ownership on
every page task was rejected because it would make legitimate disjoint page
refinements contend for global app metadata.

## Files

Page-worker implementation and guidance:

- `mozaiksai/core/workflow/generator_support/code_files.py`
- `factory_app/workflows/AppGenerator/structured_outputs.yaml`
- `factory_app/workflows/AppGenerator/agents.yaml` (AppPlan/AppSchema sections only)
- `factory_app/build_context/AppGenerator/file_contracts.yaml`
- `docs/architecture/builder/appgenerator-output-assembly-contract.md`

CAS follow-up:

- `mozaiksai/core/data/persistence/persistence_manager.py`
- `docs/runtime-failed-run-durability.md`

Regression tests and handoff:

- `tests/test_appschema_scoped_manifest.py` (new)
- `tests/test_appgenerator_revision_baseline.py` (verified assembly regression)
- `tests/test_failed_run_durability.py` (seven CAS race/failure regressions)
- This document.

## Verification

All tests used current checkout source via the existing process-local offline
runner, not installed-pin proof. The interpreter was the existing App worktree's
`.venv/Scripts/python.exe`; no environment was installed or changed. The runner
disables dotenv, clears operator settings, uses dummy provider keys, and rejects
nonlocal sockets. The Mongo boundary test uses its isolated database fixture.

Results:

- CAS-focused: **103 passed, 0 skipped, 0 failed in 10.46s**.
  Report: `.local/failed-run-validation/cas-focused.xml`.
- Consolidated manifest regression: **632 passed, 1 skipped, 0 failed in 89.23s**.
  Report: `.local/failed-run-validation/scoped-manifest-consolidated.xml`.
- After moving the archive fixture test to its owning module and sorting imports:
  **67 passed, 0 skipped, 0 failed in 9.85s**.
  Report: `.local/failed-run-validation/scoped-manifest-cas-final.xml`.
- Whole-tree Ruff: **All checks passed**.

The consolidated skip was `test_live_refinement_task_batch_smoke`, intentionally
not enabled because it requires provider calls. Two dependency deprecation
warnings came from Starlette/httpx/AnyIO. The initial new-test-only run had six
fixture/schema-boundary assertion failures; all were corrected and covered by
the passing runs. Initial Ruff findings were limited to the new test module.

Exact commands, from the integration worktree:

```powershell
$python = 'C:/Repos/BlocUnitedRepo/mozaiks-app/.local/worktrees/factory-contract-consumption/.venv/Scripts/python.exe'
& $python -I .local/failed-run-validation/run.py tests/test_failed_run_durability.py tests/test_workflow_bridge_failure_settlement.py tests/test_chat_execution_lease.py tests/test_persistence_manager_helpers.py tests/test_workflow_bridge_failure_registry_mongo.py -q -o addopts='' --no-cov -p no:cacheprovider --tb=short --show-capture=no '--basetemp=\\?\C:\Repos\BlocUnitedRepo\mozaiks\.local\worktrees\factory-repeatable-integration\.local\failed-run-validation\temp-cas-1' --junitxml=.local/failed-run-validation/cas-focused.xml
& $python -I .local/failed-run-validation/run.py tests/test_appschema_scoped_manifest.py tests/test_generator_code_file_materialization.py tests/test_code_files_helpers.py tests/test_task_batch_contracts.py tests/test_task_batches_helpers.py tests/test_task_batches_extended_helpers.py tests/test_appgenerator_task_batch_contracts.py tests/test_appgenerator_materializing_task_authority.py tests/test_appgenerator_save_app_schema.py tests/test_save_app_schema_helpers.py tests/test_save_app_schema_extended_helpers.py tests/test_save_app_schema_normalizer_helpers.py tests/test_assemble_app_tasks_helpers.py tests/test_appgenerator_revision_baseline.py tests/test_app_plan_review.py tests/test_appplan_materialization_acceptance.py tests/test_refinement_task_batch_smoke.py tests/test_structured_output_provider_boundary.py tests/test_structured_output_cache_invalidation.py tests/test_auto_tool_structured_output_contract.py -q -o addopts='' --no-cov -p no:cacheprovider --tb=short --show-capture=no '--basetemp=\\?\C:\Repos\BlocUnitedRepo\mozaiks\.local\worktrees\factory-repeatable-integration\.local\failed-run-validation\temp-schema-consolidated-1' --junitxml=.local/failed-run-validation/scoped-manifest-consolidated.xml
& $python -I .local/failed-run-validation/run.py tests/test_appschema_scoped_manifest.py tests/test_appgenerator_revision_baseline.py tests/test_failed_run_durability.py -q -o addopts='' --no-cov -p no:cacheprovider --tb=short --show-capture=no '--basetemp=\\?\C:\Repos\BlocUnitedRepo\mozaiks\.local\worktrees\factory-repeatable-integration\.local\failed-run-validation\temp-schema-cas-final' --junitxml=.local/failed-run-validation/scoped-manifest-cas-final.xml
& $python -I -m ruff check . --output-format concise
```

Use fresh basetemp names for another Windows run. All finite sessions exited:
CAS `56118`, consolidated `29366`, final selection `63036`.

## Residuals

- Parent full OSS suite, live refinement acceptance, and immutable revision
  verification remain separate prerequisites. This bounded change is ready for
  that integration run, not proof that the entire dirty branch is merge-ready.
- Preview was inspected only and deferred. The canonical SandboxPort path needs
  the existing platform host and packaged shell in a prepared Python/Node/runtime
  image. No alternate host, fabricated main.py, image download, or preview source
  edit was introduced. The parent subsequently verified archive/source equality;
  no export-drift defect is claimed here.
- Existing generic runtime treatment of historic Factory failure receipts is
  unchanged; the CAS fix adds no migration or alternate durable state.
