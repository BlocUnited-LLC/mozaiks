# Design System Specification

**Status:** Specification
**Created:** 2026-04-06
**Depends on:** UI_SYSTEM_SPEC.md

This document specifies the design system, theming model, and UI abstraction layer for Mozaiks applications.

---

## Overview

The Mozaiks UI system uses a **component library as an internal implementation detail**. AI and app definitions interact only with structured primitives and theme configurations. The underlying components are pre-installed and vendored - developers never need to install them separately.

### Core Constraints

1. **App/page AI NEVER imports UI components directly**
2. **App/page AI generates structured schemas that map to primitives**
3. **Agent UI tool React may be generated, but only by composing shipped primitives and the UI tool contract**
4. **Transition UI uses registered React components with a routing contract, not page schemas**
5. **Primitives are the controlled interface to the shared design foundation**
6. **Persistent app pages do not get bespoke per-app React by default; customization flows through schemas or new platform-owned primitives**

---

## Surface Model

Mozaiks has **three UI surface families** that share one visual/design foundation but use different runtime contracts:

1. **App UI**
   - Declarative page schemas rendered by the page primitive system
   - Used for persistent product/application pages
   - AI outputs schemas, not React

2. **Agent UI tools**
   - Event-driven React surfaces rendered inside chat/artifact flows
   - Used for approvals, wizards, status panels, action plans, and other workflow interaction surfaces
   - Generator workflows may create these React components, but they must compose shipped primitives and honor the UI tool payload/action contract

3. **Transition UI**
   - Router/session components rendered between workflows or journey phases
   - Used for workflow choice, gating, progress, and session-routing transitions
   - These components are registered UI surfaces with routing props, not page schemas and not UI tools

### Live Source Of Truth

The runtime-shipped primitive catalog is defined by code, not by doc examples:

- `chat-ui/src/ui/primitives/index.js` — shipped primitive exports used by generated UI tool components
- `chat-ui/src/ui/page-renderer/PrimitiveRegistry.js` — shipped page primitive registry used by app/page schemas
- `mozaiksai/core/workflow/ui_primitives.py` — runtime utility that reads those files and validates generator output

Docs may describe the broader target primitive model, but generators and validators must trust the live shipped registry.

---

## 1. UI Abstraction Layer

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        UI ABSTRACTION LAYERS                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  LAYER 1: AI / App Definition                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                                                                        │ │
│  │  UI Schema (YAML/JSON)                                                 │ │
│  │  ┌──────────────────────────────────────────────────────────────────┐ │ │
│  │  │ - type: DataTable                                                 │ │ │
│  │  │   columns: [...]                                                  │ │ │
│  │  │   data_source: "module:contacts:list"                             │ │ │
│  │  └──────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                        │ │
│  │  Theme Config                                                          │ │
│  │  ┌──────────────────────────────────────────────────────────────────┐ │ │
│  │  │ theme:                                                            │ │ │
│  │  │   primary: blue                                                   │ │ │
│  │  │   variant: modern                                                 │ │ │
│  │  └──────────────────────────────────────────────────────────────────┘ │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  LAYER 2: Primitive Resolver                                                │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                                                                        │ │
│  │  Maps schema → Primitive definitions                                   │ │
│  │  Validates against allowed primitives                                  │ │
│  │  Resolves data bindings                                                │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  LAYER 3: Component Compositor                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                                                                        │ │
│  │  Primitives → Composed base UI components                              │ │
│  │  Applies theme tokens                                                  │ │
│  │  Handles responsive behavior                                           │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  LAYER 4: Base Components (Internal, Pre-installed)                         │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                                                                        │ │
│  │  Table, Card, Button, Dialog, Input, Select, etc.                      │ │
│  │  Tailwind CSS                                                          │ │
│  │  Radix UI primitives                                                   │ │
│  │                                                                        │ │
│  │  ⚠️ AI NEVER SEES THIS LAYER                                          │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### What AI Can and Cannot Do

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     AI UI GENERATION RULES                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ AI CAN:                                                                  │
│  ─────────                                                                  │
│  • Select from predefined primitives (DataTable, Form, Card, etc.)          │
│  • Configure primitive properties (columns, fields, actions)                │
│  • Choose from predefined theme options (primary: blue, variant: modern)    │
│  • Select from predefined font sets (font: inter)                           │
│  • Compose primitives into page layouts                                     │
│  • Define data bindings to modules                                          │
│  • Configure navigation structure                                           │
│                                                                              │
│  ❌ AI CANNOT:                                                               │
│  ─────────────                                                              │
│  • Import React components                                                  │
│  • Import UI components directly                                            │
│  • Write JSX/TSX code                                                       │
│  • Write CSS or Tailwind classes                                            │
│  • Create custom components                                                 │
│  • Override component styling directly                                      │
│  • Install npm packages                                                     │
│  • Modify the component library                                             │
│                                                                              │
│  ⚠️ INVALID AI OUTPUT:                                                       │
│  ───────────────────────                                                    │
│  │ import { Button } from "@/components/ui/button"                       │  │
│  │ <div className="flex gap-4 p-6">                                      │  │
│  │ npm install some-ui-library                                           │  │
│                                                                              │
│  ✓ VALID AI OUTPUT:                                                         │
│  ──────────────────                                                         │
│  │ - type: DataTable                                                     │  │
│  │   columns:                                                            │  │
│  │     - key: name                                                       │  │
│  │       label: Name                                                     │  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. UI Primitives

