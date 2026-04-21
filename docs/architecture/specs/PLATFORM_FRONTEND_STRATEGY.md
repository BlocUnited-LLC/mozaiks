# Platform & Frontend Strategy

**Status:** Specification
**Created:** 2026-04-06
**Depends on:** DESIGN_SYSTEM_SPEC.md, RUNTIME_SPEC.md, UI_SYSTEM_SPEC.md

This document extends the existing architecture to address the OSS/Platform split, frontend asset provisioning, E2B testing, and the CLI story.

---

## Overview: What This Document Adds

| Area | Existing Spec | This Document Adds |
|------|---------------|-------------------|
| **Theming** | DESIGN_SYSTEM_SPEC.md defines tokens | How fonts/themes are provisioned and resolved at runtime |
| **Primitives** | DESIGN_SYSTEM_SPEC.md defines registry | When they're installed vs generated |
| **UI System** | UI_SYSTEM_SPEC.md defines schemas | Frontend asset provisioning model |
| **Runtime** | RUNTIME_SPEC.md defines responsibilities | E2B environment preparation |
| **CLI** | Not yet specified | Complete OSS developer experience |
| **Platform** | Implied but not explicit | Proprietary advantages framework |

---

## UI Surface Contracts

Mozaiks frontend behavior is split across **three different UI surface contracts**:

1. **App UI**
   - Declarative page schemas rendered from the page primitive registry
   - Used for persistent application pages

2. **Agent UI tools**
   - Event-driven React components rendered inline or as artifacts in chat
   - Used for workflow-specific approvals, forms, dashboards, and interaction surfaces

3. **Transition UI**
   - Router/session components rendered between workflows or workflow-sequence phases
   - Used for workflow routing, choice transitions, prerequisite redirects, and progress views

All three share the same design foundation, but they should not be collapsed into one generic contract.

For the platform-owned app shell:
- `app.json + pages/*.yaml` defines route entry, page labels, order, and transition/component routing
- `app.json` owns app startup behavior such as `landing_spot`
- `shell.json` owns shell content and behavior such as header pills, notifications, profile, footer, and header actions
- `theme_config.json` owns visual theme tokens, shared primitives, and semantic `ui.shell` / `ui.page` / `ui.chat` styling
- `asset_manifest.json` owns reusable media inventory metadata (asset ids, source/path/url, provenance, usage hints)
- the runtime-owned `PageFrame` consumes those page tokens for persistent page width, spacing, title treatment, and transparent background behavior
- transition screens render inside that shell by default for non-core platform routes

**Live source of truth**:
- `chat-ui/src/ui/primitives/index.js`
- `chat-ui/src/ui/page-renderer/PrimitiveRegistry.js`
- `mozaiksai/core/workflow/ui_primitives.py`

### Deterministic Workflow UI Loading

For agent UI tools and workflow surfaces, component loading should stay deterministic and workflow-scoped:

1. workflow packs declare surfaces in `tools.yaml` (`UI_Tool` or `UI_Surface`)
2. workflow packs export those components from `ui/index.js`
3. `@chat-workflows` auto-discovery registers components with both plain and namespaced keys (`Workflow:Component`)
4. `WorkflowUIRouter` resolves namespaced keys first, then plain fallback keys

Do not rely on legacy per-workflow dynamic import paths such as `workflows/<name>/components/index.js`.
The canonical contract is `ui/index.js` plus registry-based resolution.

Generators and validators should trust those live registries rather than older hand-maintained primitive lists in docs or prompts.

### Page Customization Boundary

For persistent application pages, Mozaiks should optimize for **declarative customization first**:

1. define `ExperienceSpec` using page archetypes and theme intent
2. compile page archetypes into shipped primitives and richer primitive composition
3. add new platform-owned primitives/patterns when the registry is too small
4. use explicit custom slots for novel persistent surfaces
5. reserve freeform React for agent UI tools and transition surfaces

That means generated app pages should not default to raw React files. If a product needs a novel persistent page surface, the clean path is to promote that surface into the shared archetype/primitive system so future apps can reuse it consistently.
AppGenerator no longer carries a raw frontend page/component generation path. If a product needs a novel persistent page surface, promote it into the shared archetype/primitive system rather than adding a second AppGenerator React lane.
Theme choices should come from approved upstream brand context, not ad hoc page-level styling. In the current workflow chain that means preserving `brand_intent` from ValueEngine, optionally refining it through `ThemeCapture`, then compiling it into `theme_config_patch` only when the approved direction differs from the platform default.
Shell content should compile separately into `shell_config` when the app needs specific header/profile/notifications/footer behavior.
Media inventory should compile separately into `asset_manifest` when the app needs non-default logos/icons/imagery.
The shell and `PageFrame` should own outer page structure. Agents should only control declarative page layout, primitive composition, data bindings, approved theme tokens, and shell content inside that frame.

For existing-app onboarding, theme capture is a separate step from page generation:

- `ThemeCapture` extracts host brand evidence into canonical theme tokens
- those tokens style Mozaiks-owned shells, page frames, or embedded AI surfaces
- they do not imply automatic recreation of a bespoke host header, footer, or page system
- they do not emit header actions, footer links, or profile menus; those stay in `shell.json`
- media inventory for Mozaiks-owned surfaces should be cataloged in `asset_manifest.json`, not mixed into theme/shell token slots
- embedded Mozaiks surfaces must still talk to runtime through the canonical session flow, not a custom socket protocol

