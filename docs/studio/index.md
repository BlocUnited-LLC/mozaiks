# Studio

Studio is the browser management surface for a Mozaiks workspace. It is not the
CLI with a UI, and it is not a generic admin portal. Studio helps an operator
create apps, continue builds, see what needs attention, and manage app-level
access, usage, and integration readiness without exposing internal runtime
machinery.

## Surface Model

Studio has two scopes:

| Scope | Routes | Purpose |
| --- | --- | --- |
| Workspace Studio | `/apps`, `/usage`, `/integrations` | Manage the portfolio: app records, workspace-wide usage, and shared provider setup. |
| App Studio | `/apps/:appId/overview`, `/apps/:appId/access`, `/apps/:appId/usage`, `/apps/:appId/support` | Manage one app: current state, access, usage, and support follow-up. |

Hidden detail routes can exist when a task needs deeper diagnostics:

| Route | Use |
| --- | --- |
| `/apps/:appId/health` | Deep operational diagnostics linked from Overview only when needed. |
| `/apps/:appId/integrations` | App-specific integration setup details linked from Overview or workspace Integrations. |
| `/apps/:appId/activity` | Build history and artifact preservation audit linked from Overview or Support when needed. |

`/create` and `/apps/new` are workflow entrypoints, not persistent Studio
navigation. Create always starts a new app journey. Continue-build belongs on
the existing app record.

## User Reasoning

Every Studio page should answer a concrete operator question:

| Page | Question |
| --- | --- |
| Apps | What apps exist, what state are they in, and which one needs action? |
| Usage | Where are tokens and cost coming from across the workspace? |
| Integrations | Which shared services are connected, which app-used services need setup, and what is available later? |
| Overview | What is happening with this app, and what should I do next? |
| Access | Who can use this app, what can they do, and who is blocked? |
| App Usage | Which chats and workflows are driving this app's tokens and cost? |
| Support | Which support chats need a reply, which are being handled, and which are resolved? |

If information does not help answer that page's question, it should move to a
drill-down, hover/detail state, or a different page.

## Product Rules

- Use customer-facing terms: `Apps`, `Overview`, `Access`, `Usage`,
  `Integrations`, `Create App`, `Continue Build`.
- Keep internal terms out of primary UI copy: `factory_app`, `Control Plane`,
  `workflow_sequence`, `extension_registry`, `adapter`.
- Keep Health and Build History routable as diagnostics, but do not make them
  primary navigation unless the operator is following a specific issue.
- Show integration setup globally first. App-specific integration detail is a
  secondary route.
- Use `Chats` for end-user activity counts. Avoid `tracked executions` in the
  UI unless the page is explicitly an engineering diagnostic.
- Prefer one primary action per page. Secondary actions should be quiet.

## Visual Direction

Studio should feel like a focused operations console: dense enough to scan,
quiet enough to use repeatedly, and structured enough that users know where to
click next.

Default layout:

1. Page hero/header with scope, title, short subtitle, and primary actions.
2. `SummaryStrip` for the 3-4 facts that decide urgency.
3. One main work surface: list, trend chart, access table, or setup catalog.
4. Secondary panels only when they add actionability.
5. Collapsible diagnostics for health, pricing, and setup details.

Avoid marketing-style hero sections, decorative page cards, nested cards, and
large explanatory blocks inside the app.

## Implementation Sources

The current Studio routes are declared in:

- `factory_app/app/ui/route_manifest.json`

`factory_app/app/admin/admin_registry.yaml` is reserved for AdminPortal extension
pages and should not duplicate first-party Studio routes.

Reusable UI primitives are owned by `chat-ui` and re-exported through:

- `chat-ui/src/ui/primitives/`
- `factory_app/app/ui/components/StudioShared.jsx`

Architecture context lives in:

- [Studio Product Model](../architecture/builder/studio-product-model.md)
- [Admin System](../architecture/app/admin-system.md)
