# MozaiksAI Design System & Dynamic Theming

## Overview
Multi-tenant design system enabling app-specific branding (fonts, colors, logos) at runtime while maintaining consistent UI patterns across all workflow artifacts.

---

## Files

### 📐 `artifactDesignSystem.js`
**Centralized design tokens** - Single source of truth for UI consistency.

**Exports:**
- `fonts` - Font utility classes (font-sans, font-heading, font-logo)
- `typography` - Pre-composed text classes (display, heading, body, label)
- `colors` - Organized color palettes (primary, status, slate)
- `components` - Ready-to-use patterns (cards, buttons, badges, accordions)
- `spacing` - Consistent spacing scales (section, subsection, group, padding, gap)
- `layouts` - Common layout structures (artifactContainer, grids)
- `utils` - Helper classes (truncate, transitions, hover effects)

**Usage:**
```javascript
import { typography, components, spacing, layouts } from './styles/artifactDesignSystem';

<div className={layouts.artifactContainer}>
  <div className={components.card.primary}>
    <h1 className={typography.display.xl}>Title</h1>
    <button className={components.button.primary}>Action</button>
  </div>
</div>
```

---

### 🎨 `themeProvider.js`
**Dynamic theme loading** - Manages app-specific branding at runtime.

**Key Functions:**
- `initializeTheme(appId)` - Load and apply theme (call once in App.js)
- `DEFAULT_THEME` - Fallback MozaiksAI branding
- `loadThemeFromAPI(appId)` - Fetch from `/api/themes/{appId}` with timeout + graceful fallback
- `applyTheme(theme)` - Inject fonts, CSS variables, favicon
- `clearThemeCache(appId?)` - Invalidate per-app or global cache for hot-reload
- `getThemeMetadata(appId?)` - Read cached `source`, `updatedAt`, `updatedBy` for UI messaging
- `getCurrentAppId()` - Get active app context

**Theme Structure:**
```javascript
{
  fonts: {
    body: { family, fallbacks, googleFont, tailwindClass },
    heading: { family, fallbacks, googleFont, tailwindClass },
    logo: { family, localFont, tailwindClass }
  },
  colors: {
    primary: { main, light, dark, name },
    secondary: { main, light, dark, name },
    accent: { main, light, dark, name },
    success/warning/error: { ... }
  },
  branding: { name, logo, favicon }
}
```

**Integration:**
```javascript
// App.js
import { initializeTheme } from './styles/themeProvider';

useEffect(() => {
  const appId = getAppIdFromAuth();
  initializeTheme(appId);
}, []);
```

---

### ⚛️ `useTheme.js`
**React hook** - Component-level theme access for dynamic branding.

**Exports:**
- `useTheme(appId?)` - Main hook returning `{ theme, loading }`
- `getThemeColor(theme, colorKey)` - Extract color config
- `getThemeFont(theme, fontKey)` - Extract font config
- `getDynamicColorClass(theme, type, variant, property)` - Generate Tailwind classes

**Usage:**
```javascript
import useTheme, { getThemeColor } from './styles/useTheme';

const MyComponent = () => {
  const { theme, loading } = useTheme();
  const primaryColor = getThemeColor(theme, 'primary');
  
  return (
    <div style={{ backgroundColor: primaryColor.main }}>
      Dynamic branded content
    </div>
  );
};
```

---

### 📖 `THEME_INTEGRATION_EXAMPLE.js`
**Complete working examples** showing integration patterns:
- App.js initialization with loading state
- Component-level theme usage
- Dynamic button components
- Theme switching logic
- Backend API integration pattern
- Testing different app themes

---

## Documentation

### 📘 `docs/UI_COMPONENT_DESIGN_GUIDE.md`
Complete reference for AI agents (especially UIFileGenerator) to generate consistent UI components.

**Sections:**
- Font system & typography classes
- Color palette organization
- Pre-built component patterns
- Spacing & layout guidelines
- Icon library (lucide-react)
- Complete component template
- Testing checklist
- **8 mandatory rules for AI agents**

---

### 📗 `docs/DESIGN_SYSTEM_INTEGRATION.md`
Integration summary and architecture documentation.

**Covers:**
- Problem statement & solution
- File structure & responsibilities
- Usage examples (manual + AI-generated)
- Font stack alignment across layers
- Color migration roadmap (static → CSS variables)
- App theme API specification
- Implementation checklist

---

## Architecture

### Layer 1: Static Design Tokens (`artifactDesignSystem.js`)
- Pre-defined classes and patterns
- Imported directly into components
- Maps to Tailwind utilities

### Layer 2: Dynamic Theme Provider (`themeProvider.js`)
- Loads app-specific configuration
- Injects Google Fonts dynamically
- Injects CSS custom properties (--color-primary, etc.)
- Updates favicon/branding

### Layer 3: React Integration (`useTheme.js`)
- Provides theme access in components
- Helper functions for colors/fonts
- Loading states for theme initialization

### Layer 4: AI Agent Integration
- UIFileGenerator references design system in system message
- Generated components automatically use design tokens
- Ensures consistency across dynamically created artifacts

---

## Theming Flow