---

## 1. Frontend Asset Provisioning Model

### Core Principle

> **Agents describe and configure UI, not provision frontend infrastructure.**

This means:
- Agents do NOT run component installation commands during app generation
- Agents do NOT install fonts dynamically
- Agents do NOT generate arbitrary raw styling
- The runtime/template provides UI system, fonts, theme tokens, and components **ahead of time**

### Provisioning Timeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    WHEN ARE FRONTEND ASSETS PROVISIONED?                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ════════════════════════════════════════════════════════════════════════   │
│  BUILD TIME (Template/Runtime Development)                                   │
│  ════════════════════════════════════════════════════════════════════════   │
│                                                                              │
│  WHO: Mozaiks core team / OSS contributors                                  │
│  WHEN: During runtime/template development, before any apps are created     │
│                                                                              │
│  ✓ All base UI components pre-installed                                     │
│  ✓ Core Mozaiks primitives implemented                                      │
│    (the shipped runtime set; catalog expands over time)                    │
│  ✓ All supported fonts bundled or configured                                │
│  ✓ Theme token system implemented                                           │
│  ✓ Base layouts and shell components built                                  │
│  ✓ Icon library configured                                                  │
│  ✓ CSS/Tailwind configuration complete                                      │
│                                                                              │
│  OUTPUT: Pre-built runtime template                                          │
│                                                                              │
│  ════════════════════════════════════════════════════════════════════════   │
│  APP GENERATION TIME (AI Workflow Execution)                                 │
│  ════════════════════════════════════════════════════════════════════════   │
│                                                                              │
│  WHO: AI agents / Users                                                      │
│  WHEN: During app creation workflows                                         │
│                                                                              │
│  ✓ Select theme options (primary: blue, font: inter)                        │
│  ✓ Define pages using primitives                                            │
│  ✓ Configure navigation structure                                           │
│  ✓ Set up data bindings                                                     │
│  ✓ Define workflows and modules                                             │
│                                                                              │
│  ✗ NO component installation                                                 │
│  ✗ NO font downloading                                                       │
│  ✗ NO CSS generation                                                         │
│  ✗ NO npm package installation                                               │
│                                                                              │
│  OUTPUT: App definition (YAML/JSON schemas)                                  │
│                                                                              │
│  ════════════════════════════════════════════════════════════════════════   │
│  RUNTIME (App Execution)                                                     │
│  ════════════════════════════════════════════════════════════════════════   │
│                                                                              │
│  WHO: Runtime system                                                         │
│  WHEN: When app is loaded/served                                             │
│                                                                              │
│  ✓ Load app definition                                                      │
│  ✓ Resolve theme tokens → CSS variables                                     │
│  ✓ Load appropriate font from pre-bundled set                               │
│  ✓ Render pages using pre-built primitives                                  │
│  ✓ Apply theme configuration                                                │
│                                                                              │
│  OUTPUT: Rendered application                                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### What Is Pre-Bundled

```yaml
# Pre-bundled in runtime template (NOT generated per-app)

pre_bundled:
  # ═══════════════════════════════════════════════════════════════════════
  # BASE UI COMPONENTS (Pre-installed in template)
  # ═══════════════════════════════════════════════════════════════════════
  base_components:
    - accordion
    - alert
    - alert-dialog
    - avatar
    - badge
    - breadcrumb
    - button
    - calendar
    - card
    - chart
    - checkbox
    - collapsible
    - command
    - context-menu
    - dialog
    - drawer
    - dropdown-menu
    - form
    - hover-card
    - input
    - label
    - menubar
    - navigation-menu
    - pagination
    - popover
    - progress
    - radio-group
    - resizable
    - scroll-area
    - select
    - separator
    - sheet
    - skeleton
    - slider
    - sonner  # Toast
    - switch
    - table
    - tabs
    - textarea
    - tooltip

  # ═══════════════════════════════════════════════════════════════════════
  # MOZAIKS PRIMITIVES (Built on base components)
  # NOTE:
  # - `implemented_core` is what the runtime ships today.
  # - `catalog_target` is the broader primitive family the platform may grow into.
  # - Agents may only reference implemented primitives unless a platform author
  #   has explicitly added and registered more.
  # ═══════════════════════════════════════════════════════════════════════
  primitives:
    implemented_core:
      layout: [Card, Grid]
      data: [DataTable]
      dashboard: [Stat]
      form: [Form]
      overlay: [Modal]
      action: [Button]
      feedback: [Alert, Skeleton, Empty]
      content: [Badge]

    catalog_target:
      layout: [Page, Section, Card, Grid, Stack, Divider, Spacer]
      data: [DataTable, List, DetailView, Timeline, Tree]
      dashboard: [Stat, StatGroup, Chart, ProgressRing, Sparkline]
      form: [Form, FormField, FormSection, FormActions]
      input: [TextInput, TextArea, NumberInput, Select, MultiSelect, Checkbox,
              RadioGroup, Switch, DatePicker, DateRangePicker, TimePicker,
              FileUpload, ColorPicker, Slider]
      overlay: [Modal, Drawer, Popover, Tooltip, DropdownMenu, ContextMenu, CommandPalette]
      action: [Button, IconButton, ButtonGroup, ActionBar, FloatingAction]
      feedback: [Alert, Toast, Banner, Progress, Spinner, Skeleton, Empty]
      navigation: [NavBar, Sidebar, Breadcrumb, Tabs, Stepper, Pagination]
      content: [Text, Heading, Badge, Avatar, AvatarGroup, Icon, Image, Code, Markdown]
      chat: [ChatContainer, MessageList, Message, MessageInput, TypingIndicator, Artifact]

  # ═══════════════════════════════════════════════════════════════════════
  # FONTS (Pre-configured, not downloaded per-app)
  # ═══════════════════════════════════════════════════════════════════════
  fonts:
    bundled:
      - system       # No external load needed
      - inter        # Bundled or Google Fonts link pre-configured
      - roboto
      - opensans
      - lato
      - poppins
      - nunito
      - montserrat
      - raleway
      - source-code-pro  # Monospace
      - jetbrains-mono   # Monospace

    heading_options:
      - playfair
      - merriweather

  # ═══════════════════════════════════════════════════════════════════════
  # ICONS (Lucide bundled)
  # ═══════════════════════════════════════════════════════════════════════
  icons:
    library: lucide-react
    tree_shaking: true  # Only used icons in final bundle

  # ═══════════════════════════════════════════════════════════════════════
  # CHARTING (Pre-installed)
  # ═══════════════════════════════════════════════════════════════════════
  charting:
    library: recharts
    pre_configured: true
```

