# UI System Quality Gates

Mozaiks has two UI validation layers. They solve different problems and should
not be collapsed into one loop.

## 1. AG2 Quality Gate

The AG2 quality gate runs before assembly. It is deterministic and cheap.

Purpose:

- stop unsupported primitive names
- stop removed generated primitives such as `Card`, `Stat`, and `Badge`
- stop noisy generated UI patterns before files are finalized
- produce agent-readable revision feedback

AppGenerator state:

- `app_ui_quality_warnings`
- `app_ui_quality_status`
- `app_ui_quality_revision_request`
- `app_ui_quality_revision_count`

Agent routing:

- `passed` -> assemble app artifacts
- `needs_revision` -> send the revision request back to `AppSchemaAgent`
- `blocked` -> user/operator review

This gate catches contract problems. It does not prove the browser render is
perfect.

## 2. Browser Acceptance Gate

Browser acceptance runs after artifacts exist. Playwright is the current runner.

Purpose:

- catch route/render failures
- catch `console.error` and `pageerror`
- catch invisible headings and missing main content
- catch horizontal overflow
- catch broken declarative form behavior
- catch mobile regressions that a schema audit cannot see

Runner:

- `scripts/generated_ui_acceptance.py`
- `npm --prefix web_shell run test:generated-ui:app`

Output:

- `validation_run` with ordered gate results and canonical issues
- `repair_decision` with `accept`, `repair`, or `block`
- `returncode`

Browser findings are structured:

```json
{
  "gate_id": "generated_ui_browser",
  "code": "layout",
  "severity": "error",
  "route": "/tickets",
  "message": "Horizontal overflow on mobile.",
  "suggested_fix": "Use ResourceTable responsive behavior or reduce columns.",
  "repair_owner": "AppSchemaAgent"
}
```

The canonical acceptance controller owns the bounded routing decision:

- `accept` -> continue delivery
- `repair` -> revise generated artifacts within budget
- `block` -> user/operator review

## Why Two Gates

The AG2 gate is a contract gate. It tells agents what to fix before a broken UI
is assembled.

The browser gate interprets evidence for its declared workload. Mocked rendering
does not prove module effects, authorization, persistence or real-host behavior.

Keeping them separate prevents noisy retry loops. Playwright is not registered
as a live AppGenerator tool or handoff. A primitive violation should not require
launching a browser. A browser-only failure should not pollute the primitive
catalog rules.

## Current Playwright Targets

Fixture acceptance:

```powershell
npm --prefix web_shell run test:generated-ui
```

Generated app root acceptance:

```powershell
$env:MOZAIKS_GENERATED_UI_APP_ROOT="generated/apps/{app_id}/{build_id}/app"
npm --prefix web_shell run test:generated-ui:app
```

Structured readiness output:

```powershell
python scripts/generated_ui_acceptance.py `
  --app-root "generated/apps/{app_id}/{build_id}/app" `
  --output generated-ui-acceptance.json
```

The default mode is `mocked_rendering_smoke`. The generic specification uses
mocked shell/API responses and visits declarative routes. Its result is rendering
smoke evidence, not full-app or custom-JSX acceptance.

The Python runner rejects missing/malformed JSON, inconsistent reporter totals,
zero executed cases, required skips and failed attempts (including a later retry
that passes). It uses the existing `ValidationRegistry` and
`AcceptanceController`; it does not maintain a second repair state machine.
Harness, coverage and identity failures use that controller with zero automatic
repair attempts and no page-worker repair owner. Actual render failures retain
the existing bounded `AppSchemaAgent` repair decision.

### Reviewed real-host route probes

`--mode real_host` uses an already-running loopback frontend and real shell/API
responses. The caller owns process startup, isolation, synthetic data and cleanup.
This mode does not start or admit executable generated code. It requires a clean,
committed OSS checkout, an app bundle and a reviewed JSON test profile:

```json
{
  "candidate": {
    "app_id": "example",
    "revision": "accepted-revision",
    "source_sha": "<40 lowercase hex characters>",
    "bundle_sha256": "<bundle_digest(app_root)>"
  },
  "routes": [
    {"path": "/tasks", "component": "SchemaPage", "schema": "tasks", "kind": "declarative", "heading": "Tasks"},
    {"path": "/board", "component": "TaskBoard", "kind": "custom", "heading": "Planning board"},
    {"path": "/me", "component": "ProfilePage", "kind": "extension", "excluded_reason": "Separate account regression profile"}
  ],
  "cases": [
    {"id": "route:/tasks", "route": "/tasks", "project": "generated-desktop"},
    {"id": "route:/tasks", "route": "/tasks", "project": "generated-mobile"},
    {"id": "route:/board", "route": "/board", "project": "generated-desktop"},
    {"id": "route:/board", "route": "/board", "project": "generated-mobile"}
  ]
}
```

This is an illustrative test profile, not a generated-app schema or a runnable
fixture. Its route inventory must match the actual composed shell, including
auth, workflow and extension routes. Non-app exclusions require a reason before
execution. Declarative/custom app routes cannot be excluded. Required routes need
a reviewed heading and a case in both projects; parameterized routes also need a
matching concrete `visit_path`. Existing schema and custom-registration owners
validate app routes before dispatch. Missing files, registrations, routes, cases
or mismatched local identities fail the gate.

```powershell
python scripts/generated_ui_acceptance.py `
  --mode real_host --app-root "<admitted app>" --profile "<reviewed profile.json>" `
  --base-url "http://127.0.0.1:<port>" --output "<receipt.json>"
```

