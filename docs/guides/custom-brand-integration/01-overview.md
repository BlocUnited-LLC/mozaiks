# App Shell And Branding

Use this guide when you need to change the visible shell identity of a Mozaiks
app without changing framework-owned runtime code.

## What Branding Owns

Branding is split across a few app-owned files:

- `app/brand/theme_config.json` for visual tokens and theme inputs
- `app/brand/assets/` for logos and icons
- `app/brand/fonts/` for local fonts
- `app/config/shell.json` for shell content and navigation chrome
- `app/app.json` for startup route and auth intent, not raw theme tokens

For the local product workspace in this repo, the same contract lives under
`mozaiks-platform/app/`.

## What Branding Does Not Own

Do not put these concerns into branding files:

- workflow logic
- module business rules
- runtime host wiring
- admin shell ownership

## Typical Changes

Common branding tasks include:

- updating logos and icons
- adjusting theme tokens
- changing header actions or footer links
- changing the startup route
- configuring auth-facing shell chrome

## Read Next

- [Platform Authoring](../../architecture/foundations/platform-authoring.md)
- [App Manifest And Platform Targets](../../architecture/foundations/app-manifest-and-platform-targets.md)
- [Getting Started](../../getting-started.md)