### What Agents Can Configure

These are emitted configuration outcomes, not freeform styling decisions invented in isolation. For consistent generation, theme and branding settings should compile from the approved upstream concept and `brand_intent`.

```yaml
# What AI can set in app.json + pages/*.yaml (NOT what it provisions)

configurable_by_agents:
  # Theme selection (from predefined options)
  theme:
    primary: blue        # From: [slate, gray, blue, indigo, ...]
    variant: modern      # From: [default, modern, soft, brutalist, glassmorphic]
    radius: medium       # From: [none, small, medium, large, full]
    appearance: system   # From: [light, dark, system]
    font: inter          # From: pre-bundled list
    font_heading: null   # Optional: from heading_options
    density: comfortable # From: [compact, comfortable, spacious]

  # Page definitions (using primitives)
  pages:
    - path: /contacts
      title: Contacts
      content:
        - type: DataTable
          # ... primitive configuration

  # Navigation structure
  navigation:
    - label: Contacts
      icon: users
      path: /contacts

  # Branding (platform mode)
  branding:
    logo_url: "..."
    app_name: "My App"
```

### What OSS Developers Can Customize (Manually)

```yaml
# OSS/CLI mode: developers can modify anything

oss_customizable:
  # Direct component customization
  - components/ui/*.tsx          # Modify base components
  - tailwind.config.js           # Full Tailwind control
  - app/globals.css              # CSS overrides

  # Primitive extension
  - components/primitives/*.tsx  # Custom primitives
  - lib/primitive-registry.ts    # Register new primitives

  # Font customization
  - fonts/                       # Self-host fonts
  - next.config.js               # Font optimization

  # Theme extension
  - lib/theme/custom-tokens.ts   # Additional theme tokens
  - lib/theme/variants/*.ts      # Custom variants
```

---

## 2. Font and Theming Strategy

### Font Resolution Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       FONT RESOLUTION FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  APP DEFINITION                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ theme:                                                                 │ │
│  │   font: inter          # Token, not URL                               │ │
│  │   font_heading: playfair                                              │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  FONT REGISTRY (Pre-configured)                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ fonts = {                                                              │ │
│  │   "inter": {                                                           │ │
│  │     family: "'Inter', sans-serif",                                     │ │
│  │     source: "google",  // or "bundled", "local"                        │ │
│  │     url: "https://fonts.googleapis.com/css2?family=Inter:wght@..."    │ │
│  │     weights: [400, 500, 600, 700],                                     │ │
│  │     variable: "--font-sans"                                            │ │
│  │   },                                                                   │ │
│  │   "playfair": { ... },                                                 │ │
│  │   ...                                                                  │ │
│  │ }                                                                      │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  RUNTIME RESOLUTION                                                          │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                                                                        │ │
│  │  if (mode === "platform") {                                            │ │
│  │    // Fonts served from Mozaiks CDN                                    │ │
│  │    fontUrl = `https://fonts.mozaiks.io/${fontToken}`;                  │ │
│  │  } else if (mode === "oss_bundled") {                                  │ │
│  │    // Fonts bundled in app via next/font                               │ │
│  │    fontUrl = null; // Already in bundle                                │ │
│  │  } else if (mode === "oss_google") {                                   │ │
│  │    // Google Fonts (default for OSS)                                   │ │
│  │    fontUrl = fonts[fontToken].url;                                     │ │
│  │  }                                                                     │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  OUTPUT: CSS Variables + Font Loading                                        │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ :root {                                                                │ │
│  │   --font-sans: 'Inter', system-ui, sans-serif;                         │ │
│  │   --font-heading: 'Playfair Display', serif;                           │ │
│  │   --font-mono: 'JetBrains Mono', monospace;                            │ │
│  │ }                                                                      │ │
│  │                                                                        │ │
│  │ @font-face or <link> injected based on source type                     │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Font Handling by Mode

