# Managed Capability Packs

Managed capability packs let generated apps consume operator-managed services
without receiving the hosted service implementation.

This is the public OSS contract. It describes the reusable pattern only. Hosted
product policy, payment-provider mechanics, payout policy, fee policy,
marketplace settlement, campaign economics, and private service registries live
outside OSS in the hosted product workspace that operates those services.

## Definition

A managed capability is a capability whose engine is operated outside the
generated app, while the generated app receives a safe app-owned surface for
calling it.

```text
generated app page
  -> generated facade module action
  -> app/services/integrations/{service}_client.py
  -> hosted service API
  -> provider/product implementation outside the generated app
```

The generated app owns its facade and local UX. The hosted product owns the
service engine.

## Ownership Boundary

| Concern | OSS owns | Hosted/operator layer owns |
| --- | --- | --- |
| Pack selection | Build-context metadata and AppBuildPlan ownership class | Which hosted services are commercially available |
| Generated app client | Thin HTTP client under `app/services/integrations/` | Hosted API implementation |
| Generated app facade | App-owned module actions and pages | Product-side module actions and provider mechanics |
| Secrets contract | Names-only env handles and connector field names | Credential issuance and secret delivery |
| Runtime effects | Provider-neutral commands and runtime enforcement | Verification of the commercial/provider fact |
| Scanner guards | Forbidden generated output rules | Private implementation inventory and readiness |

## Pack Shape

Reusable packs live under a named build context:

```text
factory_app/build_context/{pack_id}/
├── context.yaml
├── contract.yaml
├── provider_api_contract.yaml      # optional public API shape
└── templates/
    ├── services/integrations/{pack_id}_client.py
    ├── modules/{facade_module}/
    ├── ui/pages/
    └── config/
```

`context.yaml` registers the pack and declares only structural metadata:

- `context_id`
- `applies_to_workflows`
- `assets[]`
- optional `pack`
- optional `capabilities`
- optional `facades`
- optional `projections.context_variables`

`contract.yaml` declares typed generation rules:

- `selection_rules`
- `required_integrations`
- `required_outputs`
- `forbidden_outputs`
- `runtime_boundaries`
- `facades`

Templates mirror the generated app tree under `app/`.

## Generated App Output

A selected managed capability may generate:

- `app/services/integrations/{service}_client.py`
- `app/modules/{facade_module}/module.yaml`
- `app/modules/{facade_module}/backend/*`
- `app/ui/pages/*.yaml`
- names-only config such as `app/config/subscriptions.yaml`
- env handles in `env.example`

It must not generate:

- hosted product modules
- provider SDK wrappers for the managed service
- payment-provider webhook handlers
- app-local wallet or payout engines
- provider customer/account IDs
- raw credentials
- hosted product fee, settlement, or campaign policy

## Facade Rule

Pages should bind to generated app facade modules, not directly to hosted
service internals.

Good:

```text
page -> /api/modules/billing_portal/create_checkout_session
     -> app/services/integrations/mozaikspay_client.py
     -> hosted MozaiksPay API
```

Bad:

```text
page -> /api/modules/hosted_billing/create_subscription_checkout_session
page -> /api/modules/mozaikspay_checkout/create_checkout_session
page -> payment-provider SDK
```

The facade keeps the generated app deterministic and replaceable. If the hosted
service changes providers, the generated app contract does not need to change.

## Auth And Secrets

Managed packs declare integration requirements as structured fields. Public
configuration may be frontend safe; secret fields must remain backend-only and
names-only in generated artifacts.

Example:

```yaml
required_integrations:
  - service: service_id
    provider: hosted_provider
    kind: api_key
    required_fields:
      - name: api_base
        type: url
        frontend_safe: true
      - name: api_key
        type: secret
        frontend_safe: false
```

Generated artifacts may include env names such as:

```text
SERVICE_API_BASE=
SERVICE_API_KEY=
```

They must not include real values.

## Runtime Effects

If a managed service changes runtime state, the effect must cross the boundary
as a provider-neutral command or event.

For monetized or metered apps:

```text
trusted external fact
  -> provider-neutral fulfillment command
  -> app/config/subscriptions.yaml assignment/token effect
  -> EntitlementPort / token guard enforcement
```

The runtime should not care whether the trusted fact came from MozaiksPay, an
enterprise invoice system, a test fixture, or a custom provider adapter.

## MozaiksPay As Default Managed Adapter

MozaiksPay is the default managed monetization pack when the app needs
subscriptions, billing portal redirects, token top-ups, usage status, or paid
feature gates and the user has not explicitly selected another provider.

That default is modular:

- generated apps get a MozaiksPay client/facade
- runtime entitlements and token guards remain provider-neutral
- the payment implementation lives outside the generated bundle
- another explicitly selected adapter can satisfy the same facade/fulfillment
  boundary

The generated app should see MozaiksPay-branded API handles, not raw payment
provider details.

## Provider Replacement

If a user explicitly wants another provider, do not fork the runtime. Generate
an `external_adapter` boundary that satisfies the same app-facing contract:

- facade action creates checkout or portal session
- adapter verifies the provider fact
- adapter emits or applies a provider-neutral fulfillment command
- runtime entitlements and token guards remain unchanged

This keeps the framework reusable without requiring OSS to become a payment
platform.

## Scanner Requirements

Generated bundle scanners should reject managed-capability leakage:

- raw provider imports for managed services
- app-local wallet ledgers for hosted wallet services
- app-local usage ledgers when OSS runtime owns token accounting
- direct calls to hosted internals such as product module routes
- webhook handlers for managed payment/provider callbacks
- provider customer/account IDs in generated config
- raw secrets in source, config, deployment artifacts, or docs

Scanner rules should be derived from pack `forbidden_outputs` and known
managed-service path patterns.

## Decision Checklist

Use this when adding a capability to AppGenerator:

- Is the capability universal runtime/platform behavior?
  Use `host_universal`.
- Is it a reusable OSS pack with deterministic templates?
  Use `framework_pack`.
- Is it operated by a hosted product or operator service?
  Use `managed_capability`.
- Is it app-specific business logic?
  Use `generated_module`.
- Is it an app-owned direct integration with an outside provider?
  Use `external_adapter`.

For `managed_capability`, verify:

- generated app output is only client, facade, config, pages, and env handles
- provider mechanics are outside the generated app
- entitlement/token effects cross through provider-neutral runtime commands
- scanner guards block internal/provider leakage
- tests cover pack materialization and app loading

## Cross References

- [AppGenerator Capability Planning](appgenerator-capability-planning.md)
- [Module System](module-system.md)
- [Build Context Packs](../workflows/build-context-packs.md)
- [Monetization Contract](../mozaiksai/monetization-contract.md)
