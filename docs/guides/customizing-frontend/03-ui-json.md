# Step 3 — ui.json

> **Guide:** Customizing Your Frontend · Step 3 of 5  
> **Live:** https://docs.mozaiks.ai/guides/custom-frontend/ui-json.html

File location: `brand/public/ui.json` — served at `/ui.json`

Configures the header, profile dropdown, notifications, footer links, and chat mode styling.
No colors or font definitions belong here — those live in `brand.json`.

---

## Full example

```json
{
  "chat": {
    "modes": {
      "ask":      { "tint": "#06b6d4", "label": "Ask" },
      "workflow": { "tint": "#8b5cf6", "label": "Workflow" }
    },
    "bubbleRadius": "18px"
  },
  "header": {
    "logo": {
      "src":      "mozaik_logo.svg",
      "wordmark": "mozaik.png",
      "alt":      "App logo",
      "href":     "https://yourapp.com"
    },
    "actions": [
      {
        "id":      "discover",
        "label":   "Discover",
        "icon":    "sparkle.svg",
        "variant": "gradient",
        "visible": true
      }
    ]
  },
  "profile": {
    "icon":         "profile.svg",
    "show":         true,
    "defaultLabel": "Commander",
    "sublabel":     "Mission Control",
    "menu": [
      { "id": "profile-settings", "label": "Profile Settings", "icon": "settings.svg", "action": "navigate", "href": "/profile" },
      { "id": "divider-1",        "type":  "divider" },
      { "id": "signout",          "label": "Sign Out",         "icon": "logout.svg",   "action": "signout",  "variant": "danger" }
    ]
  },
  "notifications": {
    "icon":      "notifications.svg",
    "show":      true,
    "emptyText": "No alerts"
  },
  "footer": {
    "links": [
      { "label": "Legal Notice",     "href": "/legal" },
      { "label": "Terms of Service", "href": "/terms" }
    ],
    "visible":   true,
    "poweredBy": null
  }
}
```

---

## Field reference

### chat

| Field | Type | Description |
|-------|------|-------------|
| `chat.modes.ask.tint` | string | Accent color hex for Ask mode indicator |
| `chat.modes.ask.label` | string | Human-readable mode label |
| `chat.modes.workflow.tint` | string | Accent color hex for Workflow mode indicator |
| `chat.modes.workflow.label` | string | Human-readable mode label |
| `chat.bubbleRadius` | string optional | CSS border-radius for chat bubbles. Default `"18px"` |

### header.logo

| Field | Type | Description |
|-------|------|-------------|
| `header.logo.src` | string | Logomark filename from `/assets/` |
| `header.logo.wordmark` | string optional | Logotype / wordmark filename |
| `header.logo.alt` | string | Alt text |
| `header.logo.href` | string optional | Logo link URL. Defaults to `"/"` |

### header.actions

Each action button in the header toolbar.

| Field | Type | Description |
|-------|------|-------------|
| `[*].id` | string required | Unique id — passed to `onAction(id, item)` |
| `[*].label` | string | Button label |
| `[*].icon` | string required | Filename string (must contain `.`). See [Step 4 — Assets](04-assets.md) |
| `[*].variant` | string optional | `"gradient"` for cyan–violet styling |
| `[*].visible` | boolean optional | Defaults to `true` |

### profile

| Field | Type | Description |
|-------|------|-------------|
| `profile.icon` | string | Fallback avatar icon filename |
| `profile.show` | boolean | `false` hides the profile button |
| `profile.defaultLabel` | string | Shown when no user is authenticated |
| `profile.sublabel` | string optional | Subtitle in dropdown header |

### profile.menu

Three item types:

**Navigate item:**
```json
{ "id": "…", "label": "…", "icon": "settings.svg", "action": "navigate", "href": "/path" }
```

**Custom / destructive action:**
```json
{ "id": "signout", "label": "Sign Out", "icon": "logout.svg", "action": "signout", "variant": "danger" }
```

**Divider:**
```json
{ "id": "divider-1", "type": "divider" }
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string required | Unique key |
| `type` | `"divider"` optional | Renders horizontal rule; all other fields ignored |
| `label` | string | Visible label text |
| `icon` | string optional | Icon filename (must contain `.`) |
| `action` | string | Forwarded to `onAction`. Built-in: `"navigate"`, `"signout"` |
| `href` | string optional | Route path for navigate actions |
| `variant` | `"danger"` optional | Red coloring for destructive actions |

### notifications

| Field | Type | Description |
|-------|------|-------------|
| `notifications.icon` | string | Icon filename |
| `notifications.show` | boolean | `false` hides the button |
| `notifications.emptyText` | string optional | Panel text when no notifications |

### footer

| Field | Type | Description |
|-------|------|-------------|
| `footer.links` | array | `{ "label": string, "href": string }[]` |
| `footer.visible` | boolean | `false` hides the footer |
| `footer.poweredBy` | string | null | Attribution text or `null` |

---

**Prev:** [Step 2 — brand.json](02-brand-json.md)  
**Next:** [Step 4 — Assets & Icons](04-assets.md)
