# UI System Specification

**Status:** Specification
**Created:** 2026-04-06
**Depends on:** MODULAR_ARCHITECTURE_V2.md

This document specifies the non-chat UI system for Mozaiks applications.

---

## Overview

Mozaiks has **two distinct UI systems**:

1. **Chat UI** - Agent interaction interface (ephemeral, conversation-based)
2. **App UI** - Persistent application interface (pages, navigation, data views)

This document focuses on the **App UI** system.

Agent UI tools and workflow transition components are separate surface contracts:

- **Agent UI tools** use event-driven React components in chat/artifact flows.
- **Transition UI** uses router/session components between workflows or workflow-sequence phases.

Those surfaces may share the same primitive/design foundation, but they are not app-page schemas and should not be modeled as such.

---

## Design Philosophy

### Core Constraints

1. **AI generates structured definitions, NOT raw code**
   - No arbitrary React/Vue/HTML generation
   - UI definitions are declarative schemas
   - Schemas map to a controlled component library

2. **UI primitives, NOT raw components**
   - Predefined building blocks (Page, DataTable, Form, Card, etc.)
   - Consistent design language across all apps
   - AI can compose primitives but cannot invent new ones

3. **Clear separation between UI and logic**
   - UI definitions describe WHAT to render
   - Modules handle data fetching
   - Workflows handle AI-driven actions
   - Runtime orchestrates connections

### Shipped Primitive Source Of Truth

This spec describes the app-page primitive model. The **live shipped** page primitive subset is defined by code:

- `chat-ui/src/ui/page-renderer/PrimitiveRegistry.js`
- `chat-ui/src/ui/primitives/index.js`
- `mozaiksai/core/workflow/ui_primitives.py`

If this document lists a broader primitive family than the runtime currently ships, generators must use the shipped registry rather than the larger conceptual catalog.

### Producer Contracts

Mozaiks has three distinct producer-side UI contracts. They share primitives, but they do not use the same backend API:

1. **Interactive agent UI tools**
   - Backend producer uses `mozaiksai.core.workflow.ui_tools.use_ui_tool(...)`
   - Declarative manifest type is `UI_Tool`
   - Frontend surface is mounted by `WorkflowUIRouter`
   - React component receives shell props like `payload`, `onResponse`, `onCancel`, `ui_tool_id`, `eventId`, `workflowName`, `componentId`

2. **One-way artifact/status emitters**
   - Backend producer uses `mozaiksai.core.workflow.ui_tools.emit_ui_surface(...)` when no user response is required
   - Declarative manifest type is `UI_Surface`
   - This is the sanctioned helper for read-only artifacts and status surfaces
   - Use this for artifacts such as read-only previews, diagrams, or progress/status surfaces

3. **Workflow transition UI**
   - Declared in routing/session metadata, not app-page schemas
   - Resolved by `SessionRouter` and shell transition rendering, not by agent UI tool helpers

`Agent_Tool` is the third declarative type and means backend-only logic with no UI metadata.

Generators and handwritten workflows should not mix these contracts. If the surface expects a user response, treat it as an interactive UI tool and use `use_ui_tool(...)`. If it is fire-and-forget, use `emit_ui_surface(...)` rather than calling transport directly.

### Deterministic Workflow UI Resolution

Workflow UI surfaces are resolved through registered workflow barrels, not ad hoc dynamic imports:

- each workflow that declares `UI_Tool`/`UI_Surface` entries in `tools.yaml` must provide `ui/index.js`
- each declared `ui.component` must be exported by that workflow `ui/index.js`
- the shell registers workflow components under both:
  - namespaced key: `WorkflowName:ComponentName` (primary deterministic lookup)
  - plain key: `ComponentName` (secondary fallback)
- `WorkflowUIRouter` resolves workflow-scoped keys first (`workflow:component`), then plain keys

This keeps generated and handwritten workflows modular while preventing cross-workflow component collisions.

### App Customization Ladder

Persistent app pages should stay declarative by default. The intended customization ladder is:

1. **Define `ExperienceSpec` using page archetypes**
   - Start from higher-level page types such as dashboard, entity-list, entity-detail, feed, thread, or analytics overview.
2. **Compile archetypes into shipped primitives**
   - Use richer primitive config, better layout choices, and theme overrides in `app.yaml` + `pages/*.yaml`.
