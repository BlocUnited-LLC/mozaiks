# Common Ground

Common Ground is an interactive community reference for the web-journey
milestone proposed in PR #809's ADR 0013. Members can publish posts, open conversations, comment,
react and remove their own contributions. It uses the normal Mozaiks platform
host, data contracts, OIDC sign-in and shared React shell.

## Application boundaries

- Public posts, comments, reactions and author-controlled deletion.
- App-specific branding and responsive desktop/phone browser layouts.
- Module HTTP requests and durable MongoDB records.
- Explicit loading, empty, error and retry states.

All content in this reference is public within the authenticated community.
The module contract rejects `private` and `friends` visibility before dispatch.
Use a fresh database; do not point this reference at an existing social app's
private records. Its app-wide collections permit shared reading. Author checks
in the service control deletion; normal members receive no moderation grant.
The canonical posts service also enforces visibility when reading stored records:
public published posts are shared; private, friends, and unknown visibility values
remain author-only. Friends-based sharing is not implemented. Hidden or deleted
posts cannot be read, commented on, reacted to, or expose their comments and
reaction summaries through these module actions.

Post and comment list limits are clamped to 1–99 so the persistence query can
reserve its hundredth row for next-page detection. Posts default to 20 and
comments to 50. Continue with `next_cursor` as `before` for posts or `after`
for comments until it is null. Account export uses its separate full traversal.

The page keeps a failed submission's draft while it remains mounted. It does
not promise offline storage, offline posting, or preservation across a full
browser reload. Reaction toggles are not automatically retried after an
ambiguous response; the viewer must reload their actual reaction state first.

The reference does not establish PWA installation, native mobile delivery,
private/friends-only behavior, managed hosting, or concurrent-load readiness.
It is an authored acceptance workload, not evidence that a Factory generation,
refinement or promotion run produced it. The shared shell's assistant affordance
is inherited; this workspace declares no AI workflow.

## Reuse without a second backend

`app/modules/user_posts/` is derived from the canonical social pack. Its Python
and event files are identical to the pack renderer's output. The reference
narrows only the create/list visibility enums in `module.yaml` to `public`.
Friends, activity-feed and profile companions are not selected here: this is
a posts-module derivative, not an installation of the entire pack.
The module HTTP helper is also copied from the canonical generator template
and checked by the same refresh command.

The app data contract owns its collections and indexes. Its additive migration
is derived from that same contract. This developer command checks or refreshes
only the declared derived files, from the OSS checkout root:

```powershell
python examples/canonical-apps/community/scripts/refresh_social_reference.py --check
python examples/canonical-apps/community/scripts/refresh_social_reference.py
```

The running workspace does not import Factory. Do not edit a copied backend
to repair a framework defect; correct the canonical pack and regenerate it.

## Run with your local environment

Configure the names in `.env.example` for your own MongoDB and OIDC provider.
The provider must grant `user_posts.read`, `user_posts.create` and
`user_posts.react`; requesting a scope does not grant it. Configure the matching
API audience, scope claim and access-token discriminator. The browser client
uses authorization code with PKCE and an exact callback URI.
The **signed access token must carry `app_id=common-ground`**. If your issuer
uses a different claim name, set `AUTH_APP_ID_CLAIM` to that name and require
the same exact value. The page's app manifest does not supply token identity.

From the repository environment:

```powershell
mozaiks serve ./examples/canonical-apps/community --host platform
```

This starts the API, not the browser app. It also prepares local workspace
guidance and dotenv files; use a disposable copy of the example for your own
app rather than treating those files as changes to the canonical example.

In a second terminal under `web_shell/`, export the configured `VITE_OIDC_*`
values from the workspace's `.env` into that terminal. Vite does not read the
workspace's `.env` automatically. Then select and build this app explicitly:

```powershell
$env:PLATFORM_PATH = (Resolve-Path ../examples/canonical-apps/community/app).Path
$env:MOZAIKS_APP_WORKSPACE_PATH = (Resolve-Path ../examples/canonical-apps/community).Path
$env:MOZAIKS_HOST = 'platform'
$env:VITE_API_URL = 'http://127.0.0.1:8000' # Use the API URL printed by serve.
$env:MOZAIKS_BACKEND_URL = $env:VITE_API_URL
npm run build
npm run preview -- --host 127.0.0.1 --port 14443 --strictPort
```

