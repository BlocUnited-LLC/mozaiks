# Instruction Prompt: Managing Brand Assets

**Task:** Set up logos, icons, fonts, and images

**Complexity:** Low (file management)

---

## Context for AI Agent

You are helping a user set up brand assets for their MozaiksAI application. Assets include logos, icons, custom fonts, and background images.

---

## Step 1: Understand Asset Needs

Ask the user:

1. **"What assets do you have ready?"**
   - Logo files
   - Custom icons
   - Custom fonts
   - Background images

2. **"What formats are your assets?"**
   - SVG (preferred for logos/icons)
   - PNG (fallback)
   - OTF/WOFF/WOFF2 (fonts)

---

## Step 2: Asset Folder Structure

```
brand/public/
├── assets/
│   ├── logo.svg              # Main logo
│   ├── wordmark.png          # Text-based logo
│   ├── favicon.png           # Browser tab icon
│   ├── chat_bg.png           # Chat background (optional)
│   ├── profile.svg           # Profile icon
│   ├── notifications.svg     # Notification bell
│   ├── settings.svg          # Settings gear
│   ├── logout.svg            # Logout icon
│   └── [custom icons...]     # Additional icons
│
└── fonts/
    ├── CustomFont.otf        # Custom font files
    └── CustomFont.woff2      # Web font format
```

---

## Step 3: Required Assets

### Logo Files

| Asset | Format | Usage |
|-------|--------|-------|
| `logo.svg` | SVG | Header, app icon |
| `wordmark.png` | PNG | Header (if logo is icon-only) |
| `favicon.png` | PNG (32x32 or 64x64) | Browser tab |

### System Icons

| Icon | Usage |
|------|-------|
| `profile.svg` | Profile menu trigger |
| `notifications.svg` | Notification bell |
| `settings.svg` | Settings menu item |
| `logout.svg` | Sign out menu item |

---

## Step 4: Copy Assets

```powershell
# Create directories if needed
New-Item -ItemType Directory -Force -Path "brand/public/assets"
New-Item -ItemType Directory -Force -Path "brand/public/fonts"

# Copy your logo
Copy-Item "[path/to/your/logo.svg]" "brand/public/assets/logo.svg"
Copy-Item "[path/to/your/wordmark.png]" "brand/public/assets/wordmark.png"
Copy-Item "[path/to/your/favicon.png]" "brand/public/assets/favicon.png"

# Copy custom fonts (if any)
Copy-Item "[path/to/CustomFont.otf]" "brand/public/fonts/CustomFont.otf"
```

---

## Step 5: Reference Assets in brand.json

```json
{
  "assets": {
    "logo": "logo.svg",
    "wordmark": "wordmark.png",
    "favicon": "favicon.png",
    "backgroundImage": "chat_bg.png"
  }
}
```

Asset paths are relative to `brand/public/assets/`.

---

## Step 6: Reference Icons in ui.json

```json
{
  "header": {
    "logo": {
      "src": "logo.svg",
      "wordmark": "wordmark.png"
    },
    "actions": [
      {
        "icon": "sparkle.svg"
      }
    ]
  },
  "profile": {
    "icon": "profile.svg",
    "menu": [
      { "icon": "settings.svg" },
      { "icon": "logout.svg" }
    ]
  },
  "notifications": {
    "icon": "notifications.svg"
  }
}
```

---

## Step 7: Configure Custom Fonts

### In brand.json

```json
{
  "fonts": {
    "logo": {
      "family": "CustomFont",
      "localFont": true,
      "src": "/fonts/CustomFont.otf",
      "tailwindClass": "font-logo"
    }
  }
}
```

### Font Format Priority

| Format | Browser Support | Size |
|--------|-----------------|------|
| WOFF2 | Modern browsers | Smallest |
| WOFF | Older browsers | Medium |
| OTF/TTF | All browsers | Largest |

Use WOFF2 for best performance, with OTF as fallback.

---

## Step 8: Optimize Assets

### SVG Optimization

```powershell
# Install SVGO (if not installed)
npm install -g svgo

# Optimize SVG files
svgo "brand/public/assets/logo.svg" -o "brand/public/assets/logo.svg"
```

### Image Optimization

```powershell
# For PNG files, use imagemin or online tools like TinyPNG
# Recommended: Keep images under 100KB where possible
```

### Recommended Sizes

| Asset | Recommended Size |
|-------|------------------|
| Logo | 200x60 or less |
| Favicon | 32x32 or 64x64 |
| Icons | 24x24 |
| Background | 1920x1080 max |

---

## Step 9: Verify Assets

```powershell
# List all assets
Get-ChildItem "brand/public/assets" -Recurse
Get-ChildItem "brand/public/fonts" -Recurse

# Start dev server
npm run dev

# Check browser:
# - Logo appears in header
# - Favicon in browser tab
# - Icons load in menus
# - Fonts render correctly
```

---

## Summary Template

```markdown
## Brand Assets Configured

### Logos
- [ ] logo.svg — [added/pending]
- [ ] wordmark.png — [added/pending]
- [ ] favicon.png — [added/pending]

### Icons
- [ ] profile.svg
- [ ] notifications.svg
- [ ] settings.svg
- [ ] logout.svg
- [ ] [custom icons...]

### Fonts
- [ ] [FontName].woff2 — [added/pending]

### Images
- [ ] chat_bg.png — [added/pending]

### Files Updated
- ✅ brand/public/assets/ (assets copied)
- ✅ brand/public/fonts/ (fonts copied)
- ✅ brand.json (asset references)
- ✅ ui.json (icon references)

### Verification
- [ ] Logo appears in header
- [ ] Favicon shows in browser tab
- [ ] Icons load in menus
- [ ] Custom fonts render
```

---

## Troubleshooting

### "Asset not loading"

1. Check file exists: `Get-ChildItem "brand/public/assets"`
2. Verify filename matches JSON exactly (case-sensitive)
3. Check browser network tab for 404 errors

### "SVG not displaying correctly"

1. Open SVG in browser directly to verify it works
2. Check SVG has `viewBox` attribute
3. Remove any external references in SVG

### "Font not rendering"

1. Check font file path in brand.json
2. Verify `localFont: true` is set
3. Check browser console for font loading errors
4. Try WOFF2 format instead of OTF

### "Background image too large"

1. Compress image (TinyPNG, ImageOptim)
2. Use WebP format if supported
3. Keep under 500KB for best performance
