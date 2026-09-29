# Mozaiks test-suite speed and redundancy audit

Date: 2026-09-28

Base: `f4f53338` (`origin/main`)
Scope: analysis and proposal only. No tests, `pyproject.toml`, CI workflow, or test configuration were changed.

## 1. Baseline

The required serial baseline was:

```text
pytest -q --no-cov -p no:cacheprovider --durations=0 -rA > durations.txt
19907 passed, 130 skipped, 26 warnings in 2307.47s (0:38:27)
```

The worktree inherited the main checkout's `.env` through `find_dotenv`; all serial,
coverage, xdist, and shard comparisons used that same discovery behavior. The baseline
rewrote two tracked acceptance zips in `generated_artifacts/`; those writes are evidence
that a single shared worktree is not a safe parallel boundary.

### Duration profile

Pytest reported 5,828 phase records for 4,850 nodes. Reported phase time was:

| Phase | Reported time | Records |
|---|---:|---:|
| call | 1,341.36s | 4,472 |
| setup | 641.18s | 1,268 |
| teardown | 32.30s | 88 |
| reported total | 2,014.84s | 5,828 |
| wall time | 2,307.47s | 19,907 tests |

The 292.63s difference is pytest/unreported sub-threshold and process overhead. The
share figures below are lower bounds because they use reported durations against the
full wall-time denominator:

| Slowest tests | Reported time | Wall-time share |
|---:|---:|---:|
| top 1% (200 tests) | 1,192.93s | 51.70% |
| top 5% (996 tests) | 1,891.11s | 81.96% |
| top 10% (1,991 tests) | 1,965.11s | 85.16% |

Top 50 tests, using aggregate setup/call/teardown time where pytest emitted a phase
record:

