# Studio Interface Conventions

Studio follows consistent conventions across all pages so you always know where
to look and what to click next.

## Page Structure

Every Studio page follows the same layout order:

1. **Header** — scope, app name (when in an app), and primary action
2. **Summary strip** — the 3–4 numbers that answer the page's core question at a glance
3. **Main work surface** — list, trend chart, access table, or setup catalog
4. **Supporting panels** — secondary detail when it adds a clear action
5. **Collapsed diagnostics** — health, pricing, and setup detail, only expanded when something needs attention

This means the most important information is always in the first viewport. You
should not need to scroll to know whether action is required.

## Progressive Disclosure

Studio surfaces the lowest useful level of detail by default:

| What you need | Where to find it |
| --- | --- |
| Quick status read | Summary strip or status label on a row |
| Choose an item | The main list or table |
| Details for one item | Slide-over panel or side detail |
| Engineering diagnostics | Health page or collapsed detail block |

Examples:

- Workspace Usage shows cost and tokens by app. App Usage shows the breakdown
  by workflow and chat.
- App Overview shows the health score and active issues. Full diagnostics are
  one click away on the Health page.
- The lifecycle next step appears once in the app header. It does not repeat in
  lower panels.

## Status Labels

Studio uses short, operator-readable labels. If a status is not green, the page
always shows what to do about it.

| Area | Labels |
| --- | --- |
| App lifecycle | Draft, Building, Review, Configuring, Deploying, Active, Needs Revision, Archived |
| Access | Active, Invited, Inactive, Suspended, Blocked, Unassigned |
| Integration | Configured, Partial, Not configured, Ready, Needs setup |
| Pricing | Ready, Fallback prices, Unpriced models, Catalog unavailable |
| Health | Healthy, Needs attention, Critical, Pending |

## Mobile

Every list and table in Studio has a stacked mobile layout. The same information
shown in the desktop row is available on mobile — title and status appear first,
then compact facts, then the row action.

---

Building Studio pages? See [Studio Product Model](../architecture/builder/studio-product-model.md)
for the full component system, design rules, and implementation sources.
