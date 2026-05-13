# Shell Navigation

`app/config/shell.json` should stay small. It defines shell chrome and app-wide
placement policy; page and module contracts should own the navigation items they
introduce.

Navigation controls where route links appear. Chrome modes control whether the
header, footer, mobile bottom bar, and local navigation are visible on a route.
Use [shell-chrome-modes.md](./shell-chrome-modes.md) for that route-level chrome
contract.

## Ownership

- Page-owned routes use `ui/pages/*.yaml -> navigation`.
- App-wide placement rules use `app/config/shell.json -> navigation.policy`.
- Built-in account/footer chrome can use `app/config/shell.json -> shortcuts`.
- Explicit `header`, `profile`, `footer`, or `mobile` objects are escape hatches
  for custom labels, icons, role gates, paths, or one-off chrome behavior.

## Page Route Contribution

```yaml
name: Messages
route: /messages
title: Messages
layout: full-width
navigation:
  scope: global
  icon: message-circle
  order: 30
sections:
  - id: inbox
    primitive: ResourceTable
    config:
      title: Conversations
```

Add `shell_mode` beside `layout` when the route needs a specific chrome posture:

```yaml
shell_mode: conversation
```

For example, DM thread pages should normally use `conversation` so the mobile
bottom bar does not compete with the message composer.

Navigation scopes:

- `global`: primary app destinations such as Dashboard, Messages, Wallet, Jobs.
- `local`: workspace/module subsections that should not crowd global shell nav.
- `profile`: account-owned destinations such as Profile or Preferences.
- `footer`: legal/support links.

Set `navigation: null` or omit it for routes reached only by direct links or
in-page actions.

## Shell Placement Policy

```json
{
  "navigation": {
    "policy": {
      "desktop": {
        "global": "header",
        "local": "sidebar",
        "footer": "visible"
      },
      "mobile": {
        "global": "bottomBar",
        "local": "sheet",
        "footer": "hidden"
      },
      "maxMobileItems": 5,
      "autoFromPages": false
    }
  }
}
```

Default Mozaiks policy:

- desktop global nav: `header`
- desktop local nav: `sidebar`
- mobile global nav: `bottomBar`
- mobile local nav: `sheet`
- mobile footer: `hidden`
- mobile bottom bar max: `5`

`autoFromPages` should usually remain `false`; generated pages should explicitly
declare `navigation` when they belong in shell navigation.

## Resolver Output

The platform host resolves page navigation, shell `navigation.items`, and
`shortcuts` into `/api/shell-config`:

- legacy frontend slots: `header`, `profile`, `footer`, `mobile.bottomBar`
- resolved model: `navigation.policy`, `navigation.items`,
  `navigation.resolved`

This lets existing shell components keep working while newer layouts can render
sidebars, rails, local sheets, or more menus from the resolved model.

## Generation Rules

- Do not duplicate one route in page `navigation`, `shortcuts`, and
  `navigation.items`.
- Use page `navigation` for page-owned routes.
- Use `shortcuts` for built-in profile, auth, notification, footer, and common
  route ids.
- Use `navigation.items` only for entries not owned by a page schema.
- Mobile bottom navigation must stay at five or fewer primary items; overflow
  belongs in a sheet or profile/more menu.
- Do not use navigation scope to hide chrome on chat, DM, focused, or immersive
  routes; use `shell_mode` instead.