| # | Test | Seconds |
|---:|---|---:|
| 1 | `tests/test_ag2_terminology_hygiene.py::test_active_repo_surfaces_do_not_reintroduce_legacy_ag2_terms` | 70.30 |
| 2 | `tests/test_runtime_database_indexes.py::test_platform_startup_applies_indexes_when_data_contract_is_loaded` | 30.44 |
| 3 | `tests/test_artifact_revision_store.py::test_refinement_siblings_use_parent_and_generation_cas` | 26.44 |
| 4 | `tests/test_app_auth_generation.py::test_validation_materializes_auth_from_admitted_repairs_idempotently` | 21.57 |
| 5 | `tests/test_artifact_revision_store.py::test_stale_generation_and_aba_attempt_leave_current_unchanged` | 21.48 |
| 6 | `tests/test_artifact_revision_store.py::test_persist_cold_resolve_restore_and_idempotency` | 21.20 |
| 7 | `tests/test_suite_order_independence.py::test_dotted_path_monkeypatch_survives_studio_import_in_either_order` | 19.46 |
| 8 | `tests/test_suite_order_independence.py::test_workflow_catalog_survives_tool_loader_then_persistence_policy_restore` | 17.13 |
| 9 | `tests/test_suite_order_independence.py::test_dotted_path_monkeypatch_pair_passes_repeatedly_in_inverted_order` | 16.98 |
| 10 | `tests/test_plan_authority_enforcement.py::test_fresh_process_cold_resolution_repeats_rederivation` | 14.05 |
| 11 | `tests/test_ai_research_workspace_golden_path.py::test_ai_research_workspace_offline_golden_path` | 13.24 |
| 12 | `tests/test_appgenerator_assembly_failures.py::test_owned_module_failure_repairs_overlay_before_real_reassembly_and_validation` | 11.77 |
| 13 | `tests/test_artifact_revision_store.py::test_same_revision_digest_cannot_hide_unequal_stored_document` | 11.71 |
| 14 | `tests/test_artifact_revision_store.py::test_missing_blob_foreign_app_and_stale_manifest_fail_closed` | 11.71 |
| 15 | `tests/test_artifact_revision_store.py::test_swapped_blob_locations_fail_exact_digest_verification` | 11.26 |
| 16 | `tests/test_structured_output_provider_boundary.py::test_canonical_model_schema_is_stable_across_process_and_provider_order` | 11.16 |
| 17 | `tests/test_workspace_app_intelligence_acceptance.py::test_active_workspace_app_intelligence_feeds_refinement_and_chat_ui` | 11.16 |
| 18 | `tests/test_structured_output_provider_boundary.py::test_nested_exact_closure_is_independent_of_process_hash_order` | 10.86 |
| 19 | `tests/test_artifact_revision_store.py::test_modified_blob_and_foreign_scope_fail_restore` | 10.81 |
| 20 | `tests/test_artifact_revision_store.py::test_concurrent_genesis_has_exactly_one_winner_and_retry_is_idempotent` | 10.76 |
| 21 | `tests/test_structured_output_canonical_identity.py::test_cold_process_and_hash_seed_preserve_identity` | 10.75 |
| 22 | `tests/test_appgenerator_recovery_resume.py::test_readiness_user_reply_materializes_auth_before_real_validation[live]` | 10.74 |
| 23 | `tests/test_appgenerator_recovery_resume.py::test_readiness_user_reply_materializes_auth_before_real_validation[reopen]` | 10.55 |
| 24 | `tests/test_app_family_materialization_b2a.py::test_cross_process_determinism` | 10.47 |
| 25 | `tests/test_suite_order_independence.py::test_workspace_resolution_identical_before_and_after_host_import` | 9.18 |
| 26 | `tests/test_compilation_plan_authority.py::test_config_bearing_plan_digest_is_hashseed_independent[1]` | 8.33 |
| 27 | `tests/test_suite_order_independence.py::test_global_workflow_catalog_survives_a_workspace_scoped_reinitialization` | 8.22 |
| 28 | `tests/test_host_import_isolation.py::test_real_studio_boot_binds_factory_workflow_catalog_for_external_workspace` | 8.17 |
| 29 | `tests/test_slice_5c_offline_golden.py::test_offline_revision_publication_restore_and_loader_golden` | 8.10 |
| 30 | `tests/test_compilation_plan_authority.py::test_config_bearing_plan_digest_is_hashseed_independent[2]` | 8.02 |
| 31 | `tests/test_compilation_plan_authority.py::test_config_bearing_plan_digest_is_hashseed_independent[12345]` | 7.95 |
| 32 | `tests/test_app_plan_task_requirements.py::test_omitted_selected_packs_are_constructed_without_selecting_catalog_entries[framework_pack]` | 7.77 |
| 33 | `tests/test_app_plan_constructed_structure.py::test_complete_correct_plan_passes_unchanged` | 7.24 |
| 34 | `tests/test_structured_output_canonical_identity.py::test_fixed_plan_authority_is_independent_of_ambient_descriptors` | 7.20 |
| 35 | `tests/test_host_import_isolation.py::test_studio_import_registers_startup_bootstrap_without_running_it` | 7.02 |
| 36 | `tests/test_app_plan_task_requirements.py::test_omitted_selected_packs_are_constructed_without_selecting_catalog_entries[managed_capability]` | 6.99 |
| 37 | `tests/test_ag2_watchpoint_governance.py::test_private_api_register_is_complete_and_protected` | 6.98 |
| 38 | `tests/test_host_import_isolation.py::test_caller_env_survives_studio_import_unchanged` | 6.98 |
| 39 | `tests/test_host_import_isolation.py::test_plain_studio_import_leaves_process_state_byte_identical` | 6.97 |
| 40 | `tests/test_ag2_watchpoint_governance.py::test_retired_classic_ag2_agent_apis_are_not_imported_by_production` | 6.96 |
| 41 | `tests/test_app_plan_task_requirements.py::test_omitted_selected_packs_are_constructed_without_selecting_catalog_entries[operator_pack]` | 6.96 |
| 42 | `tests/test_artifact_revision_contracts.py::test_revision_contract_is_closed_required_nullable_and_deterministic` | 6.59 |
| 43 | `tests/test_deterministic_page_materialization.py::test_cross_process_determinism` | 6.52 |
| 44 | `tests/test_executable_plan_contracts.py::test_structured_output_schema_identity_invalidates_agent_author_reuse` | 5.88 |
| 45 | `tests/test_suite_order_independence.py::test_import_module_directly_leaves_no_fabricated_parent_stubs` | 5.84 |
| 46 | `tests/test_app_plan_module_task_capability_repair.py::test_drift_repair_composes_with_task_identity_and_managed_facade_repairs` | 5.43 |
| 47 | `tests/test_artifact_revision_contracts.py::test_every_authoritative_revision_digest_changes_identity[bundle_digest]` | 5.38 |
| 48 | `tests/test_app_plan_constructed_structure.py::test_minimal_judgment_plan_constructs_all_omitted_structure[wrong_labels]` | 5.15 |
| 49 | `tests/test_app_plan_constructed_structure.py::test_missing_or_repeated_task_ids_compose_with_omitted_structure[repeated_task_type]` | 5.13 |
| 50 | `tests/test_app_plan_managed_facade_repair.py::test_facade_descriptor_display_entities_do_not_invent_app_persistence` | 5.11 |