3. **Add a platform-owned primitive, page pattern, or explicit custom slot**
   - If the shipped page registry is insufficient, developers extend `PrimitiveRegistry` and the generator/validator contracts once, then future apps can use that new surface declaratively.
4. **Use React only for non-page surfaces**
   - Freeform React belongs to agent UI tools and transition UI, not to generated persistent application pages.

This keeps app pages modular, testable, and runtime-agnostic while still allowing the platform to grow its visual vocabulary over time.

### Theme And Shell Artifact Boundary

Persistent app pages compile alongside three shell-facing artifacts:

1. `brand/theme_config.json`
  - owns visual tokens, shared primitives, and semantic `ui.chat`, `ui.shell`, and `ui.page` spacing/sizing tokens

2. `config/shell.json`
  - owns header, profile, notifications, and footer content/behavior only

3. `config/asset_manifest.json`
  - owns reusable media inventory metadata (asset id, path/url, source/provenance, usage hints)

The page renderer and shell should consume those artifacts at runtime.
Generated page schemas should not inline ad hoc shell spacing/chrome content or ad hoc media inventories, and freeform React still belongs to agent UI tools and transition UI.

---

## 1. UI Primitives

### Primitive Categories

The diagram below is the **target primitive model**, not a guarantee that every primitive is already shipped in the current runtime.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            UI PRIMITIVES                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  LAYOUT                    DATA DISPLAY              FORMS                  │
│  ├── Page                  ├── DataTable             ├── Form               │
│  ├── Section               ├── DetailView            ├── FormField          │
│  ├── Card                  ├── List                  ├── Input              │
│  ├── Grid                  ├── Stat                  ├── Select             │
│  ├── Stack                 ├── Chart                 ├── Checkbox           │
│  ├── Tabs                  ├── Timeline              ├── DatePicker         │
│  └── Modal                 └── KPI                   └── FileUpload         │
│                                                                              │
│  NAVIGATION                ACTIONS                   FEEDBACK               │
│  ├── NavBar                ├── Button                ├── Alert              │
│  ├── Sidebar               ├── ActionMenu            ├── Toast              │
│  ├── Breadcrumb            ├── Toolbar               ├── Progress           │
│  ├── NavItem               └── ActionButton          ├── Skeleton           │
│  └── TabBar                                          └── Empty              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Primitive Schema Example

```python
# packages/ui/src/mozaiks_ui/primitives/schema.py

from typing import Literal, List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field


class UINode(BaseModel):
    """Base for all UI nodes."""
    id: Optional[str] = None
    className: Optional[str] = None
    style: Optional[Dict[str, Any]] = None


class DataTableColumn(BaseModel):
    """Column definition for DataTable."""
    key: str
    label: str
    type: Literal["text", "number", "date", "badge", "avatar", "actions"] = "text"
    sortable: bool = False
    width: Optional[str] = None
    format: Optional[str] = None  # e.g., "currency", "percentage"


class DataTable(UINode):
    """Data table primitive."""
    type: Literal["DataTable"] = "DataTable"
    columns: List[DataTableColumn]
    data_source: str            # e.g., "module:contacts:list"
    selection: Literal["none", "single", "multi"] = "none"
    pagination: bool = True
    page_size: int = 20
    search: bool = True
    actions: List["Action"] = []


class FormField(BaseModel):
    """Field definition for Form."""
    name: str
    label: str
    type: Literal["text", "email", "number", "select", "checkbox", "textarea", "date"] = "text"
    required: bool = False
    placeholder: Optional[str] = None
    options: Optional[List[Dict[str, str]]] = None  # For select
    validation: Optional[Dict[str, Any]] = None


class Form(UINode):
    """Form primitive."""
    type: Literal["Form"] = "Form"
    fields: List[FormField]
    submit_action: str          # e.g., "module:contacts:create"
    submit_label: str = "Submit"
    layout: Literal["vertical", "horizontal", "grid"] = "vertical"


class Action(BaseModel):
    """Action definition."""
    id: str
    label: str
    icon: Optional[str] = None
    variant: Literal["primary", "secondary", "danger", "ghost"] = "primary"
    trigger: "ActionTrigger"
    requires_selection: bool = False
    confirm: Optional[str] = None  # Confirmation message


class ActionTrigger(BaseModel):
    """What happens when action is triggered."""
    type: Literal["module", "workflow", "navigate", "modal"]
    target: str                 # module:name:action, workflow:name, /path, modal:name
    params: Dict[str, Any] = {}


class Stat(UINode):
    """Stat/KPI display."""
    type: Literal["Stat"] = "Stat"
    label: str
    value_source: str           # e.g., "module:analytics:get_count"
    format: Optional[str] = None
    trend_source: Optional[str] = None
    icon: Optional[str] = None


class Card(UINode):
    """Card container."""
    type: Literal["Card"] = "Card"
    title: Optional[str] = None
    subtitle: Optional[str] = None
    children: List["UIComponent"] = []
    actions: List[Action] = []


class Section(UINode):
    """Section layout."""
    type: Literal["Section"] = "Section"
    title: Optional[str] = None
    description: Optional[str] = None
    children: List["UIComponent"] = []
    collapsible: bool = False


class Grid(UINode):
    """Grid layout."""
    type: Literal["Grid"] = "Grid"
    columns: int = 3
    gap: str = "4"
    children: List["UIComponent"] = []


# Union of all components
UIComponent = Union[DataTable, Form, Stat, Card, Section, Grid, ...]
```

