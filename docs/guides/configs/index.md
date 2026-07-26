# Config Files

Use this page as a map. Most app changes need one or two files, not the whole
architecture reference.

In an active workspace, app-owned files live under `app/`. During generation,
the same paths may appear without the leading `app/`, such as `config/ai.json`
or `data/contract.json`. They become `app/config/ai.json` and
`app/data/contract.json` after promotion into a workspace.

## Start Here

For most apps, these are the files you actually care about:

| I want to change... | Edit this |
|---------------------|-----------|
| App name, default route, auth-required flag, admins | `app/app.json` |
| Ask/chat startup or the default workflow | `app/config/ai.json` |
| Navigation, header actions, footer, mobile shell | `app/config/shell.json` |
| Colors, fonts, logo, density, radius | `app/brand/theme_config.json` |
| Pages and routes | `app/ui/pages/`, `app/ui/route_manifest.json` |
| Backend actions and durable app behavior | `app/modules/{module_id}/` |
| AI workflows | `workflows/{workflow_id}/` |

Add the optional files below only when the app needs that feature.

## Optional Files

| Need | File | Notes |
|------|------|-------|
| Login/auth | `app/config/auth.yaml` | Provider-neutral OIDC routes and env handles. No secrets. |
| External services | `app/config/integrations.yaml` | Service requirements such as email, AI provider, storage, or payment facade. |
| Deployment intent | `app/config/targets.json` | Runtime shape, health path, env names, domain intent, deployment lanes. |
| Secret names | `app/security/secrets.yaml` | Names and env handles only. Never raw values. |
| Stable data aliases or indexes | `app/data/contract.json` | Skip this unless default module persistence is not enough. |
| Paid plans or feature gates | `app/config/subscriptions.yaml` | App plans, capabilities, usage limits, token wallets, token allowances. |
| Refinement policy | `app/config/refinement_policy.yaml` | Model profiles and enablement for artifact-aware refinement. |
| App-local refinement routing | `refinement_harness/config/harness.yaml` | Optional advanced routing, prompts, tools, validation, and promotion policy. |

## Common Rules

- Do not store real keys, passwords, tokens, connection strings, or webhook
  secrets in source.
- Do not create module-local `contracts/subscriptions.yaml`; app subscription
  logic belongs in `app/config/subscriptions.yaml`.
- Do not use root `hosted_services.yaml` or `monetization.yaml` to build a
  normal OSS Mozaiks app. Hosted products may derive operator summaries, but
  those summaries are not generated-app source of truth.
- Keep business actions, permissions, lifecycle state, emitted events, and
  persistence authority in modules.

## Where To Go Next

| Task | Read |
|------|------|
| Configure AI startup | [AI Runtime Startup](../extending-ai-functionality/02-ai-runtime-startup.md) |
| Add or tune refinement | [Extending AI Functionality](../extending-ai-functionality/01-overview.md) |
| Add integrations | [Integrations](../integrations/01-overview.md) |
| Change branding or shell behavior | [App Shell and Branding](../custom-brand-integration/01-overview.md) |
| Add a backend capability | [Add a Module](../adding-modules/01-overview.md) |
| Add a page | [Add a Page](../adding-pages/01-overview.md) |
| Understand the full workspace shape | [Canonical App Structure](../../architecture/app/canonical-app-structure.md) |