### 1. App Initialization
```javascript
// App.js loads theme once
initializeTheme(appId)
  ↓
loadThemeFromAPI(appId)  // Fetches via /api/themes/{id}
  ↓
applyTheme(theme)
  ↓
  ├─ loadGoogleFont(fontUrl)     // Injects <link> tags
  ├─ updateCSSVariables(colors)  // Sets CSS custom properties
  └─ updateFavicon(faviconUrl)   // Updates browser icon
```

### 2. Component Rendering
```javascript
// Components access theme via hook
const { theme } = useTheme()
  ↓
getThemeColor(theme, 'primary')   // Extracts color values
  ↓
Apply via inline styles or Tailwind classes
```

### 3. Design System Usage
```javascript
// Components use pre-built patterns
import { typography, components } from './styles/artifactDesignSystem'
  ↓
className={typography.display.xl}  // Resolves to theme fonts
className={components.card.primary} // Uses theme colors (future CSS vars)
```

---

## Current State vs. Future State

### ✅ Implemented (Phase 1)
- Centralized design system with typography, spacing, components
- Dynamic font loading per app (Google Fonts + local fonts)
- Theme provider infrastructure with caching
- React hook for component-level theme access
- CSS variable injection for colors
- AI agent integration (UIFileGenerator system message updated)

### 🔄 In Progress (Phase 2)
- **Color migration**: Hardcoded Tailwind (border-cyan-500) → CSS variables (border-[var(--color-primary)])
- Backend theme API endpoint (`/api/apps/{id}/theme`)
- Theme management admin UI

### 📋 Planned (Phase 3)
- Theme preview/editor in admin panel
- Theme inheritance (app → default)
- Theme export/import for backup
- A/B testing different themes
- Analytics on theme usage

---

## Multi-Tenant Benefits

1. **Brand Consistency**: Each app sees their own fonts, colors, logos
2. **Runtime Flexibility**: No code changes or rebuilds required
3. **Scalability**: Single codebase serves unlimited apps
4. **Performance**: Theme caching prevents redundant API calls
5. **Developer Experience**: Design system prevents style drift
6. **AI-Friendly**: Clear patterns for automated component generation

---

## Quick Start

### For Developers (Manual Components)
```javascript
// 1. Import design system
import { typography, components, spacing, layouts } from '../styles/artifactDesignSystem';

// 2. Optionally import theme hook
import useTheme, { getThemeColor } from '../styles/useTheme';

// 3. Build component
const MyComponent = () => {
  const { theme } = useTheme();
  
  return (
    <div className={layouts.artifactContainer}>
      <div className={components.card.primary}>
        <h1 className={typography.display.xl}>Hello</h1>
      </div>
    </div>
  );
};
```

### For AI Agents (Generated Components)
UIFileGenerator automatically includes design system imports and uses constants instead of raw Tailwind classes. See `docs/UI_COMPONENT_DESIGN_GUIDE.md` for complete patterns.

---

## Testing Themes

### Mock Different apps
```javascript
// In themeProvider.js loadThemeFromAPI, replace with:
const MOCK_THEMES = {
  'default': { /* MozaiksAI cyan/violet */ },
  'app-a': { /* Corporate blue */ },
  'app-b': { /* Startup green */ },
  'app-c': { /* Agency purple */ },
};
return MOCK_THEMES[appId] || MOCK_THEMES['default'];
```

### Switch apps
```javascript
localStorage.setItem('mozaiks.current_app_id', 'app-a');
window.location.reload();
```

---

## Migration Path (Colors)

### Current (Hardcoded Tailwind)
```javascript
// artifactDesignSystem.js
colors.primary.cyan.border = 'border-cyan-500'

// Component
<div className="border-cyan-500">...</div>
```

### Future (CSS Variables)
```javascript
// artifactDesignSystem.js
colors.primary.border = 'border-[var(--color-primary)]'

// Component
<div className="border-[var(--color-primary)]">...</div>

// themeProvider.js injects:
:root {
  --color-primary: #06b6d4;  /* From theme.colors.primary.main */
}
```

**Benefits:**
- Support any hex color (not limited to Tailwind palette)
- True runtime theming without hardcoded color names
- Smooth CSS transitions between theme changes

---

## Support

- **Design System Reference**: `docs/UI_COMPONENT_DESIGN_GUIDE.md`
- **Integration Guide**: `docs/DESIGN_SYSTEM_INTEGRATION.md`
- **Theme Runtime Guide**: `docs/app_THEME_MANAGEMENT.md`
- **Code Examples**: `THEME_INTEGRATION_EXAMPLE.js`
- **AI Agent Config**: `workflows/Generator/agents.json` (UIFileGenerator section)

---

## Theme Validation Workflow

Use the shared CLI to confirm JSON structure before deploying branding updates:

```powershell
# Validate partial overrides
.\.venv\Scripts\python.exe -m core.data.theme_validation --mode update --input path\to\theme_update.json --summary

# Inspect merged output (default + overrides)
.\.venv\Scripts\python.exe -m core.data.theme_validation --mode update --input path\to\theme_update.json --print-merged
```

Failed validations highlight the exact JSON path to fix, preventing malformed themes from reaching the runtime.

---

**Last Updated**: Session with dynamic theming implementation
**Status**: Phase 1 complete (fonts dynamic, colors infrastructure ready, migration pending)
