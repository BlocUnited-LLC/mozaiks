# Config Files

Use this guide when you need to know which file owns which part of a Mozaiks
app. Most changes touch one or two files.

In an active workspace, app-owned files live under `app/`. During generation,
the same paths may appear without the leading `app/`, such as `config/ai.json`
or `data/contract.json`. Promotion places them under the workspace's `app/`
folder.

## Start Here

| I want to change... | Edit this |
|---------------------|-----------|
| App name, default route, auth-required flag, admins | `app/app.json` |
| Ask/chat startup or the default workflow | `app/config/ai.json` |
| Navigation, header actions, footer, mobile shell | `app/config/shell.json` |
| Workspace/App Dashboard portals | `app/dashboard/dashboard.yaml` |
| Colors, fonts, logo, density, radius | `app/brand/theme_config.json` |
| Pages and routes | `app/ui/pages/`, `app/ui/route_manifest.json` |
| Backend actions and durable app behavior | `app/modules/{module_id}/` |
| AI workflows | `workflows/{workflow_id}/` |

## Build a Mozaiks App Checklist

1. Set identity and startup: `app/app.json` and `app/config/ai.json`.
2. Shape the experience: `app/config/shell.json`,
   `app/dashboard/dashboard.yaml`, `app/brand/theme_config.json`, and `app/ui/`.
3. Add durable behavior: `app/modules/{module_id}/module.yaml` plus module
   backend files.
4. Add AI work: `workflows/{workflow_id}/`.
5. Declare service needs: `app/config/integrations.yaml`,
   `app/security/secrets.yaml`, and optional `app/services/` support code.
6. Add paid access only when needed: `app/config/subscriptions.yaml`.
7. Add artifact-aware refinement only when the app needs routed revisions:
   `app/config/refinement_policy.yaml` and `refinement_harness/config/`.

## Config Ownership

| File | Owns | Read |
|------|------|------|
| `app/app.json` | App identity, auth-required flag, default route, admin bootstrap | [Canonical App Structure](../../architecture/app/canonical-app-structure.md) |
| `app/config/ai.json` | Ask mode prompt, chat startup mode, default workflow entry point | [AI Startup](ai-startup.md) |
| `app/config/shell.json` | Header, footer, profile menu, notifications, mobile shell | [App Shell and Branding](../custom-brand-integration/01-overview.md) |
| `app/dashboard/dashboard.yaml` | Workspace/App Dashboard portals, panels, and dashboard actions | [App Dashboard Contract](../../architecture/app/app-dashboard-contract.md) |
| `app/config/auth.yaml` | Provider-neutral auth behavior and env handles | [Integrations](../integrations/01-overview.md) |
| `app/config/integrations.yaml` | App service requirements and managed capability needs | [Integrations](../integrations/01-overview.md) |
| `app/config/targets.json` | Runtime, deployment, health, domain, and environment intent | [Self-Hosting](../self-hosting.md) |
| `app/config/subscriptions.yaml` | Products, plans, capabilities, usage limits, token wallets, top-ups, add-ons | [Subscriptions](subscriptions.md) |
| `app/config/refinement_policy.yaml` | Refinement Engine enablement and model profiles | [Refinement](refinement.md) |
| `app/security/secrets.yaml` | Secret names, env handles, provider policy | [Integrations](../integrations/01-overview.md) |
| `app/data/contract.json` | Stable data aliases, indexes, aggregate ownership | [Add a Module](../adding-modules/01-overview.md) |
| `app/modules/{module_id}/contracts/` | Module companion manifests | [Module Contracts](module-contracts.md) |
| `refinement_harness/config/` | Refinement routes, checkpoints, prompt ids, tool ids | [Refinement](refinement.md) |

## Current Rules

- Secret files carry names and env handles only. Real values stay in the
  configured secret backend.
- App-level paid access and provider-neutral add-on product definitions belong
  in `app/config/subscriptions.yaml`. Module actions reference entitlement gates
  by capability id.
- Modules own business actions, permissions, lifecycle state, emitted events,
  and persistence authority.
- `app/services/` supports modules, workflows, and app-level routes. It is not
  the owner of durable app behavior.

## Where To Go Next

| Task | Read |
|------|------|
| Configure ask/chat/workflow startup | [AI Startup](ai-startup.md) |
| Add paid plans or token usage | [Subscriptions](subscriptions.md) |
| Add refinement routing | [Refinement](refinement.md) |
| Add module service or commercial metadata | [Module Contracts](module-contracts.md) |
| Add integrations | [Integrations](../integrations/01-overview.md) |
| Change branding or shell behavior | [App Shell and Branding](../custom-brand-integration/01-overview.md) |
| Add a backend capability | [Add a Module](../adding-modules/01-overview.md) |
| Add a page | [Add a Page](../adding-pages/01-overview.md) |
