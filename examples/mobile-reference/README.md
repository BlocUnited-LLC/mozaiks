# Android reference

This example packages [Common Ground](../canonical-apps/community/README.md)
through the reusable [Android delivery command](../../docs/architecture/targets/android-delivery.md)
and the OSS [`mobile` pack](../../factory_app/build_context/mobile/context.yaml).
The same command accepts an existing external workspace. Ordinary Factory app
generation does not select an Android target automatically.

## Package the reference

Install Mozaiks from an exact Git revision with its Factory resources, Node
22.12 or newer, Java 21, and Android SDK 36. Use a separate empty output
directory; on Windows, keep dependency installations outside Git worktrees.

```powershell
mozaiks package android examples/canonical-apps/community `
  --config examples/mobile-reference/android.json `
  --output C:/Temp/common-ground-android
```

The example configuration has an invalid backend origin, so this command creates
a packaging diagnostic. Set `backend_origin` to the intended HTTPS backend in
your own configuration for a connected app. The backend must project auth
configuration for the separately registered native public OIDC client and its
exact callback. Arbitrary browser auth adapters are rejected during preparation.

`--prepare-only` creates source/tooling archives and an exported workspace
without installing dependencies or compiling. Its `workspace/mobile/build.mjs`
entrypoint runs the same build later with the matching OSS installation.
Use a fresh output directory for each build.

The source workspace is unchanged. Native preparation preserves its browser
extension barrel in a disposable app copy and adds the native facade there.
The pack facade supplies external-browser navigation through Capacitor App and
Browser. The shared [`chat-ui` auth adapter](../../chat-ui/src/auth/authAdapter.js)
owns discovery, PKCE, state, nonce, token validation, and session storage.

`build-result.json` binds the request, source, pack, framework revision, bundled
asset hashes, generated Android package/version, and actual debug APK checksum.
Compilation reports `device_acceptance: not_run`; device evidence is separate.
Remote-page `server.url`, release signing, and store publication are unsupported.

## Authenticated emulator acceptance

The [mobile workflow](../../.github/workflows/mobile-reference.yml) has two jobs:

- `android-project` copies Project Hub to an external temporary workspace and
  packages it through the CLI under its own Android identity. Its backend is
  intentionally invalid; the retained receipt and APK prove packaging only.
- `native-acceptance` packages Common Ground using the same materializer and
  build helper, then exercises actual authentication and actions on Android.

The latter starts disposable MongoDB, Keycloak, and the platform host through
the ordinary community environment launcher with these paired options:

```bash
python examples/canonical-apps/community/scripts/live_environment.py start \
  --evidence-dir .local/evidence/community-env/native-local \
  --native-callback org.mozaiks.examples.commonground:/auth/callback \
  --client-origin https://localhost
```

The launcher keeps the browser registration and adds a separate Android public
client on the same backend. It registers the exact native redirect and logout callback and allows
`https://localhost` through CORS. `adb reverse` forwards API port 18443 and
identity port 28443. The build helper's explicit `--acceptance` option accepts
only a loopback HTTP backend and writes mixed-content/cleartext overrides to
the Android **debug source set**. The main configuration remains unchanged.
These APKs are disposable test artifacts.

[`android-acceptance.mjs`](scripts/android-acceptance.mjs) checks:

1. Bundled WebView origin and rejection of an anonymous create action.
2. External-browser sign-in, native callback return, and authenticated identity.
3. UI post creation, backend read, WebView reload, and author deletion.
4. External-browser logout, rejected anonymous writes, and a fresh credential prompt
   after a second Android browser launch.

The workflow retains sanitized proof, screenshots, packaging receipts, service
provenance, and the exact APK identified by the device proof's SHA-256 digest.
Read the job result and proof together. Tokens, authorization responses,
credentials, and browser traces are excluded from uploaded evidence.

## Callback and session limits

Android receives the reverse-domain scheme; the shared adapter checks the exact
callback URI and transaction. Callback delivery stores only a SHA-256 receipt
to prevent replay of a retained launch intent after WebView reload. A process
restart that loses the pending transaction requires fresh sign-in. Bootstrap
reads a retained native callback only when the shared adapter has a surviving
PKCE transaction; a post-logout launch intent cannot delay sign-in readiness.
Session
storage follows the existing browser contract. Persistent native refresh-token
storage needs separate design and qualification.

Physical devices, process restoration, accessibility, additional app pages and
workflows, release builds, and iOS require separate acceptance. Mozaiks App can
consume this OSS output contract for premium build/download orchestration;
entitlements, durable hosted jobs, signing custody, and store delivery remain
owned by that product.
