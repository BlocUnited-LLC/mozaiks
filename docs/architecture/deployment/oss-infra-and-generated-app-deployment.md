# OSS Infra And Generated App Deployment

## Purpose

This document explains three different things that are easy to conflate:

1. the repo-local `infra/` directory in this OSS repo
2. the first-party `factory_app/` builder/reference workspace shipped with Mozaiks
3. the deployment artifacts that AppGenerator can emit for future generated apps

The goal is to make the current implemented boundary explicit for OSS users and
contributors.

## Current Source Of Truth

Current implemented behavior is anchored in these files:

- `.dockerignore`
- `infra/compose/docker-compose.yml`
- `infra/compose/docker-compose.prod.yml`
- `infra/docker/Dockerfile`
- `infra/helm/mozaiks/`
- `.github/workflows/ci.yml` (`infra-build` job)
- `mozaiksai/hosts/bootstrap.py`
- `mozaiksai/hosts/runtime.py` (health/readiness/liveness routes)
- `mozaiks_cli/commands/serve.py`
- `factory_app/workflows/AppGenerator/tools/generate_and_download.py`
- `factory_app/workflows/AppGenerator/tools/deployment_contract.py`
- `factory_app/workflows/AppGenerator/tools/app_validation.py`
- `docs/architecture/deployment/generated-app-deployment-contract.md`

When docs and implementation differ, follow those files and the related tests.

## What `infra/` Is Today

`infra/` is **repo-local operational scaffolding for this OSS repository**.

It currently holds:

- local and repo-hosted Docker Compose files
- the repo Docker image definition
- the repo Helm chart
- Keycloak export/import helpers
- Grafana dashboard templates

This directory is for operating **Mozaiks itself**, especially the first-party
Studio/builder stack that lives in this repo.

It is **not** the canonical source for generated customer-app deployment.

## What `factory_app/` Is In This Story

`factory_app/` is the first-party builder/reference workspace that dogfoods the
same app workspace contract external users and hosted products consume.

It has two separate roles:

1. `factory_app/app/` — first-party Studio app bundle
2. `factory_app/workflows/` — shared builder workflows

Current repo-local infra uses those assets directly.

Examples from the current implementation:

- Docker Compose mounts Keycloak realm and theme assets from
  `factory_app/app/brand/`
- Studio host bootstrap falls back to `factory_app/app/` when no external app
  workspace is supplied
- Studio host bootstrap prefers `factory_app/workflows/` as the workflow root

That means the repo infra is currently oriented around the **first-party Studio
stack**, not around a generated app bundle.

## What Generated Apps Get Today

Generated apps can receive **provider-neutral deployment artifacts at the app
bundle root**.

Current artifact family:

- `Dockerfile`
- `docker-compose.yml`
- `env.example`
- `deployment.manifest.json`
- `.github/workflows/deploy.yml`

Those files are emitted by the AppGenerator deployment contract renderer during
download/export.

They are:

- deterministic
- names-only for env and secret contracts
- provider-neutral
- app-bundle-root outputs, not `app/services/` files

They are **not** currently the same thing as this repo's `infra/` directory.

## What Generated Apps Do Not Inherit From `infra/`

Generated apps do **not** automatically inherit the repo-local:

- Helm chart under `infra/helm/mozaiks/`
- Keycloak realm export flow under `infra/keycloak/`
- Grafana dashboard under `infra/grafana/`
- repo-local compose stack under `infra/compose/`

That separation is intentional.

The generated-app deployment contract answers:

> how does this app run, what files describe that, and what handoff metadata is
> needed?

It does **not** answer:

> which cloud/provider should host it, which platform owns deployment state,
> who injects secrets, or which product-specific adapters mutate infrastructure?

## How An OSS User Should Use This Today

### Path 1: Work On Mozaiks Itself

If you are changing Mozaiks OSS, Studio, the builder workflows, shell behavior,
or repo-local auth/dev infrastructure:

- use the repo-local Studio/dev path
- treat `factory_app/app/` as the active first-party app bundle
- treat `factory_app/workflows/` as the shared builder workflow root
- use the repo-local scripts and local setup guidance for backend/frontend/dev
  startup

This is the path described in `docs/local-setup.md`.

In this mode, `infra/` is part of the OSS contributor experience.

### Path 2: Create Or Run An App Workspace

If you want to create or run an app built with Mozaiks:

- scaffold or generate an app workspace
- serve that workspace through `mozaiks serve <workspace> --host platform`
- treat app-root deployment artifacts as the deployment handoff for that app

In this mode, the app workspace owns:

- `app/`
- `workflows/`
- optional app-root deployment artifacts

In this mode, the repo's `infra/` directory is **reference infrastructure for
the OSS repo**, not the app's deployment system.

## Current State Vs Target State

### Current

Current implemented behavior:

- repo infra operates the first-party Studio/builder stack
- generated apps may emit provider-neutral deployment artifacts when requested
- generated app deployment artifacts are optional in some build flows
- provider-specific execution remains outside the generated app bundle

### Target

Canonical target behavior:

- `infra/` remains repo-local OSS operational scaffolding
- generated apps always have a clear app-root deployment handoff when the build
  profile is production-oriented
- hosted or operator layers consume `deployment.manifest.json` and perform real
  deployment execution outside the generated bundle
- provider-specific policy, secret delivery, rollout state, and deployment
  records live outside the generated app workspace

## Current Gaps Before This Is Production-Ready

The current docs should not present repo infra as production-ready for public
consumption yet.

Resolved in this pass:

1. `infra/docker/Dockerfile` no longer references removed root files
   (`run_server.py`, `shared_app.py`, `workflows/`, `config/`). It installs the
   real `mozaiks` package from `pyproject.toml` and serves `factory_app/`
   through `mozaiks serve . --host studio`.
2. `infra/helm/mozaiks/values.yaml` liveness probe now targets the route that
   actually exists (`/api/health/live`), not `/api/health/liveness`.
3. `infra/compose/docker-compose.yml`'s dev `app` service no longer runs a
   `watchmedo`/`run_server.py` command with no matching dependency; it runs
   `mozaiks serve . --host studio --reload` against the bind-mounted repo.
4. CI now has an `infra-build` job (`.github/workflows/ci.yml`) that builds
   `infra/docker/Dockerfile`, smoke-runs the image against a real MongoDB
   until `/api/health` reports healthy, and lints/renders the Helm chart with
   a regression check on the probe paths.
5. CI now runs the `scripts/production_readiness_gate.py` source hygiene scan
   on every PR/push instead of only on demand.

Still open:

1. generated app deployment artifacts are still optional in some AppGenerator
   paths instead of being mandatory for a production build profile
2. the generated-app deployment contract exists, but provider-specific
   execution is intentionally outside this OSS bundle — a hosted or self-host
   operator layer that turns `deployment.manifest.json` into a real deployment
   still needs to be built outside this repo
3. `infra/` covers Docker Compose, Docker, and Helm for the repo host; it does
   not yet cover TLS/ingress hardening, session affinity, or a documented
   backup/restore drill run against the current Helm chart

Those are current implementation and build-process gaps, not reasons to blur the
ownership boundary.

## Decision Rule For Contributors

When deciding where a deployment-related change belongs:

- change `infra/` when the change is about operating this OSS repo's first-party
  Studio/builder stack
- change AppGenerator deployment contract files when the change is about what a
  generated app exports at its bundle root
- change hosted/operator adapters outside this layer when the change is about
  provider-specific deployment execution or platform-owned deployment records

Do not copy repo-local infrastructure mechanics into generated app bundles just
because both are deployment-related.

## Practical Summary

For an OSS user, the simplest mental model is:

- `infra/` helps run **this repo**
- `factory_app/` is the **first-party builder/reference workspace** inside this
  repo
- AppGenerator deployment artifacts help describe how a **future generated app**
  runs
- a hosted product or self-host operator still needs a separate layer that turns
  that contract into real deployment execution