---

## 2. Page Definitions

Pages are the top-level UI unit. They declare layout, components, and data bindings.

### Page Schema

```yaml
# pages/contacts.yaml

name: contacts_page
title: Contacts
description: Manage your contacts

# Navigation integration
nav:
  label: Contacts
  icon: users
  order: 10
  badge_source: "module:contacts:count_new"  # Dynamic badge

# Page layout
layout: sidebar  # full | sidebar | split

# Page-level data loading
data:
  contacts:
    source: "module:contacts:list"
    params:
      status: "active"
  stats:
    source: "module:contacts:get_stats"

# Page content
content:
  - type: Section
    title: Overview
    children:
      - type: Grid
        columns: 4
        children:
          - type: Stat
            label: Total Contacts
            value_source: "data.stats.total"
            icon: users

          - type: Stat
            label: New This Week
            value_source: "data.stats.new_this_week"
            icon: trending-up
            trend_source: "data.stats.new_trend"

          - type: Stat
            label: Active
            value_source: "data.stats.active"
            icon: check-circle

          - type: Stat
            label: Engagement Rate
            value_source: "data.stats.engagement_rate"
            format: percentage

  - type: Section
    title: Contact List
    children:
      - type: DataTable
        data_source: "data.contacts"
        selection: multi
        columns:
          - key: name
            label: Name
            type: text
            sortable: true
          - key: email
            label: Email
            type: text
          - key: company
            label: Company
            type: text
          - key: status
            label: Status
            type: badge
          - key: created_at
            label: Added
            type: date
            format: relative

        actions:
          - id: create_contact
            label: New Contact
            icon: plus
            trigger:
              type: modal
              target: create_contact_modal

          - id: analyze_contacts
            label: Analyze with AI
            icon: sparkles
            requires_selection: true
            trigger:
              type: workflow
              target: contact_analyzer

          - id: export_contacts
            label: Export
            icon: download
            requires_selection: true
            trigger:
              type: module
              target: contacts:export

# Modals
modals:
  create_contact_modal:
    title: Create Contact
    size: medium
    content:
      - type: Form
        fields:
          - name: name
            label: Name
            type: text
            required: true
          - name: email
            label: Email
            type: email
            required: true
          - name: company
            label: Company
            type: text
          - name: notes
            label: Notes
            type: textarea
        submit_action: "module:contacts:create"
        submit_label: Create Contact
        on_success: close_and_refresh
```

### Page Interface

```python
# packages/ui/src/mozaiks_ui/pages/schema.py

from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel


class NavConfig(BaseModel):
    """Navigation configuration."""
    label: str
    icon: Optional[str] = None
    order: int = 99
    badge_source: Optional[str] = None
    group: Optional[str] = None


class DataBinding(BaseModel):
    """Data source binding."""
    source: str                 # "module:name:action" or "workflow:name"
    params: Dict[str, Any] = {}
    refresh_interval: Optional[int] = None  # Seconds


class ModalDefinition(BaseModel):
    """Modal dialog definition."""
    title: str
    size: Literal["small", "medium", "large", "full"] = "medium"
    content: List[Dict[str, Any]]  # UI components


class PageDefinition(BaseModel):
    """Complete page definition."""
    name: str
    title: str
    description: Optional[str] = None

    # Navigation
    nav: Optional[NavConfig] = None

    # Layout
    layout: Literal["full", "sidebar", "split"] = "full"

    # Data
    data: Dict[str, DataBinding] = {}

    # Content
    content: List[Dict[str, Any]]  # UI components

    # Modals
    modals: Dict[str, ModalDefinition] = {}

    # Access control
    roles: List[str] = []  # Empty = all authenticated users
```

