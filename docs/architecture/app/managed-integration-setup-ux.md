# Managed Integration Setup UX

Status: architecture and OSS generation contract

Scope: Studio, Factory workflows, generated app integration contracts, and hosted product extensions.

## Purpose

Mozaiks should not make users hunt for provider API keys before they can build
or run an app. Users should choose the capability they want, and the platform
should resolve the safest available setup path.

This document defines the canonical API-light setup model:

```text
user selects capability
  -> Studio explains what the app needs
  -> resolver offers managed, connected-account, or bring-your-own setup
  -> generated app receives provider-neutral config and safe secret handles
  -> runtime checks readiness without exposing raw credentials
```

The goal is not to pretend APIs do not exist. The goal is to keep API-key work
behind clear product choices and to avoid forcing non-technical users to visit
developer dashboards unless there is no managed or OAuth path.

## User-Facing Rule

Users configure outcomes, not providers.

Good:

```text
Payments are ready through MozaiksPay.
Email needs a sender connection.
Research is ready through managed web research.
```

Avoid:

```text
Paste STRIPE_SECRET_KEY.
Paste SENDGRID_API_KEY.
Paste DDG_API_KEY.
```

Provider details may appear in advanced setup, operator notes, runbooks, and
self-hosting docs. They should not be the first task presented to a builder.

## Setup Lanes

Every integration requirement resolves through one of these lanes.

| Lane | User action | Secret owner | Best for |
| --- | --- | --- | --- |
| Managed by Mozaiks | Click "Use managed service" | Mozaiks App or operator service | Payments, hosted auth, web research, email, deployment, domains |
| Connect account | OAuth or provider login | Connected user/workspace account | Google, GitHub, Microsoft, Slack, Stripe-owned accounts |
| Bring your own key | Paste/write secret once | Workspace or app operator | Self-hosting, enterprise-owned providers, unsupported services |
| Not required | No setup | None | Optional features or no-op local defaults |

The UI should default to the first viable lane in that order. Bring-your-own
keys are advanced setup, not the main path.

## App-Agnostic OSS Contract

OSS records integration requirements as provider-neutral capabilities.
AppGenerator materializes those requirements into
`app/config/integrations.yaml` during assembly. The file is safe to package with
the generated app because it contains setup field names and frontend-safe
metadata only, never raw credential values.

```yaml
integration_id: payments_checkout
capability_id: payments.checkout
purpose: Create checkout sessions for paid plans and token top-ups.
required_lifecycle: runtime
preferred_setup_lane: managed
allowed_setup_lanes:
  - managed
  - connect_account
  - bring_your_own_key
managed_default:
  service: mozaikspay
  display_name: MozaiksPay
required_fields:
  - name: api_base
    type: url
    frontend_safe: true
  - name: api_key
    type: secret
    frontend_safe: false
```

The generated app should bind pages and actions to app-owned facade modules and
thin clients. It should not bind UI directly to provider internals.

```text
page
  -> app module facade action
  -> app/services/integrations/{service}_client.py
  -> managed or connected service API
```

This keeps the contract replaceable. If a workspace changes from MozaiksPay to
another provider, the generated app should preserve the same capability
surface, entitlement gates, and token behavior.

## Hosted Product Extension Contract

Mozaiks App may provide managed implementations for OSS capabilities, but the
OSS framework must not depend on proprietary hosted logic.

| Concern | OSS owns | Mozaiks App owns |
| --- | --- | --- |
| Capability requirement shape | `app/config/integrations.yaml`, app integration declarations, build-context pack metadata | Managed-service availability and commercial policy |
| Generated app surface | Facade module, client, env names, readiness status | Hosted API implementation |
| Secret contract | Names-only handles and write-only setup fields | Secret issuance, rotation, storage, and provider credentials |
| Runtime effects | Entitlement gates, token guards, provider-neutral fulfillment commands | Payment/provider verification and hosted fulfillment source facts |
| UI extension | Studio setup slots and safe status rendering | Managed-service onboarding flows and hosted-account UI |

MozaiksPay is the recommended managed payment option. It must remain a selected
managed adapter, not a hardcoded OSS payment runtime.

## UI Model

Studio should show a single integration setup surface with plain task states.

```text
Your app needs 4 services

Ready
- Payments: MozaiksPay managed
- Research: Mozaiks managed web research

Needs setup
- Email: connect sender account
- Google Calendar: connect Google

Advanced
- Custom CRM: add API key
```

Each integration detail view should include:

- display name and what the service enables
- current lane and readiness status
- recommended setup action
- safe fields only
- health status when a registered health plugin exists
- operator note
- advanced setup collapsed by default

Secret values are write-only. The frontend can show "configured" or "missing",
never the stored value.

`app/config/targets.json` is materialized at the same assembly boundary. It
records provider-neutral deployment intent such as runtime shape, health path,
deployment profile, and expected environment variable names. It does not record
live provider state, cloud tenant ids, hosted-product policy, or secrets.

## Workflow Behavior

Build workflows should ask for setup only at real blocking points.

```text
AppGenerator records integration needs
  -> IntegrationReadinessAgent resolves current workspace state
  -> ready: continue build
  -> managed available: offer managed setup
  -> OAuth available: offer account connection
  -> key required: show advanced BYO form
  -> missing required setup: pause workflow with integration.required
  -> setup saved: recheck readiness and resume
```

