# Android workspace delivery

`mozaiks package android` packages an existing canonical app workspace with the
shared web shell and a Capacitor Android wrapper. The implementation belongs to
the OSS Factory deployment renderer. A hosted product can consume that same
renderer behind its own authenticated actions, entitlements, and delivery records.

This implements the local Android packaging portion of
[ADR 0013](../../adr/0013-multisurface-applications-and-managed-delivery.md).
The supported output is a debug APK for development and acceptance. Store
submission, release signing, and iOS delivery are outside this contract.

## Usage

The input is a workspace containing `app/app.json` and optionally `workflows/`.
Create a delivery specification:

```json
{
  "schema_version": "mozaiks.android_delivery.v1",
  "package_id": "com.example.community",
  "display_name": "Community",
  "version_name": "1.0.0",
  "version_code": 1,
  "backend_origin": "https://api.example.com",
  "build_type": "debug"
}
```

```sh
mozaiks package android ./my-app --config ./android.json --output ./android-delivery
```

Compilation requires Python 3.11+, Node.js 22.12+, npm, Java 21, and an Android SDK
with platform 36. Set `JAVA_HOME` and `ANDROID_HOME` to the installed toolchains.
The command installs pinned npm dependencies and uses the generated Gradle
wrapper. Dependency downloads require network access.

Use `--prepare-only` to validate and export without running Node, npm, or Java:

```sh
mozaiks package android ./my-app --config ./android.json --output ./android-delivery --prepare-only
```

The output directory must be new or empty. The source workspace is read without
modification. Installed npm dependencies, temporary app views, and native sources
stay inside the exported delivery directory. npm and Gradle may also use their
normal user-level download caches.

`backend_origin` must be a canonical HTTPS origin using a public hostname or
globally routable IP address. Ordinary delivery rejects loopback, private,
link-local, reserved and multicast addresses, IPv4-mapped IPv6 aliases,
single-label names, and local/internal hostname suffixes. This validation is
deterministic and does not resolve DNS; the execution host still owns checks on
resolved destinations and reachability. Local test addressing is confined to
the reference acceptance tooling's explicit override, not a manifest option.

## Source admission

One export policy checks every captured `app/` and `workflows/` file before either
archive is constructed or the output directory is written. Verification of an
extracted delivery applies that policy again, independently of inventory hashes.
Known environment, credential, signing and developer files are rejected,
including developer JSON/YAML, credential stores and developer IDE directories.
Virtual environments and type-checker caches under app or workflow inputs are
rejected; other installed dependencies and local build caches remain outside
the captured input.

Recognizable credential literals in configuration and Python/JavaScript
assignments are rejected, including hardcoded environment lookup defaults.
The policy parses JSON, YAML and TOML values, carrying credential context
through nested objects and lists. Literal values in that context are refused
even when a nested scalar uses an unfamiliar payload label such as
`literalValue`; descriptive metadata fields and names-only references remain
portable. Direct credential lists may contain descriptive objects, but literal
scalar entries are refused.
It inspects env-style text and checks XML and
property-list fields. Credential elements and elements identified by a
credential `name` or `key` reject literal-bearing `content` and default/value
attributes on themselves or nested payload elements. Descriptive XML child
fields and names-only references remain portable. Credential query values in URLs
are checked in text and parsed configuration, including encoded query syntax.
Statically joined Python credential assignments are checked too.
A documented `postgresql://user:pass@host:port/dbname` placeholder is permitted.
Declare runtime secret names in the canonical `app/security/secrets.yaml`
contract and resolve them through the configured secret backend. Environment
handles, named vault references, runtime lookups and empty/example placeholders
are supported. Diagnostics identify the path and rule without echoing values.
These checks do not establish that arbitrary source or media contains no hidden
or obfuscated secrets; operators remain responsible for the source they export.

Because the web build publishes `app/brand/`, that directory accepts only the
canonical `theme_config.json` and public PNG, JPEG, GIF, WebP, ICO, SVG, WOFF,
WOFF2 and TTF assets. Other configuration, scripts, documents and media types
are rejected there. Binary assets must match their type signature; SVG files
must have an SVG root. Accepted bytes are preserved, and recognizable plaintext
credentials in assets are also rejected. The policy checks sensitive SVG
elements and attributes and bounded PNG `tEXt`, `zTXt` and `iTXt` metadata.
XML carried in PNG text metadata and encoded SVG metadata is checked too.
This is admission checking, not a media sanitizer; pixels, arbitrary binary
metadata and deliberately obscured values are outside its proof. Move developer
configuration out of the exported app/workflow inputs.

## Output and provenance

```text
android-delivery/
├── source.zip
├── android-workspace.zip
└── workspace/
    ├── app/
    ├── workflows/                 # when supplied
    └── mobile/
        ├── package.json
        ├── package-lock.json
        ├── capacitor.config.json
        ├── build.mjs
        ├── delivery.manifest.json
        ├── .local/               # local resources and build inputs
        ├── android/              # created by compilation
        └── build-result.json     # created by compilation
```

`source.zip` records the captured app and workflow files. `android-workspace.zip`
adds the mobile pack's native facade and renderer-owned tooling. These archives
use deterministic ZIP construction; timestamps, installed dependencies, Android
build directories, APKs, and signing material are excluded.

