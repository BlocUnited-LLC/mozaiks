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

- `status`
- `findings`
- `revision_request`
- `returncode`

Browser findings are structured:

```json
{
  "severity": "error",
  "route": "/tickets",
  "category": "layout",
  "message": "Horizontal overflow on mobile.",
  "suggested_fix": "Use ResourceTable responsive behavior or reduce columns."
}
```

Production readiness should use the same bounded status vocabulary:

- `passed` -> continue delivery
- `needs_revision` -> revise generated artifacts before promotion
- `blocked` -> user/operator review

## Why Two Gates

The AG2 gate is a contract gate. It tells agents what to fix before a broken UI
is assembled.

The browser gate is an acceptance gate. It proves that the assembled UI behaves
inside the actual shell.

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

The generated app root acceptance target is intentionally generic. It verifies
that each declarative page route renders without browser-level regressions. It
does not replace product-specific E2E tests.
