# Shell System

This is the canonical frontend shell contract for generated apps and the
first-party Mozaiks Console. It replaces the older split shell specs.

## Ownership

Shell behavior is split by owner:

| Concern | Owner | File |
|---|---|---|
| App-wide chrome policy | AppGenerator / app author | `app/config/shell.json` |
| Page navigation membership | page author | `app/ui/pages/*.yaml -> navigation` |
| Custom route metadata | route author | `app/ui/route_manifest.json -> pages[].meta` |
| Workflow entry routes | workflow pack author | `extension_registry.json -> entrypoints[]` |
| Visual tokens | brand author | `app/brand/theme_config.json` |

Do not duplicate a route in page `navigation`, shell `shortcuts`, and
`navigation.items`. Each route should have one navigation owner.

## Chrome Modes

Routes declare their chrome intent. The shell renders header, footer, bottom
bar, and local navigation from that mode.

| Mode | Use for |
|---|---|
| `standard` | normal app pages |
| `workspace` | dashboards, admin/profile/module workspaces, dense CRUD |
| `conversation` | chat, inbox thread, support conversation |
| `focused` | onboarding, setup, review, approval, checkout, workflow transitions |
| `immersive` | full-viewport map, canvas, media, kiosk |
| `public` | marketing, legal, public informational routes |

Examples:

```yaml
shell_mode: workspace
navigation:
  scope: global
  icon: messages
  order: 30
```

```json
{
  "id": "create_app",
  "path": "/create",
  "label": "Create App",
  "transition": "app_type_selector",
  "meta": { "shellMode": "focused" }
}
```

## Navigation

Navigation controls where route links appear. Chrome modes control whether the
shell is visible.

Navigation scopes:

- `global`: primary destinations such as Apps, Usage, Billing, Messages, Jobs.
- `local`: workspace/module subsections that should not crowd global nav.
- `profile`: account-owned destinations such as Profile or Preferences.
- `footer`: legal/support links.

Generated pages should explicitly declare navigation when they belong in shell
navigation. Use `navigation: null` or omit it for routes reached only by direct
links or in-page actions.

Default placement policy:

- desktop global nav: `header`
- desktop local nav: `sidebar`
- mobile global nav: `bottomBar`
- mobile local nav: `sheet`
- mobile footer: `hidden`
- mobile bottom bar max: `5`

## Shortcuts

`shortcuts` are the compact authoring layer for common shell chrome. The backend
expands them into concrete `header`, `profile`, `notifications`, `footer`, and
`mobile` objects for `/api/shell-config`.

```json
{
  "shortcuts": {
    "header": ["dashboard"],
    "profile": ["profile", "settings", "signout"],
    "mobile": ["dashboard", "notifications", "profile"],
    "footer": ["legal", "terms", "cookies"],
    "footerHideOnMobile": true
  }
}
```

Common ids include `dashboard`, `create`, `profile`, `messages`,
`notifications`, `settings`, `admin`, `support`, `signout`, `signin`, `legal`,
`terms`, `cookies`, and `privacy`.

Use explicit shell arrays only when a shortcut needs custom labels, icons, role
gates, or paths.

## Shell Actions

`header.actions` are global shell CTAs or utility controls. Keep them sparse:
one primary action is preferred, and two visible actions is the normal upper
bound.

Each action chooses exactly one launch family:

- route navigation: `path` or `path_by_role`
- external navigation: `href` or `href_by_role`
- direct workflow launch: `trigger` with `type: workflow`

Prefer a durable route `path` when the action enters a workflow sequence or
transition screen. Example: `Create App` points to `/create`, and `/create` is
owned by `extension_registry.json`.

Use `trigger.type = workflow` only for a direct single-workflow launch that has
no transition or sequence entrypoint.

## Generation Rules

- AppPlanAgent classifies page intent with a shell mode hint.
- AppSchemaAgent writes `AppPageSchema.shell_mode`.
- AgentGenerator writes focused chrome for transition entrypoints and
  transition UI unless the route intentionally needs normal product chrome.
- Do not put per-route chrome decisions into `shell_config.chrome`.
- Do not use chrome mode to solve navigation placement.
- Do not use page action keys like `action_type`, `workflow_id`, `event_type`,
  or `payload` in `config/shell.json`.
- Do not create page proxies just to start workflows from shell chrome.
- The notification bell owns only a compact shell summary. Its full route
  belongs in `notifications.path`.
- The profile dropdown owns account/menu routes through `profile.menu`.
- If a first-party page repeats a shell CTA such as `Create App`, resolve that
  CTA by shell action id and reuse the action record instead of cloning paths.

## AgentGenerator Boundary

AgentGenerator `entrypoints` are browser-addressable pack routes for workflows,
transitions, and workflow sequences. They are not header CTA buttons or
profile-menu controls.

AppGenerator may point a shell action at an AgentGenerator entrypoint route, but
it should not duplicate the entrypoint as a separate page.
