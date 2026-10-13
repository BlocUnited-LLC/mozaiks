# UI production baseline — 2026-10-13

This index maps the supplied production UI plan to OSS source at
`7bf7407954ca9483868d8a2c1dbf095268152d9e`. It records source inspection and
ownership, not acceptance. **No test was executed while making this index.**
Located tests below are reuse candidates; their presence does not establish a
passing result or complete coverage of the plan's case.

## Ownership and first implementation slice

| Work | Refreshed state | Scope boundary |
| --- | --- | --- |
| Experience fidelity | [#961](https://github.com/BlocUnited-LLC/mozaiks/pull/961), merged `5ad02469` | Preserve the existing policy, schema descriptions and prompt projection. |
| Native UI responses | [#962](https://github.com/BlocUnited-LLC/mozaiks/pull/962), draft `052115b7ed5b00c9f3f2e714479eab2232b9abf3` | Transport, workflow lookup, action projection and response tests. Current source includes registration rejection and conflicting-response handling absent from its older evidence description. Exact-head review remains open. |
| Paired Projects/Tasks | [#963](https://github.com/BlocUnited-LLC/mozaiks/pull/963), draft `40902a5df0f8639485ceb5209802dd430c05ab7e` | Scripted assembly/real-host API fixture; its worktree has further uncommitted runner/test changes. Browser/live-model claims remain separate. |
| Revision intent | [#966](https://github.com/BlocUnited-LLC/mozaiks/pull/966), draft `2a3046a1723fe48be1513120802f19e6d395d24b` | ValueEngine prompt interpretation; does not change theme or page rendering. |
| Selected-pack pages | [#967](https://github.com/BlocUnited-LLC/mozaiks/pull/967), draft `32827caf9fe0244b04aca09cbea5a990f3c70c79` | AppGenerator plan coverage; does not change theme or page rendering. |
| Transition response | [#964](https://github.com/BlocUnited-LLC/mozaiks/pull/964), merged at this baseline | Already landed; do not repeat. |

All open OSS PR file inventories were checked. None owns `PageFrame`,
`PageHeader`, `PageRenderer`, theme provider/validation, or the ThemeCapture
contract. U2 is therefore the first independent unclaimed source slice while
U1 continues in #962. The rendering work starts from this baseline, not an open
PR branch. Other open UI work owns Genesis review (#876), shell account
visibility (#791), and dependency manifests (#838/#968); package-script edits
need particular overlap care.

Plan delta: no architecture change is needed to begin U2. The frozen source
baseline is still current. The clean complete dependency environment is a
separate U0 requirement, not established by this source audit. Consumer pin
and package integration receipts stay in consumer evidence; this index
contains no proprietary source.

## Findings mapped to source

Paths are relative to the repository root. Findings describe the baseline,
before the U2 candidate changes.

| Finding | Canonical source / current disposition |
| --- | --- |
| F01 | `tests/test_ui_experience_fidelity.py` and merged #961 already cover shared fidelity guidance and composed prompts. Keep. |
| F02 | `chat-ui/src/ui/page-renderer/PageFrame.jsx` forces uppercase/heavy/spaced titles; `chat-ui/src/ui/primitives/PageHeader.jsx` has a different fixed title treatment. U2. |
| F03 | `chat-ui/src/ui/page-renderer/PageRenderer.jsx` has fixed `gap-6`, a non-stacking sidebar and unknown-layout fallback. U2. |
| F04 | `mozaiksai/core/runtime/app/page_schema.py` already owns the four-value layout contract. Extend closure tests; no new backend enum. |
| F05 | `chat-ui/src/ui/page-renderer/PrimitiveCatalog.js` exports `DEFAULT_PAGE_RECIPE`. Consumer reach still needs tracing before changing guidance. |
| F06 | `factory_app/build_context/AppGenerator/shell_presets.yaml` and `mozaiksai/hosts/shell_config.py` distinguish prompt presets from runtime chrome modes. Selection/visual acceptance remains open. |
| F07 | `mozaiksai/core/workflow/workflow_ui_catalog.py` distinguishes shipped/generated/built-in entries; reuse workflow export tests. |
| F08 | `web_shell/tests/chatWidgetRoutes.browser.test.js` exercises Ask launchers and full workflow routes independently. Preserve this split. |
| F09 | `web_shell/playwright/generated-app.generic.acceptance.spec.js` is a mocked declarative render smoke. U4 requires real-host mixed/custom browser evidence. |
| F10 | `scripts/generated_ui_acceptance.py` ignores skipped test results while collecting failures. Reporter/expected-route acceptance remains open. |
| F11 | AppGenerator `context_variables.yaml`, `middleware.yaml` and registered build-context assets retain context ownership; U3 tests effective projections. |
| F12 | #966 owns latest-feedback interpretation. Its reported WAL/prompt evidence is not a live reproduction in this audit. |
| F13 | #967 owns selected template-page admission. Do not duplicate its plan-review fix. |
| F14 | `chat-ui/src/registry/componentRegistry.js` and generated-UI registry validation distinguish runtime overrides and ambiguous generated registration. Complete preflight/override acceptance remains open. |
| F15 | `tests/test_app_layout_registry.py` and `docs/architecture/frontend/mozaiks-ui-v1.md` distinguish current contracts from target documentation. No roadmap API is inferred. |
| F16 | `tests/test_appgenerator_checkout_page_contracts.py` and page runtime validation retain the checkout-specific realization restriction. |
| F17 | Installed consumer acceptance remains U7; no private framework copy or sibling editable checkout qualifies. |

## Test/evidence index

Every row below is **not run by this audit**. “Missing” means a required
witness was not established here, not a claim that the entire repository lacks
related tests. Python paths are under `tests/`; browser/unit JS paths are under
`web_shell/tests/` unless stated otherwise.

| Case | Existing entrypoints to reuse | Remaining witness / owner |
| --- | --- | --- |
| T01 | `test_ui_contract_drift.py`, `test_ui_primitives_catalog.py`, `test_workflow_ui_tool_contracts.py` | Eligible reference closure across actual generated output; U1/U4. |
| T02 | `test_page_schema_runtime_validation.py`, `test_generated_ui_contract.py`, `test_context_authority_policy.py` | Invalid explicit frontend layout must fail visibly; U2. Other boundaries retain separate tests. |
| T03 | `test_deterministic_page_materialization.py` | Exact paired source/assets/toolchain byte-stability receipt; U5. |
| T04 | `test_experience_spec_realization.py`, `test_appschema_scoped_manifest.py` | Pipeline witness retaining custom empty-section intent; U3/U4. |
| T05 | `test_appschema_scoped_manifest.py`, `test_generated_ui_contract_pure_helpers.py` | Real mixed-route loading and permitted explicit override witness; U4. |
| T06 | `test_workflow_ui_tool_contracts.py`, `test_app_auth_generation.py`, `chatWidgetRoutes.browser.test.js` | Full generated capability-reuse acceptance; U1/U4. |
| T07 | `test_build_context_messaging_pack.py`, `test_generated_messaging_scope_http.py` | Domain capability versus framework capability generation witness; U1/U4. |
| T08 | #967 `test_plan_review_pack_page_inventory.py` | Consume reviewed selected-pack correction; U3. |
| T09 | `test_workflow_ui_tool_contracts.py`, `test_generated_ui_contract.py` | Generated wrapper/no-phantom-import acceptance; U1/U4. |
| T10 | `test_appgenerator_checkout_page_contracts.py`, `test_page_schema_runtime_validation.py` | Keep runtime checkout restriction alongside category-neutral guidance. |
| T11 | `test_ui_experience_fidelity.py::test_runtime_composed_prompt_preserves_approved_experience`, `test_factory_build_context_contract.py` | Each relevant real agent after complete middleware, including current source/security exclusions; U3. |
| T12 | `test_ui_experience_fidelity.py::test_repeated_projection_replaces_one_section_without_replaying_context` | Ten-turn effective prompt/state witness for each affected projection; U3. |
| T13 | `test_value_engine_concept_review.py::test_native_revision_uses_feedback_and_current_model_output`, #966 | Changed resulting artifact, not only wording/prompt delivery; U3/U5. |
| T14 | `test_ag2_build_context_resume.py`, `test_context_authority_policy.py`, #966 evidence | Current committed channel-state ordering on exact candidate; U3. |
| T15 | `test_appschema_scoped_manifest.py`, `test_appgenerator_materializing_task_authority.py` | Concurrent worker/stale-baseline proof; U3/U5. |
| T16 | `test_studio_page_refinement.py`, `test_app_workbench_refinement_scope.py`, `test_refinement_scoped_execution.py` | Actual mobile-inspector refinement plus unchanged desktop/other-route manifests; U5. |
| T17 | `test_ui_theme_round_trip.py`, `test_appgenerator_captured_theme_assembly.py` | Two-theme shared rendering plus no unrelated regeneration; U2/U5. |
| T18 | `test_page_contract_dependencies.py`, `test_appgenerator_bounded_recovery.py` | Dependency invalidation and unaffected artifact retention in paired app; U5. |
| T19 | `test_appgenerator_bounded_recovery.py`, `test_task_batch_recovery.py` | No-progress receipt retaining failed attempts on declared budget; U3/U5. |
| T20 | `test_ag2_build_context_resume.py`, `test_trusted_build_context_handoff.py` | Durable approved app/revision session continuation; U3/U5. |
| T21 | #963 host/API fixture; `schemaPageActions.browser.test.js`, `schemaPageServerPagination.browser.test.js` | Combined non-mocked browser data/save/reload and negative authorization; #963/U4. |
| T22 | #963 planning-board JSX fixture | Pointer/keyboard, details, error/retry and persisted status via real host; #963/U4. |
| T23 | `responsiveArtifactContinuity.browser.test.js`, `artifactPanelContinuity.browser.test.js` | Matched full-command reproduction and candidate continuity; U2/U4. |
| T24 | `artifactPanelContinuity.browser.test.js`, `transitionScreenStaleResponse.browser.test.js` | Revision reset/close/focus acceptance remains distinct from continuity. |
| T25 | `conceptReview.browser.test.js`, `outcomeFeedback.browser.test.js`, `test_value_engine_concept_review.py` | Full declared native response lifecycle; #962/U4. |
| T26 | `test_ui_response_ownership.py`, #962 transport/browser changes | Unknown/tampered/stale/wrong-principal rejection with explicit free-form control; #962. |
| T27 | #962 `test_transport_ui_tools.py` changes | Exact-head concurrent finalization/conflicting acknowledgement proof; #962. |
| T28 | `chatWidgetRoutes.browser.test.js` | Real-host generated app with one Ask launcher and full workflow destination; U4. |
| T29 | `test_ask_page_context.py`, `test_ask_context_contract_closure.py`, `test_general_mode_ask_context.py` | Current-principal real-host negative context probes; U4. |
| T30 | `widgetTranscript.browser.test.js` | Real-host disconnect/identity/history receipt, separately from API-mocked fixture evidence; U4/U6. |
| T31 | `test_page_frame_contracts.py`, `test_ui_theme_round_trip.py`, `themeBootstrap.browser.test.js` | Both shared title paths under two app themes; U2. |
| T32 | `web_shell/playwright/apps.responsive.smoke.spec.js`, responsive continuity tests | Page composition at 320/390/768/1280/1440, short-height and zoom; U2/U4. |
| T33 | Responsive continuity focus tests; `web_shell/playwright/` browser suite | Critical keyboard and screen-reader tasks, non-drag alternative, status/error announcements; U2/U4. |
| T34 | Theme and responsive browser fixtures | Reduced-motion/contrast/content-extreme visual and interaction review; U2/U4. |
| T35 | `test_app_acceptance_isolation.py`, `test_generated_ui_contract.py` | Executable hostile custom-preview probes in existing isolation; U4/U6. |
| T36 | `test_installed_first_run_smoke.py`, `test_selfhost_clean_install.py` | Accepted installed candidate in consumer workspace, exact pin/import receipt; U7. |
| T37 | npm `test:ui-contracts`, `test:factory-contracts`, required full Python/CI commands | Complete clean dependencies and matched base/candidate executions with clean exit; U0/all changed surfaces. |
| T38 | Transport ownership and widget/artifact restoration tests | Separate reconnect/reload/process-death/multi-worker receipts; #962 explicitly does not establish durable AG2 wait recovery. U6. |
| T39 | Existing host/isolation owners | Declared topology/workload, concurrent identities, latency/resource/recovery results not evaluated; U6. |
| T40 | Existing generation/validation controller | Bounded live-model protocol and human UX evaluation not evaluated; provider/model/spend authority required before paid calls. U6. |

Run receipts belong beside the implemented change and must record the exact
source, complete environment, commands, executed/skipped counts and process
exit. Fixture, scripted-model, mock-API, real-host and live-model evidence stay
distinct. This index does not close [#960](https://github.com/BlocUnited-LLC/mozaiks/issues/960).