### Primitive Categories

```yaml
# Target primitive registry
#
# NOTE:
# - This section describes the broader primitive model the platform can support.
# - The currently shipped subset is defined by the live registry files listed above.
# - Generator prompts and validators must use the shipped subset, not the full target catalog.

primitives:
  # ═══════════════════════════════════════════════════════════════════════
  # LAYOUT PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  layout:
    - Page           # Full page container with optional sidebar
    - Section        # Content section with optional title
    - Card           # Bordered container with optional header/footer
    - Grid           # CSS grid layout
    - Stack          # Vertical or horizontal flex stack
    - Divider        # Visual separator
    - Spacer         # Empty space

  # ═══════════════════════════════════════════════════════════════════════
  # DATA DISPLAY PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  data:
    - DataTable      # Sortable, filterable data table
    - List           # Simple list of items
    - DetailView     # Key-value display for single record
    - Timeline       # Chronological event display
    - Tree           # Hierarchical data display

  # ═══════════════════════════════════════════════════════════════════════
  # DASHBOARD PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  dashboard:
    - Stat           # Single metric with optional trend
    - StatGroup      # Group of related stats
    - Chart          # Data visualization (bar, line, pie, area)
    - ProgressRing   # Circular progress indicator
    - Sparkline      # Inline mini chart

  # ═══════════════════════════════════════════════════════════════════════
  # FORM PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  form:
    - Form           # Complete form with validation
    - FormField      # Individual field wrapper
    - FormSection    # Group of related fields
    - FormActions    # Submit/cancel buttons

  # ═══════════════════════════════════════════════════════════════════════
  # INPUT PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  input:
    - TextInput      # Single line text
    - TextArea       # Multi-line text
    - NumberInput    # Numeric input with stepper
    - Select         # Dropdown selection
    - MultiSelect    # Multiple selection
    - Checkbox       # Boolean toggle
    - RadioGroup     # Single selection from options
    - Switch         # Toggle switch
    - DatePicker     # Date selection
    - DateRangePicker # Date range selection
    - TimePicker     # Time selection
    - FileUpload     # File upload
    - ColorPicker    # Color selection
    - Slider         # Range slider

  # ═══════════════════════════════════════════════════════════════════════
  # OVERLAY PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  overlay:
    - Modal          # Dialog overlay
    - Drawer         # Slide-in panel
    - Popover        # Contextual popup
    - Tooltip        # Hover information
    - DropdownMenu   # Action menu
    - ContextMenu    # Right-click menu
    - CommandPalette # Keyboard-driven command interface

  # ═══════════════════════════════════════════════════════════════════════
  # ACTION PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  action:
    - Button         # Clickable button
    - IconButton     # Button with only icon
    - ButtonGroup    # Group of related buttons
    - ActionBar      # Toolbar with actions
    - FloatingAction # Floating action button

  # ═══════════════════════════════════════════════════════════════════════
  # FEEDBACK PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  feedback:
    - Alert          # Inline alert message
    - Toast          # Temporary notification
    - Banner         # Full-width announcement
    - Progress       # Linear progress indicator
    - Spinner        # Loading spinner
    - Skeleton       # Loading placeholder
    - Empty          # Empty state with message/action

  # ═══════════════════════════════════════════════════════════════════════
  # NAVIGATION PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  navigation:
    - NavBar         # Top navigation bar
    - Sidebar        # Side navigation
    - Breadcrumb     # Navigation breadcrumbs
    - Tabs           # Tab navigation
    - Stepper        # Step-by-step navigation
    - Pagination     # Page navigation

  # ═══════════════════════════════════════════════════════════════════════
  # CONTENT PRIMITIVES
  # ═══════════════════════════════════════════════════════════════════════
  content:
    - Text           # Styled text
    - Heading        # Section heading
    - Badge          # Status badge
    - Avatar         # User avatar
    - AvatarGroup    # Group of avatars
    - Icon           # Icon from icon set
    - Image          # Responsive image
    - Code           # Code block with syntax highlighting
    - Markdown       # Rendered markdown

  # ═══════════════════════════════════════════════════════════════════════
  # CHAT PRIMITIVES (for chat UI)
  # ═══════════════════════════════════════════════════════════════════════
  chat:
    - ChatContainer  # Full chat interface
    - MessageList    # List of messages
    - Message        # Single message bubble
    - MessageInput   # Chat input with send button
    - TypingIndicator # Typing status
    - Artifact       # Rich artifact display
```

