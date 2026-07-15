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
| Header | Workspace integration purpose in one sentence. |
| Summary | Connected, needs setup, and used-by-apps counts. |
| Needs attention | Services currently used by apps but missing setup. |
| Connected | Services ready from environment variables or saved workspace connectors. |
| Available | Supported services that are not blocking any app. |
| Detail | Status, app usage, credential source, operator note, collapsed advanced setup. |

**UI primitives:**

- `PageHeader` or `WorkspaceStudioHero`
- `SummaryStrip`
- `Panel` plus compact rows for provider lists
- `StatusPill` for configured/partial/missing
- `StudioSlideOver` for setup detail
- `Alert` for save/load errors only

**UX rules:**

- Global setup owns shared provider credentials and notes.
- Organize by task state, not provider category. Categories may appear as small
  metadata pills, but they should not be the main page structure.
- Catalog entries are permanent inventory. The global detail drawer may delete
  a saved workspace connector, but it must not imply that environment-managed
  credentials or catalog providers can be deleted from the UI.
- App-specific integration requirements should summarize on app Overview and
  link to the hidden app integration detail route.
- The workspace catalog ordering still leads with AI providers, then Payments,
  then operational providers inside task sections. The app dashboard should not
  list providers the app did not declare or inherit from a selected managed
  capability.
- Mozaiks Pay is shown as the removable default payments integration for
  monetized apps. Removing it writes an app-level removal record so later
  defaulting passes do not silently restore it.
- Never display secret values. Only display names and presence status.
- Required environment names and setup steps belong in collapsed advanced
  details, not in the default page scan.

## Support

**Route:** `/support`

**User question:** Which apps have support conversations that need attention?

**Primary action:** Open the selected app's support dashboard.

**Content contract:**

| Area | Content |
| --- | --- |
| Header | Workspace support purpose in one sentence. |
| Summary | Apps with open support, needs-reply count, in-progress count, resolved count. |
| App list | App name, support status, open chat count, latest user message preview, action. |
| Detail | Opens only after selecting an app or routing to `/apps/:appId/support`. |

**UI primitives:**

- `WorkspaceLayout`
- `WorkspaceStudioHero`
- `SummaryStrip`
- `ResourceList`
- `StatusPill`
- `LinkButton`
- `InlineEmptyState`

**UX rules:**

- Keep the global support page as an app index. Do not show every chat across
  every app by default.
- Use the same app drill-down pattern as `/apps` and `/usage`.
- User-facing statuses are `Needs reply`, `In progress`, and `Resolved`.
- Conversation storage belongs to the `messages` module; support owns tickets
  and routes the user to the app-specific support page.
- Global Support groups records by the ticket's subject app id. It should not
  expose workspace-wide social DMs or friends activity.
