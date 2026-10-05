# Android packaging reference

This experimental fixture packages [Common Ground](../canonical-apps/community/README.md)
with Capacitor. It exercises the existing Mozaiks web shell and app bundle, using
the same React components and backend contracts as the browser reference.

The scope is a web build, a generated Android project, and verification that the
project contains the exact web assets. Native sign-in, complete mobile operation,
APK compilation, and store release have not been qualified. This fixture does
not enable a Factory mobile target or install a CapabilityPack.

The [mobile packaging workflow](../../.github/workflows/mobile-reference.yml)
repeats these checks with an invalid backend URL and retains the asset report.
It requires no backend, Android SDK, signing credentials, or app-store account.

## Prepare the project

Use Node 22.12 or newer. Install the existing frontend dependencies with `npm ci`
in `chat-ui/` and `web_shell/` in a disposable checkout. On Windows, follow the
repository's worktree dependency isolation rules; do not attach shared
`node_modules` junctions to a worktree.

From the repository root, in a fresh PowerShell terminal:

```powershell
$env:PLATFORM_PATH = (Resolve-Path examples/canonical-apps/community/app).Path
$env:MOZAIKS_APP_WORKSPACE_PATH = (Resolve-Path examples/canonical-apps/community).Path
$env:MOZAIKS_HOST = 'platform'
$env:VITE_MOZAIKS_HOST = 'platform'
# Deliberately unreachable: this step proves packaging, not backend connectivity.
$env:VITE_API_URL = 'https://runtime.example.invalid'
$env:VITE_CORE_URL = $env:VITE_API_URL
$env:VITE_WS_URL = 'wss://runtime.example.invalid'
npm --prefix web_shell run build
```

`VITE_CORE_URL` is required separately by the existing theme loader. The
canonical Vite configuration selects Common Ground, copies its brand assets,
and preserves app and workflow registration. This fixture does not rewrite
fetch calls or create a separate frontend renderer.

Then, from this directory:

```powershell
npm ci --ignore-scripts
npm test
npm run android:add
npm run android:sync
npm run verify:assets
```

Run `android:add` once in a new checkout. After rebuilding the web app, use
`android:sync` and `verify:assets` again. `android/` is disposable generated
output and is ignored by git. The dependency lock pins the Capacitor tooling;
no paid build service is needed for these commands.

`verify:assets` fails on missing or changed packaged assets, a mismatched web
title or copied Capacitor configuration, or remote-page loading via `server.url`.
It reports SHA-256 digests for the copied files and explicitly excludes native
acceptance and Android package identity. Capacitor sync copies configuration but
does not rename an existing Android project's identifier or display name; changes
to those require native project work and verification. Save the JSON output with
the source revision and build logs as local evidence. Do not use that report as
a release or promotion verdict.

## Native build prerequisite

Generating and synchronizing the project needs Node. Compiling an APK also
requires the compatible JDK and Android SDK described in the
[Capacitor environment documentation](https://capacitorjs.com/docs/getting-started/environment-setup).
Once those tools are configured, the generated project's ordinary build command
is:

```powershell
Set-Location android
.\gradlew.bat assembleDebug
```

The expected output is `android/app/build/outputs/apk/debug/app-debug.apk`.
A successful build would still need emulator and physical-device acceptance.
The sample uses an invalid backend URL until a reachable development backend
and the client integration gaps below are addressed.

## Integration work before this can become a supported pack

| Existing owner | Required follow-up |
| --- | --- |
| `chat-ui/src/auth/authAdapter.js` | Native browser authorization and validated callback handoff. The current adapter uses browser navigation, session storage, and an exact browser-origin callback. |
| `chat-ui/src/adapters/api.js` and its consumers | Consistent backend URL resolution. Some shell, notification, and schema-page calls still use relative `/api/*` URLs. |
| `mozaiksai/hosts/runtime.py` | Configure the actual client origin through existing CORS settings, then verify it with authentication enabled. |
| Community local acceptance environment | Device-reachable API/issuer addresses and native callback registration. The current browser fixture's loopback issuer and callback are insufficient. |
| Factory packs, targets, and artifact owners | A coordinated typed mobile delivery contract, materializer, validation, and provenance after the reference works. This directory is an example, not a generated-app output convention. |
| mozaiks-app managed delivery | Entitlement enforcement, native artifact results, signing preparation, and release tracking through the public OSS contract. |

Do not qualify native support by disabling authentication, adding a global fetch
rewrite, or loading the remote website through `server.url`. Keep this reference
on bundled assets and repair reusable behavior at its canonical owner.

The next acceptance step is a supported native sign-in and community action
journey. The same pack must later package mozaiks-app and a separate customer
reference, with their source revisions and target evidence recorded.
