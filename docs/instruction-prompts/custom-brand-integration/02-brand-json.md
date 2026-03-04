# Instruction Prompt: Configuring brand.json

**Task:** Configure visual identity in brand.json

**Complexity:** Low (JSON configuration)

---

## Context for AI Agent

You are helping a user configure `brand/public/brand.json` which controls visual identity: colors, fonts, logos, shadows, and gradients.

---

## Step 1: Gather Brand Information

Ask the user:

1. **"What is your app name and tagline?"**

2. **"What are your brand colors?"**
   - Primary color (main brand color)
   - Secondary color (accent)
   - Any specific hex codes?

3. **"What logo files do you have?"**
   - SVG logo (preferred)
   - PNG wordmark
   - Favicon

4. **"What fonts do you want to use?"**
   - Google Fonts
   - Local/custom fonts

---

## Step 2: Create brand.json

### Basic Template

```json
{
  "name": "[AppName]",
  "tagline": "[Your tagline]",

  "assets": {
    "logo": "[logo.svg]",
    "wordmark": "[wordmark.png]",
    "favicon": "[favicon.png]",
    "backgroundImage": "[optional_bg.png]"
  },

  "colors": {
    "primary": {
      "main": "[#hex]",
      "light": "[#hex]",
      "dark": "[#hex]",
      "name": "[color name]"
    },
    "secondary": {
      "main": "[#hex]",
      "light": "[#hex]",
      "dark": "[#hex]",
      "name": "[color name]"
    },
    "accent": {
      "main": "[#hex]",
      "light": "[#hex]",
      "dark": "[#hex]",
      "name": "[color name]"
    },
    "success": { "main": "#10b981", "light": "#34d399", "dark": "#059669", "name": "emerald" },
    "warning": { "main": "#f59e0b", "light": "#fbbf24", "dark": "#d97706", "name": "amber" },
    "error": { "main": "#ef4444", "light": "#f87171", "dark": "#dc2626", "name": "red" },
    "background": {
      "base": "[#hex]",
      "surface": "[#hex]",
      "elevated": "[#hex]",
      "overlay": "rgba(0,0,0,0.5)"
    },
    "border": {
      "subtle": "[#hex]",
      "strong": "[#hex]",
      "accent": "[#hex]"
    },
    "text": {
      "primary": "[#hex]",
      "secondary": "[#hex]",
      "muted": "[#hex]",
      "onAccent": "#ffffff"
    }
  },

  "fonts": {
    "body": {
      "family": "[FontName]",
      "fallbacks": "ui-sans-serif, system-ui, sans-serif",
      "googleFont": "[Google Fonts URL]",
      "tailwindClass": "font-sans"
    },
    "heading": {
      "family": "[FontName]",
      "fallbacks": "sans-serif",
      "googleFont": "[Google Fonts URL]",
      "tailwindClass": "font-heading"
    }
  }
}
```

---

## Step 3: Configure Colors

### Color Palette Structure

Each color group needs three shades:
- `main` — Primary usage
- `light` — Hover states, backgrounds
- `dark` — Active states, emphasis

### Example: Blue Theme

```json
"colors": {
  "primary": { "main": "#3b82f6", "light": "#60a5fa", "dark": "#2563eb", "name": "blue" },
  "secondary": { "main": "#8b5cf6", "light": "#a78bfa", "dark": "#6d28d9", "name": "violet" },
  "accent": { "main": "#f59e0b", "light": "#fbbf24", "dark": "#d97706", "name": "amber" }
}
```

### Example: Dark Background

```json
"background": {
  "base": "#0f172a",
  "surface": "#1e293b",
  "elevated": "#334155",
  "overlay": "rgba(15,23,42,0.8)"
},
"text": {
  "primary": "#f8fafc",
  "secondary": "#94a3b8",
  "muted": "#64748b"
}
```

### Example: Light Background

```json
"background": {
  "base": "#ffffff",
  "surface": "#f8fafc",
  "elevated": "#f1f5f9",
  "overlay": "rgba(0,0,0,0.5)"
},
"text": {
  "primary": "#0f172a",
  "secondary": "#475569",
  "muted": "#94a3b8"
}
```

---

## Step 4: Configure Fonts

### Google Fonts

```json
"fonts": {
  "body": {
    "family": "Inter",
    "fallbacks": "ui-sans-serif, system-ui, sans-serif",
    "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap",
    "tailwindClass": "font-sans"
  },
  "heading": {
    "family": "Poppins",
    "fallbacks": "Inter, sans-serif",
    "googleFont": "https://fonts.googleapis.com/css2?family=Poppins:wght@600;700&display=swap",
    "tailwindClass": "font-heading"
  }
}
```

### Local/Custom Fonts

```json
"fonts": {
  "logo": {
    "family": "CustomFont",
    "localFont": true,
    "src": "/fonts/CustomFont.otf",
    "tailwindClass": "font-logo"
  }
}
```

Place font files in `brand/public/fonts/`.

---

## Step 5: Configure Assets

### Required Assets

```json
"assets": {
  "logo": "logo.svg",           // Main logo (SVG recommended)
  "wordmark": "wordmark.png",   // Text-based logo
  "favicon": "favicon.png"      // Browser tab icon
}
```

### Optional Assets

```json
"assets": {
  "backgroundImage": "chat_bg.png",  // Chat background
  "heroImage": "hero.png"            // Landing page
}
```

Place all assets in `brand/public/assets/`.

---

## Step 6: Configure Shadows (Optional)

```json
"shadows": {
  "primary": "0 20px 45px rgba([primary-rgb],0.24)",
  "subtle": "0 4px 12px rgba(0,0,0,0.08)",
  "glow": "0 0 32px rgba([primary-rgb],0.6)"
}
```

---

## Step 7: Verify Configuration

```powershell
# Check JSON is valid
node -e "console.log(JSON.parse(require('fs').readFileSync('brand/public/brand.json')))"

# Start dev server
npm run dev

# Check browser console for errors
```

---

## Summary Template

```markdown
## brand.json Configured

### Basic Info
- Name: [AppName]
- Tagline: [tagline]

### Colors
- Primary: [hex]
- Secondary: [hex]
- Theme: [dark/light]

### Fonts
- Body: [font name] (source: Google Fonts/local)
- Heading: [font name]

### Assets Added
- [ ] logo.svg
- [ ] wordmark.png
- [ ] favicon.png
- [ ] background image (optional)

### Files Modified
- ✅ brand/public/brand.json
- ✅ brand/public/assets/ (added files)
- ✅ brand/public/fonts/ (if custom fonts)
```

---

## Troubleshooting

### "Font not loading"

1. Check Google Font URL is correct
2. Verify font file path for local fonts
3. Check browser network tab for 404s

### "Colors not applying"

1. Verify JSON syntax is valid
2. Check color hex codes include #
3. Clear browser cache and reload

### "Logo not showing"

1. Verify file exists in assets folder
2. Check filename matches exactly (case-sensitive)
3. Try SVG format if PNG doesn't work
