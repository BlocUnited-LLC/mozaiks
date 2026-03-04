# Instruction Prompt: Customize Colors and Theme

**Task:** Help the user change their app's visual appearance (colors, fonts, shadows)

**Complexity:** Low (JSON editing only)

**Time:** 5-10 minutes

---

## Context for AI Agent

You are helping a user customize the visual appearance of their MozaiksAI app. All branding is controlled by a single JSON file - no code changes needed.

### Key File

```
app/brand/public/brand.json    # Controls all visual styling
```

This file is served at `/brand.json` and the app reads it at startup.

### What Can Be Customized

- **Colors:** Primary, secondary, accent, success, warning, error, background, text, borders
- **Fonts:** Body text, headings, logo text (Google Fonts or local files)
- **Shadows:** Elevation effects
- **Assets:** Logo, favicon, background images

---

## Step 1: Understand the User's Goals

Ask the user: **"What colors and style do you want for your app?"**

Get specifics:
- **Primary color:** (e.g., "blue", "#3B82F6", "like Slack's purple")
- **Secondary color:** (optional)
- **Overall vibe:** (e.g., "dark mode", "light and minimal", "bold and colorful")
- **Font preference:** (e.g., "modern sans-serif", "specific font name")

---

## Step 2: Read Current Configuration

Read the current brand.json to understand what's there:

```bash
cat app/brand/public/brand.json
```

Note the current structure - we'll modify specific sections.

---

## Step 3: Update Colors

### Color Structure

Each semantic color has three variants:

```json
{
  "main": "#3B82F6",   // Primary shade
  "light": "#60A5FA",  // Lighter variant (hover states, backgrounds)
  "dark": "#2563EB",   // Darker variant (pressed states, contrast)
  "name": "blue"       // Human-readable name (for debugging)
}
```

### Color Tokens

| Token | Purpose |
|-------|---------|
| `primary` | Main brand color (buttons, links, accents) |
| `secondary` | Supporting color (secondary buttons, highlights) |
| `accent` | Attention-grabbing color (badges, notifications) |
| `success` | Positive actions/states (green by default) |
| `warning` | Caution states (amber/yellow by default) |
| `error` | Error states (red by default) |
| `background` | Page backgrounds (`base`, `surface`, `elevated`, `overlay`) |
| `border` | Border colors (`subtle`, `strong`, `accent`) |
| `text` | Text colors (`primary`, `secondary`, `muted`, `onAccent`) |

### Example: Change to Blue Theme

```json
"colors": {
  "primary": {
    "main": "#3B82F6",
    "light": "#60A5FA",
    "dark": "#2563EB",
    "name": "blue"
  },
  "secondary": {
    "main": "#8B5CF6",
    "light": "#A78BFA",
    "dark": "#7C3AED",
    "name": "violet"
  }
}
```

### Example: Dark Mode Background

```json
"background": {
  "base": "#0F172A",      // Darkest (page background)
  "surface": "#1E293B",   // Cards, panels
  "elevated": "#334155",  // Modals, dropdowns
  "overlay": "rgba(15, 23, 42, 0.8)"  // Modal backdrops
}
```

### Example: Light Mode Background

```json
"background": {
  "base": "#FFFFFF",
  "surface": "#F8FAFC",
  "elevated": "#FFFFFF",
  "overlay": "rgba(0, 0, 0, 0.5)"
}
```

---

## Step 4: Update Fonts

### Font Structure

```json
"fonts": {
  "body": {
    "family": "Inter",           // CSS font-family name
    "fallbacks": "system-ui, sans-serif",  // Fallback fonts
    "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap",
    "tailwindClass": "font-sans"
  },
  "heading": {
    "family": "Inter",
    "fallbacks": "system-ui, sans-serif",
    "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@600;700&display=swap",
    "tailwindClass": "font-heading"
  }
}
```

### Popular Font Combinations

**Modern & Clean:**
```json
"body": { "family": "Inter", "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" },
"heading": { "family": "Inter", "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@600;700&display=swap" }
```

**Technical & Bold:**
```json
"body": { "family": "JetBrains Mono", "googleFont": "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap" },
"heading": { "family": "Space Grotesk", "googleFont": "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&display=swap" }
```

**Friendly & Rounded:**
```json
"body": { "family": "Nunito", "googleFont": "https://fonts.googleapis.com/css2?family=Nunito:wght@400;600&display=swap" },
"heading": { "family": "Poppins", "googleFont": "https://fonts.googleapis.com/css2?family=Poppins:wght@600;700&display=swap" }
```

### Using Local Fonts

If the user has a custom font file:

1. Place the font file in `app/brand/public/fonts/`
2. Configure like this:

