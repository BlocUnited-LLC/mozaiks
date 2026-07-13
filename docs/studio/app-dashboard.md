# App Dashboard

App Studio pages manage one app. The user should always understand which app
they are viewing, what state it is in, and whether any action is required.

## Overview

**Route:** `/apps/:appId/overview`

**User question:** How is this app performing, what does it cost, who is using
it, and what needs action right now?

**Primary action:** Lifecycle-aware next step, such as continue build, review,
configure integrations, or open usage.

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | App banner image when available, logo or initials, app name, lifecycle, tagline, app description, and the single lifecycle-aware next step. |
| Summary | Revenue, runtime cost, margin, active users, and chats. |
| Build state | Current build/version, approval state, validation state, artifact state. |
| Activity | Latest chat/build activity and links to deeper detail when available. |

**Hero identity contract:**

| Field | Source | Placeholder |
| --- | --- | --- |
| App name | App registry `name`, falling back to `app.json.appName` | App id or `App` |
| Logo | Theme/asset manifest logo, falling back to deterministic initials | Initials |
| Banner | Theme/asset manifest banner or cover image | Quiet card background |
| Tagline | `app.json.tagline`, falling back to theme identity tagline or value proposition | Hidden |
| Description | App registry `description`, falling back to `app.json.description` | `App description will appear after the concept brief is captured.` |
| Next step | Lifecycle/build summary recommendation | `No operator action is needed right now.` |

**Population contract:**

1. ValueEngine owns the first durable product identity. `GapAnalysisAgent`
   emits `ConceptBlueprint.app_name`, `concept_overview`, and
   `value_proposition`.
2. `save_value_manifest` persists the ValueEngine manifest and updates the app
   registry with the approved app name and description. This makes the hero
   usable before the generated app bundle is complete.
3. AppGenerator owns the final generated app bundle identity.
   `AppSchemaAgent` emits `AppManifest.app_name`, `description`, `tagline`, and
   `value_proposition`, preserving the ValueEngine identity unless the user
   explicitly revised it.
4. `save_app_schema` persists those fields to `app.json`. Studio summary reads
   app registry values first, then `app.json`, so promoted/generated bundles and
   in-progress registry records stay aligned.
5. Logo and banner imagery come from the generated theme/asset outputs. Until
   those exist, the dashboard uses initials and a neutral banner surface.

**UI primitives:**

- `WorkspaceLayout`
- `AppStudioHero`
- `SummaryStrip`
- `Panel`
- `Metric`
- `StatusPill`
- `Alert` for approval or blocking states
- `StudioInlineEmptyState` for draft or no-build states
- `LinkButton` for deep diagnostics or setup

**UX rules:**

- Overview is the executive/operator snapshot, not a dumping ground.
- Overview is business-first. Revenue, cost, margin, active users, and chats
  should appear before build mechanics.
- Do not add a `Business snapshot` panel below the summary strip. It repeats
  the same information and makes the page feel heavier than it is.
- The lifecycle action should appear once. Lower panels may link to diagnostics,
  history, or setup details, but they should not repeat the primary CTA.
- Put the lifecycle action in the hero's `Next step` slot, beside the app logo,
  name, tagline, and description. Do not add a second standalone next-step card.
- The app dashboard hero should show the app banner image when present. Use the
  app logo when present and deterministic initials as the fallback.
- Do not block the dashboard on unfinished app identity. Use placeholders until
  ValueEngine or AppGenerator has populated the identity fields.
- Admin Portal or module panels are drill-down destinations. Overview should
  summarize their strongest signal, not duplicate their full tables.
- Health diagnostics belong at `/apps/:appId/health`, not as a large Overview
  panel.
- Help desk, stalled runs, escalations, and support-facing diagnostics belong
  at `/apps/:appId/support`.
- Integration setup belongs at `/apps/:appId/integrations` and should only be
  linked from Overview when it is the lifecycle next step.
- Avoid duplicating the same cost metric in multiple panels.

## Health

**Route:** `/apps/:appId/health`

**User question:** Is this app operationally healthy, and what technical issue
needs attention?

**Primary action:** Open the related support, integration, or activity detail
when a health issue needs follow-up.

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | App identity and health summary metrics. |
| Current app health | Overall score, label, and issue list. |
| Release confidence | Latest artifact validation, runtime readiness, hosting uptime. |
| Workflow reliability | Workflow runs, runtime errors, latency. |
| Integration posture | Missing credentials or incomplete connectors. |