Optional `--storage-state` uses existing legitimate browser authentication; keep
that file private. It only restores Playwright-supported state, not
`sessionStorage`. The current Mozaiks OIDC adapter uses `sessionStorage`, so its
trusted app-specific specification must complete the existing real UI sign-in.
Optional `--spec` selects a trusted app-specific specification
under `web_shell/playwright/generated-ui/`. The default specification only checks
actual shell identity/route registration, navigation, reviewed headings and browser
errors. It does not prove create/edit, keyboard interactions or persisted effects.
Product cases must exercise those real actions and emit the same `mozaiks.case_id`,
`mozaiks.route`, `mozaiks.run_id` and `mozaiks.candidate` annotations after success.
The runner checks exact case/project counts, run correlation and candidate values.
Both runner and browser verify the reviewed profile hash; the runner rechecks
profile, checkout and app bytes after execution. Direct Playwright exit zero is
not an acceptance-controller result.

The external-host CLI cannot attest that the running
backend and compiled frontend use the locally reviewed source and bundle. Echoed
candidate annotations are not runtime provenance. It therefore always reports
`runtime_identity: not_evaluated` and an identity finding through the canonical
controller; aggregate `success` remains false even when `route_probe_passed` is
true. There is no CLI flag or serialized receipt to bypass this gate. Route
counts and failures remain available independently.

The existing Community local acceptance driver can also run a generated workspace
through `run(args, evidence, exercise=...)`. It starts disposable Mongo, Keycloak
and the platform host with explicit environments. Its `frontend_preview` context
builds that same app and owns a strict-port Vite preview. An in-process
`OwnedBrowserLaunch.from_driver(...)` connects those process handles and launch
inputs to `run_playwright_acceptance(..., owned_launch=...)`. The runner verifies
the clean source revision, pre-launch bundle bytes, child origins/auth settings,
live processes and served build bytes before and after the browser workload.
This controlled local path can report `runtime_identity: verified_owned_launch`;
it is not an attestation against a hostile local process owner.

Run the opt-in paired scripted fixture from a clean committed checkout with
isolated Python/frontend dependencies, cached `mongo:7` and
`quay.io/keycloak/keycloak:26.0` images, and Playwright Chromium installed:

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
$env:MOZAIKS_PAIRED_BROWSER='1'
.venv/Scripts/python.exe -m pytest tests/test_paired_experience_browser.py -q --no-cov --reruns 0
```

Each presentation gets a fresh database and real Keycloak browser sign-in.
Unique local evidence directories retain the profile, exact app bytes, raw
browser report, screenshots/traces and controller result, including failures.
The synthetic identity environment is private local test material. The fixture
proves only its declared routes and interactions; it is scripted AG2 generation,
not live-model generation. It selects the verified Messaging pack before plan
admission and records its version, digest and actual composed outputs. Public
action permissions come from the admitted modules in a private synthetic realm;
runtime authorization remains enabled. Pack browser cases exercise persisted
conversation actions and reject a non-participant. Projects, Tasks and the board
use bounded server paging and literal search, with more records than the API's
default first-page limit. Desktop board tests
exercise pointer drag and keyboard status changes; mobile uses the native status
control. Touch dragging is not established. Inherited profile changes persist
through `/api/me`, and assistant session setup is exercised without sending model
requests or starting workflows.

Browser success does not replace `validate_generated_app_candidate`. The
canonical candidate validator also checks reachable declared write actions,
module quality, contained loading, real runtime behavior and compilation. The
module quality owner treats TODO comments, docstrings and explanatory messages
as unfinished code while allowing the exact `todo` domain value in defaults and
enumerations; other placeholder and fake-data checks remain unchanged.

The opt-in `tests/test_paired_browser_refinement.py` uses a saved accepted B
browser candidate, validates it with the canonical Docker strategy, saves it
through the existing artifact owner and submits the selected board source to
the authorized Studio refinement endpoint. Only the AG2 provider responses are
scripted. Mongo/GridFS, JWT checks, owner checks, scoped finalization and candidate
validation remain real. It saves a child review draft, verifies all unaffected
source bytes, and runs the child through the same owned browser acceptance with
mobile bottom-panel assertions. Only after that passes does the authenticated
owner accept the draft through the existing endpoint. It does not promote the
app. The pack-inclusive child repeats the same pack behavior and records hashes
for changed and retained files, including pack outputs, contracts, routes and
theme. The effective provider requests capture the worker's saved source and
read-only design/pack context. Upstream design documents absent from this scripted
fixture are explicitly marked absent. This route integration does not qualify
full Studio startup or live-model creativity.

```powershell
$env:MOZAIKS_PAIRED_REFINEMENT='1'
$env:MOZAIKS_PAIRED_BROWSER_BASELINE='<accepted B evidence directory>'
$env:MOZAIKS_APP_RUNTIME_IMAGE_ID='sha256:<canonical local image digest>'
$env:MOZAIKS_PAIRED_VALIDATOR_SOURCE_SHA='<verified image source commit>'
.venv/Scripts/python.exe -m pytest tests/test_paired_browser_refinement.py -q --no-cov --reruns 0
```

Keep source inspection, authored fixtures, scripted AG2 output, admitted
materialization, real-host interactions, actual refinement and live-model output
as separate evidence categories. A complete route probe is only one part of
the application's declared acceptance workload.
