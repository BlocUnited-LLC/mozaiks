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

---

Working on Studio itself? See [Studio Product Model](../architecture/builder/studio-product-model.md) and [Admin System](../architecture/app/admin-system.md) for design rules, UI primitives, and implementation sources.
