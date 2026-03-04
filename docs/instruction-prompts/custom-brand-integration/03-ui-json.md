# Instruction Prompt: Configuring ui.json

**Task:** Configure UI structure in ui.json

**Complexity:** Low (JSON configuration)

---

## Context for AI Agent

You are helping a user configure `brand/public/ui.json` which controls UI structure: header, footer, profile menu, notifications, and chat modes.

---

## Step 1: Gather UI Requirements

Ask the user:

1. **"What header actions do you need?"**
   - Navigation links
   - Action buttons
   - Custom icons

2. **"What should be in the profile menu?"**
   - Profile settings link
   - Account settings
   - Sign out
   - Custom items

3. **"What footer links do you need?"**
   - Privacy policy
   - Terms of service
   - Custom links

---

## Step 2: Basic ui.json Template

```json
{
  "chat": {
    "modes": {
      "ask": { "tint": "[#hex]", "label": "Ask" },
      "workflow": { "tint": "[#hex]", "label": "Workflow" }
    },
    "bubbleRadius": "16px"
  },

  "header": {
    "logo": {
      "src": "[logo.svg]",
      "wordmark": "[wordmark.png]",
      "alt": "[App Name]",
      "href": "[homepage URL]"
    },
    "actions": []
  },

  "profile": {
    "icon": "profile.svg",
    "show": true,
    "defaultLabel": "User",
    "sublabel": "",
    "menu": []
  },

  "notifications": {
    "icon": "notifications.svg",
    "show": true,
    "emptyText": "No notifications"
  },

  "footer": {
    "links": [],
    "copyright": "© [Year] [Company]"
  }
}
```

---

## Step 3: Configure Header Actions

### Add Action Buttons

```json
"header": {
  "actions": [
    {
      "id": "discover",
      "label": "Discover",
      "icon": "sparkle.svg",
      "variant": "gradient",
      "visible": true
    },
    {
      "id": "docs",
      "label": "Docs",
      "icon": "book.svg",
      "variant": "default",
      "visible": true,
      "href": "https://docs.yourapp.com"
    }
  ]
}
```

### Action Variants

- `default` — Standard button
- `gradient` — Gradient background
- `outline` — Border only
- `ghost` — No background

### Action Types

- **Navigate:** Add `href` property
- **Modal:** Add `action: "modal"` and `modalId`
- **Custom:** Add `action: "custom"` and handle in code

---

## Step 4: Configure Profile Menu

### Basic Menu

```json
"profile": {
  "icon": "profile.svg",
  "show": true,
  "defaultLabel": "User",
  "sublabel": "Account",
  "menu": [
    {
      "id": "profile-settings",
      "label": "Profile Settings",
      "icon": "settings.svg",
      "action": "navigate",
      "href": "/profile"
    },
    {
      "id": "divider-1",
      "type": "divider"
    },
    {
      "id": "signout",
      "label": "Sign Out",
      "icon": "logout.svg",
      "action": "signout",
      "variant": "danger"
    }
  ]
}
```

### Menu Item Types

- **Navigate:** Links to a page
- **Signout:** Signs user out
- **Modal:** Opens a modal
- **Divider:** Visual separator

### Menu Item Variants

- `default` — Normal styling
- `danger` — Red/warning styling (for destructive actions)

---

## Step 5: Configure Footer

### Basic Footer

```json
"footer": {
  "links": [
    { "label": "Privacy Policy", "href": "/privacy" },
    { "label": "Terms of Service", "href": "/terms" },
    { "label": "Contact", "href": "/contact" }
  ],
  "copyright": "© 2026 YourCompany. All rights reserved."
}
```

### Footer with External Links

```json
"footer": {
  "links": [
    { "label": "Documentation", "href": "https://docs.yourapp.com", "external": true },
    { "label": "GitHub", "href": "https://github.com/yourorg", "external": true },
    { "label": "Support", "href": "mailto:support@yourapp.com" }
  ]
}
```

---

## Step 6: Configure Chat Modes

### Chat Mode Colors

```json
"chat": {
  "modes": {
    "ask": {
      "tint": "#3b82f6",
      "label": "Ask AI"
    },
    "workflow": {
      "tint": "#8b5cf6",
      "label": "Workflows"
    }
  },
  "bubbleRadius": "16px"
}
```

The `tint` color is used for:
- Mode selector tabs
- Message bubble accents
- Active state indicators

---

## Step 7: Configure Notifications

```json
"notifications": {
  "icon": "notifications.svg",
  "show": true,
  "emptyText": "All caught up!"
}
```

Set `show: false` to hide notifications icon.

---

## Step 8: Verify Configuration

```powershell
# Check JSON is valid
node -e "console.log(JSON.parse(require('fs').readFileSync('brand/public/ui.json')))"

# Start dev server
npm run dev

# Check header, footer, profile menu render correctly
```

---

## Summary Template

```markdown
## ui.json Configured

### Header
- Logo: [configured/default]
- Actions: [list of actions]

### Profile Menu
- Items: [list of menu items]
- Sign out: [configured]

### Footer
- Links: [list of links]
- Copyright: [text]

### Chat Modes
- Ask: [color]
- Workflow: [color]

### Notifications
- Enabled: [yes/no]

### Files Modified
- ✅ brand/public/ui.json
- ✅ brand/public/assets/ (icons added)
```

---

## Troubleshooting

### "Header action not showing"

1. Check `visible: true` is set
2. Verify icon file exists in assets
3. Check browser console for errors

### "Profile menu not opening"

1. Check `profile.show: true`
2. Verify menu array has items
3. Check for JSON syntax errors

### "Icons not loading"

1. Verify files exist in assets folder
2. Check filenames match exactly
3. Use SVG format for best results
