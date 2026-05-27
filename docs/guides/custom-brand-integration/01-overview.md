# App Shell And Branding

Use this guide when you need to change the visible shell identity of a Mozaiks
app without changing core runtime code.

## What Branding Owns

Branding is split across a few app-owned files:

- `app/brand/theme_config.json` for visual tokens and theme inputs
- `app/brand/assets/` for logos and icons
- `app/brand/fonts/` for local fonts
- `app/config/shell.json` for shell content and navigation chrome
- `app/app.json` for startup route and auth intent, not raw theme tokens

## What Branding Does Not Control

Do not put these concerns into branding files:

- workflow logic
- module business rules
- runtime startup wiring
- admin ownership and behavior

## Typical Changes

Common branding tasks include:

- updating logos and icons
- adjusting theme tokens
- changing the startup route
- choosing shell chrome modes and app-wide placement policy

## Read Next

- [Platform Authoring](../../architecture/app/platform-authoring.md)
- [App Manifest And Platform Targets](../../architecture/app/app-manifest-and-platform-targets.md)
- [Getting Started](../../getting-started.md)