### Primitive Schema Definitions

```yaml
# Primitive schemas with all configurable properties

DataTable:
  description: "Sortable, filterable data table with pagination"
  properties:
    columns:
      type: array
      required: true
      items:
        type: object
        properties:
          key: { type: string, required: true }
          label: { type: string, required: true }
          type: { type: string, enum: [text, number, date, badge, avatar, actions] }
          sortable: { type: boolean, default: false }
          width: { type: string }
          format: { type: string }
          align: { type: string, enum: [left, center, right] }

    data_source:
      type: string
      required: true
      description: "Data binding: module:name:action or data.key"

    selection:
      type: string
      enum: [none, single, multi]
      default: none

    pagination:
      type: boolean
      default: true

    page_size:
      type: number
      default: 20

    search:
      type: boolean
      default: true

    filters:
      type: array
      items:
        type: object
        properties:
          key: { type: string }
          label: { type: string }
          type: { type: string, enum: [text, select, date, daterange] }
          options: { type: array }

    actions:
      type: array
      items: { $ref: "#/definitions/Action" }

    row_actions:
      type: array
      items: { $ref: "#/definitions/Action" }

    empty_state:
      type: object
      properties:
        title: { type: string }
        description: { type: string }
        action: { $ref: "#/definitions/Action" }

# ───────────────────────────────────────────────────────────────────────────

Form:
  description: "Form with validation and submission"
  properties:
    fields:
      type: array
      required: true
      items:
        type: object
        properties:
          name: { type: string, required: true }
          label: { type: string, required: true }
          type:
            type: string
            enum: [text, email, password, number, select, checkbox, textarea, date, file]
          required: { type: boolean, default: false }
          placeholder: { type: string }
          default_value: { type: any }
          options: { type: array }  # For select
          validation:
            type: object
            properties:
              min: { type: number }
              max: { type: number }
              pattern: { type: string }
              message: { type: string }

    layout:
      type: string
      enum: [vertical, horizontal, grid]
      default: vertical

    columns:
      type: number
      default: 1
      description: "Grid columns when layout is grid"

    submit_action:
      type: string
      required: true
      description: "Action on submit: module:name:action"

    submit_label:
      type: string
      default: "Submit"

    cancel_action:
      type: string
      description: "Action on cancel: navigate:/path or close"

    on_success:
      type: string
      enum: [close, redirect, refresh, toast]

    success_message:
      type: string

# ───────────────────────────────────────────────────────────────────────────

Stat:
  description: "Single metric display with optional trend"
  properties:
    label:
      type: string
      required: true

    value_source:
      type: string
      required: true
      description: "Data binding for value"

    format:
      type: string
      enum: [number, currency, percentage, compact]

    trend_source:
      type: string
      description: "Data binding for trend value"

    trend_direction:
      type: string
      enum: [up_good, up_bad, neutral]
      default: up_good

    icon:
      type: string

    color:
      type: string
      enum: [default, primary, success, warning, danger]

# ───────────────────────────────────────────────────────────────────────────

Chart:
  description: "Data visualization chart"
  properties:
    type:
      type: string
      required: true
      enum: [bar, line, area, pie, donut, scatter]

    data_source:
      type: string
      required: true

    x_axis:
      type: object
      properties:
        key: { type: string }
        label: { type: string }
        type: { type: string, enum: [category, time, number] }

    y_axis:
      type: object
      properties:
        key: { type: string }
        label: { type: string }
        format: { type: string }

    series:
      type: array
      items:
        type: object
        properties:
          key: { type: string }
          label: { type: string }
          color: { type: string }

    legend:
      type: boolean
      default: true

    height:
      type: number
      default: 300

# ───────────────────────────────────────────────────────────────────────────

Modal:
  description: "Dialog overlay"
  properties:
    title:
      type: string
      required: true

    size:
      type: string
      enum: [small, medium, large, full]
      default: medium

    closable:
      type: boolean
      default: true

    content:
      type: array
      items: { $ref: "#/definitions/UIComponent" }

    footer:
      type: array
      items: { $ref: "#/definitions/Action" }
```