Open `http://127.0.0.1:14443/community`. Set `VITE_OIDC_REDIRECT_URI` and your
provider's registered callback to `http://127.0.0.1:14443/auth/callback` before
building. Set the backend's `FRONTEND_URL`/`REACT_DEV_ORIGIN` to that browser
origin too. Adjust both app paths if using a copied workspace. No model API key
is needed for community actions.

## Reproduce local acceptance

The dedicated launcher uses **already installed** Docker images
`quay.io/mongodb/mongodb-community-server:7.0-ubi8` and
`quay.io/keycloak/keycloak:26.0`, the repository's Python environment and its
frontend dependencies. It creates separate disposable containers on loopback
ports; it does not adopt or reset your normal development services. The realm
has two synthetic fixture accounts and is unsuitable for deployment.
Install Python dependencies with `python -m pip install -e ".[dev]"` and run
`npm ci` in both `chat-ui/` and `web_shell/`. Install Chromium from `web_shell/`
with `npx playwright install chromium`. The launcher records the installed AG2
version; acceptance must use the exact pin in `pyproject.toml` (currently 1.1.2).
It uses normal workspace workflow discovery, with no synthetic workflow root.

From the checkout root, keep this command running in a terminal:

```powershell
python examples/canonical-apps/community/scripts/live_environment.py start --evidence-dir .local/evidence/community-env/my-run
```

In another terminal, from `web_shell/`, build and serve the actual app:

```powershell
$front = Get-Content ../.local/evidence/community-env/my-run/frontend-env.json -Raw | ConvertFrom-Json
foreach ($entry in $front.PSObject.Properties) {
  [Environment]::SetEnvironmentVariable($entry.Name, $entry.Value, 'Process')
}
npm run build
npm run preview -- --host 127.0.0.1 --port 14443 --strictPort
```

From a third terminal in `web_shell/`, run the browser acceptance against those
services. `COMMUNITY_PYTHON` must point to the same Python interpreter used for
the launcher; it is used to restart only the owned API process:

```powershell
$env:COMMUNITY_LIVE_CONFIG = (Resolve-Path ../.local/evidence/community-env/my-run/environment.json).Path
$env:COMMUNITY_PYTHON = (Get-Command python).Source
npx playwright test -c playwright.community.config.js
```

This suite uses real browser sign-in and actual HTTP/database operations. It
does not fabricate app data or identity responses. One race test delays an
actual server response to check that it cannot revive a deleted post. Other
checks hold real responses or interrupt connectivity to inspect loading and
retry states. Authentication is local Keycloak authorization code with PKCE;
this does not qualify a real external identity provider. The phone
project is a phone-sized Chromium browser, not iOS/Android device acceptance.
Screenshots and results go under `web_shell/test-results/`; environment and
process evidence remain under `.local/evidence/community-env/`. Traces contain
local fixture sessions; do not publish them as public documentation.

Stop the preview with Ctrl+C. From the repository root, stop the owned backend
and containers, preserving their logs:

```powershell
python examples/canonical-apps/community/scripts/live_environment.py stop --evidence-dir .local/evidence/community-env/my-run
```

Deterministic contract/dispatch checks also run without Docker:

```powershell
pytest -q --no-cov tests/test_community_reference.py tests/test_social_post_privacy.py tests/test_build_context_social_pack.py tests/test_canonical_example_apps.py
```

`.github/workflows/community.yml` separately builds this app and runs all four
journeys on desktop and phone Chromium (eight tests) for PRs and main. It
installs the pinned Python dependencies, uses disposable MongoDB/Keycloak,
and retains results, screenshots and safe version/cleanup evidence. Raw realm
credentials, browser traces and runtime environment files are not uploaded.
This browser gate is separate from the ordinary unit suite; it proves the
authored reference, not Factory generation or external-provider readiness.
