# Mozaiks Platform: .NET to Python Migration Plan

**Status:** Archived / superseded.

This page described an older migration plan for a separate platform-services
layer and included a now-retired service-centric layout. That plan is no
longer the architectural basis for Mozaiks.

## Use These Instead

- `ARCHITECTURE.md`
- `docs/architecture/foundations/overview.md`
- `docs/architecture/foundations/canonical-app-structure.md`
- `docs/architecture/foundations/core-product-app-bundle-boundary.md`
- `docs/architecture/foundations/account-admin-and-platform-services.md`

## Current Rule

- The canonical runtime and app host live in `mozaiksai/hosts/`.
- Active app bundles are rooted under `app/`.
- Platform-owned product behavior should be expressed through current host,
  app-bundle, and app-backend contracts rather than through the retired
  migration plan in this file.

If migration planning is revived later, it should be rewritten from the current
layered host model instead of preserving the older service-layout proposal.