---

## 3. Theming System

### Theme Configuration Schema

In the current runtime, this schema is persisted in `brand/theme_config.json`.
AppGenerator should emit a `theme_config_patch` artifact that deep-merges into that file;
it should not inline raw theme data into page schemas or shell content config.

```yaml
# brand/theme_config.json

theme:
  # ═══════════════════════════════════════════════════════════════════════
  # COLOR SCHEME
  # ═══════════════════════════════════════════════════════════════════════
  primary:
    type: string
    enum: [slate, gray, zinc, neutral, stone, red, orange, amber, yellow,
           lime, green, emerald, teal, cyan, sky, blue, indigo, violet,
           purple, fuchsia, pink, rose]
    default: blue
    description: "Primary brand color"

  # ═══════════════════════════════════════════════════════════════════════
  # DESIGN VARIANT
  # ═══════════════════════════════════════════════════════════════════════
  variant:
    type: string
    enum:
      - default     # Standard component styling
      - modern      # Bolder colors, sharper edges
      - soft        # Pastel colors, rounded
      - brutalist   # High contrast, minimal
      - glassmorphic # Translucent, blurred backgrounds
    default: default

  # ═══════════════════════════════════════════════════════════════════════
  # BORDER RADIUS
  # ═══════════════════════════════════════════════════════════════════════
  radius:
    type: string
    enum: [none, small, medium, large, full]
    default: medium

  # ═══════════════════════════════════════════════════════════════════════
  # APPEARANCE
  # ═══════════════════════════════════════════════════════════════════════
  appearance:
    type: string
    enum: [light, dark, system]
    default: system

  # ═══════════════════════════════════════════════════════════════════════
  # TYPOGRAPHY
  # ═══════════════════════════════════════════════════════════════════════
  font:
    type: string
    enum: [system, inter, roboto, opensans, lato, poppins, nunito,
           montserrat, raleway, playfair, merriweather, source-code-pro]
    default: system

  font_heading:
    type: string
    description: "Optional separate font for headings"

  # ═══════════════════════════════════════════════════════════════════════
  # DENSITY
  # ═══════════════════════════════════════════════════════════════════════
  density:
    type: string
    enum: [compact, comfortable, spacious]
    default: comfortable

  # ═══════════════════════════════════════════════════════════════════════
  # CUSTOM BRANDING (Platform mode only)
  # ═══════════════════════════════════════════════════════════════════════
  branding:
    logo_url: { type: string }
    favicon_url: { type: string }
    app_name: { type: string }

primitives:
  radius:
    surface: { type: string }
    control: { type: string }
    bubble: { type: string }

  measure:
    shell: { type: string }
    chat_feed: { type: string }

  spacing:
    tight: { type: string }
    base: { type: string }
    loose: { type: string }

ui:
  shell:
    frame:
      maxWidth: { type: string }
    header:
      height: { type: string }
      paddingX: { type: string }
      gap: { type: string }
      clusterGap: { type: string }
      navGap: { type: string }
      navPaddingLeft: { type: string }
      controlGap: { type: string }
      actionHeight: { type: string }
      actionPaddingX: { type: string }
      utilitySize: { type: string }
      utilityPadding: { type: string }
      avatarSize: { type: string }
      avatarLargeSize: { type: string }
      profileHeight: { type: string }
      profilePaddingX: { type: string }
      panelRadius: { type: string }
    footer:
      maxWidth: { type: string }
      paddingY: { type: string }
      paddingX: { type: string }
      gap: { type: string }

  page:
    maxWidth:
      grid: { type: string }
      sidebar: { type: string }
      full-width: { type: string }
      split: { type: string }
    paddingX:
      base: { type: string }
      md: { type: string }
      xl: { type: string }
    paddingY:
      base: { type: string }
      md: { type: string }
    sectionGap: { type: string }
    titlePaddingBottom: { type: string }

  chat:
    modes:
      ask:
        tint: { type: string }
        label: { type: string }
      workflow:
        tint: { type: string }
        label: { type: string }
    bubbleRadius: { type: string }
    feedMaxWidth: { type: string }
    feedPaddingTop:
      base: { type: string }
      sm: { type: string }
      md: { type: string }
    feedPaddingBottom:
      base: { type: string }
      sm: { type: string }
      md: { type: string }
    feedPaddingX:
      base: { type: string }
      sm: { type: string }
      md: { type: string }
    bubblePaddingY: { type: string }
    bubblePaddingX: { type: string }
    userBubblePaddingY: { type: string }
    userBubblePaddingX: { type: string }
    bubbleGap: { type: string }
    bubbleHeaderGap: { type: string }
    namePillPaddingY: { type: string }
    namePillPaddingX: { type: string }
    namePillFontSize: { type: string }
```

