# React Native Web Migration Plan

## Goal

Make `chat-ui/` the single shared UI package for both web and native.
An agent (or developer) edits one place — the change ships on all platforms.

---

## Current state

```
app/                    web shell  →  imports from chat-ui/src/  (HTML + Tailwind)
chat-ui/src/            core logic + web-only components
clients/mobile/src/     duplicate screens/components  (RN primitives)
```

---

## Target state

```
app/                    web shell  →  imports from chat-ui/src/ui/  (RNW primitives)
chat-ui/src/
  core/                 portable logic (unchanged)
  platform/             platform bridge (unchanged)
  ui/                   ← NEW — cross-platform components (RN Web primitives)
    components/         MessageBubble, MessageInput
    screens/            ConversationListScreen, ChatScreen
    navigation/         RootNavigator
    index.js            public export surface
clients/mobile/         thin shell only — imports screens from @mozaiks/chat-ui/ui
```

---

## Changes

### 1. `chat-ui/package.json`
- Add `react-native-web` as a dependency
- Add `react-native` as an optional peer dependency (native hosts provide it; web uses RNW alias)
- Add `"./ui"` to the `exports` map → `./src/ui/index.js`

### 2. `chat-ui/src/ui/`  (new)
- `components/MessageBubble.tsx` — shared bubble using `View`, `Text`
- `components/MessageInput.tsx` — shared input using `TextInput`, `Pressable`
- `screens/ConversationListScreen.tsx` — shared list screen
- `screens/ChatScreen.tsx` — shared chat screen
- `navigation/index.tsx` — shared navigator using `@react-navigation/native`
- `index.js` — barrel export

### 3. `app/vite.config.js`
- Add alias: `react-native` → `react-native-web`
- Add alias: `@mozaiks/chat-ui/ui` → `chat-ui/src/ui/index.js`

### 4. `clients/mobile/`
- Delete `src/screens/` and `src/components/` (now live in `chat-ui/src/ui/`)
- `App.tsx` imports `RootNavigator` from `@mozaiks/chat-ui/ui`
- `babel.config.js` adds `@mozaiks/chat-ui/ui` alias
- `package.json` adds `react-native-web` (for type resolution only; Metro auto-excludes on native)

---

## Result

| Platform | Bundle | Source of UI components |
|---|---|---|
| Web browser | Vite | `chat-ui/src/ui/` via `react-native-web` |
| iOS / Android | Metro | `chat-ui/src/ui/` via `react-native` |

Agent edits `chat-ui/src/ui/` → ships to all platforms.

---

## What stays separate (intentional, cannot be unified)

| Concern | Web (`app/`) | Mobile (`clients/mobile/`) |
|---|---|---|
| Entry point | `index.html` + Vite | `index.js` + Metro |
| Auth | Keycloak / `window.mozaiksAuth` | Token in MMKV |
| Platform bridge setup | browser defaults | `src/platform/setup.ts` |
| Native gestures / APIs | n/a | platform-specific overrides |
