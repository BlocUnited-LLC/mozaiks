# Mozaiks Platform Integration Strategy

**Status:** Archived / superseded.

This document previously described a separate platform-service integration model
and a packaged greenfield app shell layout.
That model is retired and is not a valid source of truth for current Mozaiks
builds or runtime decisions.

## Use These Instead

- `ARCHITECTURE.md`
- `docs/architecture/foundations/overview.md`
- `docs/architecture/foundations/canonical-app-structure.md`
- `docs/architecture/foundations/core-product-app-bundle-boundary.md`
- `docs/architecture/foundations/account-admin-and-platform-services.md`
- `docs/architecture/foundations/workflow-architecture.md`

## Current Rule

- Greenfield apps target the active app root under `app/`, not a packaged
  legacy shell layout.
- Deterministic app behavior is hosted by `mozaiksai/hosts/platform.py`, with
  the product host composed in `mozaiksai/hosts/mozaiks.py`.
- Brownfield integrations should use the app-backend boundary and host
  contracts, not revive the retired packaged shell/runtime model.

If any future integration strategy is needed, rewrite it against the current
host architecture instead of extending this archived document.