---

## 3. Data Bindings

### Binding Syntax

```
module:<module_name>:<action>
workflow:<workflow_name>
data.<key>
context.<property>
selection.<property>
```

### Examples

```yaml
# Static module call
data_source: "module:contacts:list"

# Module call with parameters
data_source: "module:contacts:list"
params:
  status: "active"
  limit: 50

# Reference to page data
value_source: "data.stats.total"

# Reference to selection
trigger:
  type: module
  target: contacts:delete
  params:
    ids: "selection.ids"  # Selected row IDs

# Reference to context
filter_source: "context.user.team_id"
```

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA FLOW IN UI                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PAGE LOAD                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ 1. Page definition loaded                                              │ │
│  │       │                                                                │ │
│  │       ▼                                                                │ │
│  │ 2. Data bindings resolved                                              │ │
│  │       data:                                                            │ │
│  │         contacts:                                                      │ │
│  │           source: "module:contacts:list"   → Fetch contacts           │ │
│  │         stats:                                                         │ │
│  │           source: "module:contacts:stats"  → Fetch stats              │ │
│  │       │                                                                │ │
│  │       ▼                                                                │ │
│  │ 3. Data injected into page context                                     │ │
│  │       page.data = {                                                    │ │
│  │         contacts: [...],                                               │ │
│  │         stats: {...}                                                   │ │
│  │       }                                                                │ │
│  │       │                                                                │ │
│  │       ▼                                                                │ │
│  │ 4. Components render with data                                         │ │
│  │       <DataTable data_source="data.contacts" />                        │ │
│  │       <Stat value_source="data.stats.total" />                         │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  USER ACTION                                                                │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ 1. User clicks "Analyze with AI" (with contacts selected)              │ │
│  │       │                                                                │ │
│  │       ▼                                                                │ │
│  │ 2. Action trigger resolved                                             │ │
│  │       trigger:                                                         │ │
│  │         type: workflow                                                 │ │
│  │         target: contact_analyzer                                       │ │
│  │       │                                                                │ │
│  │       ▼                                                                │ │
│  │ 3. Workflow triggered with selection                                   │ │
│  │       POST /api/actions/execute                                        │ │
│  │       {                                                                │ │
│  │         action_id: "analyze_contacts",                                 │ │
│  │         selection: ["id_1", "id_2"],                                   │ │
│  │         context: {...}                                                 │ │
│  │       }                                                                │ │
│  │       │                                                                │ │
│  │       ▼                                                                │ │
│  │ 4. Result displayed (modal, toast, navigation)                         │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Component Registry

### Built-in Components

```python
# packages/ui/src/mozaiks_ui/components/registry.py

from typing import Dict, Type


COMPONENT_REGISTRY: Dict[str, Type] = {
    # Layout
    "Page": PageComponent,
    "Section": SectionComponent,
    "Card": CardComponent,
    "Grid": GridComponent,
    "Stack": StackComponent,
    "Tabs": TabsComponent,
    "Modal": ModalComponent,

    # Data Display
    "DataTable": DataTableComponent,
    "DetailView": DetailViewComponent,
    "List": ListComponent,
    "Stat": StatComponent,
    "Chart": ChartComponent,
    "Timeline": TimelineComponent,
    "KPI": KPIComponent,

    # Forms
    "Form": FormComponent,
    "FormField": FormFieldComponent,

    # Actions
    "Button": ButtonComponent,
    "ActionMenu": ActionMenuComponent,
    "Toolbar": ToolbarComponent,

    # Feedback
    "Alert": AlertComponent,
    "Progress": ProgressComponent,
    "Skeleton": SkeletonComponent,
    "Empty": EmptyComponent,
}


def get_component(type_name: str) -> Type:
    """Get component class by type name."""
    if type_name not in COMPONENT_REGISTRY:
        raise ValueError(f"Unknown component type: {type_name}")
    return COMPONENT_REGISTRY[type_name]


def register_component(type_name: str, component_class: Type):
    """Register a custom component (for extensions)."""
    COMPONENT_REGISTRY[type_name] = component_class
```

