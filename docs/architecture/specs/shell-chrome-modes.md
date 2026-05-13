# Shell Chrome Modes

Shell chrome modes are the app-agnostic contract for deciding whether the shell
header, footer, mobile bottom bar, and local navigation are visible on a route.
They keep route intent on the route while keeping `app/config/shell.json` small.

## Ownership

- Route intent belongs on the page or entrypoint:
  - `ui/pages/*.yaml -> shell_mode`
  - `ui/route_manifest.json -> pages[].meta.shellMode`
  - `extension_registry.json -> entrypoints[].meta.shellMode`
- App-wide mode behavior belongs in `app/config/shell.json -> chrome`.
- Navigation membership still belongs in `navigation`; chrome mode does not make
  a route appear in menus.

## Modes

| Mode | Use For | Default Chrome Intent |
| --- | --- | --- |
| `standard` | Normal app pages | Desktop header/footer, mobile header/bottom bar |
| `workspace` | Dashboards, admin/profile/module workspaces, dense CRUD | Header, local navigation, no desktop footer, mobile bottom bar |
| `conversation` | Chat, DM, inbox thread, support conversation | Header only; no footer or bottom bar |
| `focused` | Onboarding, setup, review, approval, checkout, workflow transitions | Header only; no footer or bottom bar |
| `immersive` | Full-viewport map, canvas, media, game, kiosk | No shell chrome |
| `public` | Marketing, legal, public informational routes | Desktop header/footer, mobile header only |

## Page Example

```yaml
name: Messages
route: /messages
title: Messages
layout: full-width
shell_mode: workspace
navigation:
  scope: global
  icon: messages
  order: 30
sections:
  - id: inbox
    primitive: ResourceTable
    config:
      title: Conversations
```

A concrete DM thread route should usually use:

```yaml
shell_mode: conversation
```

That suppresses the footer and mobile bottom bar so the thread composer owns the
bottom of the viewport.

## Shell Policy Example

Use `chrome` only when the app needs to override the platform defaults:

```json
{
  "chrome": {
    "defaultMode": "standard",
    "modes": {
      "workspace": {
        "desktop": { "header": true, "footer": false, "bottomBar": false, "localNav": true },
        "mobile": { "header": true, "footer": false, "bottomBar": true, "localNav": "sheet" }
      },
      "conversation": {
        "desktop": { "header": true, "footer": false, "bottomBar": false, "localNav": false },
        "mobile": { "header": true, "footer": false, "bottomBar": false, "localNav": false }
      }
    }
  }
}
```

## Workflow Transitions

Workflow entry routes should declare `meta.shellMode: "focused"` when they mount
transition UI. Transition declarations can also set `ui.shell_mode: "focused"`.
The platform uses the route mode first and falls back to the transition UI mode
when composing `/api/shell-config`.

```json
{
  "entrypoints": [
    {
      "id": "create_app",
      "path": "/create",
      "label": "Create App",
      "transition": "app_type_selector",
      "meta": { "title": "Create App", "shellMode": "focused" }
    }
  ],
  "transitions": [
    {
      "id": "app_type_selector",
      "transition_type": "user_choice_context",
      "ui": { "component": "AppTypeSelector", "mode": "screen", "shell_mode": "focused" }
    }
  ]
}
```

## Generation Rules

- AppPlanAgent should classify page intent with `shell_mode_hint`.
- AppSchemaAgent writes `AppPageSchema.shell_mode`.
- AgentGenerator writes focused chrome for transition entrypoints and transition
  UI unless the route intentionally needs normal product chrome.
- Do not put per-route chrome decisions into `shell_config.chrome`.
- Do not use chrome mode to solve navigation placement; use
  `navigation.scope`, `navigation.placement`, and `navigation.policy`.
