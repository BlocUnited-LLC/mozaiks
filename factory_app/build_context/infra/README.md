# Infra Scaffold Templates

Templates emitted by `InfraScaffoldAgent` during app generation.
After first emit the operator owns these files — regeneration is explicit and opt-in.

## Template Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `{{APP_NAME}}` | `app/app.json .name` (or `.slug`) | Slug used in image tags, container names, nginx conf |
| `{{MOZAIKS_VERSION}}` | `requirements.txt` or latest at generation time | Pinned mozaiks pip version |
| `{{OIDC_CLIENT_ID}}` | `app/config/auth.yaml .oidc.client_id` | OIDC client ID baked into the Vite SPA bundle |
| `{{REGISTRY}}` | generator input | Container registry prefix (e.g. `ghcr.io/org`) |
| `{{DEPLOY_TARGET}}` | generator input | `azure_container_apps` \| `fly` \| `render` \| `generic` |

## Files

| File | Purpose |
|------|---------|
| `Dockerfile.template` | Multi-stage build: frontend (Node/Vite) → Python deps → nginx+uvicorn runtime |
| `workflows/deploy.yml.template` | CI/CD: build + push image → deploy → health verify with rollback stub |
| `scripts/provision.sh.template` | Secret-sync: reads `app/security/secrets.yaml`, validates env file, syncs to GitHub + deploy target |

## Usage After Generation

1. Copy template files to the generated app bundle root (substituting variables).
2. Fill in deploy target–specific sections (registry login, deploy command, rollback).
3. Copy `.env.staging.example` → `.env.staging` and fill in secrets.
4. Run `scripts/provision.sh staging` to validate and sync secrets.
5. Push to `main` or `staging` to trigger the deploy workflow.

## Operator Ownership

Generated infra files belong to the operator after first emit.
The generator will not overwrite them on subsequent runs unless `--force-infra` is passed.
Review the diff carefully before accepting any regenerated infrastructure changes.

## Deploy Target Notes

**Azure Container Apps (default)**
- Requires: `AZURE_OIDC_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `ACR_NAME`
- Uncomment the Azure login and `az containerapp update` blocks in `deploy.yml`.

**Fly.io**
- Requires: `FLY_API_TOKEN`
- Replace deploy block with `fly deploy --image $IMAGE_TAG --app {{APP_NAME}}`.

**Render**
- Requires: `RENDER_SERVICE_ID`, `RENDER_API_KEY`
- Use Render Deploy Hook or API to update the service image.

**Generic (VPS/Docker Compose)**
- SSH to the host and `docker pull && docker compose up -d`.
- Store `SSH_PRIVATE_KEY` and `SSH_HOST` as GitHub secrets.

## See Also

- [OSS Deployment Scaffold Design](../../../docs/oss-deployment-scaffold-design.md) (in mozaiks-app)
- `app/security/secrets.yaml` — canonical secret name registry for this app
- `docs/architecture/deployment/oss-infra-and-generated-app-deployment.md`