`delivery.manifest.json` records the specification, source digest, mobile pack
version and digest, framework commit and resource digest, and file inventories.
The framework must resolve to an exact commit with matching resource bytes.
An extracted portable workspace can restore those resources from the matching
installed Mozaiks revision when `node mobile/build.mjs` runs; set
`MOZAIKS_PYTHON` if that installation uses a specific Python executable.

A Git checkout reads its revision from Git, refuses changed tracked source, and
requires every staged package input to belong to that commit.
Wheel and source-distribution builds embed that exact commit as package data;
an installed wheel verifies the embedded file and shared-shell resources against
its distribution record before export. The record detects installed-file drift;
it does not authenticate an independently modified source distribution. The
release workflow's exact-main checkout and controlled artifact handoff remain
the source of release authority. The release build refuses to create a
source distribution without that revision. A wheel without it can run generic
hosts but cannot export Android. Package CI installs the built wheel and checks
that the exporter resolves the checked-out commit. This makes normal wheel
installs usable without requiring their `direct_url.json` to name a Git clone.

Repo-local Docker builds exclude `.git` from their build context. They can
install a generic host wheel without revision metadata; Android export in that
image remains unavailable. A release wheel or source distribution built from
a clean, exact Git checkout carries the revision needed for mobile delivery.

Shared-shell resources use LF text checkouts through the repository's
`.gitattributes`; binary resources retain their bytes. This keeps Windows Git
installs and Linux compiler installs identical for the recorded resource digest.
Install the matching revision again if an older checkout policy produced CRLF
resources. The verifier rejects mismatched bytes even when commit IDs match.

After compilation, `mobile/build-result.json` binds the output to those input
identities and records the APK's relative path, byte size, and SHA-256. The CLI
verifies that receipt against the actual file before returning success. A
failed build records failure. Compilation alone reports
`device_acceptance: "not_run"`; device acceptance is separate evidence.

## Canonical owners

| Concern | Owner |
| --- | --- |
| Specification, source validation, portable export | `factory_app/workflows/AppGenerator/tools/android_delivery.py` |
| Shared app/workflow export admission | `factory_app/workflows/AppGenerator/tools/android_export_policy.py` |
| Local command | `mozaiks_cli/commands/package.py` |
| Declared native facade and pinned package inputs | `factory_app/build_context/mobile/` |
| Build execution and APK receipt | Exported `mobile/build.mjs` |
| OAuth, PKCE, authenticated sessions | Shared chat UI auth implementation |
| Platform backend, actions, persistence, authorization | Existing app host and modules |
| Premium access, delivery jobs, authorized downloads | Hosted product, when implemented |

Mobile templates remain app-relative build-context templates. The layout
registry admits the five renderer-owned `mobile/` tooling files at workspace
scope only through the explicit `android_delivery` extension with
`pack_id: mobile`. The validated Android renderer selects this extension.
The default registry and ordinary semantic compilation plans retain their
existing identity. Generated native build outputs do not become app contracts.
This command does not change AppBuildPlan, DownloadRequest, or deployment target
taxonomies, and does not create a second runtime or persistence authority.

## Authentication and supported inputs

Browser source remains intact. The build creates a temporary app view with the
native browser adapter, which delegates OAuth and PKCE to the shared auth
implementation. Android opens the system browser and returns to the callback
derived from the package identifier and the app's canonical callback route.

Authenticated delivery requires a separate public OIDC client registered for
that exact native callback. Set these public values on the app backend:

```text
MOZAIKS_ANDROID_OIDC_CLIENT_ID=<registered Android public client id>
MOZAIKS_ANDROID_OIDC_REDIRECT_URI=com.example.community:/auth/callback
```

The canonical auth contract supplies these fixed handles under
`frontend.android`. `/api/shell-config` projects their configured values as
`auth.frontend.android`; a partial registration projects no Android profile.
The mobile facade selects only its client and callback, checks the callback
against the packaged Android identity, and delegates OAuth to the shared
adapter. Missing profiles, reused browser client ids, and mismatched callbacks
fail before sign-in. The browser continues using its existing `VITE_OIDC_*`
profile. Both clients use the same issuer, scopes, and backend API audience.
Registration must allow public-client PKCE, the exact login/logout callback,
and the Android WebView origin for token requests. Configure the backend's CORS
for that origin too. These values grant no identity or authorization; access
tokens still pass the normal backend validation and module permission checks.

Supported apps use the shared default auth adapter or its canonical generated
facade. Custom auth adapters and inherited Factory workflow registries are
rejected in this slice. App-local workflows can be included. Source capture
rejects path collisions, links, private environment/signing files, oversized
inputs, and invalid app contracts. Canonical names-only environment examples
are validated through the existing deployment secret rules.

Public delivery specifications require an HTTPS backend origin. The build
driver's explicit `--acceptance` mode permits a loopback HTTP backend in a
disposable debug build for local emulator tests. It is not a public delivery
specification option.

## Acceptance

The [mobile reference](https://github.com/BlocUnited-LLC/mozaiks/blob/main/examples/mobile-reference/README.md) consumes
this packaging path. Its CI checks Android browser authentication, app actions,
sign-out, and rejection cases against disposable local services. A separately
named external workspace exercises reuse outside the OSS checkout. Evidence
must identify the tested source revision and actual APK hash; a successful
compilation is insufficient evidence of an authenticated device journey.
