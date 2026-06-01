# Shell System

This is the canonical frontend shell contract for generated apps and the
first-party Mozaiks Studio.

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

Shell config is not a theme file. `app/config/shell.json` must not define
colors, font families, radius scales, spacing scales, shadows, or page-local
visual palettes. Those values belong in `app/brand/theme_config.json` and flow
to the shell through semantic tokens.

## Canonical `shell.json` Shape

Generated apps author only this compact shell surface:

```json
{
  "header": {
    "logo": { "src": "logo.svg", "alt": "App logo", "href": "/" },
    "actions": [
      {
        "id": "create-app",
        "intent": "create_app",
        "label": "Create App",
        "path": "/create",
        "variant": "primary",
        "variants": [
          {
            "when": { "surface": "workflow_session" },
            "label": "Open Studio",
            "pathTemplate": "/apps/{appId}",
            "fallbackPath": "/apps",
            "variant": "secondary"
          }
        ]
      }
    ]
  },
  "shortcuts": {
    "profile": ["profile", "signout"],
    "mobile": ["dashboard", "notifications", "profile"],
    "footer": ["legal", "terms", "cookies"],
    "footerHideOnMobile": true
  },
  "navigation": {
    "policy": {
      "desktop": { "global": "header", "local": "sidebar", "footer": "visible" },
      "mobile": { "global": "bottomBar", "local": "sheet", "footer": "hidden" },
      "maxMobileItems": 5,
      "autoFromPages": false
    }
  },
  "chrome": {
    "defaultMode": "standard",
    "modes": {}
  }
}
```

The shell contract is intentionally small. It describes app chrome, navigation
placement, and route chrome modes. It does not define reusable UI building
blocks; those belong to the shared `chat-ui` primitive catalog and are consumed
by page schemas or bounded custom React.

Generated shell actions should choose semantic variants such as `primary`,
`secondary`, or `ghost`. They should not carry raw class strings with color or
font decisions. Visual rendering of those variants is brand-driven.

## Shell Presets

`factory_app/workflows/AppGenerator/tools/shell_presets.yaml` is prompt-time
guidance for generated apps. It is not loaded by the runtime and is not copied
into app bundles.

AppGenerator may select an `AppBuildPlan.shell_preset_hint` such as
`product_app`, `workspace_studio`, `public_plus_app`, `conversation_app`, or
`flow_app`. AppSchemaAgent then compiles that hint into the normal runtime
artifacts:

- `ui/pages/*.yaml -> navigation`
- `ui/pages/*.yaml -> shell_mode`
- `config/shell.json` only when app-wide defaults need a real override

The preset id itself must not appear in generated app files. Presets keep shell
authoring consistent; they do not create extra header actions or replace the
page primitive system.

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
expands them into concrete `header`, `profile`, `footer`, and `mobile`
objects for `/api/shell-config`.

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
`notifications`, `settings`, `admin`, `support`, `signout`,
`signin`, `legal`, `terms`, `cookies`, and `privacy`.

`admin` targets a generated app's framework admin shell. Do NOT include
`admin_portal` in any shortcuts list — it is automatically injected by the
framework into the profile menu for all admin users. App config and generated
output must never declare it.

Use page `navigation` for page-owned routes. Use `navigation.items` only for
app-level entries that are not owned by a page schema. Do not define custom
shortcut catalogs inside `shortcuts`.

## Shell Actions

`header.actions` are global shell CTAs or utility controls. Keep them sparse:
one primary action is preferred, and two visible actions is the normal upper
bound.

Each action chooses exactly one default launch family:

- route navigation: `path`
- external navigation: `href`
- direct workflow launch: `trigger` with `type: workflow`

Prefer a durable route `path` when the action enters a workflow sequence or
transition screen. Example: `Create App` points to `/create`, and `/create` is
owned by `extension_registry.json`.

Use `trigger.type = workflow` only for a direct single-workflow launch that has
no transition or sequence entrypoint.

Actions can declare semantic variants for context-aware labels and targets.
Variants are resolved by the shared shell action resolver used by both desktop
header and mobile shell chrome.

Supported variant context:

- `surface`: `studio`, `app_studio`, `workflow_session`, `transition`, `public`, or `page`
- `shellMode`: `standard`, `workspace`, `conversation`, `focused`, `immersive`, or `public`
- `workflow_sequence`: a workflow sequence id such as `build`
- `transition`: a transition id
- `has_active_app`: `true` or `false`

Use variants for semantic shell behavior, not path patches. For example, the
factory `Create App` shell action becomes `Open Studio` while the user is in a
workflow session:

```json
{
  "id": "create-app",
  "intent": "create_app",
  "label": "Create App",
  "path": "/create",
  "variants": [
    {
      "when": { "surface": "workflow_session" },
      "label": "Open Studio",
      "pathTemplate": "/apps/{appId}",
      "fallbackPath": "/apps"
    }
  ]
}
```

`pathTemplate` supports `{appId}` for app-scoped Studio targets. When the
active app id is unavailable, `fallbackPath` is used.

## Generation Rules

- AppPlanAgent classifies page intent with a shell mode hint.
- AppSchemaAgent writes `AppPageSchema.shell_mode`.
- AgentGenerator writes focused chrome for transition entrypoints and
  transition UI unless the route intentionally needs normal product chrome.
- Do not put per-route chrome decisions into `shell_config.chrome`.
- Do not use chrome mode to solve navigation placement.
- Do not use page action keys like `action_type`, `workflow_id`, `event_type`,
  or `payload` in `config/shell.json`.
- Do not encode path-prefix or query-param override rules in shell actions.
  Use semantic `variants[].when` context instead.
- Do not create page proxies just to start workflows from shell chrome.
- The notification bell owns only a compact shell summary. Its full route is
  configured by the first-party shell when notification support exists.
- The profile dropdown is generated from profile shortcuts and host defaults.
  Do not hardcode admin routes inside the React header.
- If a first-party page repeats a shell CTA such as `Create App`, resolve that
  CTA by shell action id and reuse the action record instead of cloning paths.

## AgentGenerator Boundary

AgentGenerator `entrypoints` are browser-addressable pack routes for workflows,
transitions, and workflow sequences. They are not header CTA buttons or
profile-menu controls.

AppGenerator may point a shell action at an AgentGenerator entrypoint route, but
it should not duplicate the entrypoint as a separate page.


