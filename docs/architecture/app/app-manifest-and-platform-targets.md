# App Manifest And Platform Targets

This document defines what `app/app.json` should be for Mozaiks.

## Core Rule

`app/app.json` should be a small author-facing manifest.

It should describe app intent.

It should not dump low-level platform configuration onto the user.

## Current Repo Note

The canonical target is a self-contained app workspace with `app/app.json`
inside the app repository. In this repo, `factory_app/app/app.json` is the
first-party Studio app bundle manifest served by the Studio host that follows that same contract.

## The Right Mental Model

Most users should only need to answer questions like:

- what is my app called
- should it run on web
- should it run on mobile
- where should the app land when opened
- should people sign in
- who should get admin access by default

That is enough to start.

## Recommended Author-Facing Shape

Target direction:

```json
{
  "appName": "My App",
  "targets": {
    "web": true,
    "mobile": false
  },
  "startup": {
    "landing_spot": "/dashboard"
  },
  "authRequired": true,
  "admins": [
    "owner@example.com"
  ]
}
```

`appId` should be derived from `appName` unless the user explicitly overrides it.

## What Should Stay In `app.json`

Keep only fields that most users actually understand:

- `appName`
- target enablement such as `web` and `mobile`
- app startup route such as `startup.landing_spot`
- simple auth requirement
- a short list of admin emails

## What Should Be Derived By Default

These should not be user-authored by default:

- `apiUrl`
- `wsUrl`
- auth provider internals
- Keycloak client ids
- Keycloak theme names
- mobile display name
- mobile version name and code
- iOS and Android per-platform config
- redirect schemes
- redirect paths
- auth scopes
- dev user fixtures
- mock auth toggles
- app id

These also do not belong in `app.json`:

- colors
- logo filenames
- header actions
- footer links
- login-theme assets

If the platform can infer it, generate it.

If the platform owns it, hide it.

## Mobile

If `mobile` is enabled, the default assumption should be:

- use the repo-owned mobile shell
- target both iOS and Android
- use platform defaults for package metadata and auth plumbing

Do not ask the user to care about iOS versus Android unless they are actively
doing release-specific work.

So for most users:

```json
{
  "targets": {
    "mobile": true
  }
}
```

should be enough.

## Auth

Users should not configure auth at the provider-plumbing level first.

They should answer one simple product question:

- does this app require sign-in

That is why `authRequired` is clearer than a jargon field like `signIn` or
`account`.

## Brand And Shell UI

Do not overload `app.json` with brand concerns.

Branding belongs in:

- `app/brand/theme_config.json` for visual tokens, typography, radius, density,
  shadows, and brand asset references
- `app/brand/assets/` for images and icons
- `app/brand/fonts/` for fonts
- `app/brand/login-theme/` for Keycloak login theme assets

Shell and navigation behavior belongs in `app/config/shell.json`, not in
`app.json` and not in theme config. `shell.json` owns header/profile/footer
behavior, navigation policy, shortcuts, and chrome mode defaults; it does not
own colors, fonts, radius, or visual palettes.

That is the real shell-brand contract.

## Development Convenience

Do not put local dev login shortcuts into `app/app.json`.

Local development convenience belongs in `.env`, for example:

- `VITE_DEV_AUTH_MODE=keycloak`
- `VITE_DEV_AUTOLOGIN=true`
- `VITE_MOCK_MODE=false`

That keeps the app manifest focused on product intent.

## Advanced Overrides

There will still be advanced cases.

Those should be:

- generated internally
- or exposed as advanced override files later

They should not be the default authoring experience.

## One Backend Rule

`app.json` should never make the user think they are configuring backend
internals.

Mozaiks should behave like one backend for the app author.

## Admin Bootstrap

Admin access is a framework-level concern.

The right author-facing question is not:

- how do I wire admin UI

It is:

- who should count as an admin for this app

That is why `admins` belongs in `app/app.json`, while framework admin panels
and APIs stay host-owned.

## Cross References

- [Foundations Overview](../foundations/overview.md)
- [canonical-app-structure.md](canonical-app-structure.md)
