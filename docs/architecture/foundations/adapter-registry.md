# Adapter Registry

Status: **implemented mechanism.** `mozaiksai/core/adapters/registry.py` and the
`config/adapters.yaml` contract described here are live and loaded at app
startup. Provider ports and implementations are not shipped by this mechanism —
see [Ports do not live here](#ports-do-not-live-here).

## What this is

A declarative way for an app to say which provider adapters it has, and a
loader that **verifies each one satisfies the port it claims** before the app
finishes starting.

```yaml
# app/config/adapters.yaml
schema_version: mozaiks.adapters.v1
areas:
  dns:
    port: app.services.adapters.dns.port:DnsProviderPort
    active_env: DNS_ADAPTER      # optional; overrides `active` per deployment
    active: cloudflare           # optional default
    providers:
      cloudflare: app.services.adapters.dns.cloudflare:CloudflareDnsAdapter
      stub: app.services.adapters.dns.stub:StubDnsAdapter
```

At startup `AppLoader` resolves every declared reference, checks conformance,
and exposes the result as `AppLoadResult.adapter_registry` — surfaced on the
platform host as `app.state.adapter_registry`. An app with no
`config/adapters.yaml` gets `None` and is entirely unaffected.

## Ports do not live here

This module defines **no ports and knows no providers**. Both the port and the
implementation are named by import reference, so they may live in the app
bundle, in a capability pack, or in a hosted product. The runtime never needs
to know which.

That is deliberate, and it is a correction of a pattern that did not work. A
port the OSS runtime does not call through does not belong in OSS: it becomes
an orphaned published type that commits the project to an API with no consumer
and no verification. The test for whether a port belongs in `mozaiksai/core/ports/`
is simply **does OSS runtime code call through it** — `EntitlementPort` is
checked at module dispatch, `OrchestrationPort` drives workflow execution, so
both qualify. A DNS or SSL port has no OSS caller and belongs beside whatever
consumes it.

This loader is what makes that placement possible without giving up load-time
verification.

## Conformance is the point

"Adapters are modular" is a weak property; anything with a dict of callables is
modular. The property worth having is that **a declared adapter is verified
against its contract at load time**.

Conformance is checked by member presence, reading `__protocol_attrs__` when
the port is a `Protocol`. Attribute presence is used rather than `issubclass`
because `runtime_checkable` protocols only support `issubclass` for method-only
protocols, and its failure mode is an opaque `TypeError` instead of a message
naming the missing method. The registry instead reports:

```
adapters.yaml area 'dns' provider 'partial': <class 'PartialAdapter'> does not
satisfy 'DnsProviderPort' — missing delete_record
```

**Every declared provider is verified, not only the active one.** A declaration
that cannot be satisfied is a defect regardless of which provider happens to be
switched on today, and finding it at startup is the entire purpose.

## Fail-closed, deliberately

Unlike `subscriptions.yaml` — which degrades to a warning and disables
entitlement enforcement — a broken adapter declaration **raises and stops
startup**.

An adapter that cannot be imported, or that does not satisfy its port, will
fail at the moment of use. Surfacing it at startup with a precise message is
strictly better than an `AttributeError` in production, and degrading silently
here would reproduce exactly the swallowed-import failure mode this mechanism
exists to prevent.

## Declaration versus runtime config

The declaration answers *what implementations exist*. Runtime config answers
*which one is on*.

`active_env` names an environment variable that, when set and non-empty, wins
over `active`. An operator can therefore switch providers per deployment
without editing the contract. Conditional logic does **not** belong in the
YAML — putting it there would make the declaration non-statically-checkable and
forfeit the main benefit.

An `active_env` value naming an undeclared provider fails closed rather than
silently selecting nothing, so an operator typo is loud.

## Why this scales

Adding a provider is **adding a directory**. A capability pack ships the
adapter template plus a contract fragment declaring its area, provider id, and
port; nothing else changes:

- AppGenerator selects it through existing `selection_rules` — no prompt change
- the Jinja resolver renders it — no generator change
- this loader resolves and verifies it — no registry change
- Studio `/integrations` renders its credential form from the pack's
  `required_integrations` — no UI change

The generator's prompt surface does not grow with provider count. AppGenerator
never learns "Cloudflare"; it learns "select a dns pack". A hundred providers
cost what three cost.

## Known trade-off: fix propagation

Packs render adapter code **into** each app bundle, so every generated app
carries its own copy. A bug fix in a provider adapter does **not** propagate on
its own — it is a pack version bump plus a refinement pass per app.

This is consistent with the workspace model (self-contained, portable,
provider-neutral bundles) and the provenance manifest already records which
pack version an app received. It is recorded here as a deliberate choice rather
than an emergent one: the alternative — adapters as a runtime dependency apps
import — propagates fixes instantly but couples every app to the framework for
provider mechanics and undercuts portability.

## Cross references

- [Public Architecture And OSS Boundary](public-architecture-and-oss-boundary.md)
- [OSS Boundary Family Registry](oss-boundary-families.md)
- [Distribution And Workspace Model](distribution-and-workspace-model.md)