### Theme vs Shell Ownership

The contract boundary is strict:

- `brand/theme_config.json` owns visual primitives plus semantic shell/page/chat tokens.
- `config/shell.json` owns shell content and behavior such as header actions, profile menus, notification text, and footer links.
- `config/asset_manifest.json` owns reusable media inventory metadata (asset ids, source/path/url, provenance, usage).
- `ui/pages/*.yaml` own persistent page structure and primitive composition.

Do not put raw spacing, padding, widths, or density controls in `shell.json`.
Do not put header actions or footer links in `theme_config.json`.
Do not put reusable image/icon/video inventory in `theme_config.json` or `shell.json`.
This keeps theme capture reusable and allows AppGenerator to compile experience intent into stable artifacts instead of mixing content with styling.

### Theme Examples

```yaml
# Modern SaaS app
theme:
  primary: blue
  variant: modern
  radius: medium
  font: inter
  appearance: system
  density: comfortable

# Soft, friendly app
theme:
  primary: violet
  variant: soft
  radius: large
  font: nunito
  appearance: light
  density: spacious

# Data-heavy dashboard
theme:
  primary: slate
  variant: default
  radius: small
  font: roboto
  appearance: dark
  density: compact

# Premium feel
theme:
  primary: amber
  variant: glassmorphic
  radius: medium
  font: playfair
  font_heading: playfair
  appearance: dark
```

### Theme Token Mapping

```typescript
// packages/ui/src/theme/tokens.ts

/**
 * Maps theme config to CSS custom properties.
 * This is internal - AI never sees this.
 */

const colorScales = {
  blue: {
    50: "239 246 255",
    100: "219 234 254",
    // ... full scale
    900: "30 58 138",
  },
  // ... other colors
};

const radiusScale = {
  none: "0",
  small: "0.25rem",
  medium: "0.5rem",
  large: "0.75rem",
  full: "9999px",
};

const fontFamilies = {
  system: "system-ui, -apple-system, sans-serif",
  inter: "'Inter', sans-serif",
  roboto: "'Roboto', sans-serif",
  // ... other fonts
};

export function generateThemeTokens(config: ThemeConfig): CSSVariables {
  const primary = colorScales[config.primary];

  return {
    "--primary": primary[500],
    "--primary-foreground": "255 255 255",
    "--radius": radiusScale[config.radius],
    "--font-sans": fontFamilies[config.font],
    // ... complete token set
  };
}
```

