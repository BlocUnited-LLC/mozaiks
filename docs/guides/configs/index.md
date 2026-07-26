# Configs

This guide is the practical file map for a Mozaiks app. Use it when you need to
know where app data lives, which file owns a setting, and which files should be
left alone.

In an active workspace, app-owned files live under `app/`. During generation,
the same bundle paths are often shown without the leading `app/`, such as
`config/ai.json` or `data/contract.json`. They refer to the same app-bundle
contracts after promotion into `app/`.

## File Map

| File | Owns | Use When |
|------|------|----------|
| `app/app.json` | App identity, startup route intent, auth-required flag, admin allowlist, high-level targets | Every app |
| `app/config/ai.json` | Ask/chat startup and workflow entry point | The app starts AI workflows or chat |
| `app/config/shell.json` | Header, navigation, shell chrome, footer, mobile shortcuts | The app needs navigation or shell changes |
| `app/brand/theme_config.json` | Brand tokens, typography, colors, density, radius, logos | The app needs visual identity |
| `app/config/auth.yaml` | Provider-neutral auth routes and OIDC env handles | Authenticated apps |
| `app/config/integrations.yaml` | Provider-neutral external service requirements | The app needs external or managed services |
| `app/config/targets.json` | Runtime, deployment, domain, DNS, and environment target intent | The app is packaged or deployed |
| `app/config/subscriptions.yaml` | App-owned plans, capability gates, usage limits, token wallets, token allowances | SaaS apps and apps with paid feature gates |
| `app/security/secrets.yaml` | Names-only secret requirements, env handles, provider/vault policy | The app needs secrets or environment handles |
| `app/data/contract.json` | Durable data ownership, aliases, collection mappings, indexes, additive migrations | The default module persistence shape is not enough |
| `app/config/refinement_policy.yaml` | App-local Refinement Engine policy and model profiles | The app supports artifact-aware refinement |
| `refinement_harness/config/harness.yaml` | Optional app-local refinement routing bundle | The app overrides refinement sequences, prompts, tools, or promotion policy |
| `app/modules/{module_id}/contracts/service.yaml` | Optional module service boundary metadata | A module exposes a stable service boundary |
| `app/modules/{module_id}/contracts/commercial.yaml` | Optional module commercial metadata outside subscription gates | A module owns fees, terms, or custom money-flow metadata |

## Required Versus Optional

Start with the smallest valid app:

- `app/app.json`
- `app/config/ai.json`
- at least one app surface: `workflows/`, `app/modules/`, `app/ui/pages/`, or
  custom UI registered through `app/ui/index.js`

Add more files only when the app needs them:

- Add `app/config/shell.json` and `app/brand/theme_config.json` when the shell
  or brand should differ from defaults.
- Add `app/security/secrets.yaml` when the app needs named environment values
  or vault-backed secrets.
- Add `app/config/integrations.yaml` when the app depends on external services.
- Add `app/config/targets.json` when build or deployment intent must be carried
  with the bundle.
- Add `app/config/subscriptions.yaml` only when the app sells access tiers,
  quotas, credits, token packs, or paid feature gates.
- Add `app/data/contract.json` only when module default persistence does not
  express enough durable data intent.
- Add `app/config/refinement_policy.yaml` only when the app needs app-local
  refinement policy.
- Add `refinement_harness/` only when the app needs an app-local refinement
  harness bundle.

## Source Of Truth Rules

Each durable concern has one canonical home:

- App identity stays in `app/app.json`.
- Runtime AI startup stays in `app/config/ai.json`.
- Shell and navigation stay in `app/config/shell.json`.
- Visual tokens stay in `app/brand/theme_config.json`.
- Secret names and vault policy stay in `app/security/secrets.yaml`.
- Durable collection intent stays in `app/data/contract.json`.
- SaaS plans and entitlement gates stay in `app/config/subscriptions.yaml`.
- App-local refinement model policy stays in `app/config/refinement_policy.yaml`.
- Business actions, lifecycle state, permissions, emitted events, and
  persistence authority stay in modules.

Do not create module-local `contracts/subscriptions.yaml`. The app-level
subscription contract is `app/config/subscriptions.yaml`.

You also do not need root `hosted_services.yaml` or `monetization.yaml` files
to build a normal OSS Mozaiks app. Hosted products may keep operator summaries,
but those summaries are not the generated-app source of truth.

## Next Pages

- [App Identity](app-identity.md)
- [AI Startup](ai-startup.md)
- [Shell and Navigation](shell-navigation.md)
- [Integrations and Targets](integrations-targets.md)
- [Subscriptions](subscriptions.md)
- [Secrets and Data](secrets-data.md)
- [Refinement](refinement.md)

For the full architecture reference, see
[Canonical App Structure](../../architecture/app/canonical-app-structure.md)
and [App Bundle Declaratives](../../architecture/app/app-bundle-declaratives.md).
