# Refinement Live Coding Worker Smoke

This smoke is a manual check for the staged refinement worker path.

It verifies a live or manual coding worker can produce structured proposed file
changes and that those changes are applied only to the staging workspace
through the scoped execution boundary.

## Run

```bash
python scripts/smoke_refinement_live_coding_worker.py
python scripts/smoke_refinement_live_coding_worker.py --run-live --user-id <owner-id>
python scripts/smoke_refinement_live_coding_worker.py --save-fixture
python scripts/smoke_refinement_live_coding_worker.py --run-live --user-id <owner-id> --scenario all --save-fixture
```

## Required environment

- `OPENAI_API_KEY` must be available for the live worker call.
- The standard Refinement Engine configuration must resolve a coding LLM config.
- No model settings are changed by the smoke.
- The script stays in skip mode unless `--run-live` is provided.
- Live calls require an explicit `--user-id` for usage attribution.

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

The live smoke also uses a smoke-local Refinement Engine tool executor for the
coding checkpoint context tools. That keeps the manual smoke self-contained and
avoids hitting Mongo-backed stores just to assemble prompt context. The real
Refinement Engine executor still surfaces actual tool failures in non-smoke paths.

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
- `managed_facade`

Use `--scenario all` to run the matrix. Each scenario writes only to its own
staging workspace and uses `apply_scoped_refinement_changes(...)` as the only
mutation boundary.

## Safety guarantees

- worker output is structured data, not direct file writes
- all mutations go through `apply_scoped_refinement_changes(...)`
- source files are never mutated by the smoke
- promotion and restore are out of scope
- no `mozaiks-app` files are touched

## Studio and E2B acceptance

The diagnostic above does not prove the Studio chat, artifact UI, or running
preview. Verify those separately against a recorded OSS commit:

1. Start an isolated Studio with its normal auth posture and an owned database.
   Record the exact source commit, model configuration, and E2B template build.
   Rebuild the template when the preview runtime changes; updating Studio alone
   does not update code installed inside an existing template.
2. Use an owned saved app bundle with passed checks. Open **Review builds** from
   its app overview. The registered AppWorkbench loads the saved version; it
   does not need a fabricated workflow completion. If the baseline comes from
   deterministic fixture materialization, state that explicitly: it proves
   refinement of that fixture, not a completed live generation journey.
3. Start its draft preview and exercise a real app action in the iframe. For
   public apps, each public page explicitly declares `meta.requiresAuth: false`.
   App-level public identity alone does not make every page public. Check the
   landing route as a visitor, not just `/api/health` or `/api/pages/...`.
4. Type a bounded change into **App change request** and apply it with real
   provider calls. Verify the saved revision, file diff, and fresh validation
   evidence. Repeat for a second revision. Include an app using a capability
   pack with generated support files, so refinement cannot silently lose the
   original pack declaration.
5. While a change runs, try the existing preview. Once a newer draft is ready,
   its label must still identify the version actually displayed. **Update
   preview** explicitly replaces that sandbox. With one allowed preview, the
   previous session must stop before another starts; a short restart interval
   is expected. A failed change must not advance the selected preview version.
6. Inspect desktop and narrow-screen screenshots. Status and the next action
   stay visible; technical failure details and logs can be expanded when needed.
   Verify no horizontal overflow, working controls, expiry, and reload behavior.
7. Stop the preview and verify the provider session is absent. A background
   health check can briefly return a busy response; stopping retries that
   response up to three requests, using a bounded `Retry-After` delay. A failed
   stop must retain its cleanup handle and block replacement allocation. Seeing
   the Start button again is not proof of cleanup: verify the stop response and
   provider absence. Stop owned local services and retain private evidence
   without publishing credentials.

The **AppReview conversation** is a separate entry into the same refinement
boundary. Test it through a real journey with its prerequisite artifacts;
materializing only an app bundle does not satisfy those prerequisites. Do not
report Workbench text input as proof that chat messages, pause/resume, or the
full journey worked.

### Chat artifact preview

AppReview presents `AppReviewWorkspace` in the existing artifact panel. It uses
the shared preview lifecycle and loads saved bundles through the authenticated
Studio bundle endpoint. The preview appears first; acceptance and activation
remain gated by that saved version's checks and lifecycle.

For acceptance, continue a real AppReview conversation and verify:

1. Open the artifact panel and start the preview. Exercise an app control, then
   hide and reopen the panel. Its iframe and app state must remain mounted.
   On mobile, switch between chat and artifact without creating another sandbox.
2. Request a change in the chat composer. Keep using the original preview while
   the agents work. A checked successor enables **Update preview**; an unsuccessful
   attempt keeps the previous working preview and identifies the failed change.
   Confirm the actual `chat.revision_requested` websocket event reaches the
   normal Studio trigger endpoint with the saved artifact version. An agent's
   acknowledgement alone does not prove that an edit started. The source review's
   completion must not cover an ongoing edit, approval decision, or error with
   a success dialog.
3. After an inline refinement, Studio creates a new AppReview session bound to
   the settled build. The client adopts only its matching server descriptor;
   connecting its websocket starts the review through the existing launch owner.
   The previous run's immutable build binding is never rewritten. Verify a
   second chat edit addresses the new saved version.
4. Reject cross-app/version bundle responses and late responses from an abandoned
   chat. A failed review handoff must show an error rather than implying that
   another chat is ready. Acceptance must refetch the canonical saved review
   before activation becomes available.
5. Repeat the explicit update and provider-confirmed Stop checks above. Record
   real model/provider receipts separately from browser tests with HTTP fixtures.
6. From **Review builds**, select the current saved version and choose **Continue
   in chat**. This also supports a saved inline result with no active review chat.
   Verify the new review loads the owned current artifact without allocating a
   build, and historical selections cannot start this recovery. Explicit old
   source chats and stale resumed bindings must still be rejected. During a chat
   edit, the review heading reads **Updating your draft**; acceptance and
   activation remain disabled until that edit settles with saved checks.

Passing checks makes a draft ready for the user's review. **Accept this draft**
records that acceptance; **Activate this version** promotes it into the app
workspace. Neither action provisions hosting or publishes a public deployment.