### CSS Variable Output

```css
/* Generated from theme config - AI never writes this */

:root {
  /* Colors */
  --background: 0 0% 100%;
  --foreground: 222.2 84% 4.9%;
  --card: 0 0% 100%;
  --card-foreground: 222.2 84% 4.9%;
  --popover: 0 0% 100%;
  --popover-foreground: 222.2 84% 4.9%;
  --primary: 221.2 83.2% 53.3%;
  --primary-foreground: 210 40% 98%;
  --secondary: 210 40% 96.1%;
  --secondary-foreground: 222.2 47.4% 11.2%;
  --muted: 210 40% 96.1%;
  --muted-foreground: 215.4 16.3% 46.9%;
  --accent: 210 40% 96.1%;
  --accent-foreground: 222.2 47.4% 11.2%;
  --destructive: 0 84.2% 60.2%;
  --destructive-foreground: 210 40% 98%;
  --border: 214.3 31.8% 91.4%;
  --input: 214.3 31.8% 91.4%;
  --ring: 221.2 83.2% 53.3%;

  /* Radius */
  --radius: 0.5rem;

  /* Typography */
  --font-sans: 'Inter', system-ui, sans-serif;
  --font-heading: var(--font-sans);
  --font-mono: 'JetBrains Mono', monospace;

  /* Spacing (density) */
  --spacing-unit: 0.25rem;
  --spacing-xs: calc(var(--spacing-unit) * 1);
  --spacing-sm: calc(var(--spacing-unit) * 2);
  --spacing-md: calc(var(--spacing-unit) * 4);
  --spacing-lg: calc(var(--spacing-unit) * 6);
  --spacing-xl: calc(var(--spacing-unit) * 8);
}

.dark {
  --background: 222.2 84% 4.9%;
  --foreground: 210 40% 98%;
  /* ... dark mode overrides */
}
```

---

## 4. Typography System

### Font Configuration

```yaml
# Font definitions (internal)

fonts:
  system:
    family: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    import: null  # No import needed

  inter:
    family: "'Inter', sans-serif"
    import: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
    weights: [400, 500, 600, 700]

  roboto:
    family: "'Roboto', sans-serif"
    import: "https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap"
    weights: [400, 500, 700]

  poppins:
    family: "'Poppins', sans-serif"
    import: "https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap"
    weights: [400, 500, 600, 700]

  playfair:
    family: "'Playfair Display', serif"
    import: "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;500;600;700&display=swap"
    weights: [400, 500, 600, 700]

  # ... other fonts
```

### Typography Scale

```yaml
# Typography scale (internal)

typography:
  # Base sizes
  xs: { size: "0.75rem", line_height: "1rem" }
  sm: { size: "0.875rem", line_height: "1.25rem" }
  base: { size: "1rem", line_height: "1.5rem" }
  lg: { size: "1.125rem", line_height: "1.75rem" }
  xl: { size: "1.25rem", line_height: "1.75rem" }
  2xl: { size: "1.5rem", line_height: "2rem" }
  3xl: { size: "1.875rem", line_height: "2.25rem" }
  4xl: { size: "2.25rem", line_height: "2.5rem" }

  # Semantic aliases
  body: { $ref: "base" }
  caption: { $ref: "sm" }
  label: { $ref: "sm", weight: 500 }
  h1: { $ref: "4xl", weight: 700 }
  h2: { $ref: "3xl", weight: 600 }
  h3: { $ref: "2xl", weight: 600 }
  h4: { $ref: "xl", weight: 600 }
  h5: { $ref: "lg", weight: 600 }
  h6: { $ref: "base", weight: 600 }
```

---

## 5. Customization Modes

### OSS / CLI Mode