| Mode | Font Source | How It Works |
|------|-------------|--------------|
| **Platform (Hosted)** | Mozaiks CDN | Pre-optimized subsets served from our CDN |
| **OSS (Default)** | Google Fonts | Links to Google Fonts, configured in template |
| **OSS (Bundled)** | next/font | Developer runs build step to bundle fonts |
| **OSS (Self-hosted)** | Local | Developer places fonts in `/fonts` directory |
| **E2B Preview** | Google Fonts | Same as OSS default (no CDN in sandbox) |

### Theme Token Resolution

```typescript
// packages/ui/src/theme/resolver.ts

interface ThemeConfig {
  primary: ColorToken;      // "blue" | "indigo" | ...
  variant: VariantToken;    // "default" | "modern" | ...
  radius: RadiusToken;      // "none" | "small" | ...
  appearance: AppearanceToken;
  font: FontToken;
  fontHeading?: FontToken;
  density: DensityToken;
}

interface ResolvedTheme {
  cssVariables: Record<string, string>;
  fontLinks: string[];      // URLs to load (if any)
  darkModeVariables: Record<string, string>;
}

export function resolveTheme(
  config: ThemeConfig,
  mode: "platform" | "oss"
): ResolvedTheme {
  // 1. Resolve color palette from token
  const colors = colorPalettes[config.primary];

  // 2. Apply variant modifications
  const variantMods = variants[config.variant];

  // 3. Resolve font URLs based on mode
  const fontLinks = resolveFontUrls(config, mode);

  // 4. Generate CSS variables
  const cssVariables = {
    // Colors
    "--primary": colors[500],
    "--primary-foreground": colors[50],
    ...variantMods.colorOverrides,

    // Radius
    "--radius": radiusScale[config.radius],

    // Typography
    "--font-sans": fontFamilies[config.font].family,
    "--font-heading": config.fontHeading
      ? fontFamilies[config.fontHeading].family
      : "var(--font-sans)",

    // Density
    "--spacing-unit": densityScale[config.density],
  };

  return { cssVariables, fontLinks, darkModeVariables };
}
```

### Key Constraint: Agents Do NOT Download Fonts

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        FONT HANDLING RULES                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✓ AGENTS CAN:                                                               │
│  ───────────────────────────────────────────────────────────────────────    │
│  • Select font: "inter" from predefined list                                │
│  • Specify font_heading: "playfair" for headings                            │
│  • Configure which pages use which font (if supported)                      │
│                                                                              │
│  ✗ AGENTS CANNOT:                                                            │
│  ───────────────────────────────────────────────────────────────────────    │
│  • Download fonts from Google Fonts                                         │
│  • Install font packages (npm i @fontsource/inter)                          │
│  • Specify arbitrary font URLs                                              │
│  • Modify @font-face declarations                                           │
│  • Upload custom fonts (platform mode)                                      │
│                                                                              │
│  WHY:                                                                       │
│  ───────────────────────────────────────────────────────────────────────    │
│  • Security: No arbitrary network requests during generation                │
│  • Performance: Pre-bundled fonts are optimized                             │
│  • Consistency: Known fonts = predictable rendering                         │
│  • E2B: No need to download fonts in sandbox                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### E2B Font Handling

```yaml
# E2B preview environment: fonts are pre-available

e2b_font_strategy:
  # Fonts are loaded via Google Fonts links (fast CDN)
  # No local bundling needed for previews

  template_includes:
    - Google Fonts preconnect headers
    - CSS with @import for each supported font
    - Font-display: swap for fast rendering

  during_preview:
    - Theme token "inter" → Google Fonts link activated
    - No npm install needed
    - No font download commands
    - Fast preview startup
```

---

## 3. UI Generation Boundaries