```json
"heading": {
  "family": "MyCustomFont",
  "localFont": true,
  "src": "/fonts/MyCustomFont.otf",
  "tailwindClass": "font-heading"
}
```

---

## Step 5: Update App Name and Tagline

At the top of brand.json:

```json
{
  "name": "Your App Name",
  "tagline": "Your catchy tagline here"
}
```

---

## Step 6: Apply and Verify

### Save the file

After editing `brand.json`, save it.

### Refresh the browser

The app reads `brand.json` at startup, so:
- If using `npm run dev`: Just refresh the browser (Vite hot-reloads)
- If using production build: Restart the frontend

### Verify in browser

1. Open http://localhost:5173
2. Check that colors match what you set
3. Check that fonts are loading (no fallback system fonts)
4. Open DevTools (F12) → Console to check for any errors

### Check CSS Variables

In browser DevTools:
1. Right-click any element → Inspect
2. Look at `:root` styles
3. You should see `--color-primary`, `--color-secondary`, etc.

---

## Common Color Palettes

### Slack-inspired (Purple/Teal)
```json
"primary": { "main": "#611f69", "light": "#8b2d97", "dark": "#4a154b" },
"secondary": { "main": "#36c5f0", "light": "#5fd3f3", "dark": "#1a9fc1" }
```

### GitHub-inspired (Blue/Green)
```json
"primary": { "main": "#0969da", "light": "#218bff", "dark": "#0550ae" },
"secondary": { "main": "#1f883d", "light": "#2da44e", "dark": "#196c2e" }
```

### Linear-inspired (Purple/Blue)
```json
"primary": { "main": "#5e6ad2", "light": "#8086f0", "dark": "#4850b5" },
"secondary": { "main": "#26b5ce", "light": "#4cc9dd", "dark": "#1a8fa3" }
```

### Notion-inspired (Neutral/Minimal)
```json
"primary": { "main": "#37352f", "light": "#55534e", "dark": "#1b1b18" },
"secondary": { "main": "#2eaadc", "light": "#4dbce0", "dark": "#2392be" }
```

---

## Troubleshooting

### Colors not updating

1. Hard refresh: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)
2. Check browser cache: DevTools → Network → Disable cache
3. Verify JSON is valid: Paste into https://jsonlint.com

### Font not loading

1. Check the Google Fonts URL is correct
2. Verify the font name matches exactly (case-sensitive)
3. Check browser DevTools → Network tab for failed requests

### JSON syntax error

Common issues:
- Missing comma after a property
- Trailing comma on last property
- Missing quotes around strings

Use a JSON validator to find errors.

---

## Full Example: Modern Dark Theme

```json
{
  "name": "MyApp",
  "tagline": "AI-Powered Everything",

  "colors": {
    "primary": { "main": "#3B82F6", "light": "#60A5FA", "dark": "#2563EB", "name": "blue" },
    "secondary": { "main": "#8B5CF6", "light": "#A78BFA", "dark": "#7C3AED", "name": "violet" },
    "accent": { "main": "#F59E0B", "light": "#FBBF24", "dark": "#D97706", "name": "amber" },
    "success": { "main": "#10B981", "light": "#34D399", "dark": "#059669", "name": "emerald" },
    "warning": { "main": "#F59E0B", "light": "#FBBF24", "dark": "#D97706", "name": "amber" },
    "error": { "main": "#EF4444", "light": "#F87171", "dark": "#DC2626", "name": "red" },
    "background": { "base": "#0F172A", "surface": "#1E293B", "elevated": "#334155", "overlay": "rgba(15,23,42,0.8)" },
    "border": { "subtle": "#334155", "strong": "#475569", "accent": "#3B82F6" },
    "text": { "primary": "#F1F5F9", "secondary": "#94A3B8", "muted": "#64748B", "onAccent": "#FFFFFF" }
  },

  "fonts": {
    "body": {
      "family": "Inter",
      "fallbacks": "system-ui, sans-serif",
      "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap",
      "tailwindClass": "font-sans"
    },
    "heading": {
      "family": "Inter",
      "fallbacks": "system-ui, sans-serif",
      "googleFont": "https://fonts.googleapis.com/css2?family=Inter:wght@600;700&display=swap",
      "tailwindClass": "font-heading"
    }
  },

  "shadows": {
    "primary": "0 4px 14px rgba(59, 130, 246, 0.25)",
    "secondary": "0 4px 14px rgba(139, 92, 246, 0.25)",
    "elevated": "0 8px 30px rgba(0, 0, 0, 0.4)",
    "focus": "0 0 0 3px rgba(59, 130, 246, 0.5)"
  }
}
```
