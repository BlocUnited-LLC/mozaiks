---
name: add-branding
description: Customize app shell branding - themes, colors, navigation, logos. Works with app/brand/ and app/config/ declarative files.
argument-hint: "[what you want to change - theme, colors, logo, navigation]"
---

Help the user customize the Mozaiks app shell using declarative config.

## Key Files

Read these first:
- `app/app.json` — App identity, auth, admin emails
- `app/config/shell.json` — Shell chrome config
- `app/config/ai.json` — Startup mode, workflow entry point
- `app/brand/theme_config.json` — Theme tokens

For assets:
- `app/brand/assets/` — Logos, icons
- `app/brand/fonts/` — Custom fonts

## Questions to Ask

1. Changing visuals, shell behavior, or both?
2. Should app open on chat, a page, or adapter route?
3. Should chat start in `ask` mode or `workflow` mode?
4. Changing logos, icons, or fonts?
5. Any auth changes?

## What Each File Controls

| File | Controls |
|------|----------|
| `app/app.json` | App name, auth requirement, admin emails, startup landing spot |
| `app/brand/theme_config.json` | Colors, fonts, spacing, dark/light mode, shell chrome |
| `app/config/shell.json` | Compact shell shortcuts, navigation policy, chrome mode policy, plus header/footer/profile/notification/mobile overrides |
| `app/config/ai.json` | Entry workflow, startup mode |

## Rules

- Do NOT invent files like `brand.json`, `ui.json`, or `auth.json` — these don't exist
- Prefer declarative config changes over React code changes
- Keep workflow startup settings in `ai.json`
- Shell branding (logos, fonts, login theme) belongs in `app/brand/`
- `app/app.json` is for app identity and startup intent — not colors or shell chrome
- Prefer `app/config/shell.json -> shortcuts` for common shell items so the file stays small.
- Prefer route-level `ui/pages/*.yaml -> navigation` for page-owned navigation entries; use `app/config/shell.json -> navigation.policy` for app-wide placement rules.
- Prefer route-level `ui/pages/*.yaml -> shell_mode` for per-route header/footer/bottom-bar behavior; use `app/config/shell.json -> chrome` only for app-wide mode defaults.
- Use explicit `header`, `profile`, `notifications`, `footer`, or `mobile` objects only when labels, icons, roles, or paths need custom overrides.
- Common shortcut ids include `home`, `apps`, `workspace`, `wallet`, `create`, `profile`, `messages`, `notifications`, `settings`, `admin`, `support`, `signout`, `signin`, `legal`, `terms`, `cookies`, and `privacy`. Use `dashboard` only when the app declares a page or route with id `dashboard`.

## Shell Shortcuts

```json
{
  "shortcuts": {
    "header": ["home", "wallet"],
    "profile": ["profile", "wallet", "signout", "signin"],
    "mobile": ["home", "apps", "create", "profile"],
    "footer": ["legal", "terms", "cookies"],
    "footerHideOnMobile": true
  }
}
```

The backend expands this into the full shell config returned by `/api/shell-config`.

## Dynamic Navigation Policy

```json
{
  "navigation": {
    "policy": {
      "desktop": { "global": "header", "local": "sidebar", "footer": "visible" },
      "mobile": { "global": "bottomBar", "local": "sheet", "footer": "hidden" },
      "maxMobileItems": 5,
      "autoFromPages": false
    }
  }
}
```

Keep `autoFromPages` false unless you intentionally want every page route to
become global navigation.

## Dynamic Chrome Modes

Pages choose a `shell_mode`:

```yaml
shell_mode: workspace
```

Mode meanings:
- `standard`: normal app page.
- `workspace`: dense dashboard, admin/profile/module workspace, or local nav surface.
- `conversation`: chat, DM, inbox thread, or support conversation.
- `focused`: onboarding, setup, review, approval, checkout, or transition-like screen.
- `immersive`: full-viewport map, canvas, media, game, or kiosk.
- `public`: public, legal, marketing, or unauthenticated route.

Override mode behavior in `shell.json` only when the app-wide default is wrong:

```json
{
  "chrome": {
    "defaultMode": "standard",
    "modes": {
      "conversation": {
        "desktop": { "header": true, "footer": false, "bottomBar": false, "localNav": false },
        "mobile": { "header": true, "footer": false, "bottomBar": false, "localNav": false }
      }
    }
  }
}
```

## Verification

After changes:
1. Verify JSON parses correctly
2. Check `/api/theme-config` returns expected values
3. Check `/api/shell-config` returns expected values
4. Verify any referenced assets exist under `app/brand/assets/`

