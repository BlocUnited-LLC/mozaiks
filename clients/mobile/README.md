# @mozaiks/mobile

React Native client for the Mozaiks AI platform.

Consumes the portable `@mozaiks/chat-ui/core` and `@mozaiks/chat-ui/platform` packages from the monorepo.

The canonical app manifest is `platform/app.json`. Mobile naming, backend URLs, and mobile enablement are read from that file.

In normal usage, end users should not need to edit files under `clients/`. That directory is the repo-owned native shell and generated project layer. The user-facing configuration surface is `platform/app.json`.

---

## Prerequisites

| Tool | Version |
|---|---|
| Node.js | ≥ 18 |
| React Native CLI | latest (`npm i -g @react-native-community/cli`) |
| Xcode (iOS) | ≥ 15 |
| Android Studio (Android) | Hedgehog+ |
| CocoaPods (iOS) | ≥ 1.14 |

---

## Setup

```bash
# 1 — Install JS dependencies
cd clients/mobile
npm install

# 2 — Prepare native projects
npm run native:prepare

# 3 — Set backend URL (copy and edit)
cp .env.example .env
#   → set MOZAIKS_API_URL and MOZAIKS_WS_URL
```

### Shared manifest

Edit `platform/app.json` instead of changing mobile files directly:

```json
{
  "appName": "My App",
  "appId": "my-app",
  "apiUrl": "http://localhost:8000",
  "wsUrl": "ws://localhost:8000",
  "platforms": {
    "web": { "enabled": true },
    "mobile": {
      "enabled": true,
      "displayName": "My App",
      "version": {
        "name": "1.0.0",
        "code": 1
      },
      "ios": {},
      "android": {},
      "auth": { "provider": "token" }
    },
    "desktop": { "enabled": false }
  }
}
```

Current behavior:

- `platforms.mobile.enabled=false` stops the mobile prepare step and runtime startup
- `platforms.mobile.displayName` is synced into `clients/mobile/app.json`
- `platforms.mobile.version` is synced into native iOS/Android version fields
- `platforms.mobile.ios.bundleId` overrides the derived iOS bundle identifier
- `platforms.mobile.android.applicationId` / `namespace` override the derived Android identifiers
- `apiUrl` / `wsUrl` are used by both web and mobile unless overridden via env vars
- `platforms.mobile.auth.provider` is the canonical place for mobile auth mode selection

Supported mobile auth providers:

- `token` — built-in token storage / backend session mode
- `external` — app host injects auth via `registerNativeAuthProvider()`
- `oidc` — native OIDC/Keycloak flow supplied through `registerNativeAuthProvider()`
- `keycloak-native` — alias for native Keycloak/OIDC bridge mode

What `npm run native:prepare` does:

- verifies `ios/` and `android/` exist in the repo
- on macOS: installs Ruby gems from `Gemfile` and runs `bundle exec pod install`
- on Windows/Linux: skips CocoaPods cleanly and leaves Android ready to run

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `MOZAIKS_API_URL` | `http://localhost:8000` | Backend HTTP base URL |
| `MOZAIKS_WS_URL` | derived from API URL | Backend WebSocket base URL |

Set these in a `.env` file and load them with [`react-native-config`](https://github.com/lugg/react-native-config) (add it as a dependency when you need env support beyond local dev).

---

## Running

```bash
# Start Metro
npm start

# iOS (separate terminal)
npm run ios

# Android (separate terminal)
npm run android
```

---

## Architecture

```
index.js
  └─ src/platform/setup.ts   ← configurePlatform() with MMKV storage + backend URLs
App.tsx
  └─ ChatUIProvider           ← shared state/context from @mozaiks/chat-ui/core
      └─ RootNavigator         ← shared UI from @mozaiks/chat-ui/ui

ios/                          ← generated Xcode project (repo-owned)
android/                      ← generated Gradle project (repo-owned)
```

### Shared core wiring

`configurePlatform()` (called in `src/platform/setup.ts`) injects:

- **storage** — `react-native-mmkv` synchronous KV (required — `AsyncStorage` is async and cannot be used here)
- **auth** — token reader from MMKV (set the token key after login via `src/auth/tokenStore.ts`)
- **getBaseUrls** — returns your backend HTTP and WS base URLs

After `configurePlatform()` runs, all shared hooks (`useChatUI`, `useCoreWebSocket`, etc.) route through the native implementations instead of browser globals.

### Auth

This shell now switches auth behavior from `platform/app.json`.

- `token` mode uses `src/auth/tokenStore.ts`
- `external` / `oidc` / `keycloak-native` require a native auth bridge registered through `src/auth/nativeAuthBridge.ts`

That means the user should choose the provider declaratively in `platform/app.json`, while the repo-owned mobile shell handles the adapter wiring.

### UI tools

Workflow UI tools (form widgets, approval UIs, etc.) rendered by the agent runtime will arrive as `uiToolEvent` on messages. Native equivalents need to be built in `src/components/uitools/` and registered with a `uiToolRenderer` prop on `<ChatUIProvider>`.

---

## What is NOT included yet

- Login / auth screen
- Shared UI tool renderers (forms, approvals, structured output)
- Push notification wiring
- Offline support
- App Store / Play Store signing configuration
