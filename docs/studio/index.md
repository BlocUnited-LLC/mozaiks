# Studio

Studio is the browser management surface for a Mozaiks workspace. It is not the
CLI with a UI, and it is not a generic admin portal. Studio helps an operator
create apps, continue builds, see what needs attention, and manage app-level
usage, health, and integration readiness without exposing internal runtime
machinery.

## Surface Model

Studio has two scopes. Every page in a menu is declared twice, as a route in
`ui/route_manifest.json` and as a portal in `dashboard/dashboard.yaml`, and a
test keeps the two lists equal.

| Scope | Menu | Purpose |
| --- | --- | --- |
| Workspace Dashboard | My Apps `/apps`, Performance `/performance`, Token Usage `/usage`, Integrations `/integrations`, Support `/support`, AI models `/settings/ai-models` | Manage the portfolio: app records, cross-app performance and usage, shared provider setup, support activity, and the AI models builds may use. |
| App Dashboard | Overview, Revenue, Users, Project, Integrations, Launch, Billing, Token Usage, Health and Support, each under `/apps/:appId/...` | Manage one app. |

In the workspace menu, **My Apps** lists the apps you own. **Performance** shows
revenue and user metrics across those apps. Under **Manage an app**, pick a tool
such as **Integrations**, then pick an app. Studio opens that app's Integrations
page. You can also open the app first and choose Integrations from its menu.

Studio builds these shortcuts from the selected-app pages available to you.
A host that adds, hides, or renames a page gets the corresponding menu change
in both places. Each app page uses its existing authorization checks.

Workspace administrators also see the workspace-wide Token Usage, Integrations,
Support, and AI models pages. AI model settings require a signed-in workspace.

One detail route is linked from other pages and is not in a menu:

| Route | Use |
| --- | --- |
| `/apps/:appId/activity` | Build review: a saved build's files, preview, validation result, and artifact history, linked from Overview and Building. |

A page appears in a menu only when it has data behind it. The first-party
Studio has no account directory, so it has no Users-by-account or Access page;
user counts are on Performance and on each app's Users page.

`/create` and `/apps/new` are workflow entrypoints, not persistent Studio
navigation. Create always starts a new app journey. Continue-build belongs on
the existing app record.

## App Ownership

Studio app-scoped integration, connector, and source-context actions use the app
registry to verify that the signed-in user owns the resolved app, including when
the request omits `app_id` and resolves to the host default. An app missing from
that user's registry view returns 404; a selected app that conflicts with an
app-bound token still returns 403. The shared dashboard manifest remains
available without selecting an app because it does not read app resources.

With authentication disabled and local development access enabled, Studio uses
the configured `MOZAIKS_DEFAULT_USER_ID` (default `demo-user`) as the single
developer's registry owner. Apps created through Studio belong to that user,
and later app-scoped management actions resolve ownership through the same
registry record.

## User Reasoning

Every Studio page should answer a concrete operator question:

| Page | Question |
| --- | --- |
| Apps | What apps exist, what state are they in, and which one needs action? |
| Performance | How are revenue and users trending across my apps, and which app needs attention? |
| Usage | Where are tokens and cost coming from across the workspace? |
| Integrations | Which shared services are connected, which app-used services need setup, and what is available later? |
| AI models | Which AI models may builds use, and which one is the default? |
| Overview | What is happening with this app, and what should I do next? |
| App Usage | Which chats and workflows are driving this app's tokens and cost? |
| Health | Is anything wrong with this app's latest build, its runs, or its integrations? |
| Support | Which support chats need a reply, which are being handled, and which are resolved? |

If information does not help answer that page's question, it should move to a
drill-down, hover/detail state, or a different page.

---

Working on Studio itself? See [Studio Product Model](../architecture/builder/studio-product-model.md) and [Admin System](../architecture/app/admin-system.md) for design rules, UI primitives, and implementation sources.
For the canonical dashboard manifest, see [App Dashboard Contract](../architecture/app/app-dashboard-contract.md).
