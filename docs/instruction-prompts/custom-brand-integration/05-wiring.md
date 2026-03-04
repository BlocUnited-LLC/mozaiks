# Instruction Prompt: Wiring Brand Configuration

**Task:** Connect brand configuration to the application

**Complexity:** Medium (understanding the build process)

---

## Context for AI Agent

You are helping a user understand how brand configuration files are loaded and applied in MozaiksAI. This covers the wiring between JSON configs and the React application.

---

## Step 1: Understand the Flow

```
brand/public/
├── brand.json ─────┐
├── ui.json ────────┼──> Loaded at runtime
├── auth.json ──────┘    via fetch()
└── assets/ ────────────> Served statically
         │
         ▼
    React App
    (chat-ui)
         │
         ▼
    ThemeProvider
    applies CSS vars
```

---

## Step 2: How Configuration Loads

### 1. Dev Server Startup

When you run `npm run dev`, Vite:
1. Serves `brand/public/` at the root URL
2. Makes `brand.json`, `ui.json`, etc. available at `/brand.json`, `/ui.json`

### 2. App Initialization

The React app fetches configuration on mount:

```javascript
// Simplified version of what happens
const brand = await fetch('/brand.json').then(r => r.json());
const ui = await fetch('/ui.json').then(r => r.json());
```

### 3. Theme Application

Brand colors become CSS variables:

```css
:root {
  --color-primary: #3b82f6;
  --color-primary-light: #60a5fa;
  --color-primary-dark: #2563eb;
  /* ... all brand.json colors */
}
```

### 4. Component Usage

Components use Tailwind classes that reference CSS variables:

```jsx
<button className="bg-primary text-on-accent">
  Click me
</button>
```

---

## Step 3: File Locations

| Config | Loaded From | Applied To |
|--------|-------------|------------|
| `brand.json` | `/brand.json` | CSS variables, theme context |
| `ui.json` | `/ui.json` | Header, footer, profile components |
| `auth.json` | `/auth.json` | Login/registration pages |
| `assets/*` | `/assets/*` | Image tags, backgrounds |

---

## Step 4: Verify Configuration Loading

### Check JSON is Served

```powershell
# Start dev server
npm run dev

# In another terminal, check endpoints
curl http://localhost:5173/brand.json
curl http://localhost:5173/ui.json
```

### Check Browser DevTools

1. Open browser DevTools (F12)
2. Go to Network tab
3. Refresh page
4. Look for `brand.json` and `ui.json` requests
5. Verify 200 status and valid JSON

### Check CSS Variables Applied

1. Open browser DevTools
2. Go to Elements tab
3. Select `<html>` element
4. Check computed styles for `--color-primary` etc.

---

## Step 5: Hot Reload

Configuration changes require page refresh:

```powershell
# After editing brand.json or ui.json
# Refresh browser (Ctrl+R or Cmd+R)
```

Asset changes may require hard refresh:
```
Ctrl+Shift+R (Windows/Linux)
Cmd+Shift+R (Mac)
```

---

## Step 6: Production Build

For production, brand files are copied to the build output:

```powershell
# Build the app
npm run build

# Check build output
Get-ChildItem "dist/"
# Should include:
# - brand.json
# - ui.json
# - assets/
```

---

## Step 7: Common Integration Points

### ThemeProvider

The main theme provider component:
- Location: `chat-ui/src/providers/ThemeProvider.jsx` (or similar)
- Fetches `brand.json`
- Applies CSS variables to `:root`
- Provides theme context to components

### UIConfigProvider

The UI configuration provider:
- Location: `chat-ui/src/providers/UIConfigProvider.jsx` (or similar)
- Fetches `ui.json`
- Provides header, footer, profile config to components

### Header Component

- Location: `chat-ui/src/components/Header.jsx` (or similar)
- Reads from `ui.json` via context
- Renders logo, actions, profile menu

---

## Step 8: Debugging Configuration Issues

### Config Not Loading

```javascript
// Add to browser console to debug
fetch('/brand.json')
  .then(r => r.json())
  .then(console.log)
  .catch(console.error);
```

### CSS Variables Not Applied

```javascript
// Check if variables exist
getComputedStyle(document.documentElement)
  .getPropertyValue('--color-primary');
```

### Theme Not Updating

1. Check browser cache is cleared
2. Verify JSON file has no syntax errors
3. Check browser console for errors
4. Try incognito/private window

---

## Summary Template

```markdown
## Brand Wiring Verified

### Configuration Files
- [ ] brand.json loads at /brand.json
- [ ] ui.json loads at /ui.json
- [ ] auth.json loads at /auth.json
- [ ] Assets served from /assets/

### CSS Variables
- [ ] --color-primary set correctly
- [ ] Font families applied
- [ ] Background colors correct

### Components
- [ ] Header renders with correct logo
- [ ] Footer shows configured links
- [ ] Profile menu has correct items

### Hot Reload
- [ ] JSON changes apply on refresh
- [ ] Asset changes apply on hard refresh
```

---

## Troubleshooting

### "Config returns 404"

1. Check file exists in `brand/public/`
2. Verify dev server is running
3. Check Vite config includes public folder

### "CSS variables undefined"

1. Check ThemeProvider wraps the app
2. Verify brand.json has valid color values
3. Check console for fetch errors

### "Changes not appearing"

1. Clear browser cache
2. Try incognito window
3. Check JSON syntax is valid
4. Restart dev server

### "Assets not loading"

1. Check asset path in JSON is correct
2. Verify file exists in assets folder
3. Check browser network tab for errors
