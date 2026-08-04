# App Overview Page — Review & Readiness Report

**Scope:** `/apps/:appId/overview` — `AppOverviewPage.jsx`  
**Date:** 2026-07-11  
**Reviewer:** Internal  
**Last reconciled against code:** 2026-07-11

---

## Scores

| Dimension | Score | Target |
|---|---|---|
| **Production Readiness** | 7 / 10 | 10 |
| **Enterprise Grade** | 2 / 10 | 10 |
| **UI / UX** | 6 / 10 | 10 |

---

## What's already in the current code

These items from the original review are resolved in `AppOverviewPage.jsx` as it stands:

| Item | How it's resolved |
|---|---|
| `next_step` always showing fallback | Uses `getLifecycleGuidance(lifecycle)` — reads real lifecycle-aware text |
| `total_cost` wrong source | Reads `snapshot.usage?.totals?.estimated_cost_usd` from the usage endpoint, not stats |
| `approval_state` pills always "Not started" | Conditional `{(approvalState || planState) && …}` — hides pills when build is `{}` |
| No relative timestamps | `formatRelativeTime()` used throughout |
| No refresh mechanism | Manual Refresh button in hero actions, `refresh()` callback in `useAppStudioData` |
| Draft apps show empty two-panel layout | `isDraft` check renders `StudioInlineEmptyState` instead of two panels |
| Activity panel has no empty state | `StudioInlineEmptyState` shown when `!latestRun` |

---

## Production Readiness — 6 / 10

### Remaining issues

| Issue | Severity | Detail |
|---|---|---|
| Cost shows `$0.00` on apps with no runs | Low | `snapshot.usage?.totals?.estimated_cost_usd` is correct — the ledger does return `totals.estimated_cost_usd`. Cost is genuinely `0` when there are no runs. The SummaryStrip shows `Pending` in that case. This is honest — not a bug. |
| `/api/studio/build` 404 falls back silently | Medium | When no active build session exists, `buildState` is `null`, `build` becomes `{}`. Approval and plan state pills correctly hide, but the build panel subtitle still says "Current request and artifact state." with no explicit empty-state message. |
| No error boundary | Medium | If any of the 8 parallel fetches in `useAppStudioData` returns malformed JSON, the page crashes to the generic error state with no per-panel recovery path. |
| No data freshness beyond manual refresh | Low | The 30s polling recommendation from the original review was not implemented. Manual refresh covers most cases but a completing build is invisible without user action. |

### Tasks to reach 10

- [ ] Verify `/api/admin/usage` response shape — confirm whether `totals.estimated_cost_usd` is a real field or needs client-side aggregation from `by_run` / `events` arrays
- [ ] If no server-side aggregate: compute `totalCost` by summing `snapshot.usage?.by_run` or `snapshot.usage?.events` in `appStudioDataHelpers.js`
- [ ] Add explicit empty-state message to `BuildStatusPanel` when `build` is `{}` and no artifact exists yet
- [ ] Wrap the page body in a React error boundary so a single panel failure degrades gracefully

---

## Enterprise Grade — 2 / 10

### Remaining gaps

| Gap | Severity | Detail |
|---|---|---|
| No role-aware content | Critical | Every user sees the same page regardless of role. Operator, developer, and executive have different needs. No RBAC-driven panel visibility. |
| No audit trail surface | Critical | No last-modified-by, no change log link, no approval history visible on the overview. |
| Errors not actionable | High | No error count in the current page (removed from Activity panel). If errors surface again, they need a link to logs, not just a count. |
| No incident drill-down | High | Overview now has an operational health score, uptime, latency, validation, and error signal. It still needs a real incident/log drill-down before this becomes enterprise-grade. |
| No environment badge | High | No indication of which environment this is (dev / staging / prod). |
| No inline approval action | High | Approval state surfaces as an alert banner + "Review in Build Studio →" link. Approvers cannot approve/reject from this page directly. |
| Integration health needs stronger actionability | Medium | Overview now shows connected services and integration setup blockers. It still needs clearer remediation links once provider health checks are implemented. |
| No cost budget context | Medium | Cost shown with no budget ceiling, forecast, or alert threshold. |

### Tasks to reach 10

- [x] Add operational health summary to Overview — score, uptime, latency, validation, error, and integration setup signals
- [x] Add connected-services summary to Overview — app-declared services with setup blockers
- [ ] Add provider health remediation links once workspace integration health checks are implemented
- [ ] Add compact state transition history — last 3 lifecycle events (who changed what, when) from build history events
- [ ] Surface inline approval actions when `approval_state === 'pending'` and user has approver role
- [ ] Add environment badge to the app hero

---

## UI / UX — 6 / 10

### Remaining issues

| Issue | Severity | Detail |
|---|---|---|
| SummaryStrip and ActivityPanel both show cost | High | Cost appears in the strip at the top AND in the Activity panel below. Same fact, no deeper context the second time. |
| Build version is not a link | Medium | "Build v1" in `BuildStatusPanel` is plain text. Should navigate to the artifact in build review. |
| No primary CTA hierarchy | Medium | The next-step `SurfaceCard` has a `LinkButton` but it sits at the same visual weight as the panels. Approval-pending state does surface via the Alert, but the CTA in the card is always the same regardless of urgency. |
| Mobile panel order | Low | Activity appears before Build Status on narrow screens due to `xl:grid-cols-2` stacking. Build Status is more actionable and should come first on mobile. |

### Tasks to reach 10

- [ ] Remove `LLM Cost` `Metric` from `ActivityPanel` — it's already in the SummaryStrip. Replace with a more useful stat (e.g. last run duration, most-used workflow, or latest run status)
- [ ] Make "Build v{n}" a `LinkButton` to `/apps/:appId/activity` or the build review page
- [ ] Swap panel order for mobile: `BuildStatusPanel` first, `ActivityPanel` second using `order-first/order-last` on narrow screens

---

## Priority order for next implementation pass

1. **Remove cost from ActivityPanel** — it's already in the SummaryStrip; use that space for latest run workflow name + status instead.
2. **Integration health dot in hero** — workspace integrations module is live; surface a green/yellow/red summary that links to `/integrations`.
3. **Build version as link** — one-line change: wrap "Build v{n}" in a `LinkButton` to the build review page.
4. **Explicit empty state for build panel** — when `build` is `{}` and no artifact exists, say "No build sessions yet" instead of leaving the subtitle orphaned.
5. **Error boundary** — `React.ErrorBoundary` wrapper around the two panels so a single network failure doesn't blank the page.
