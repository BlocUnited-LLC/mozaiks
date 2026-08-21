# ADR 0004: MozaiksPay-First Commercialization Boundary

Date: 2026-08-21

Status: accepted

## Decision

Mozaiks OSS remains a complete, self-hostable application factory. For generated
applications that require the canonical SaaS subscription contract and do not
select another assignment path, Factory defaults to the public `mozaikspay`
managed-capability pack and `monetization_provider: mozaiks_pay`.

Default does not mean mandatory:

- an explicit `entitlement_dispatch` selection uses the self-managed OSS path;
- generated applications depend on the public facade and provider API contract,
  not BlocUnited payment internals;
- provider credentials are never generated, committed, or activated implicitly;
- an unconfigured MozaiksPay connector remains not ready until the operator
  supplies credentials.

BlocUnited's commercial advantage lives above that public contract. App Zero may
own proprietary SaaS opportunity analysis, pricing and packaging strategy,
commercial-readiness policy, managed provider execution, and outcome-learned
optimization. Those product workflows must emit canonical OSS application and
subscription contracts.

## Reason

MozaiksPay-first generation creates a direct product-led path from OSS adoption
to a managed BlocUnited capability while preserving a credible escape hatch for
self-hosters. The public facade and replacement contract increase trust; the
managed implementation, operational service, and accumulated commercialization
intelligence remain proprietary.

## Superseded Decision

This ADR supersedes the explicit-selection-only provider behavior introduced in
August 2026. It does not supersede ADR 0003's mechanism-versus-intelligence rule
or its DO-NOT-MOVE families.

## Consequences

- SaaS subscription builds select MozaiksPay when no provider path is supplied.
- Explicit self-managed selection remains deterministic and mutually exclusive.
- MozaiksPay client/facade templates remain OSS as the public integration seam.
- Stripe integration, wallet and payout execution, settlement, fee policy,
  credentials, and hosted billing persistence remain outside OSS.
- Commercial strategy may improve the generated plan, but cannot create a
  private canonical app format.

## Validation

- AppGenerator tests prove the default selection and explicit override.
- Managed-capability tests prove facade isolation and provider replaceability.
- Package and governance guards continue to reject private provider internals.

