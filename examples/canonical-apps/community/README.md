# Common Ground

Common Ground is an interactive community reference for the web-journey
milestone in ADR 0013. Members can publish posts, open conversations, comment,
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

From the repository environment:

```powershell
mozaiks serve ./examples/canonical-apps/community --host platform
```

Follow the CLI's local URLs; the callback URI registered with your provider
must match the browser origin. No model API key is needed for community actions.

## Reproduce local acceptance

The dedicated launcher uses **already installed** Docker images `mongo:7` and
`quay.io/keycloak/keycloak:26.0`, the repository's Python environment and its
frontend dependencies. It creates separate disposable containers on loopback
ports; it does not adopt or reset your normal development services. The realm
has two synthetic fixture accounts and is unsuitable for deployment.

From the checkout root, keep this command running in a terminal:

```powershell
python examples/canonical-apps/community/scripts/live_environment.py start --evidence-dir .local/evidence/community-env/my-run
```

In another terminal, from `web_shell/`, build and serve the actual app:

```powershell
$front = Get-Content ../.local/evidence/community-env/my-run/frontend-env.json -Raw | ConvertFrom-Json -AsHashtable
foreach ($entry in $front.GetEnumerator()) {
  [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
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
actual server response to check that it cannot revive a deleted post. The phone
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
pytest -q --no-cov tests/test_community_reference.py tests/test_build_context_social_pack.py
```

Live acceptance is an explicit local gate, not part of the ordinary unit suite.