Agents should output structured integration needs. Tools should only persist,
emit UI events, or call safe status APIs. Tools should not infer provider
choices from keyword matching.

## API-Key Reduction Policy

Use this policy whenever a generated app needs an external capability:

1. Prefer managed capability packs for high-friction provider setup.
2. Prefer OAuth/connect-account flows over manual key entry.
3. Ask for raw API keys only when the app explicitly owns that provider
   integration or the workspace chooses self-host/enterprise mode.
4. Store raw secrets only through the configured secret backend.
5. Generate names-only env handles and secret contracts.
6. Keep provider SDKs out of generated apps when a managed service is selected.
7. Keep readiness configuration-based by default; provider health checks are
   explicit operator actions.

## Production Tasks

### Phase 1: Contract Cleanup

- [x] Define `preferred_setup_lane` and `allowed_setup_lanes` on catalog-backed
  integration requirements.
- [x] Add `preferred_setup_lane`, `allowed_setup_lanes`, and optional
  `managed_default` to the integration requirement schema.
- [x] Materialize `app/config/integrations.yaml` and `app/config/targets.json`
  from AppGenerator assembly.
- [x] Validate those fields in the workspace integrations module.
- [x] Update AppGenerator structured outputs and prompts to emit capability-first
  integration needs.
- [x] Keep MozaiksPay as the recommended managed payment capability for
  monetizable apps while requiring explicit provider selection.
- [x] Add scanner rules that reject direct provider leakage when a managed lane is
  selected.

Acceptance:

- A build can declare `payments.checkout` without naming Stripe.
- Generated artifacts contain safe env names and facade/client code only.
- Tests fail if a managed integration emits raw provider SDK imports, webhook
  handlers, or raw secrets.

### Phase 2: Studio UX

- Add a resolver that groups app needs into Ready, Needs setup, Optional, and
  Advanced.
- Render one recommended action per missing requirement.
- Add "Use managed service" actions for managed defaults.
- Add "Connect account" actions where OAuth support exists.
- Keep "Add my own key" in advanced setup.
- Render connector health only when a health plugin is registered.
- Make readiness and health messages safe for frontend display.

Acceptance:

- A novice builder can see what is missing without reading env var names.
- The setup page never displays raw secret values.
- Manual key entry is still available for self-host and enterprise use.

### Phase 3: Hosted Managed Services

- In Mozaiks App, expose hosted onboarding APIs for managed service activation.
- Let managed services issue app-scoped client credentials or OAuth grants.
- Store hosted credentials in the hosted product secret store.
- Return only safe status and display prefixes to OSS/Studio.
- Add managed setup evidence to connector health/status records.

Acceptance:

- A user can activate MozaiksPay without opening a payment-provider dashboard.
- A generated app receives only `MOZAIKSPAY_API_BASE` and a secret handle or
  managed credential reference.
- Stripe or other provider mechanics remain outside OSS and generated bundles.

### Phase 4: Workflow Resume

- Make `integration.required` events carry the setup lane options and the
  recommended default.
- Resume the paused workflow after setup succeeds.
- Recheck readiness before continuing.
- Persist setup decisions so switching between Ask and Workflow mode does not
  create duplicate setup prompts.

Acceptance:

- A build pauses once for a missing integration, resumes after setup, and does
  not ask again for the same configured service.
- Chat history and app build state both show the same integration decision.

### Phase 5: Observability And Runbooks

- Log lane resolution decisions without secrets.
- Track readiness state changes and manual health-check attempts.
- Add operator runbooks for managed setup, connected-account setup, and BYO
  setup.
- Add smoke tests for MozaiksPay managed setup, OAuth mock setup, and BYO secret
  setup.

Acceptance:

- Operators can answer why a build is blocked.
- Test evidence covers the no-key managed path and the advanced key path.
- Failure messages tell the user what action to take next.

## Implementation Ownership

| Work item | Repo | Layer |
| --- | --- | --- |
| Integration requirement schema | `mozaiks` | Platform / Studio module |
| AppGenerator prompts and structured outputs | `mozaiks` | Factory |
| Setup lane resolver | `mozaiks` | Studio |
| Workspace/app integration pages | `mozaiks` | Studio UI |
| Secret handles and names-only artifacts | `mozaiks` | App workspace contract |
| Managed service activation APIs | `mozaiks-app` | Hosted product |
| MozaiksPay provider mechanics | `mozaiks-app` | Hosted product adapters |
| Connector health plugins | `mozaiks` contract, product implementations external | Studio / hosted extension |

## Settled Decisions

- Setup lanes live directly on catalog-backed integration requirements and app
  declarations. A separate setup-policy file is unnecessary until multiple
  products need divergent lane ordering.
- The OSS side describes the managed lane and generates safe config. Hosted
  products implement managed-service activation.

## Open Decisions

- Whether managed activation creates a workspace connector record immediately
  or a pending setup record that becomes a connector only after hosted
  provisioning succeeds.
- Whether app-specific connector overrides should be visible in the workspace
  `/integrations` page or only in `/apps/{appId}/integrations`.
- Which OAuth providers are first-class in OSS docs versus product-specific
  connector plugins.

## Cross References

- [Integrations Workflow](integrations-workflow.md)
- [Managed Capability Packs](../modules-systems/managed-capability-packs.md)
- [Integrations Guide](../../guides/integrations/01-overview.md)
- [Distribution and Workspace Model](../foundations/distribution-and-workspace-model.md)