**UX rules:**

- Health is a diagnostic page, so it can show denser operational detail than
  Overview.
- Health should explain why the score changed, not just show a number.
- Link support-facing incidents to Support rather than turning Health into a
  help desk queue.

## Support

**Route:** `/apps/:appId/support`

**User question:** Which user-facing issues, escalations, or stalled runs need
operator follow-up?

**Primary action:** Open Health diagnostics or the relevant activity detail.

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | Open support items, stalled runs, runtime errors, latest run. |
| Help desk | Support notes, escalations, user-facing follow-up items. |
| Run review | Stalled runs, errored runs, and run facts useful for triage. |

**UX rules:**

- Support is the app help desk view. It should be phrased around follow-up,
  escalation, and triage rather than raw infrastructure health.
- Do not place support queues on Overview. Overview can point here when support
  work is the current next step.
- Keep Support actionable and compact; deep trace inspection belongs in
  Activity or Health.

## Access

**Route:** `/apps/:appId/users`

**Visible label:** `Access`

**User question:** Who can use this app, what can they do, and who is blocked?

**Primary action:** Export or manage the selected account until invite/edit
actions are implemented.

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | Managed account totals and access posture. |
| Summary | Managed accounts, active access, assigned plans, accounts needing review. |
| Table | Account, access status, role, plan, last activity, source, actions. |
| Detail rail | Selected account email, role/plan, activity, flags, usage link. |
| Empty state | Explain when no access source is connected yet. |

**Canonical account shape:**

| Field | Meaning |
| --- | --- |
| `account_id` | Stable account identifier. |
| `display_name` | Name shown in Studio. |
| `email` | Login/contact email when available. |
| `status` | `active`, `invited`, `inactive`, `suspended`, or `blocked`. |
| `role` | App role such as owner, admin, member, viewer, or customer. |
| `plan_id` / `plan_label` | Subscription or entitlement assignment. |
| `last_seen_at` | Last app activity. |
| `source` | Auth provider, imported backend, CSV, or external IdP. |
| `flags` | Missing plan, unverified email, seat limit, usage limit, suspension. |

**UI primitives:**

- `AppStudioHero`
- `SummaryStrip`
- `Panel`
- `ResourceList` or table with responsive mobile items
- `StatusPill`
- `StudioSlideOver` or side detail panel
- `InlineEmptyState`

**UX rules:**

- Keep the route stable for now, but user-facing copy should say `Access`, not
  `Users`.
- Do not make Overview a user-management screen. Overview only needs a compact
  access summary and alert count.
- Access statuses should be operational: active, invited, suspended, blocked,
  inactive, unassigned plan.
- Show why someone is blocked, not just that they are blocked.

## Usage

**Route:** `/apps/:appId/usage`

**User question:** Which chats and workflows are driving this app's tokens and
cost?

**Primary action:** Expand workflow groups to inspect chats.

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | App usage purpose and current data mode when relevant. |
| Trend | Spend, tokens, or chats over time. |
| Side summary | Input tokens, output tokens, LLM calls, tracked workflows. |
| Workflow table | Workflow, chats, input/output/total tokens, average tokens, cost, average cost. |
| Chat rows | Chat id, user, started time, tokens, cost, LLM call count. |
| Diagnostics | Collapsed pricing status when model costs are incomplete. |

**UI primitives:**

- `UsageTrendPanel`
- `SegmentedControl`
- `Panel`
- `Alert` for unpriced model warnings
- `PricingHealthPanel`
- Expandable grouped table rows

**UX rules:**

- Default to the metric most likely to matter: cost once pricing is complete,
  tokens when cost is unavailable.
- Keep per-chat detail nested under workflow groups.
- Label row counts as `chat` / `chats`.
- Explain $0.00 cost only when it is caused by unpriced models; do not imply
  real spend is zero when pricing is missing.

## Detail Pages

These pages are routable drill-down surfaces. They should stay focused on the
specific reason the user opened them:

| Page | Route | When to link |
| --- | --- | --- |
| Integration setup | `/apps/:appId/integrations` | From Overview connected-services blockers or workspace Integrations. |

Detail pages can use stronger diagnostic density because the user arrives with
a specific problem.
