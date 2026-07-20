# Repo-Local Infra Guide

## Purpose

This directory contains **repo-local operational scaffolding for the Mozaiks OSS
repository**.

Use it when you are operating or debugging the first-party Studio/builder stack
that lives inside this repo.

Do **not** treat this directory as the canonical generated-app deployment output.

For the architecture boundary, see:

- `docs/architecture/deployment/oss-infra-and-generated-app-deployment.md`
- `docs/architecture/deployment/generated-app-deployment-contract.md`

## What `infra/` Covers Today

Current repo-local infra includes:

- Docker Compose for local and repo-hosted stack startup
- repo Docker image definition
- Helm chart for the repo host
- Keycloak realm export/import support
- Grafana dashboard templates

Current implementation is oriented around the first-party `factory_app/`
workspace and shared builder workflows.

## When An OSS User Should Use This Directory

Use `infra/` when you are:

- changing Mozaiks OSS itself
- running the first-party Studio stack in this repo
- debugging repo-local auth, MongoDB, Keycloak, or observability wiring
- validating repo-host packaging

If you are creating or deploying a generated app workspace, the app's own
workspace root and deployment contract are the source of truth instead.

## Current Local Dev Path

The preferred OSS contributor path is the local setup flow documented in
`docs/local-setup.md`.

That path uses the repo-local `factory_app/app`, `factory_app/workflows`, and
`web_shell/` sources, with local Docker Compose infra for MongoDB and Keycloak
when needed.

## Repo-Local Compose Shortcuts

From the repo root:

```bash
cd infra/compose
docker compose up -d
```

Current compose stack is for the repo-local Studio/builder environment, not a
generic generated app deployment.

Production-flavored repo compose:

```bash
cd infra/compose
docker compose -f docker-compose.prod.yml up -d
```

## Current Caveat

This repo is not in production yet.

The Docker image, dev/prod Compose stacks, and Helm chart are now built and
smoke-tested as part of CI (`infra-build` job in `.github/workflows/ci.yml`).
Operational hardening beyond that — TLS/ingress policy, session affinity,
secrets rotation drills, and a documented backup/restore rehearsal against the
current Helm chart — is still outstanding. Do not describe this directory as
the canonical deployment path for generated customer apps.

## Generated Apps Are Separate

Generated apps may receive provider-neutral app-root deployment artifacts such
as:

- `Dockerfile`
- `docker-compose.yml`
- `env.example`
- `deployment.manifest.json`
- `.github/workflows/readiness.yml`
- `.github/workflows/deploy.yml`

Those come from AppGenerator's deployment contract renderer, not from this
repo-local `infra/` directory.
