# React Native Core Integration

This guide covers the recommended mobile path for Mozaiks.

Use this approach when you want:

- native screens and gestures
- React Native components instead of a WebView shell
- shared chat/runtime logic with a separate mobile renderer

This is the clean best-practice direction for the repo: one shared runtime core, one web renderer, and one native renderer.

## What Is Shared vs Native

Shared:

- workflow transport and WebSocket logic
- auth token plumbing and auth-provider selection
- chat/session state
- artifact/state reducers
- core adapters and services

Native-only:

- screen layout and navigation
- chat message rendering
- artifact renderers
- workflow UI tool components
- any browser-specific auth UX

The clean mental model is:

- `@mozaiks/chat-ui/core` gives you portable logic
- your React Native app provides the visual shell

## Import Surface

Use these package entrypoints:

- `@mozaiks/chat-ui/core`
- `@mozaiks/chat-ui/platform`

Do not import the root web package entrypoint for a native app unless you explicitly want browser-only modules.

## Required Platform Injection

Before mounting the shared provider, configure the platform bridge.

```js
import { configurePlatform } from '@mozaiks/chat-ui/platform';

configurePlatform({
  storage: {
    getItem: (key) => mmkv.getString(key) ?? null,
    setItem: (key, value) => mmkv.set(key, value),
    removeItem: (key) => mmkv.delete(key),
  },
  auth: {
    getAccessToken: () => tokenStore.currentToken ?? null,
  },
  getBaseUrls: () => ({
    httpUrl: 'https://api.example.com',
    wsUrl: 'wss://api.example.com',
  }),
  getConfigOverrides: () => ({
    apiUrl: 'https://api.example.com',
    wsUrl: 'wss://api.example.com',
  }),
});
```

### Storage Requirement

Storage must be synchronous.

Use a store such as MMKV. Do not use AsyncStorage for the current shared core because some initialization reads happen synchronously.

## Shared Provider Setup

Once the platform bridge is configured, mount your app around the shared provider.

```jsx
import React from 'react';
import { ChatUIProvider } from '@mozaiks/chat-ui/core';

export function AppShell({ apiAdapter, authAdapter, children }) {
  return (
    <ChatUIProvider apiAdapter={apiAdapter} authAdapter={authAdapter}>
      {children}
    </ChatUIProvider>
  );
}
```

Your native screens can then consume the shared hooks and context.

## Hooks You Can Reuse

The portable surface is intended to expose shared logic such as:

- `useConversation`
- `useArtifacts`
- `useChatWebSocket`
- `useCoreWebSocket`
- `useChatUI`

These help you keep one transport/state implementation while replacing the browser renderer.

## What You Still Need To Build

React Native still needs its own render layer.

That includes:

- message bubbles and conversation list UI
- artifact/detail panels
- workflow UI tool renderers
- navigation between screens or tabs
- keyboard handling and mobile layout behavior

The shared core removes browser assumptions from the logic layer. It does not magically make the existing web components render natively.

## UI Tool Contract

When workflows emit UI tools, your native app must map those component identifiers to native renderers.

Examples:

- `ActionPlan`
- `AgentAPIKeyInput`
- workflow-specific artifact cards

If a native renderer does not exist, degrade safely:

- show a fallback card
- show structured text
- or block with an explicit unsupported-component message

Do not silently ignore tool calls.

## Auth Guidance

Do not import browser-only auth adapters in a native host.

Examples of web-only adapters:

- Keycloak browser adapter
- mock browser auth adapter

Instead, provide a native auth adapter or inject token lookup through the platform bridge.

For this repo, mobile auth provider selection is declarative in `platform/app.json`:

```json
{
  "platforms": {
    "mobile": {
      "auth": {
        "provider": "token"
      }
    }
  }
}
```

Supported values:

- `token` — built-in token storage mode using the shared token adapter
- `external` — native host app injects an existing auth provider
- `oidc` — built-in native OIDC/PKCE flow driven from `platform/app.json`
- `keycloak-native` — built-in native Keycloak login using the same OIDC/PKCE path

For the built-in native path, configure the redirect and requested scopes directly in the shared manifest:

```json
{
  "platforms": {
    "mobile": {
      "auth": {
        "provider": "keycloak-native",
        "redirectScheme": "myapp",
        "redirectPath": "oauthredirect",
        "scopes": ["openid", "profile", "email"]
      }
    }
  }
}
```

Then run `npm --prefix clients/mobile run native:prepare` so the repo-owned native shell syncs the redirect scheme into iOS and Android.

The user should not normally edit `clients/` directly for auth selection. `clients/mobile/` is the repo-owned native shell. The user-facing switch is the manifest entry in `platform/app.json`.

## App Config Split

Keep these responsibilities separate:

- `platform/app.json` owns platform and auth settings
- `platform/config/ai.json` owns app-level chat and workflow boot defaults

Example `platform/config/ai.json`:

```json
{
  "chat": {
    "startup_mode": "ask"
  },
  "workflows": {
    "entry_point": "GreenRoom"
  }
}
```

Use that file when you want the app to boot into Ask mode first or default into
a specific workflow.

Do not put `entry_point` or `chat.startup_mode` into `platform/app.json`.

## Backend Contract

The backend contract does not change for this architecture.

Keep using:

- the same HTTP routes
- the same WebSocket contract
- the same chat/workflow lifecycle
- the same auth semantics

That is the point of the shared-core split: change the renderer, not the runtime protocol.

## Recommended Repo Strategy

If you want to build this now, the clean next step is a dedicated native client package, for example:

```text
clients/
  mobile/
```

That native client should:

- configure `@mozaiks/chat-ui/platform`
- mount the shared provider from `@mozaiks/chat-ui/core`
- implement native renderers for the app's required UI tools

## Summary

Use React Native shared-core integration when you want the clean long-term architecture:

- a real native UI
- one shared runtime/state/transport layer
- separate renderers instead of a wrapped browser UI
- `platform/app.json` for mobile platform/auth config
- `platform/config/ai.json` for app-level workflow boot defaults
