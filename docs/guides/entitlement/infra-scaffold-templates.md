# Infra Scaffold Templates

`DownloadAgent` emits deployment packaging through
`generate_and_download` and its canonical deployment contract renderer.
`AuthScaffoldAgent` only materializes auth configuration, the shared OIDC facade,
and public login/callback routes from assembled `app.json` before validation.

## Staging Terms

Mozaiks uses two different staging concepts:

- **Artifact review staging** is the Mozaiks/Studio Refinement Engine area where
  generated or refined files wait for validation, review, `ArtifactVersion`
  acceptance, and promotion.
- **Environment staging** is the operator or hosted deployment target used to
  prove a promoted/exported app before production, such as a GitHub `staging`
  environment or preview runtime.

Deployment packaging describes environment staging. It does not bypass artifact
review or provision hosted resources.

## Artifact Ownership

| File | Purpose |
|------|---------|
| `Dockerfile`, `docker-compose.yml` | Download renderer, controlled by deployment request |
| `.github/workflows/deploy.yml`, `.github/workflows/readiness.yml` | Download renderer, controlled by deployment request |
| `deployment.manifest.json`, env examples | Download renderer, validated before export |
| `scripts/provision.sh` | Not emitted by the default Factory journey; provider execution belongs to the operator or hosted adapter |

Auth contract and adapter templates live in `webapp_builder/templates/`:

| File | Purpose |
|------|---------|
| `config/auth.yaml` | Provider-neutral `mozaiks.auth.v1` behavior/env-handle contract |
| `ui/auth/authAdapter.js` | Constant facade over the shared `@mozaiks/chat-ui/auth` browser adapter |

The backend supplies the validated auth contract and effective auth mode through
the shell bootstrap. Public OIDC settings use the declared `VITE_*` handles,
either at runtime or during the SPA build. Local development identity requires
explicit backend configuration; a frontend mock flag or missing provider does
not enable it. See [Factory security](../factory-security.md) for both modes.

## Usage After Generation

1. Request deployment artifacts during Factory export; do not manually combine partial scaffold templates.
2. Review generated `deployment.manifest.json`, `.env.example`,
   `.env.staging.example`, and `.env.production.example`; all
   env examples carry names only, never secret values.
3. Fill in deploy target–specific workflow sections such as registry login,
   deploy command, rollback, and any provider-specific secret sync.
4. Copy `.env.staging.example` to ignored `.env.staging` or
   `.env.production.example` to ignored `.env.production`, then fill values
   outside source control.
5. Run the generated readiness checks locally before configuring any external target.
6. Provider execution and paid infrastructure remain explicit operator decisions.

## Operator Ownership

Generated infra files belong to the operator after first emit.
Review artifact diffs before accepting regenerated infrastructure changes.
There is no `--force-infra` overwrite policy in the Factory export contract.

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
