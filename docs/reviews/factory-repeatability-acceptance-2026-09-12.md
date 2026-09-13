# Factory Repeatability Acceptance

## Result

Contact Desk is a functioning authenticated app created through the normal
Factory browser UI with real AG2 and model calls. Its source was not manually
edited to pass acceptance. Genesis and subsequent repair requests went through
the Factory; fixes to the builder and runtime are part of this integration.

This is evidence of one working generated app, not first-try, unattended, or
production-ready generation. Nine explicit refinement attempts were needed
while repairing the builder. Several invalid intermediate artifacts were
correctly rejected; static validation also admitted earlier candidates whose
business behavior failed browser acceptance.

## Verified Artifact

- App: `draft-app-beb2ac7f` (Contact Desk).
- Artifact: `av_c9d94c76c73f415092b7f5e5`.
- Build: `build_513814f47a90432e8b597226365803e5`.
- ZIP SHA-256:
  `30de28edfb422800cb18dcbd82b5ffb16d48fcab982850623805be7e781f0282`.
- All 38 exported files matched their committed manifest and staged workspace.
- Normal Studio Accept and Promote actions selected this artifact. The app
  registry became active; promotion restored a separate workspace, never the
  executing Factory app. All 35 restored files matched the ZIP byte for byte.
  The existing restore policy skipped the three `.env.*example` templates;
  those templates remain in the authenticated export.
- The promoted app ran through the platform host with a production-built web
  shell and local operator configuration. MongoDB and Keycloak ran locally.
  No cloud resources or public release were created.

## Functional Checks

Fifteen real-browser/API checks passed against the final app, then passed again
with the backend running the promoted workspace:

- Two ordinary staff users signed in independently through OIDC.
- The original Genesis record survived artifact replacement and refinement.
- Create, read-only details, edit, cancel deletion, and confirm deletion worked.
- Required names were checked before writes; expected bad input returned 400.
- Optional email and multiline notes persisted. Omitted fields were preserved
  on update; explicit blank values cleared them. Duplicate email was allowed.
- IDs, owner scope, and timestamps remained server-owned. Anonymous and foreign
  reads/writes and spoofed ownership were rejected without changing records.
- Email search, empty results, reset, and pagination worked with real records.
- Dashboard counts and recent entries reflected the authenticated owner's data.
- Mobile creation, deletion, and navigation worked at 390 by 844, including a
  long unbroken record name without horizontal overflow.
- No uncaught browser JavaScript errors occurred.

An additional restart-only run verified the saved edited record after restarting
the backend from the promoted workspace, including the second user's inability
to access it. Authenticated download returned the exact ZIP digest; anonymous
download returned 401 and an unrelated registry returned 404.

## Regression Evidence

- Integrated OSS run: 17,247 passed, 98 skipped, two failures. Both failures were
  stale source/documentation assertions, subsequently corrected. Their final
  route, ownership, summary, and readiness slice passed 48 tests. The clean full
  rerun passed 17,252 tests with 98 skipped and four warnings. Final typing and
  indexing-error follow-ups are additionally covered by focused tests and CI.
- Final runtime/admin/Studio overlay before that correction: 83 passed.
- Shared frontend regression: 42 passed across all seven Node/browser files,
  covering schema actions, downloads, theme, UI response acknowledgement,
  concept review, workflow UI registration, and auth adapters. Five additional
  Playwright auth tests passed.
- CI exposed 18 typing errors in the integration; corrected compiler return
  types and explicit optional-value checks passed mypy across 623 source files.
  The affected generation/refinement slice passed 608 tests; six artifact
  registration tests include the new missing-context-before-read guard.
- Linux shard isolation exposed generic launcher tests inheriting Factory hooks
  and promotion tests invoking unrelated background indexing with partial store
  doubles. Their dependencies are now explicit. The indexing error path also
  exposed a missing contextual logger `exception()` method, now implemented and
  covered together with failed-job persistence. The combined CI-fix slice passed
  100 tests; the control-plane identity slice passed 301 tests.
- Whole-tree Ruff and `git diff --check` passed. A redacted Gitleaks scan of this
  branch's commit patches found no leaks.
- App Zero installed a local wheel built from OSS checkpoint `9c1078eb`, without
  editable imports or a source-path override: 231 product acceptance and 201
  contract tests passed; 1,406 installed files were verified and `pip check`
  passed. Its old VCS-pin assertion does not match a local wheel. The final
  reachable revision still needs the real dependency pin/install check.

Local evidence and credential-free acceptance scripts are under the ignored
`.local/factory-repeatability/` directory in the integration worktree. Identity
credentials are also local and are not committed. The generated app acceptance
checks use real persistence and authentication, not browser API mocks.

## OSS Change Impact

- Layer: shared Factory generation, runtime/platform contracts, and Studio/app
  shell behavior. Hosted commercial policy remains in Mozaiks App.
- Build workflow impact: finite ThemeCapture handoffs; strict plan validation;
  bounded implementation repair; verified refinement baseline carry-forward;
  task-scoped optional manifest output; real UI acknowledgement and navigation.
- Runtime/platform impact: immutable executing-app identity, owned build-target
  selection, durable terminal failures, and expected module-input errors.
  AG2 continues to own agent execution and its execution-history primitive.
- App workspace impact: validated declaratives and bounded implementation files
  remain canonical; promotion creates a separate app workspace. No Factory or
  hosted provider implementation is copied into customer output.
- Documentation: ADR 0009, generation/assembly, workflow ownership, persistence,
  failed-run durability, and module-input contracts were updated with tests.
- Compatibility risk: pre-1.0 internal contract replacements require coordinated
  hosted-consumer adoption. This integration does not preserve retired identity
  aliases or turn Factory records into a hosted commercial registry.

## Remaining Limits

- The built-in Docker preview was not exercised. A separately served platform
  host and production web build are not proof of that preview or deployment.
- Runtime build validation was reported as skipped where it did not execute.
  Static archive checks found no raw-secret or module-contract findings, but
  they are not a full SecurityReadiness workflow or a production clearance.
- Factory testing used an operator account. Ordinary generated-app user tests
  do not prove ordinary Factory signup, hosted checkout, or plan enforcement.
- The generated app contains deterministic CRUD, not app-owned AI workflows.
- Studio still has incomplete build summaries and broad patch-impact labels.
  App Intelligence workspace snapshots share the app-bundle family and can
  appear in build history; non-bundle projections are filtered out.
- Existing restore policy excludes environment example templates. Deployment
  convenience and the complete post-build readiness journey need separate work.
- Local usage records for this target total 128 instrumented calls and about
  $9.37 in catalog-estimated model cost, including the repair attempts. This
  excludes earlier failed Genesis targets and may omit control-plane calls.
  It is not a provider invoice or a defensible freemium allowance estimate.

The next acceptance should start a second app from a clean brief using these
fixes, measure the complete journey, and test the built-in preview and readiness
path before treating the Factory as repeatably self-service.
