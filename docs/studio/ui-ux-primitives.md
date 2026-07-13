# UI/UX And Primitives

Studio pages should be built from the shared Mozaiks primitives before adding
page-local UI. This keeps the product consistent while still allowing page-level
composition.

## Primitive Defaults

| Need | Primitive |
| --- | --- |
| Page frame | `WorkspaceLayout` |
| Page title and actions | `PageHeader`, `WorkspaceStudioHero`, `AppStudioHero` |
| App identity | `AppStudioHero` app logo/initials, app name, tagline, description, and lifecycle state |
| App banner | `AppStudioHero` dashboard banner mode with optional banner image and next-step slot |
| Page metrics | `SummaryStrip` |
| Trend plus side facts | `UsageTrendPanel` |
| Search and filters | `CollectionToolbar` |
| Desktop/mobile lists | `ResourceList` |
| Contained sections | `Panel` |
| Repeated item summaries | `SurfaceCard` |
| One metric inside a panel | `Metric` |
| State labels | `StatusPill` |
| Binary or mode switching | `SegmentedControl` |
| Secondary detail | `StudioSlideOver` |
| Empty, loading, error | `InlineEmptyState`, `LoadingState`, `ErrorState` |
| Urgent inline messaging | `Alert` |

Use `factory_app/app/ui/components/StudioShared.jsx` for Factory Studio aliases
and tiny adapters. Do not create page-local button, pill, card, empty-state, or
modal primitives unless the shared primitive cannot support the state.

## Page Density

Studio should be dense but not crowded.

Use this hierarchy:

1. One hero/header.
2. One summary strip.
3. One primary work surface.
4. One supporting panel row when useful.
5. Collapsed diagnostics.

Avoid:

- nested cards
- decorative cards around whole page sections
- hero-scale text inside dashboard panels
- repeated KPI grids when `SummaryStrip` already covers the facts
- visible instructional paragraphs that explain obvious UI controls
- multiple primary buttons competing for attention
- repeated copies of the same lifecycle CTA on one page

## Progressive Disclosure

Show the lowest useful level first:

| Situation | Surface |
| --- | --- |
| User needs a quick status read | Summary strip or status pill |
| User needs to choose an item | Resource list or table |
| User needs details for one item | Slide-over or side panel |
| User needs engineering diagnostics | Hidden route or collapsed detail block |

Examples:

- Workspace Usage shows cost and tokens by app. App Usage shows workflows and
  chats. Pricing health stays collapsed unless something is wrong.
- App Overview shows health score and issues. Health diagnostics live behind a
  link.
- App Overview shows the lifecycle next step in the app identity banner. Lower
  panels may link to details, but they should not repeat that primary action.
- Workspace Integrations owns provider setup. App Overview only shows whether
  this app's declared services are ready.

## Mobile Behavior

Every table-like surface needs a mobile item renderer. The mobile item should
keep the same facts as the desktop row, but stack them into scannable groups:

- title and status first
- two to four compact facts
- primary row action
- destructive or secondary actions visually quiet

Do not rely on horizontal scrolling for primary mobile workflows unless the
data is inherently tabular and secondary.

## Status Language

Use short labels. Status labels should describe what the operator can reason
about:

| Domain | Labels |
| --- | --- |
| App lifecycle | Draft, Building, Review, Configuring, Deploying, Active, Needs Revision, Archived |
| Access | Active, Invited, Inactive, Suspended, Blocked, Unassigned |
| Integration | Configured, Partial, Not configured, Ready, Needs setup |
| Pricing | Ready, Fallback prices, Unpriced models, Catalog unavailable |
| Health | Healthy, Needs attention, Critical, Pending |

Pair non-green statuses with an actionable next step. A warning without a next
step should usually become a quiet detail instead.

## Page Checklist

Before a Studio page is ready, confirm:

- The page answers one explicit user question.
- The primary action is obvious and singular.
- The first viewport includes the title, key facts, and first useful work
  surface.
- Empty, loading, and error states are implemented.
- Mobile has a dedicated stacked layout.
- Diagnostics are collapsed or linked unless the page is a diagnostics page.
- Copy uses customer-facing terms.
- Shared primitives are used before custom UI.
- No internal runtime terms are visible in primary UI.