### The Generation Stack

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        UI GENERATION BOUNDARIES                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  LAYER 1: Base Components (Pre-installed)                                    │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ Button, Table, Card, Dialog, Input, Select, etc.                     │   │
│  │                                                                       │   │
│  │ WHO TOUCHES THIS:                                                     │   │
│  │ • Mozaiks core developers (building primitives)                       │   │
│  │ • OSS developers (customizing their install)                          │   │
│  │                                                                       │   │
│  │ AI NEVER TOUCHES THIS LAYER                                           │   │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                         │
│                                    │ Composed by                             │
│                                    │                                         │
│  LAYER 2: Mozaiks Primitives                                                 │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ DataTable, Form, Stat, Chart, Modal, Page, etc.                      │   │
│  │                                                                       │   │
│  │ WHO TOUCHES THIS:                                                     │   │
│  │ • Mozaiks core developers (building primitives)                       │   │
│  │ • OSS developers (extending/adding primitives)                        │   │
│  │                                                                       │   │
│  │ AI NEVER TOUCHES THIS LAYER                                           │   │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                         │
│                                    │ Referenced by                           │
│                                    │                                         │
│  LAYER 3: Page/Layout Definitions                                            │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ - type: Page                                                          │   │
│  │   sidebar: true                                                       │   │
│  │   content:                                                            │   │
│  │     - type: DataTable                                                 │   │
│  │       columns: [...]                                                  │   │
│  │                                                                       │   │
│  │ WHO TOUCHES THIS:                                                     │   │
│  │ • AI agents (generating app structure)                                │   │
│  │ • Users (via UI builder or YAML)                                      │   │
│  │                                                                       │   │
│  │ ✓ AI WORKS AT THIS LAYER                                              │   │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                    ▲                                         │
│                                    │ Composed into                           │
│                                    │                                         │
│  LAYER 4: App Definition (app.json + pages/*.yaml)                                          │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ name: my-crm                                                          │   │
│  │ theme: { primary: blue, ... }                                         │   │
│  │ pages: [...]                                                          │   │
│  │ navigation: [...]                                                     │   │
│  │ workflows: [...]                                                      │   │
│  │ modules: [...]                                                        │   │
│  │                                                                       │   │
│  │ WHO TOUCHES THIS:                                                     │   │
│  │ • AI agents (complete app generation)                                 │   │
│  │ • Users (via platform or direct YAML)                                 │   │
│  │                                                                       │   │
│  │ ✓ AI WORKS AT THIS LAYER                                              │   │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Chat UI vs App UI

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CHAT UI vs APP UI                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  CHAT UI                              │  APP UI                              │
│  ═════════════════════════════════════│══════════════════════════════════   │
│                                       │                                      │
│  Purpose:                             │  Purpose:                            │
│  Agentic conversation interface       │  Generated application interface     │
│                                       │                                      │
│  Components:                          │  Components:                         │
│  • ChatContainer                      │  • Page                              │
│  • MessageList                        │  • Section                           │
│  • Message                            │  • DataTable                         │
│  • MessageInput                       │  • Form                              │
│  • TypingIndicator                    │  • Chart                             │
│  • Artifact (embedded previews)       │  • Modal                             │
│                                       │  • NavBar/Sidebar                    │
│                                       │                                      │
│  Who creates it:                      │  Who creates it:                     │
│  Built into platform/runtime          │  AI generates definitions            │
│  Not generated per-app                │  Rendered from app.json + pages/*.yaml              │
│                                       │                                      │
│  Relationship:                        │                                      │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│  ┌─────────────┐    generates    ┌─────────────┐                            │
│  │  Chat UI    │ ─────────────► │   App UI    │                             │
│  │             │                 │             │                             │
│  │ "Build me   │                 │ (DataTable, │                             │
│  │  a CRM"     │                 │  Forms,     │                             │
│  │             │                 │  Pages)     │                             │
│  └─────────────┘                 └─────────────┘                             │
│        │                               │                                     │
│        │    can embed artifacts        │                                     │
│        └───────────────────────────────┘                                     │
│                                                                              │
│  Artifacts:                                                                  │
│  • Chat UI can show App UI components as artifacts                          │
│  • E.g., "Here's a preview of your contacts page"                           │
│  • Artifact primitive wraps App UI primitives                               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### What AI Generates vs What Runtime Renders

| AI Generates | Runtime Does |
|--------------|--------------|
| `type: DataTable` | Instantiates `<MozaiksDataTable />` |
| `columns: [{key: "name", label: "Name"}]` | Passes as props |
| `data_source: "module:contacts:list"` | Resolves binding, fetches data |
| `theme.primary: blue` | Generates CSS variables |
| `theme.font: inter` | Loads font, sets `--font-sans` |

---

## 4. OSS CLI Story

### CLI Commands Overview

```bash
# ═══════════════════════════════════════════════════════════════════════════
# PROJECT CREATION
# ═══════════════════════════════════════════════════════════════════════════

# Create new project (interactive)
$ mozaiks new my-app
  ? Select app mode:
    ❯ full      (AI workflows + modules + UI)
      ai-only   (AI workflows only)
      modules   (Modules/CRUD only)
  ? Select template:
    ❯ blank     (Empty project)
      crm       (CRM starter)
      dashboard (Analytics dashboard)
      saas      (Multi-tenant SaaS)
  ? Include example workflows? (Y/n)

# Create with specific options
$ mozaiks new my-crm --mode full --template crm

# ═══════════════════════════════════════════════════════════════════════════
# DEVELOPMENT
# ═══════════════════════════════════════════════════════════════════════════

# Start development server
$ mozaiks dev
  ✓ Starting Python runtime on :8000
  ✓ Starting UI server on :3000
  ✓ Watching for changes...

  App running at http://localhost:3000

# Start with specific options
$ mozaiks dev --port 3001 --no-hot-reload

# ═══════════════════════════════════════════════════════════════════════════
# SCAFFOLDING
# ═══════════════════════════════════════════════════════════════════════════

# Add a new module
$ mozaiks add module contacts
  Created: modules/contacts/
  Created: modules/contacts/schema.py
  Created: modules/contacts/actions.py
  Updated: app.json + pages/*.yaml

# Add a new workflow
$ mozaiks add workflow onboarding
  Created: workflows/onboarding/
  Created: workflows/onboarding/workflow.py
  Created: workflows/onboarding/prompts.py
  Updated: app.json + pages/*.yaml

# Add a new page
$ mozaiks add page settings
  Created: pages/settings.yaml
  Updated: app.json + pages/*.yaml (navigation)

# Add a tool
$ mozaiks add tool send-email
  Created: tools/send_email.py
  Updated: app.json + pages/*.yaml

# ═══════════════════════════════════════════════════════════════════════════
# BUILDING & DEPLOYMENT
# ═══════════════════════════════════════════════════════════════════════════

# Build for production
$ mozaiks build
  ✓ Validating app.json + pages/*.yaml
  ✓ Building frontend bundle
  ✓ Packaging Python modules
  ✓ Output: dist/

# Export for self-hosting
$ mozaiks export --format docker
  ✓ Generated Dockerfile
  ✓ Generated docker-compose.yaml

$ mozaiks export --format vercel
  ✓ Generated vercel.json

# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION & TESTING
# ═══════════════════════════════════════════════════════════════════════════

# Validate app definition
$ mozaiks validate
  ✓ app.json + pages/*.yaml is valid
  ✓ All modules have valid schemas
  ✓ All workflows have valid definitions
  ✓ All pages reference valid primitives

# Run tests
$ mozaiks test
  Running module tests...
  Running workflow tests...

# Check for issues
$ mozaiks doctor
  ✓ Python 3.11+ installed
  ✓ Node 18+ installed
  ✓ Dependencies installed
  ✓ Database connection OK
  ✓ No circular imports detected
```

### Project Structure (OSS)

```
my-app/
├── app.json + pages/*.yaml                    # App definition
├── package.json                # Frontend dependencies
├── pyproject.toml              # Python dependencies
│
├── modules/                    # Data modules
│   ├── contacts/
│   │   ├── schema.py          # Pydantic models
│   │   └── actions.py         # CRUD actions
│   └── deals/
│       └── ...
│
├── workflows/                  # AI workflows
│   ├── onboarding/
│   │   ├── workflow.py
│   │   └── prompts.py
│   └── support/
│       └── ...
│
├── tools/                      # Custom tools
│   └── send_email.py
│
├── pages/                      # Page definitions (YAML)
│   ├── contacts.yaml
│   ├── deals.yaml
│   └── settings.yaml
│
├── components/                 # [OSS CUSTOMIZABLE]
│   ├── ui/                    # Base components (editable)
│   │   ├── button.tsx
│   │   └── ...
│   └── primitives/            # Custom primitives (optional)
│       └── custom-chart.tsx
│
├── lib/                        # [OSS CUSTOMIZABLE]
│   ├── theme/
│   │   └── custom-tokens.ts   # Theme extensions
│   └── primitive-registry.ts  # Custom primitive registration
│
├── public/                     # Static assets
│   ├── fonts/                 # Self-hosted fonts (optional)
│   └── images/
│
├── tailwind.config.js          # [OSS CUSTOMIZABLE] Full Tailwind control
└── next.config.js              # [OSS CUSTOMIZABLE] Next.js config
```

### OSS Customization Points

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    OSS DEVELOPER CUSTOMIZATION                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  LEVEL 1: Configuration Only                                                 │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ Modify app.json + pages/*.yaml                                                       │  │
│  │ • Theme settings                                                      │  │
│  │ • Page definitions                                                    │  │
│  │ • Navigation structure                                                │  │
│  │                                                                       │  │
│  │ Skill required: YAML                                                  │  │
│  │ Risk: Low                                                             │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  LEVEL 2: Extend Primitives                                                  │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ Create new primitives in components/primitives/                       │  │
│  │ Register in lib/primitive-registry.ts                                 │  │
│  │                                                                       │  │
│  │ Example: Custom KPI card with specific styling                        │  │
│  │                                                                       │  │
│  │ Skill required: React + TypeScript                                    │  │
│  │ Risk: Medium                                                          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  LEVEL 3: Modify Base Components                                             │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ Edit components/ui/*.tsx directly                                     │  │
│  │ Full control over component implementation                            │  │
│  │                                                                       │  │
│  │ Example: Custom button animations, table row behaviors                │  │
│  │                                                                       │  │
│  │ Skill required: React + Tailwind + component knowledge                │  │
│  │ Risk: Medium-High (may break primitives)                              │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  LEVEL 4: Full Theme Override                                                │
│  ═══════════════════════════════════════════════════════════════════════    │
│  │ Modify tailwind.config.js                                             │  │
│  │ Add custom CSS variables                                              │  │
│  │ Self-host fonts                                                       │  │
│  │                                                                       │  │
│  │ Example: Company brand system implementation                          │  │
│  │                                                                       │  │
│  │ Skill required: CSS + Tailwind + Design systems                       │  │
│  │ Risk: High (may require updating after Mozaiks updates)               │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### CLI vs Platform Mode Comparison

| Capability | CLI (OSS) | Platform (Hosted) |
|------------|-----------|-------------------|
| Create projects | `mozaiks new` | Web UI / API |
| Theme configuration | app.json + pages/*.yaml | Web theme builder |
| Modify base components | Direct file access | Not allowed |
| Custom primitives | `components/primitives/` | Not allowed |
| Font self-hosting | `public/fonts/` | Not allowed |
| Tailwind config | Full access | Not allowed |
| Deployment | Self-managed | Managed by Mozaiks |
| AI workflows | Local execution | Cloud execution |
| Secrets management | .env / local | Platform vault |

---

## 5. Hosted Platform Advantages

### Philosophy: Value, Not Lock-in

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      OSS vs PLATFORM PHILOSOPHY                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  OSS FRAMEWORK                        │  HOSTED PLATFORM                     │
│  ════════════════════════════════════ │ ════════════════════════════════    │
│                                       │                                      │
│  "I can run this myself"              │  "Mozaiks runs it better for me"    │
│                                       │                                      │
│  ✓ Portable                           │  ✓ Managed experience               │
│  ✓ Self-hostable                      │  ✓ Better intelligence              │
│  ✓ Full customization                 │  ✓ Integrated services              │
│  ✓ No vendor dependency               │  ✓ Team/org features                │
│  ✓ Own your data                      │  ✓ Less operational burden          │
│                                       │                                      │
│  Trade-off: More work to operate      │  Trade-off: Less customization      │
│                                       │                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Platform-Only Features

```yaml
# Features exclusive to hosted Mozaiks platform

platform_exclusive:
  # ═══════════════════════════════════════════════════════════════════════
  # BETTER INTELLIGENCE
  # ═══════════════════════════════════════════════════════════════════════
  intelligence:
    - name: Enhanced Generators
      description: Smarter app generation with platform-trained models
      value: Better code quality, fewer iterations

    - name: Memory & Knowledge Graph
      description: Cross-session context, org-wide learning
      value: AI remembers preferences, past decisions

    - name: Learning Loop
      description: Platform learns from usage patterns
      value: Continuously improving suggestions

    - name: Evaluation System
      description: Automated quality assessment
      value: Catch issues before deployment

  # ═══════════════════════════════════════════════════════════════════════
  # MANAGED SERVICES
  # ═══════════════════════════════════════════════════════════════════════
  managed_services:
    - name: Hosting & Deployment
      description: One-click deploy, auto-scaling, CDN
      value: No infrastructure management

    - name: Database Management
      description: Managed MongoDB, backups, scaling
      value: No database ops

    - name: Secrets Vault
      description: Secure API key storage, rotation
      value: Enterprise-grade security

    - name: Managed Integrations
      description: Pre-configured OAuth, API connections
      value: Faster integration setup

  # ═══════════════════════════════════════════════════════════════════════
  # TEAM & ORG FEATURES
  # ═══════════════════════════════════════════════════════════════════════
  collaboration:
    - name: Team Workspaces
      description: Shared apps, permissions, roles
      value: Team collaboration built-in

    - name: Billing Management
      description: Usage tracking, invoicing, plans
      value: Built-in monetization

    - name: App Discovery
      description: Marketplace, sharing, templates
      value: Community ecosystem

    - name: Governance
      description: Audit logs, compliance, policies
      value: Enterprise requirements

  # ═══════════════════════════════════════════════════════════════════════
  # UX ADVANTAGES
  # ═══════════════════════════════════════════════════════════════════════
  user_experience:
    - name: Visual Theme Builder
      description: Point-and-click theme customization
      value: No YAML editing required

    - name: App Management UI
      description: Dashboard for all your apps
      value: Easy overview and management

    - name: Analytics Dashboard
      description: Usage metrics, performance, errors
      value: Insights without setup

    - name: Preview Environments
      description: Instant preview of changes
      value: Test before deploy
```

### What Runs Where

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PLATFORM ARCHITECTURE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PYTHON LAYER (OSS Framework)                                                │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                                                                        │ │
│  │  mozaiks-runtime   mozaiks-ai   mozaiks-modules   mozaiks-core        │ │
│  │                                                                        │ │
│  │  • Runs identically in OSS and Platform                               │ │
│  │  • Open source, MIT licensed                                          │ │
│  │  • No platform-specific code                                          │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    │ Events / API calls                      │
│                                    ▼                                         │
│  .NET LAYER (Platform Services) - PROPRIETARY                                │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                                                                        │ │
│  │  Payment.API  │  Analytics  │  Learning  │  Teams  │  Hosting         │ │
│  │                                                                        │ │
│  │  • Billing/subscriptions     • Memory/knowledge graph                 │ │
│  │  • Usage metering            • Model evaluation                       │ │
│  │  • Team management           • Deployment orchestration               │ │
│  │                                                                        │ │
│  │  ⚠️ NOT OPEN SOURCE - Platform advantage                              │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  INTEGRATION BOUNDARY:                                                       │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                              │
│  OSS apps can:                                                              │
│  • Run completely standalone (no platform services)                         │
│  • Optionally connect to platform for enhanced features                     │
│  • Export and self-host anytime                                             │
│                                                                              │
│  Platform apps get:                                                         │
│  • All OSS capabilities                                                     │
│  • Plus platform services automatically integrated                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. E2B Runtime/Testing Model

### E2B Purpose

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         E2B USAGE MODEL                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  E2B IS FOR:                                                                 │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  ✓ Running and previewing generated apps                                    │
│  ✓ Testing app behavior in isolated environment                             │
│  ✓ Validating UI renders correctly                                          │
│  ✓ Executing workflows in sandbox                                           │
│  ✓ Quick iteration during app generation                                    │
│                                                                              │
│  E2B IS NOT FOR:                                                             │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  ✗ Installing npm packages per generation                                   │
│  ✗ Running component installation commands every time                       │
│  ✗ Downloading fonts on each preview                                        │
│  ✗ Building the design system from scratch                                  │
│  ✗ Production hosting                                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### E2B Environment Preparation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    E2B TEMPLATE PREPARATION                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PREPARED ONCE (Template Build Time)                                         │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  E2B Template includes:                                                      │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ /app                                                                 │   │
│  │ ├── node_modules/        # Pre-installed (Next.js, React, etc.)     │   │
│  │ ├── components/                                                      │   │
│  │ │   ├── ui/              # All base components pre-installed        │   │
│  │ │   │   ├── button.tsx                                              │   │
│  │ │   │   ├── table.tsx                                               │   │
│  │ │   │   └── ... (complete set)                                      │   │
│  │ │   └── primitives/      # Shipped Mozaiks primitive set pre-built  │   │
│  │ │       ├── data-table.tsx                                          │   │
│  │ │       ├── form.tsx                                                │   │
│  │ │       └── ... (core set + any added extensions)                   │   │
│  │ ├── lib/                                                             │   │
│  │ │   ├── theme/           # Theme resolution system                  │   │
│  │ │   └── primitive-registry.ts                                       │   │
│  │ ├── styles/                                                          │   │
│  │ │   └── globals.css      # All CSS, including font imports          │   │
│  │ ├── tailwind.config.js   # Complete Tailwind config                 │   │
│  │ └── next.config.js       # Optimized Next.js config                 │   │
│  │                                                                      │   │
│  │ /runtime                                                             │   │
│  │ ├── venv/                # Python virtual environment               │   │
│  │ └── mozaiks/             # Pre-installed mozaiks packages           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  DONE PER PREVIEW (Fast)                                                     │
│  ═══════════════════════════════════════════════════════════════════════    │
│                                                                              │
│  1. Inject app.json + pages/*.yaml (app definition)                                        │
│  2. Inject modules/ (Python modules)                                        │
│  3. Inject workflows/ (Python workflows)                                    │
│  4. Inject pages/ (YAML page definitions)                                   │
│  5. Start servers (already installed, just run)                             │
│                                                                              │
│  NO npm install, NO font downloads, NO component installation               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### E2B Startup Sequence

```typescript
// E2B preview startup - what happens when previewing an app

async function startE2BPreview(appBundle: AppBundle): Promise<PreviewUrl> {
  // 1. Start from prepared template (instant)
  const sandbox = await e2b.Sandbox.create("mozaiks-runtime-v1");

  // 2. Inject app-specific files (seconds)
  await sandbox.filesystem.write("/app/app.json + pages/*.yaml", appBundle.appYaml);

  for (const module of appBundle.modules) {
    await sandbox.filesystem.write(
      `/app/modules/${module.name}/`,
      module.files
    );
  }

  for (const workflow of appBundle.workflows) {
    await sandbox.filesystem.write(
      `/app/workflows/${workflow.name}/`,
      workflow.files
    );
  }

  for (const page of appBundle.pages) {
    await sandbox.filesystem.write(
      `/app/pages/${page.name}.yaml`,
      page.content
    );
  }

  // 3. Start servers (fast - no install needed)
  await sandbox.process.start({
    cmd: "cd /app && npm run dev",
    // node_modules already installed in template
  });

  await sandbox.process.start({
    cmd: "cd /runtime && python -m mozaiks.runtime",
    // mozaiks already installed in template
  });

  // 4. Return preview URL
  return sandbox.getHostname(3000);
}
```

### E2B Font Strategy

```yaml
# E2B font handling - no downloads during preview

e2b_fonts:
  strategy: google_fonts_preconnect

  # Template includes preconnect headers
  template_head: |
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>

  # CSS includes @import for all supported fonts
  # Browser loads only the one specified in theme
  template_css: |
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap');
    /* ... all supported fonts */

  # Theme resolution activates the right font
  runtime_behavior: |
    // When app.json + pages/*.yaml specifies font: "inter"
    // CSS variable is set, browser uses cached Google Font
    :root {
      --font-sans: 'Inter', system-ui, sans-serif;
    }

  # Why this works:
  # - Google Fonts CDN is fast and cached
  # - No npm install needed
  # - No font file bundling per-preview
  # - Browser only downloads the font actually used
```

### E2B vs Production

| Aspect | E2B Preview | Production (Platform) | Production (OSS) |
|--------|-------------|----------------------|------------------|
| Components | Pre-installed in template | Pre-installed in deploy | Pre-installed locally |
| Fonts | Google Fonts CDN | Mozaiks CDN / bundled | Self-hosted / Google |
| Startup time | ~5 seconds | Instant (deployed) | Depends on infra |
| Purpose | Testing/iteration | Live app | Live app |
| Persistence | Temporary | Permanent | Permanent |

---

## Summary

### What Stays the Same

- DESIGN_SYSTEM_SPEC.md primitive definitions
- UI_SYSTEM_SPEC.md page/schema system
- RUNTIME_SPEC.md responsibilities
- TOOLS_SPEC.md tool model
- AI generates schemas, not code
- Component library is internal implementation detail

### What This Document Adds

1. **Frontend Provisioning Model**
   - Components pre-installed at template build time
   - Agents configure, don't provision
   - Clear build-time vs runtime separation

2. **Font/Theme Strategy**
   - Font tokens resolved at runtime
   - Multiple resolution modes (platform/oss/e2b)
   - Agents select from registry, don't download

3. **UI Generation Boundaries**
   - 4-layer stack with clear ownership
   - Chat UI vs App UI distinction
   - AI works at schema layer only

4. **OSS CLI Story**
   - Complete command set
   - Project structure
   - Customization levels

5. **Platform Advantages**
   - Better intelligence (memory, learning)
   - Managed services
   - Team/org features
   - Value, not lock-in

6. **E2B Model**
   - Pre-provisioned template
   - Fast preview startup
   - No per-preview installation

### Key Principles

| Principle | Implementation |
|-----------|----------------|
| **Agents describe, don't provision** | Schemas and config only |
| **OSS = portable base** | Full functionality standalone |
| **Platform = enhanced experience** | Additional services, not lock-in |
| **E2B = prepared environment** | No dynamic provisioning |
| **Fonts/themes are tokens** | Resolved at runtime, not generated |
