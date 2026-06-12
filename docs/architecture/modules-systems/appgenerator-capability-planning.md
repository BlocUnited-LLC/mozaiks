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
- provider-backed hosted pack adapters
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
| `hosted_pack` | Hosted/private service | Generate app-side adapter and optional facade only |
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

### `hosted_pack`

Private or hosted product capability exposed to generated apps through an
app-side surface.

Rule: do not generate hosted internals. Generate or copy only declared app-side
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

Hosted pack:

```yaml
capability_pack_id: mozaikspay
capability_source: hosted_pack
surface_kind: external_integration
implementation_mode: external_integration
```

External adapter:

```yaml
capability_pack_id: stripe_billing
capability_source: external_adapter
surface_kind: external_integration
```

## Hosted Facade Pattern

Hosted packs must be consumed through generated app-owned facades:

```text
hosted_pack
  -> app/services/integrations/{pack_id}_client.py
  -> app/modules/{facade_module_id}/
  -> app/ui/pages/{page}.yaml
```

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
app/modules/hosted_billing/
app/modules/wallet/
app/services/hosted_*
app/capability_packs/
```

Those are hosted/provider internals or obsolete output shapes.

## Decision Test

Ask these questions when classifying a planned capability:

1. Is it already provided by runtime/platform?
   Use `host_universal`; do not generate it.
2. Is it normal app-owned business logic?
   Use `generated_module`.
3. Is it backed by reusable OSS build context?
   Use `framework_pack`.
4. Does it depend on a hosted/private engine?
   Use `hosted_pack` and generate only app-side facade surfaces.
5. Does it call an external provider?
   Use `external_adapter`.

## Cross References

- [Module System](module-system.md)
- [Framework Capability Classification](framework-capability-classification.md)