Top 30 files by reported phase time:

| # | File | Tests | Seconds |
|---:|---|---:|---:|
| 1 | `tests/test_artifact_revision_store.py` | 16 | 142.52 |
| 2 | `tests/test_app_plan_review.py` | 34 | 120.76 |
| 3 | `tests/test_app_plan_task_requirements.py` | 23 | 93.38 |
| 4 | `tests/test_app_plan_url_hints.py` | 22 | 82.94 |
| 5 | `tests/test_suite_order_independence.py` | 7 | 78.34 |
| 6 | `tests/test_ag2_terminology_hygiene.py` | 1 | 70.30 |
| 7 | `tests/test_app_plan_module_task_capability_repair.py` | 18 | 69.98 |
| 8 | `tests/test_app_plan_managed_facade_repair.py` | 16 | 66.42 |
| 9 | `tests/test_app_plan_closed_inventory.py` | 16 | 64.04 |
| 10 | `tests/test_app_plan_constructed_structure.py` | 13 | 57.79 |
| 11 | `tests/test_compilation_plan_authority.py` | 42 | 46.61 |
| 12 | `tests/test_app_plan_task_identity_repair.py` | 11 | 45.65 |
| 13 | `tests/test_plan_authority_enforcement.py` | 14 | 44.05 |
| 14 | `tests/test_app_family_materialization_b2a.py` | 66 | 42.51 |
| 15 | `tests/test_artifact_revision_contracts.py` | 9 | 34.50 |
| 16 | `tests/test_plan_assignment_compiler.py` | 13 | 32.52 |
| 17 | `tests/test_trusted_build_context_handoff.py` | 18 | 31.39 |
| 18 | `tests/test_runtime_database_indexes.py` | 2 | 30.47 |
| 19 | `tests/test_host_import_isolation.py` | 12 | 29.16 |
| 20 | `tests/test_app_auth_generation.py` | 23 | 27.33 |
| 21 | `tests/test_appgenerator_recovery_resume.py` | 8 | 27.30 |
| 22 | `tests/test_structured_output_provider_boundary.py` | 4 | 22.07 |
| 23 | `tests/test_app_validation_strategy.py` | 32 | 20.80 |
| 24 | `tests/test_structured_output_canonical_identity.py` | 7 | 18.00 |
| 25 | `tests/test_appgenerator_download_admission.py` | 8 | 17.85 |
| 26 | `tests/test_session_cold_imports.py` | 5 | 17.68 |
| 27 | `tests/test_appgenerator_export_snapshot.py` | 19 | 17.64 |
| 28 | `tests/test_executable_plan_contracts.py` | 14 | 16.09 |
| 29 | `tests/test_composition_ledger.py` | 9 | 15.68 |
| 30 | `tests/test_persistence_initial_messages.py` | 9 | 15.19 |

### Reruns and order safety

The rerun-free command was:

```text
pytest -q --no-cov -p no:cacheprovider --reruns 0 -rA > reruns0.txt
19907 passed, 130 skipped, 26 warnings in 1944.99s (0:32:24)
```

There were no failures, so there is no rerun-only or intermittent-failure list from
this run. This is evidence against a currently reproducible flaky test, not proof that
the suite can never flake.