### Component Mapping (Internal)

> **Note:** This mapping is an internal implementation detail. AI and app definitions work only with Mozaiks primitives - they never reference the underlying base components.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                PRIMITIVE → BASE COMPONENT MAPPING (INTERNAL)                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Mozaiks Primitive          →    Base Component(s)                          │
│  ─────────────────────────────────────────────────────────────              │
│  DataTable                  →    Table, DataTable (tanstack)                │
│  Form                       →    Form (react-hook-form), Input, etc.        │
│  Card                       →    Card, CardHeader, CardContent              │
│  Button                     →    Button                                     │
│  Modal                      →    Dialog, DialogContent                      │
│  Stat                       →    Card + custom styling                      │
│  Tabs                       →    Tabs, TabsList, TabsTrigger                │
│  Select                     →    Select, SelectContent, SelectItem          │
│  Alert                      →    Alert, AlertDescription                    │
│  Progress                   →    Progress                                    │
│  Badge                      →    Badge                                       │
│  Skeleton                   →    Skeleton                                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Navigation System

### Navigation Schema

```yaml
# In app.yaml or separate navigation.yaml

navigation:
  # Main navigation items (from pages)
  main:
    - label: Dashboard
      path: /
      icon: home

    - label: Contacts
      path: /contacts
      icon: users
      badge_source: "module:contacts:count_new"

    - label: Deals
      path: /deals
      icon: briefcase

    - label: AI Assistant
      path: /assistant
      icon: message-circle

  # Secondary/utility navigation
  secondary:
    - label: Settings
      path: /settings
      icon: settings

    - label: Help
      path: /help
      icon: help-circle

  # User menu items
  user:
    - label: Profile
      path: /profile
    - label: Preferences
      path: /preferences
    - label: Sign Out
      action: logout
```

### Navigation Builder

```python
# packages/ui/src/mozaiks_ui/navigation/builder.py

class NavigationBuilder:
    """Builds navigation from app definition."""

    def __init__(self, app_definition: AppDefinition):
        self._app_def = app_definition

    def build(self) -> Dict[str, Any]:
        """Build complete navigation structure."""
        main_items = []

        # Add page-defined navigation
        for page in self._app_def.pages:
            if page.nav:
                main_items.append({
                    "label": page.nav.label,
                    "path": page.path,
                    "icon": page.nav.icon,
                    "order": page.nav.order,
                    "badge_source": page.nav.badge_source,
                    "group": page.nav.group,
                })

        # Sort by order
        main_items.sort(key=lambda x: x["order"])

        return {
            "main": main_items,
            "secondary": self._get_secondary_nav(),
            "user": self._get_user_nav(),
        }

    async def resolve_badges(self, context: RequestContext) -> Dict[str, Any]:
        """Resolve dynamic badge values."""
        badges = {}
        for item in self._get_all_items():
            if item.get("badge_source"):
                value = await self._resolve_badge(item["badge_source"], context)
                badges[item["path"]] = value
        return badges
```

---

## 6. Chat UI vs App UI