In OSS mode, developers have **full control**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          OSS / CLI MODE                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  DEVELOPERS CAN:                                                            │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│  1. Modify pre-installed UI components                                      │
│     The UI system is pre-installed and vendored into your project.          │
│     components/ui/button.tsx  ← Modify directly if needed                   │
│     components/ui/table.tsx   ← Full customization available                │
│                                                                              │
│  2. Extend primitives                                                       │
│     Create custom primitives that wrap internal components                  │
│     Register in primitive registry                                          │
│                                                                              │
│  4. Override theme tokens                                                   │
│     tailwind.config.js  ← Full Tailwind control                             │
│     globals.css         ← CSS variable overrides                            │
│                                                                              │
│  5. Add custom CSS                                                          │
│     Tailwind utilities                                                      │
│     Custom CSS classes                                                      │
│                                                                              │
│  FILE STRUCTURE:                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ my-app/                                                              │   │
│  │ ├── components/                                                      │   │
│  │ │   ├── ui/               ← Pre-installed components (editable)     │   │
│  │ │   │   ├── button.tsx                                              │   │
│  │ │   │   ├── table.tsx                                               │   │
│  │ │   │   └── ...                                                     │   │
│  │ │   └── primitives/       ← Mozaiks primitives (editable)           │   │
│  │ │       ├── data-table.tsx                                          │   │
│  │ │       └── ...                                                     │   │
│  │ ├── tailwind.config.js    ← Full control                            │   │
│  │ └── brand/theme_config.json ← Theme config (optional)              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Platform Mode (Mozaiks Hosted)

In Platform mode, customization is **constrained and safe**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PLATFORM MODE                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  USERS CAN:                                                                 │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│  1. Configure theme via brand/theme_config.json                              │
│     theme:                                                                  │
│       primary: blue                                                         │
│       variant: modern                                                       │
│       font: inter                                                           │
│                                                                              │
│  2. Select from predefined primitives                                       │
│     Use DataTable, Form, Card, etc.                                         │
│     Configure their properties                                              │
│                                                                              │
│  3. Upload branding assets                                                  │
│     Logo                                                                    │
│     Favicon                                                                 │
│                                                                              │
│  USERS CANNOT:                                                              │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│  ❌ Modify components directly                                               │
│  ❌ Add custom CSS                                                           │
│  ❌ Install npm packages                                                     │
│  ❌ Access Tailwind config                                                   │
│  ❌ Create new component types                                               │
│                                                                              │
│  WHY:                                                                       │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                              │
│  • Visual consistency across all apps                                       │
│  • Security (no arbitrary code execution)                                   │
│  • Performance (optimized bundle)                                           │
│  • Maintainability (centralized updates)                                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Rendering Pipeline

### Schema to Component Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      RENDERING PIPELINE                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  INPUT: Page Schema                                                         │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ content:                                                               │ │
│  │   - type: DataTable                                                    │ │
│  │     columns:                                                           │ │
│  │       - key: name                                                      │ │
│  │         label: Name                                                    │ │
│  │     data_source: "data.contacts"                                       │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  STEP 1: Validate Schema                                                    │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ • Check type is registered primitive                                   │ │
│  │ • Validate properties against primitive schema                         │ │
│  │ • Resolve data binding references                                      │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  STEP 2: Resolve Data Bindings                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ data_source: "data.contacts"                                           │ │
│  │           ↓                                                            │ │
│  │ resolved_data: [{ name: "John", ... }, ...]                            │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  STEP 3: Apply Theme Tokens                                                 │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ • Load theme config from brand/theme_config.json                       │ │
│  │ • Generate CSS variables                                               │ │
│  │ • Inject font imports                                                  │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  STEP 4: Map to Primitive Component                                         │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ type: "DataTable"                                                      │ │
│  │           ↓                                                            │ │
│  │ component: <MozaiksDataTable />                                        │ │
│  │                                                                        │ │
│  │ (Internally composed from base UI components)                          │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  STEP 5: Render with Props                                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ <MozaiksDataTable                                                      │ │
│  │   columns={[{ key: "name", label: "Name" }]}                           │ │
│  │   data={[{ name: "John", ... }]}                                       │ │
│  │   selection="multi"                                                    │ │
│  │   onAction={handleAction}                                              │ │
│  │ />                                                                     │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                    │                                         │
│                                    ▼                                         │
│  OUTPUT: Rendered HTML                                                      │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ <div class="mozaiks-data-table" style="--primary: ...">               │ │
│  │   <table>...</table>                                                   │ │
│  │ </div>                                                                 │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Primitive Component Implementation

