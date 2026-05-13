# Shell Action Taxonomy

This note mirrors the generator contract for `config/shell.json`.

Agents do not read this Markdown directly. The executable guidance lives in:

- `factory_app/workflows/AppGenerator/agents.yaml`
- `factory_app/workflows/AppGenerator/tools/file_contracts.yaml`
- `factory_app/workflows/AppGenerator/tools/save_app_schema.py`
- `factory_app/workflows/AgentGenerator/agents.yaml`

Use this document as the human-readable summary of the same rules.

## App Shell Controls

- `shortcuts` are the preferred authoring layer for common shell chrome. They
  keep `shell.json` compact and are expanded by `/api/shell-config` into the
  concrete `header`, `profile`, `footer`, and `mobile` objects used by the
  frontend.
- `header.pages` are durable app-navigation links. They should use `path` and point at real routes.
- `header.actions` are global shell CTA or utility controls. Keep them sparse: one primary action is preferred, and two visible actions is the normal upper bound.
- `profile.menu` owns the header profile dropdown. Declare framework-owned
  routes like `/profile` here instead of hardcoding them in shared shell React.
- `notifications` owns the header bell behavior: visibility, notification-center route, and dropdown empty text.
- `mobile.bottomBar` owns compact mobile navigation placement. Use it for
  high-frequency destinations like `/messages`, `/notifications`, `/profile`, or
  the primary create/action route, and keep it to five visible items at most.
- `footer.hideOnMobile: true` keeps desktop legal/help links available without
  crowding mobile app chrome.
- Each action chooses exactly one launch family:
  - route navigation: `path` or `path_by_role`
  - external navigation: `href` or `href_by_role`
  - direct workflow launch: `trigger` with `type: workflow`
- `profile.menu` items use `action: navigate` plus `path` or `href` for navigation, `action: signin` / `action: signout` for auth controls, and `type: divider` only for separators.

## Shortcut Shape

```json
{
  "shortcuts": {
    "header": ["dashboard", "wallet"],
    "profile": ["profile", "wallet", "signout", "signin"],
    "mobile": ["dashboard", "wallet", "create", "profile"],
    "footer": ["legal", "terms", "cookies"],
    "footerHideOnMobile": true
  }
}
```

Built-in primitive ids include `dashboard`, `wallet`, `create`, `profile`,
`messages`, `notifications`, `settings`, `admin`, `support`, `signout`,
`signin`, `legal`, `terms`, `cookies`, and `privacy`. Route ids from
`ui/route_manifest.json`, declarative page names, and `shortcuts.items` are also
available. Use explicit shell arrays only when a shortcut needs custom labels,
icons, role gates, or paths.

## Decision Rules

- Do not use page action keys like `action_type`, `workflow_id`, `event_type`, or `payload` in `config/shell.json`.
- Prefer a durable route `path` when the action enters a workflow sequence or transition screen. Example: `Create App` points to `/create`, and `/create` is owned by `extension_registry.json`.
- Use `trigger.type = workflow` only for a direct single-workflow launch that has no transition or sequence entrypoint.
- Do not create page proxies just to start workflows from shell chrome.
- Shell actions do not own route visibility. Keep shell chrome static; if a route needs no shell, make that route opt out with route metadata instead.
- The notification bell is a compact shell summary only. Its full destination route belongs in `notifications.path`, not as a hardcoded string inside shared shell React.
- The profile dropdown is the same: route entries like `/profile` belong in `profile.menu`, while auth-state filtering remains shared shell behavior.
- If a first-party page repeats a shell CTA such as `Create App`, it should
  resolve that CTA by shell action id and reuse the action record. Do not clone
  shell CTA paths into page-local constants.

## AgentGenerator Boundary

- AgentGenerator `entrypoints` are browser-addressable pack routes for workflows, transitions, and workflow sequences.
- They are not header CTA buttons or profile-menu controls.
- AppGenerator may point a shell action at an AgentGenerator entrypoint route, but it should not duplicate the entrypoint as a separate page.