### Clear Separation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      CHAT UI vs APP UI                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  CHAT UI (Agent Interaction)           APP UI (Persistent Interface)        │
│  ─────────────────────────────         ─────────────────────────────        │
│                                                                              │
│  Purpose:                              Purpose:                              │
│  • Conversational AI interaction       • Data browsing and management        │
│  • Ephemeral message threads           • Persistent state and views          │
│  • Agent-driven flow                   • User-driven navigation              │
│                                                                              │
│  Components:                           Components:                           │
│  • Message bubbles                     • Pages                               │
│  • Typing indicators                   • Tables, forms, charts               │
│  • Artifact displays                   • Navigation                          │
│  • Tool execution status               • Toolbars and actions                │
│                                                                              │
│  Data:                                 Data:                                 │
│  • Conversation history                • Module data (CRUD)                  │
│  • Agent state                         • Real-time updates                   │
│  • Generated artifacts                 • User preferences                    │
│                                                                              │
│  Rendering:                            Rendering:                            │
│  • WebSocket streaming                 • HTTP requests                       │
│  • Real-time updates                   • Polling or SSE                      │
│  • Markdown/code rendering             • Component-based                     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Interaction Between UIs

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     UI INTERACTION PATTERNS                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PATTERN 1: App UI triggers Chat                                            │
│  ───────────────────────────────                                            │
│                                                                              │
│  User on Contacts page → Clicks "Ask AI about this contact"                 │
│       │                                                                     │
│       ▼                                                                     │
│  Opens Chat slide-over with context                                         │
│       {                                                                     │
│         context_type: "contact",                                            │
│         context_id: "contact_123",                                          │
│         initial_message: "Tell me about this contact"                       │
│       }                                                                     │
│       │                                                                     │
│       ▼                                                                     │
│  Agent responds with context-aware information                              │
│                                                                              │
│  PATTERN 2: Chat generates artifacts for App UI                             │
│  ───────────────────────────────────────────────                            │
│                                                                              │
│  User in Chat → Asks "Create a contact for John Doe"                        │
│       │                                                                     │
│       ▼                                                                     │
│  Agent executes module:contacts:create                                      │
│       │                                                                     │
│       ▼                                                                     │
│  Artifact rendered in chat: "Contact Created"                               │
│       │                                                                     │
│       ▼                                                                     │
│  Action: "View in Contacts" → Navigates to App UI                           │
│                                                                              │
│  PATTERN 3: App UI action triggers workflow (no chat)                       │
│  ─────────────────────────────────────────────────────                      │
│                                                                              │
│  User selects contacts → Clicks "Analyze with AI"                           │
│       │                                                                     │
│       ▼                                                                     │
│  Workflow triggered (no chat session)                                       │
│       │                                                                     │
│       ▼                                                                     │
│  Progress modal shown                                                       │
│       │                                                                     │
│       ▼                                                                     │
│  Result displayed in modal or toast                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Shared Context

```python
# Both UIs share the same RequestContext

@dataclass
class RequestContext:
    app_id: str
    user: Optional[UserPrincipal]
    execution_mode: str

    # UI-specific context
    ui_context: Optional[UIContext] = None


@dataclass
class UIContext:
    """Context from App UI when triggering Chat or workflows."""
    source_page: Optional[str] = None
    source_component: Optional[str] = None
    selection: Optional[List[str]] = None
    filters: Optional[Dict[str, Any]] = None
```

---

## 7. AI-Generated UI

### Generation Constraints

AI can generate page definitions, but with strict constraints:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      AI UI GENERATION RULES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ AI CAN:                                                                  │
│  • Compose pages using existing primitives                                  │
│  • Configure DataTable columns                                              │
│  • Define Form fields                                                       │
│  • Arrange layouts (Grid, Section, Tabs)                                    │
│  • Define navigation structure                                              │
│  • Create data bindings                                                     │
│  • Define actions and triggers                                              │
│                                                                              │
│  ❌ AI CANNOT:                                                               │
│  • Generate raw React/HTML/CSS code                                         │
│  • Create new component types                                               │
│  • Inject JavaScript                                                        │
│  • Override component styling arbitrarily                                   │
│  • Access browser APIs directly                                             │
│                                                                              │
│  Example of valid AI output:                                                │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ name: generated_page                                                 │   │
│  │ title: Customer Overview                                             │   │
│  │ content:                                                             │   │
│  │   - type: Section                                                    │   │
│  │     title: Summary                                                   │   │
│  │     children:                                                        │   │
│  │       - type: Stat                                                   │   │
│  │         label: Total Customers                                       │   │
│  │         value_source: "module:customers:count"                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  Example of INVALID AI output:                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ <script>alert('hello')</script>                                      │   │
│  │                                                                      │   │
│  │ const CustomComponent = () => <div onClick={...}>...</div>           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Page Generation Schema

```yaml
# Schema for AI-generated pages (validated before rendering)

type: object
required: [name, title, content]
properties:
  name:
    type: string
    pattern: "^[a-z_]+$"

  title:
    type: string
    maxLength: 100

  content:
    type: array
    items:
      $ref: "#/definitions/UIComponent"

definitions:
  UIComponent:
    type: object
    required: [type]
    properties:
      type:
        type: string
        enum: [Section, Card, Grid, DataTable, Form, Stat, ...]

      # Type-specific properties validated per component
```

---

## 8. Rendering Architecture

