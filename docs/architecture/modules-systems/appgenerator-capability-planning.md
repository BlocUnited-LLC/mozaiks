# AppGenerator Capability Planning

This document defines how AppGenerator in the Studio uses capability planning inside an
`AppBuildPlan`.

The key rule: `capability_pack_id` is planning metadata. It is not a promise
that a reusable package exists on disk.

This logic is not to be confused around the build-time context packages for factory workflows.
Documented separately in [Build Context Packs](../workflows/build-context-packs.md).

## Purpose

AppGenerator uses `capability_packs[]` to group related product intent before
deriving concrete app files.

A planned capability can become:

- generated app modules
- generated pages
- generated workflows
- app-owned integration facades
- provider-backed managed capability adapters
- cross-cutting setup tasks

The planning group gives downstream agents a stable ownership boundary for
tasks, paths, dependencies, and page binding. It does not define a filesystem
package.

## AppBuildPlan Fields

Canonical fields:

```yaml
capability_pack_id: projects
capability_source: generated_module
surface_kind: module
implementation_mode: declarative_module
```

Meanings:

| Field | Meaning |
| --- | --- |
| `capability_pack_id` | Stable feature-family id inside the build plan |
| `capability_source` | Ownership/source boundary for generation behavior |
| `surface_kind` | Realization surface such as `module`, `workflow`, `external_integration`, or `ui_only` |
| `implementation_mode` | How the surface is implemented |

## Ownership Sources

Every planned capability should resolve to one ownership source.

| Source | Owner | Generation rule |
| --- | --- | --- |
| `host_universal` | Runtime/platform | Already present; never generate |
| `framework_pack` | OSS framework/build context | Reuse declared context; generate only app-specific wiring |
| `managed_capability` | Operator-managed service | Generate app-side adapter and optional facade only |
| `generated_module` | Generated app | Generate module contracts and backend stubs |
| `external_adapter` | External provider | Generate provider-facing wiring/facade only |

### `host_universal`

Runtime/platform behavior every app receives automatically.

Examples: transport, sessions, event dispatch, admin shell, profile API, usage
ledger, entitlement runtime primitives.

Rule: never include these as generated modules or build tasks.

### `framework_pack`

Reusable OSS capability context or deterministic templates shipped by Mozaiks.

Rule: select the framework capability and generate only app-specific
composition.

### `managed_capability`

Operator-managed product capability exposed to generated apps through an
app-side surface.

Rule: do not generate provider internals. Generate or copy only declared app-side
adapters, facade modules, and pages.

### `generated_module`

Normal app-owned business logic generated for one app.

Rule: generate canonical module files under `modules/{module_id}/`.

### `external_adapter`

An app-owned facade to an outside provider.

Rule: generate integration wiring and app-owned facade code only. Do not
generate the external system.

## Examples

Generated module:

```yaml
capability_pack_id: projects
capability_source: generated_module
surface_kind: module
```

Managed capability:

```yaml
capability_pack_id: mozaikspay
capability_source: managed_capability
surface_kind: external_integration
implementation_mode: external_integration
```

External adapter:

```yaml
capability_pack_id: payment_provider_billing
capability_source: external_adapter
surface_kind: external_integration
```

## Managed Facade Pattern

Managed capabilities must be consumed through generated app-owned facades:

```text
managed_capability
  -> app/services/integrations/{pack_id}_client.py
  -> app/modules/{facade_module_id}/
  -> app/ui/pages/{page}.yaml
```

Managed capability service clients are consumer adapters. For SaaS billing, the
generated `mozaikspay_client.py` resolves the app-scoped `mozaikspay` connector
or `MOZAIKSPAY_*` env fallback, calls the public MozaiksPay provider API, and
does not call provider-owned modules such as `managed_billing` directly.
Runtime token usage remains local to the generated app runtime: `MOZAIKS_APP_URL`
or an explicit `runtime_base` connector field points at the generated app's own
`/api/me/usage` endpoint, while `MOZAIKSPAY_API_BASE` points only at the
MozaiksPay provider.

Managed capabilities declare connector requirements on
`capability_packs[].required_integrations` as structured objects, not string-only
service names. Public config such as `api_base` and `client_id` may be
`frontend_safe: true`; provider secrets such as `client_secret` must be
`type: secret` and `frontend_safe: false`. Integration readiness uses that
shape to request missing app-scoped credentials without placing raw values in
generated artifacts. When a provider can pre-provision credentials, it
should write the same app-scoped connector record through the generic connector
store; connector metadata should preserve `service`, `provider`, and
`integration_id`, while raw secret values remain only in the configured vault.

For MozaiksPay, a generated SaaS app may receive:

```text
app/services/integrations/mozaikspay_client.py
app/modules/billing_portal/
app/ui/pages/billing.yaml
app/ui/pages/usage.yaml
app/config/subscriptions.yaml
```

It must not receive:

```text
app/modules/mozaikspay/
app/modules/managed_billing/
app/modules/wallet/
app/services/managed_*
app/capability_packs/
```

Those are managed/provider internals or obsolete output shapes.

The managed facade must also satisfy normal module runtime contracts. Page
endpoints must resolve to actions declared by the facade module, handler methods
must accept runtime input as declared in `module.yaml.actions[].input_schema`,
and app-level service clients must be importable from the generated app bundle
root. The deterministic acceptance gate runs the assembled file map through
scanner checks and `AppLoader.load()`, while the production readiness replay
keeps selected managed packs covered end to end. Template drift fails before
export or promotion.

## Decision Test

Ask these questions when classifying a planned capability:

1. Is it already provided by runtime/platform?
   Use `host_universal`; do not generate it.
2. Is it normal app-owned business logic?
   Use `generated_module`.
3. Is it backed by reusable OSS build context?
   Use `framework_pack`.
4. Does it depend on an operator-managed engine?
   Use `managed_capability` and generate only app-side facade surfaces.
5. Does it call an external provider?
   Use `external_adapter`.

## Cross References

- [Module System](module-system.md)
- [Framework Capability Classification](framework-capability-classification.md)