```typescript
// packages/ui/src/primitives/data-table.tsx
// Internal implementation - AI never sees this

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
// ... other component imports

interface DataTableProps {
  columns: ColumnDef[];
  data: any[];
  selection?: "none" | "single" | "multi";
  pagination?: boolean;
  pageSize?: number;
  search?: boolean;
  actions?: Action[];
  onAction?: (action: Action, selection: any[]) => void;
}

export function MozaiksDataTable({
  columns,
  data,
  selection = "none",
  pagination = true,
  pageSize = 20,
  search = true,
  actions = [],
  onAction,
}: DataTableProps) {
  // Internal implementation using base components
  // This is the "black box" that primitives map to

  return (
    <div className="mozaiks-data-table">
      {/* Toolbar with search and actions */}
      {(search || actions.length > 0) && (
        <div className="flex items-center justify-between py-4">
          {search && (
            <Input
              placeholder="Search..."
              className="max-w-sm"
              // ...
            />
          )}
          {actions.length > 0 && (
            <div className="flex gap-2">
              {actions.map(action => (
                <Button
                  key={action.id}
                  onClick={() => onAction?.(action, selectedRows)}
                  disabled={action.requiresSelection && selectedRows.length === 0}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Table */}
      <Table>
        <TableHeader>
          <TableRow>
            {selection !== "none" && (
              <TableHead className="w-12">
                <Checkbox />
              </TableHead>
            )}
            {columns.map(column => (
              <TableHead key={column.key}>{column.label}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {/* ... */}
        </TableBody>
      </Table>

      {/* Pagination */}
      {pagination && (
        <div className="flex items-center justify-end py-4">
          {/* Pagination controls */}
        </div>
      )}
    </div>
  );
}
```

---

## 7. Icon System

### Icon Set

```yaml
# Icons are referenced by name, mapped to actual icons internally

icons:
  set: lucide  # Using Lucide icons (pre-installed)

  # AI can only use these icon names
  allowed:
    # Navigation
    - home
    - menu
    - chevron-left
    - chevron-right
    - arrow-left
    - arrow-right

    # Actions
    - plus
    - minus
    - edit
    - trash
    - save
    - download
    - upload
    - refresh
    - search
    - filter

    # Status
    - check
    - x
    - alert-circle
    - info
    - help-circle

    # Objects
    - user
    - users
    - settings
    - file
    - folder
    - image
    - calendar
    - clock
    - mail
    - message-circle
    - phone
    - link
    - star

    # Data
    - chart-bar
    - chart-line
    - chart-pie
    - trending-up
    - trending-down

    # AI-specific
    - sparkles
    - wand
    - bot
    - brain

    # Misc
    - sun
    - moon
    - eye
    - eye-off
    - lock
    - unlock
    - bell
    - bookmark
    - heart
    - share
    - external-link
```

---

## Summary

### Design System Principles

| Principle | Description |
|-----------|-------------|
| **Abstraction** | AI works with primitives, never raw components |
| **Controlled Vocabulary** | Finite set of primitives and theme options |
| **Theme Tokens** | Configuration maps to CSS variables |
| **Two Modes** | OSS (full control) vs Platform (constrained) |
| **Visual Consistency** | Same primitives render consistently everywhere |

### Key Constraints

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         DESIGN SYSTEM CONSTRAINTS                          │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  1. AI generates SCHEMAS, not code                                         │
│  2. Schemas use PRIMITIVES from a controlled set                           │
│  3. Theming uses PREDEFINED OPTIONS, not raw values                        │
│  4. UI components are INTERNAL - never exposed to AI                       │
│  5. Fonts are SELECTED from presets, not arbitrary                         │
│  6. Icons are REFERENCED by name, not imported                             │
│  7. Platform mode is MORE constrained than OSS mode                        │
│                                                                            │
└───────────────────────────────────────────────────────────────────────────┘
```