### Server-Side vs Client-Side

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      RENDERING ARCHITECTURE                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  OPTION 1: Server-Side Rendering (SSR)                                      │
│  ─────────────────────────────────────                                      │
│                                                                              │
│  Browser → Request → Runtime → Render Page → HTML Response                  │
│                                                                              │
│  Pros: Fast initial load, SEO, no JS bundle                                 │
│  Cons: Less interactivity, full reload on navigation                        │
│                                                                              │
│  Use for: Simple CRUD apps, public pages                                    │
│                                                                              │
│  ───────────────────────────────────────────────────────────────────────── │
│                                                                              │
│  OPTION 2: Client-Side Rendering (SPA)                                      │
│  ─────────────────────────────────────                                      │
│                                                                              │
│  Browser → Load React App → Fetch Page Definition → Render                  │
│                                                                              │
│  Pros: Rich interactivity, smooth navigation                                │
│  Cons: Larger bundle, slower initial load                                   │
│                                                                              │
│  Use for: Complex apps, heavy interactivity                                 │
│                                                                              │
│  ───────────────────────────────────────────────────────────────────────── │
│                                                                              │
│  OPTION 3: Hybrid (Recommended)                                             │
│  ─────────────────────────────────                                          │
│                                                                              │
│  SSR for initial load + Hydrate + SPA navigation                            │
│                                                                              │
│  1. Server renders initial HTML (fast first paint)                          │
│  2. Client hydrates (attaches event handlers)                               │
│  3. Subsequent navigation is SPA (fetch JSON, render)                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Page Renderer

```python
# packages/ui/src/mozaiks_ui/pages/renderer.py

class PageRenderer:
    """Renders page definitions to React components or HTML."""

    def __init__(self, component_registry: Dict[str, Type]):
        self._registry = component_registry

    def render_to_json(self, page: PageDefinition, data: Dict[str, Any]) -> Dict:
        """Render page to JSON (for client-side rendering)."""
        return {
            "page": page.dict(),
            "data": data,
            "components": self._prepare_components(page.content, data),
        }

    def render_to_html(self, page: PageDefinition, data: Dict[str, Any]) -> str:
        """Render page to HTML (for SSR)."""
        # Implementation using Jinja2 or similar
        ...

    def _prepare_components(
        self,
        components: List[Dict],
        data: Dict[str, Any],
    ) -> List[Dict]:
        """Resolve data bindings in components."""
        prepared = []
        for component in components:
            prepared.append(self._resolve_bindings(component, data))
        return prepared

    def _resolve_bindings(self, component: Dict, data: Dict) -> Dict:
        """Replace data source references with actual data."""
        resolved = component.copy()

        for key, value in component.items():
            if isinstance(value, str) and value.startswith("data."):
                path = value[5:]  # Remove "data." prefix
                resolved[key] = self._get_nested(data, path)
            elif isinstance(value, list):
                resolved[key] = [
                    self._resolve_bindings(v, data) if isinstance(v, dict) else v
                    for v in value
                ]
            elif isinstance(value, dict):
                resolved[key] = self._resolve_bindings(value, data)

        return resolved
```

---

## 9. UI Events

### UI → System Events

```yaml
# UI interactions emit events for tracking

# Page viewed
type: "UI.PageViewed"
payload:
  page: contacts_page
  path: /contacts
  user_id: user_123

# Action clicked
type: "UI.ActionClicked"
payload:
  action_id: analyze_contacts
  page: contacts_page
  selection_count: 5

# Form submitted
type: "UI.FormSubmitted"
payload:
  form_id: create_contact_form
  page: contacts_page
  field_count: 4

# Navigation
type: "UI.Navigated"
payload:
  from_path: /contacts
  to_path: /deals
```

---

## Summary

### Key Design Decisions

1. **Primitives over raw components** - Controlled vocabulary of UI building blocks
2. **Declarative definitions** - YAML/JSON schemas, not imperative code
3. **Data bindings** - Clear syntax for connecting UI to data sources
4. **Action triggers** - Structured way to connect UI to modules/workflows
5. **Chat and App UI are separate** - But can interact through defined patterns
6. **AI generates schemas** - Not arbitrary code

### Component Checklist

| Category | Primitives |
|----------|------------|
| Layout | Page, Section, Card, Grid, Stack, Tabs, Modal |
| Data | DataTable, DetailView, List, Stat, Chart, Timeline |
| Forms | Form, FormField, Input, Select, Checkbox, DatePicker |
| Actions | Button, ActionMenu, Toolbar |
| Feedback | Alert, Toast, Progress, Skeleton, Empty |
| Navigation | NavBar, Sidebar, Breadcrumb |
