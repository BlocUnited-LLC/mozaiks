# Workspace Pages

Workspace pages are global. They should not force the user into an individual
app unless the next task is app-specific.

## Apps

**Route:** `/apps`

**User question:** What apps exist, what state are they in, and which one needs
action?

**Primary action:** `Create App`

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | Workspace name, concise purpose, `Create App`. |
| Summary | Total revenue, runtime cost, active users, live apps, in-build apps, apps needing input. |
| Filters | All, needs input, building, live. |
| List | App name, description, lifecycle status, current state, updated time, actions. |
| Actions | Continue Build when an app needs build attention; Dashboard for app management. |

**UI primitives:**

- `WorkspaceLayout`
- `WorkspaceStudioHero`
- `SummaryStrip`
- `CollectionToolbar`
- `ResourceList`
- `StatusPill`
- `StudioSlideOver` for import or secondary workflows
- `InlineEmptyState` when no apps match the active filter

**UX rules:**

- Sort apps needing input first, then by recent activity.
- Portfolio KPIs should come from a workspace dashboard snapshot resolver, not
  from frontend aggregation over unrelated endpoints.
- Keep build continuation on the existing app row. Do not let `Create App`
  resume an old build.
- Do not show low-value timestamps in the summary strip. Updated time belongs
  on each row.
- Keep destructive actions visually quiet and confirm them.

## Usage

**Route:** `/usage`

**User question:** Where are tokens and cost coming from across the workspace?

**Primary action:** App row `Dashboard` CTA to that app's usage tab.

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | Workspace usage purpose and date range when filtering exists. |
| Trend | Spend, token volume, or chat count over time. |
| Summary | Tokens, estimated cost, chats, average cost per chat. |
| App table | App, chats, input tokens, output tokens, total tokens, cost, average cost per chat. |
| Diagnostics | Collapsed cost estimate status when catalog or model pricing needs attention. |

**UI primitives:**

- `UsageTrendPanel`
- `SegmentedControl` for spend/tokens/chats metric switching
- `ResourceList`
- `CollectionToolbar`
- `PricingHealthPanel` as a collapsed detail panel
- `Alert` only for actionable pricing gaps

**UX rules:**

- Say `Chats`, not `tracked executions`.
- Pricing health is an operator diagnostic. Keep it collapsed unless something
  needs attention.
- The app table should stay scannable. Move model-level and chat-level details
  to app usage.
- `Dashboard` should route to `/apps/:appId/usage`.

## Integrations

**Route:** `/integrations`

**User question:** Which shared services are ready for apps to use?

**Primary action:** Open setup/details for a provider.

**Content contract:**

| Area | Content |
| --- | --- |
| Hero | Workspace integration purpose and refresh status. |
| Summary | Configured, partial, missing, and total providers. |
| Catalog | Provider cards grouped by category. |
| Detail | Required env vars, safe presence status, setup steps, operator note. |

**UI primitives:**

- `PageHeader` or `WorkspaceStudioHero`
- `SummaryStrip`
- `SurfaceCard` for provider cards
- `StatusPill` for configured/partial/missing
- `StudioSlideOver` for setup detail
- `Alert` for save/load errors only

**UX rules:**

- Global setup owns shared provider credentials and notes.
- App-specific integration requirements should summarize on app Overview and
  link to the hidden app integration detail route.
- Never display secret values. Only display names and presence status.
- Group by human provider category such as payments, email, auth, storage,
  analytics, and source control.
