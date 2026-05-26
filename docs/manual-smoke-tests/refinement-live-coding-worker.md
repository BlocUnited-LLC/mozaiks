# Refinement Live Coding Worker Smoke

This smoke is a manual check for the staged refinement worker path.

It verifies a live or manual coding worker can produce structured proposed file
changes and that those changes are applied only to the staging workspace
through the scoped execution boundary.

## Run

```bash
python scripts/smoke_refinement_live_coding_worker.py
python scripts/smoke_refinement_live_coding_worker.py --run-live
python scripts/smoke_refinement_live_coding_worker.py --save-fixture
python scripts/smoke_refinement_live_coding_worker.py --run-live --scenario all --save-fixture
```

## Required environment

- `OPENAI_API_KEY` must be available for the live worker call.
- The standard control-plane configuration must resolve a coding LLM config.
- No model settings are changed by the smoke.
- The script stays in skip mode unless `--run-live` is provided.

If `OPENAI_API_KEY` is missing, the script exits cleanly without calling the
live worker.

## Fixture replay

When `--save-fixture` is used, the script writes:

```text
tests/fixtures/refinement_live_coding_worker_output.json
```

When `--scenario all` is used with `--save-fixture`, the script writes:

```text
tests/fixtures/refinement_live_coding_worker_matrix_output.json
```

The matrix smoke records multiple neutral refinement lanes in isolated temp
directories. It continues past partial failures unless `--strict` is passed.
When the matrix smoke includes a successful `ui_patch` scenario, it also
refreshes the single-scenario replay fixture used by the focused smoke test.

The replay test skips when that fixture is absent. After the fixture exists,
run:

```bash
python -m pytest tests/test_live_refinement_coding_worker_smoke.py -q
```

The replay test reconstructs the neutral dashboard fixture, applies the saved
worker output through the staged coding worker helper, and checks:

- the staged dashboard file changes
- the source file remains unchanged
- all writes stay inside the staging workspace
- no secret or traversal paths appear
- no AppGenerator or workflow execution markers appear

The live smoke treats the worker run as successful when the worker returns a
validated staged change set. The manual smoke validation hook intentionally
returns a skipped validation result, not a failure. Persistent worker-output
storage is not required for success; the fixture is the replay record.

The live smoke also uses a smoke-local control-plane tool executor for the
coding checkpoint context tools. That keeps the manual smoke self-contained and
avoids hitting Mongo-backed stores just to assemble prompt context. The real
control-plane executor still surfaces actual tool failures in non-smoke paths.

The smoke path also injects a smoke-local in-memory artifact store. That keeps
artifact persistence self-contained for the manual smoke and avoids depending
on Mongo-backed artifact storage just to record the staged validation result.
Real persistence errors still surface in non-smoke runs.

## Scenario matrix

The smoke currently supports these neutral scenarios:

- `ui_patch`
- `module_backend`
- `integration_adapter`
- `data_model_comment`
- `hosted_facade`

Use `--scenario all` to run the matrix. Each scenario writes only to its own
staging workspace and uses `apply_scoped_refinement_changes(...)` as the only
mutation boundary.

## Safety guarantees

- worker output is structured data, not direct file writes
- all mutations go through `apply_scoped_refinement_changes(...)`
- source files are never mutated by the smoke
- promotion and restore are out of scope
- no `mozaiks-app` files are touched


