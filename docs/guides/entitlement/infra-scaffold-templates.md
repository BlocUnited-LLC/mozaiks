# Infra Scaffold Templates

Templates emitted by `InfraScaffoldAgent` during app generation.
After first emit the operator owns these files — regeneration is explicit and opt-in.

## Staging Terms

Mozaiks uses two different staging concepts:

- **Artifact review staging** is the Mozaiks/Studio control-plane area where
  generated or refined files wait for validation, review, `ArtifactVersion`
  acceptance, and promotion.
- **Environment staging** is the operator or hosted deployment target used to
  prove a promoted/exported app before production, such as a GitHub `staging`
  environment or preview runtime.

These templates only handle environment staging. They do not bypass artifact
review, mutate source during generation, or provision hosted product resources.

## Template Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `{{APP_NAME}}` | `app/app.json .name` (or `.slug`) | Slug used in image tags, container names, nginx conf |
| `{{MOZAIKS_VERSION}}` | `requirements.txt` or latest at generation time | Pinned mozaiks pip version |
| `{{OIDC_CLIENT_ID}}` | `app/config/auth.yaml frontend.client_id_env` | OIDC client ID env handle baked into the Vite SPA bundle |
| `{{REGISTRY}}` | generator input | Container registry prefix (e.g. `ghcr.io/org`) |
| `{{DEPLOY_TARGET}}` | generator input | `azure_container_apps` \| `fly` \| `render` \| `generic` |
| `{{APP_SLUG}}` | `app/app.json .slug` | Lowercase slug for sessionStorage key prefix and auth adapter |
| `{{TOKEN_KEY_PREFIX}}` | defaults to `{{APP_SLUG}}` | sessionStorage key prefix for PKCE tokens |
| `{{AUTH_DEFAULT_ROUTE}}` | `app/config/auth.yaml routes.post_login_default` | Safe app-local route used after login when no return path is available |

## Template Files

| File | Purpose |
|------|---------|
| `templates/Dockerfile` | Multi-stage build: frontend (Node/Vite) → Python deps → nginx+uvicorn runtime |
| `templates/workflows/deploy.yml` | CI/CD: build + push image → deploy → health verify with rollback stub |
| `templates/workflows/readiness.yml` | Pre-production gate: image smoke, remote health, entitlement smoke |
| `templates/scripts/provision.sh` | Secret-sync: reads `app/security/secrets.yaml`, validates env file, syncs to deploy target |

Auth contract and adapter templates live in `webapp_builder/templates/`:

| File | Purpose |
|------|---------|
| `config/auth.yaml` | Provider-neutral `mozaiks.auth.v1` behavior/env-handle contract |
| `ui/auth/authAdapter.js` | State-bound OIDC PKCE auth adapter (real identity provider) |
| `ui/auth/authAdapter.mock.js` | Mock adapter for `VITE_MOCK_MODE=true` or no OIDC provider |

## Usage After Generation

1. Copy template files to the generated app bundle root, substituting variables.
2. Review generated `deployment.manifest.json`, `.env.example`,
   `.env.staging.example`, and `.env.production.example`; all
   env examples carry names only, never secret values.
3. Fill in deploy target–specific workflow sections such as registry login,
   deploy command, rollback, and any provider-specific secret sync.
4. Copy `.env.staging.example` to ignored `.env.staging` or
   `.env.production.example` to ignored `.env.production`, then fill values
   outside source control.
5. Run `scripts/provision.sh staging` when the target provider uses the
   generated provision helper.
6. Run `.github/workflows/readiness.yml` before production promotion/deploy.
7. Push to `main` or `staging`, or use workflow dispatch, to trigger deploy.

## Operator Ownership

Generated infra files belong to the operator after first emit.
The generator will not overwrite them on subsequent runs unless `--force-infra` is passed.
Review the diff carefully before accepting any regenerated infrastructure changes.

Hosted products may consume the generated manifest and map it into provider
records, secrets, readiness gates, and deployment status. Those hosted records
and provider adapters stay outside the generated app bundle.

## MozaiksPay API Key Integration

Self-hosted apps can connect to the hosted MozaiksPay service to accept payments
without deploying the full mozaiks-app hosted platform. Revenue flows through the
MozaiksPay transaction layer — meaning mozaiks-app earns a platform fee on every
transaction even for self-hosted apps.

**To enable MozaiksPay in a self-hosted app:**

1. Obtain a `MOZAIKSPAY_API_KEY` from your mozaiks-app account.
2. Add to your `.env.staging` / `.env.production`:
   ```
   MOZAIKSPAY_API_BASE=https://pay.mozaiks.app
   MOZAIKSPAY_API_KEY=mzk_live_...
   ```
3. Add `MOZAIKSPAY_API_KEY` to `app/security/secrets.yaml`:
   ```yaml
   secrets:
     - name: MOZAIKSPAY_API_KEY
       required: false
       description: MozaiksPay API key for hosted payment processing
   ```
4. The generated `services/integrations/mozaikspay_client.py` handles API-key-only
   auth — no `MOZAIKSPAY_CLIENT_ID` is required for self-hosted API key connections.

**What MozaiksPay provides to self-hosted apps:**

- Hosted subscription checkout sessions (Stripe Connect under the hood, opaque to your app)
- Billing portal for plan upgrades/downgrades
- Webhook-normalized subscription lifecycle events your `entitlement_dispatch` module reacts to
- Opaque `mzk_pay_xxx` payment IDs — your app never sees payment-provider IDs directly

**What stays on the hosted platform (not available via API key):**

- Wallet and payout flows for marketplace revenue distribution
- Investor marketplace integration
- Hosted entitlement authority for multi-tenant platform apps

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

- `app/security/secrets.yaml` — canonical secret name registry for this app
- `factory_app/build_context/AppGenerator/entitlement_dispatch_archetype.md` — write-path guide
- `factory_app/build_context/mozaikspay/context.yaml` — MozaiksPay capability pack