The explicit order probe was:

```text
pytest -q --no-cov -p no:cacheprovider --reruns 0 tests/test_suite_order_independence.py
7 passed in 112.11s (0:01:52)
```

The current checkout has 28 literal `sys.modules.pop` matches in 17 files, of which
27 are executable calls and one is the explanatory comment in
`tests/test_suite_order_independence.py`. The task description's “29 sites” count does
not match this checkout. The order probe passed, and the call sites are predominantly
intentional cleanup of synthetic imports or import-isolation fixtures. They remain
KEEP guards; no FIX is proposed from this measurement.

## 2. Proposed changes

The coverage run was:

```text
pytest -q --reruns 0 -p no:cacheprovider \
  --cov=mozaiksai --cov=factory_app --cov-context=test \
  --cov-report=term-missing --cov-fail-under=0 > coverage-run.txt
19907 passed, 130 skipped, 26 warnings in 4038.55s (1:07:18)
```

The first exact `coverage json --show-contexts -o coverage.json` attempt stopped at
the repository's non-Python template `factory_app/build_context/infra/templates/scripts/cron_tick.py`.
The successful export was:

```text
coverage json --show-contexts --ignore-errors -o coverage.json
```

It produced a 27.8 GB context JSON. The database-backed analysis of the same coverage
data found 19,941 test-phase contexts, 2,309 tests with at least one source line unique
to that test, and 17,632 with no unique source line. “No unique line” is not removal
evidence: it includes prompt/catalog scans, subprocess guards, generated-artifact
checks, and tests whose assertion is over serialized or fixture data rather than a
Python line in the two measured packages.

Package coverage from the same run:

| Scope | Statements | Missing | Coverage |
|---|---:|---:|---:|
| `mozaiksai` | 68,703 | 12,796 | 81% |
| `factory_app` | 26,571 | 5,302 | 80% |
| combined | 95,274 | 18,098 | 81% |

The following are the only changes proposed from this audit. Estimates are measured
hotspot-based ranges, not promises; each implementation PR must rerun the full suite
and coverage before being accepted.

| File/test cluster | Class | Evidence and guard decision | Estimated saving |
|---|---|---|---:|
| `tests/test_ag2_terminology_hygiene.py::test_active_repo_surfaces_do_not_reintroduce_legacy_ag2_terms` | SPEED UP | 70.30s, 0 unique measured source lines, but it is the governance/hygiene guard over the complete tracked repository. Cache the one tracked-file enumeration and text scan for the invocation; preserve every path and forbidden-term assertion. | 40–60s |
| `tests/test_runtime_database_indexes.py::test_platform_startup_applies_indexes_when_data_contract_is_loaded` | SPEED UP | 30.44s, 0 unique lines, and a startup/database-index seam guard. Move only immutable import/bootstrap setup to module scope or isolate the cold-import assertion from the repeated fake-index assertion; do not replace the real startup seam with a mock. | 20–25s |
| `tests/test_artifact_revision_store.py` | SPEED UP | 142.52s across 16 integrity/CAS/foreign-scope tests; 7 tests have 16 unique lines in aggregate and 9 have none, but the cluster contains fail-closed revision and tamper guards. Reuse immutable fixture descriptions and clone per-test mutable state; retain independent clients, blobs, CAS races, and digest checks. | 30–60s |
| `tests/test_app_plan_review.py`, `test_app_plan_task_requirements.py`, `test_app_plan_url_hints.py`, `test_app_plan_module_task_capability_repair.py`, `test_app_plan_managed_facade_repair.py`, `test_app_plan_closed_inventory.py`, `test_app_plan_constructed_structure.py` | SPEED UP | Combined reported time 555.51s. Unique-line counts are sparse (for example: review 16, task requirements 3, URL hints 0, capability repair 5, managed facade 1, closed inventory 0, constructed structure 2), but these are canonical plan/schema/closed-inventory contracts. Share read-only catalog/schema setup and avoid repeated full bundle construction; keep every distinct taxonomy, repair, and rejection branch. | 110–195s |
| `tests/test_suite_order_independence.py` | SPEED UP | 78.34s across 7 tests, 0 unique source lines because the important assertion is process/import-state behavior. Reduce subprocess startup only where one subprocess can run the same ordered matrix; retain both orders, repeated inversion, fabricated-parent cleanup, and catalog restoration. | 25–45s |
| `tests/test_compilation_plan_authority.py`, `test_plan_authority_enforcement.py`, `test_structured_output_provider_boundary.py`, `test_structured_output_canonical_identity.py` | SPEED UP | Cross-process/hash-seed/provider-order guards; aggregate reported time is 130.68s. Batch independent hash-seed cases in one controlled subprocess harness only if each result remains independently asserted. | 20–40s |
| AST-identical helper bodies found across helper test files | MERGE | Static AST scan found exact-body duplicates such as empty-list helpers and repeated content-store assertions. This is a lead, not proof: several are different canonical owners or contract surfaces. No merge is approved until each pair has zero guard role, zero unique coverage, and the same production seam; preserve distinct parameter branches. | 0–20s, gated |
| Zero-unique tests generally | REMOVE | None proposed. The measured zero count is not enough: no retiring PR or exact duplicate plus guard analysis was established for a safe deletion. | 0s |
| Rerun/order candidates | FIX | None found: the no-rerun run and explicit order probe were green. Keep the order guards and revisit only with a reproducible failure. | 0s |

