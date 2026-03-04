# Instruction Prompt: Custom Brand Integration Overview

**Task:** Help user understand what branding customization options are available

**Complexity:** Low (planning and overview)

---

## Context for AI Agent

You are helping a user customize the branding of their MozaiksAI application. Branding is configured through JSON files in the `brand/public/` folder.

---

## Step 1: Understand Customization Goals

Ask the user:

1. **"What do you want to customize?"**
   - Colors and theme
   - Logo and assets
   - Fonts
   - Header/footer layout
   - Profile menu
   - All of the above

2. **"Do you have brand assets ready?"**
   - Logo files (SVG, PNG)
   - Custom fonts
   - Background images
   - Color palette

---

## Step 2: Explain the Structure

Mozaiks branding is split into four configuration files:

| File | Purpose |
|------|---------|
| `brand.json` | Visual identity: colors, fonts, logos, shadows |
| `ui.json` | UI structure: header, footer, profile menu, chat modes |
| `auth.json` | Authentication page styling |
| `assets/` | Images, fonts, icons |

### File Locations

```
brand/public/
├── brand.json        # Colors, fonts, logos
├── ui.json           # Header, footer, menus
├── auth.json         # Login page styling
├── assets/           # Images
│   ├── logo.svg
│   ├── wordmark.png
│   └── chat_bg.png
└── fonts/            # Custom fonts
    └── CustomFont.otf
```

---

## Step 3: Route to Specific Guide

Based on user's needs, direct them:

### "I want to change colors"
```
See docs/instruction-prompts/custom-brand-integration/colors-and-theme.md
This covers the colors section of brand.json
```

### "I want to add my logo"
```
See docs/instruction-prompts/custom-brand-integration/02-brand-json.md
This covers the assets section of brand.json
```

### "I want to change fonts"
```
See docs/instruction-prompts/custom-brand-integration/02-brand-json.md
This covers the fonts section of brand.json
```

### "I want to customize the header/menu"
```
See docs/instruction-prompts/custom-brand-integration/03-ui-json.md
This covers header, profile, and footer in ui.json
```

### "I want to change everything"
Walk through each file in order:
1. brand.json (visual identity)
2. ui.json (UI structure)
3. assets (images and fonts)
4. auth.json (login page)

---

## Step 4: Quick Start Example

For a quick brand change:

```json
// brand/public/brand.json
{
  "name": "YourAppName",
  "tagline": "Your tagline here",

  "assets": {
    "logo": "your-logo.svg",
    "favicon": "favicon.png"
  },

  "colors": {
    "primary": {
      "main": "#your-color",
      "light": "#lighter-shade",
      "dark": "#darker-shade"
    }
  }
}
```

---

## Summary Template

```markdown
## Branding Customization Plan

### User Goals
- [ ] Change colors
- [ ] Add logo
- [ ] Custom fonts
- [ ] Header/footer layout
- [ ] Login page styling

### Files to Edit
- [ ] brand.json — [what to change]
- [ ] ui.json — [what to change]
- [ ] assets/ — [files to add]
- [ ] auth.json — [what to change]

### Recommended Order
1. [First file to edit]
2. [Second file to edit]
3. [etc.]
```
