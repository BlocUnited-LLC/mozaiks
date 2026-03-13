# Prompt Pack: Auth In app.json

## Task

Help a user configure auth in `platform/app.json`.

## Read First

- `platform/app.json`
- `docs/guides/custom-brand-integration/06-auth-json.md`

## Rules

- Auth config lives in `platform/app.json`.
- Do not create a separate `auth.json`.
- Keep mobile-specific auth under `platforms.mobile.auth`.
- Keep app-level chat startup and workflow entry defaults in `platform/config/ai.json`.

## What To Ask

1. Are you using Keycloak?
2. Is this web only, mobile only, or both?
3. What realm, authority URL, and client ID should be used?
4. Is login-theme customization part of this request?

## Verification

1. `platform/app.json` parses
2. auth fields are present in the correct section
3. backend and frontend are pointing at the same auth settings