No assertion is weakened, no live seam is replaced with a mock, and no test is removed
by this proposal. The “MERGE” row is a gated follow-up analysis, not authorization to
edit the tests now.

## 3. Guard inventory and gaps

The inventory below maps the non-negotiable categories to the current guard families.
These remain KEEP even when their measured unique-line count is zero.

| Guard category | Current tests/fixtures |
|---|---|
| Auth, authorization, tenancy, row ownership, fail-closed dispatch | `tests/test_auth_adapters.py`, `test_auth_config.py`, `test_auth_dependencies.py`, `test_auth_generic_jwt_adapter.py`, `test_auth_keycloak_adapter.py`, `test_auth_oidc_discovery.py`, `test_auth_principal_helpers.py`, `test_module_dispatch_authority_policy.py`, `test_module_dispatch_authority_contract.py`, `test_module_router_context_overrides.py`, `test_runtime_owner_scoping_*`, `test_tenant_isolation_*`, `test_security_hardening.py`, `test_subscriptions_fail_closed_app_loading.py`, `test_structured_output_fail_closed.py`, `test_context_authority_hostile.py`, `test_plan_authority_enforcement.py` |
| Entitlement and subscription gates | `test_entitlement_gate_closure.py`, `test_configured_entitlement_adapter.py`, `test_entitlement_drift.py`, `test_entitlement_drift_guards_generated_app.py`, `test_platform_entitlement_gate_validation.py`, `test_self_hosted_entitlement_chain.py`, `test_subscription_*`, `test_generated_app_*subscription*` |
| Secrets and credential handling | `test_app_secret_contract.py`, `test_app_secrets_helpers.py`, `test_refinement_secret_contract.py`, `test_path_ownership_secret_contract.py`, `test_appgenerator_deployment_contract.py`, `test_connector_*`, `test_security_readiness_*` |
| WebSocket auth, identity, eviction, replay, keepalive | `test_websocket_subprotocol_auth.py`, `test_runtime_websocket_contract.py`, `test_ws_eviction_preserves_execution.py`, `test_ws_identity_is_not_reused.py`, `test_ws_inbound_keepalive.py`, `test_ws_protocol_replay_suppression.py` |
| Recorded/live runs and real fixtures | `test_run_replay.py`, `test_designdocs_live_platform_auth_replay.py`, `test_managed_capability_artifact_replay.py`, `test_agentgenerator_pending_turn_replay.py`, `test_workspace_app_intelligence_acceptance.py`, `tests/fixtures/designdocs_live_*.json`, `live_conceptual_replan_output.json`, `refinement_live_coding_worker*.json`, and the workflow-interface corpora |
| Structured-output and prompt/runtime contracts | `test_structured_output_*`, `test_exact_structured_output_runtime.py`, `test_workflow_declarative_contracts.py`, `test_factory_workflow_context_contracts.py`, `test_valueengine_prompt_matches_its_schema.py`, `test_agentgenerator_semantic_prompt_inputs.py`, `test_workflow_interface_corpus_identity.py` |
| Module/page/data contracts and materialization | `test_module_loader_contracts.py`, `test_module_contract_quality_gate.py`, `test_module_contracts*`, `test_page_*contract*`, `test_runtime_data_contract_loader.py`, `test_runtime_data_contract_e2e.py`, `test_appgenerator_*contract*`, `test_app_auth_contract.py`, `test_app_backend_admin_contract.py`, `test_generated_app_functional_acceptance.py` |
| Catalogs, packs, build contexts, governance, hygiene, layering | `test_capability_pack_*`, `test_build_context_*_pack.py`, `test_hook_domain_catalog_context.py`, `test_hook_file_contract_context.py`, `test_pack_*`, `test_workflow_catalog_contract.py`, `test_factory_build_context_contract.py`, `test_ag2_terminology_hygiene.py`, `test_ag2_watchpoint_governance.py`, `test_governance_guardrails.py`, `test_oss_boundary_policy.py`, `test_host_import_isolation.py`, `test_session_cold_imports.py`, `test_registered_hooks_match_their_runners.py`, `test_package_content_guard.py` |
| Documented incident/regression guards | Examples include `test_host_import_isolation.py` (PR #418), `test_module_router_context_overrides.py` (real authorization defect), `test_cli_gen_loud_failures.py` (#379), `test_cli_gen_conversational_refusal.py` (#383), `test_ws_eviction_preserves_execution.py` / `test_ws_identity_is_not_reused.py` (#576), `test_appplan_reads_the_contract_fallback.py` (#715/#716), `test_appgenerator_task_batch_status_writers.py` (#678), and the subscription/context/hook guards citing their historical PRs. |

### Confirmed gaps

1. `mozaiksai/hosts/routers/oauth_github.py` is an exposed OAuth/token route family
   (`/status`, `/connect`, `/callback`, `/ready`, `/repos`, and the development seed
   route), but there is no `tests/test_*oauth*` or `tests/test_*github*` router test.
   Coverage shows 26% for the file (99 of 133 statements missing in the measured
   report). The missing guard should cover state validation, denial, token-exchange
   failure, one-time/TTL consumption, session-key handling, and repo-list failure
   without storing raw tokens.
2. Several host routers have substantial uncovered branches with no direct route-test
   family found by the file inventory: `hosts/routers/chat.py` (20%), `media.py` (24%),
   `notifications.py` (36%), and `sessions.py` (41%). These are coverage gaps to turn
   into security/behavior test proposals, not redundancy candidates. The audit does not
   authorize deleting tests to offset them.

## 4. Parallelism plan

### Measurements

Two consecutive full xdist runs used the same dotenv discovery and command:

```text
pytest -q --no-cov -p no:cacheprovider --reruns 0 -n auto --dist loadfile > xdist1.txt
19907 passed, 130 skipped, 38 warnings in 405.15s (0:06:45)

pytest -q --no-cov -p no:cacheprovider --reruns 0 -n auto --dist loadfile > xdist2.txt
19907 passed, 130 skipped, 26 warnings in 447.14s (0:07:27)
```

The pass count matched serial in both runs. This proves repeatability on this host, but
does not by itself make xdist the CI default: workers still need explicit filesystem and
database isolation, and `loadfile` does not provide a global inter-file order.

The eight CI shard selections were run concurrently in eight separate worktrees. The
first attempt also changed database-name environment variables and produced false
failures in tests that intentionally assert default names; it is rejected as a
comparison. The valid repeat used the same dotenv/database environment as serial and
only gave each worktree a distinct `MOZAIKS_GENERATED_ARTIFACTS_PATH`:

| Shard | Passed | Skipped | Wall time |
|---:|---:|---:|---:|
| 0 | 3,081 | 2 | 6:12 |
| 1 | 2,544 | 30 | 4:39 |
| 2 | 2,513 | 48 | 2:52 |
| 3 | 2,143 | 13 | 8:11 |
| 4 | 2,526 | 13 | 5:49 |
| 5 | 2,377 | 9 | 5:58 |
| 6 | 2,262 | 8 | 6:40 |
| 7 | 2,461 | 7 | 7:12 |
| **total** | **19,907** | **130** | **8:18 max shard** |

All eight exits were zero. The first invalid attempt demonstrates that per-shard DB
environment variables cannot be introduced without making default-name tests explicit;
the valid run demonstrates that separate worktrees and artifact roots are sufficient for
this current shard set, while the Mongo databases remained shared.

### Required isolation before enabling local/CI parallelism

* Use one worktree or checkout directory per shard, never one directory with shared
  relative outputs.
* Set `MOZAIKS_GENERATED_ARTIFACTS_PATH` to a per-shard absolute temporary root. Do not
  write the tracked acceptance zips concurrently; acceptance tests should copy fixtures
  into that root or use a per-shard artifact fixture.
* The current `mozaiksai` system database is a fixed constant in several persistence
  paths, and refinement tracking also has a fixed database name. Per-shard application
  DB variables alone do not isolate all state. A production parallel harness needs a
  separate Mongo service/port or a supported per-shard system-database injection, plus
  explicit cleanup. Do not silently rename the DB in tests that verify defaults.
* Keep pytest temporary directories per worker and make any relative logs, uploads, and
  generated bundles resolve under the worker root.
* For xdist, the 17 `sys.modules.pop` files (27 executable sites), environment mutation,
  global workflow/catalog singletons, fixed Mongo collections, and cross-process import
  tests are the blockers to audit. Each needs either worker-process isolation, a
  process-local fixture/reset, or an explicit test marker that remains serial. `--dist
  loadfile` handles file grouping but not shared external state.

## 5. Projected result

These are targets for approved follow-up PRs, not claims that unimplemented changes have
already saved time:

| Metric | Current measured | Projection after approved speedups |
|---|---:|---:|
| serial wall time | 38:27 baseline; 32:24 rerun-free | 28–33 min |
| xdist wall time | 6:45 / 7:27 | 5–7 min after isolation and hotspot work |
| eight-shard wall time | 8:18 max shard | 6–7 min after hotspot work and retained isolation |
| pass count | 19,907 | 19,907 |
| skipped count | 130 | 130 |
| `mozaiksai` coverage | 81% | 81% or higher; must not drop |
| `factory_app` coverage | 80% | 80% or higher |
| combined coverage | 81% | 81% or higher, well above CI's 70% gate |

The projection assumes no test removal and no assertion weakening. Any MERGE proposal
must demonstrate unchanged branch coverage and the same guard inventory before its
projection can be counted.

## 6. Proposed PR sequence

1. **FIX**: no PR required from this run; keep the existing order guards. If a future
   rerun/order failure reproduces, fix it first and prove two rerun-free full passes.
2. **SPEED UP**: hygiene scan, startup/index setup, revision-store fixture construction,
   app-plan read-only setup, and subprocess matrices. Each PR stays focused on one
   cluster, runs the full suite, and reports `mozaiksai`/`factory_app` coverage.
3. **MERGE**: review the AST duplicate leads one pair at a time. A merge requires exact
   duplicate evidence, no guard role, no unique coverage, and unchanged full-suite
   coverage. Do not merge the plan/catalog variants merely because their measured
   unique-line count is zero.
4. **REMOVE**: none proposed now. A later removal requires the user's explicit
   per-test sign-off plus zero unique coverage, no guard role, and either a retiring
   commit/PR or an exact duplicate identity.
5. **Parallel tooling**: after the serial PRs are green, add only the required
   per-worker artifact/database isolation and repeat the two xdist runs plus the eight
   shard run. Keep the CI 8-shard order-preserving split until that evidence is accepted.

## Commands and evidence index

All commands were run from the fresh worktree
`.local/worktrees/test-suite-audit`, except shard commands run from their named sibling
worktrees. The starting SHA was `f4f53338`. The raw measurement files were intentionally
not added to the repository because the context JSON was 27.8 GB; the exact terminal
summaries and derived measurements are recorded above.
