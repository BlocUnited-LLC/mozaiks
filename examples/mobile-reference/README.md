# Android reference

This experimental fixture packages [Common Ground](../canonical-apps/community/README.md)
with Capacitor. It uses the existing Mozaiks web shell, app bundle, backend
modules, and OIDC authentication. It does not enable a Factory mobile target or
install a CapabilityPack.

## Ownership

The native files supply browser navigation and callback delivery through the
existing app `createAuthAdapter` export. The shared
[`chat-ui` auth adapter](../../chat-ui/src/auth/authAdapter.js) owns discovery,
PKCE, state, nonce, token checks, and session storage. The shared API adapter
resolves relative requests against the configured backend and attaches the
session token only to that backend's HTTP(S) origin.

`native:prepare` copies Common Ground into an ignored temporary workspace and
adds the native auth export alongside its existing component registration.
The original community app stays usable by the browser acceptance workflow.
No global fetch rewrite, remote-page `server.url`, or separate renderer is used.

## Prepare a packaging diagnostic

Use Node 22.12 or newer. Run these commands in a fresh disposable checkout.
On Windows, follow the repository's dependency isolation rules: install outside
git worktrees and do not attach shared `node_modules` junctions.

From the repository root in PowerShell:

```powershell
npm ci --prefix chat-ui
npm ci --prefix web_shell
npm ci --ignore-scripts --prefix examples/mobile-reference
npm --prefix examples/mobile-reference test
npm --prefix examples/mobile-reference run native:prepare

$env:PLATFORM_PATH = (Resolve-Path examples/mobile-reference/.local/workspace/app).Path
$env:MOZAIKS_APP_WORKSPACE_PATH = (Resolve-Path examples/mobile-reference/.local/workspace).Path
$env:MOZAIKS_HOST = 'platform'
$env:VITE_MOZAIKS_HOST = 'platform'
$env:VITE_API_URL = 'https://runtime.example.invalid'
$env:VITE_CORE_URL = $env:VITE_API_URL
$env:VITE_WS_URL = 'wss://runtime.example.invalid'
$env:VITE_OIDC_REDIRECT_URI = 'org.mozaiks.examples.commonground:/auth/callback'
npm --prefix web_shell run build
npm --prefix examples/mobile-reference run android:add
npm --prefix examples/mobile-reference run android:sync
npm --prefix examples/mobile-reference run android:configure
npm --prefix examples/mobile-reference run verify:assets
```

The invalid backend makes this a packaging diagnostic. A working session
requires the authenticated environment below. `VITE_CORE_URL` is required by
the existing theme loader as well as `VITE_API_URL`.

Run `native:prepare` and `android:add` once per disposable checkout. After
rebuilding, repeat `android:sync`, `android:configure`, and `verify:assets`.
The source app, staged workspace, and generated Android project are distinct;
use a fresh checkout when changing the source app. Both `.local/` and `android/`
inside this example are ignored.

`verify:assets` compares every source web asset to the copied Android asset by
SHA-256. It also checks the title and copied Capacitor identity settings, and
rejects `server.url`. It does not attest to Android package identity, compilation,
device behavior, or release readiness. Capacitor sync does not rename an
existing Android project's identifier or display name.

## Authenticated emulator acceptance

The [mobile workflow](../../.github/workflows/mobile-reference.yml) has two jobs:

- `android-project` produces a diagnostic APK using the invalid backend above.
- `native-acceptance` starts disposable MongoDB and Keycloak containers plus the
  platform host with authentication enabled, builds a separate debug APK, and
  exercises it on an Android emulator. The workflow uses the runner's JDK 21,
  Android SDK, and local services; no paid build service or store account is needed.

The acceptance environment is the ordinary community launcher with these paired
options (Bash syntax):

```bash
python examples/canonical-apps/community/scripts/live_environment.py start \
  --evidence-dir .local/evidence/community-env/native-local \
  --native-callback org.mozaiks.examples.commonground:/auth/callback \
  --client-origin https://localhost
```

It registers the exact native redirect and logout callback with the disposable
identity provider and allows `https://localhost` through the existing CORS
configuration. `adb reverse` forwards API port 18443 and identity port 28443 so
the runtime, external browser, and WebView use the same loopback issuer.
The workflow builds with the launcher's `frontend_env`, stages the native app,
then runs `node scripts/prepare.mjs android --acceptance` after Capacitor sync.
This creates **debug source-set overrides** for loopback HTTP and WebView mixed
content. The tracked configuration and main Android assets keep their normal
settings. These loopback APKs are disposable test artifacts.

[`android-acceptance.mjs`](scripts/android-acceptance.mjs) checks:

1. Bundled WebView origin and rejection of an anonymous create action.
2. Actual external-browser sign-in, native callback return, and authenticated identity.
3. Post creation through the app UI, backend read, WebView reload, and author deletion.
4. External-browser logout, rejected anonymous writes, and a fresh credential prompt.

The job retains a sanitized `android-proof.json`, screenshots, asset checksums,
service provenance, and the exact APK identified by the proof's SHA-256 digest.
Read the job result and proof together. Raw tokens, authorization responses,
credentials, and browser traces are excluded from uploaded evidence.

## Native callback and session limits

The reference uses `org.mozaiks.examples.commonground:/auth/callback`. Android
receives the reverse-domain scheme; the shared adapter checks the exact URI,
transaction state, PKCE verifier, and nonce before accepting a session. Callback
delivery stores only a SHA-256 receipt to avoid replaying a retained launch
intent after a WebView reload. A process restart that loses the pending
transaction requires a fresh sign-in. Session storage follows the existing
browser contract; persistent native refresh-token storage is outside this fixture.

The adapter opens the system browser for sign-in and sign-out. Cancellation,
expiry, listener cleanup, duplicate delivery, and callback rejection have
targeted tests. Device acceptance covers the configured emulator journey;
physical-device lifecycle behavior still needs separate qualification.

## Work before a supported mobile product

| Owner | Follow-up |
| --- | --- |
| Shared shell consumers | Review remaining direct `fetch` calls and qualify additional pages, workflows, uploads, and notifications on devices. |
| Native reference | Physical devices, process restoration, accessibility, release builds, and iOS acceptance. |
| Factory pack and artifact owners | Typed mobile delivery contracts, materialization, validation, and provenance using the proven reference. |
| mozaiks-app | Entitlements and managed build, signing, and delivery records through that same OSS contract. |

The pack must eventually package mozaiks-app and a separate customer reference,
with source revisions and target evidence recorded. Store submission and release
signing remain separate product work.
