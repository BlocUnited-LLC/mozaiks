# Mozaiks Implementation Checklist

**Status:** Packaging Proposal Checklist
**Created:** 2026-04-07
**Goal:** Keep `mozaiks` as the single canonical repo and archive legacy donor material

---

## Overview

This checklist is for future package extraction work. It is not the current
runtime/source-of-truth architecture. The current canonical architecture is the
layered host model in `ARCHITECTURE.md`: `mozaiksai/hosts/runtime.py`, `mozaiksai/hosts/platform.py`,
`mozaiksai/hosts/studio.py`, and `mozaiksai/hosts/mozaiks.py`.

If you are working on **agentic app generation** specifically, this checklist is not sufficient by itself. Also follow:

- [agentic-app-generation-strategy.md](./agentic-app-generation-strategy.md)
- [agentic-app-generation-checklist.md](./agentic-app-generation-checklist.md)

Those documents define the canonical artifact model for turning user intent into deterministic product bundles plus optional agentic augmentation.

```
CURRENT STATE:
├── mozaiksai/hosts/runtime.py           # runtime substrate
├── mozaiksai/hosts/platform.py          # headless app host
├── mozaiksai/hosts/studio.py            # local/private builder host
├── mozaiksai/hosts/mozaiks.py           # hosted product host
├── factory_app/app/         # first-party Console app bundle served by the Studio host
└── external hosted product workspace
    └── app/                 # hosted product app root

TARGET STATE:
├── mozaiks/
│   └── packages/
│       ├── core/            # Shared interfaces
│       ├── ai/              # AI workflows (from mozaiksai/)
│       ├── modules/         # Non-AI module execution
│       ├── runtime/         # NEW: app composition
│       ├── ui/              # From chat-ui/
│       └── cli/             # From mozaiks_cli/
└── hosted-product/
    ├── app/                 # hosted product app root
    └── generated/
```

---

## 1. What Exists in Mozaiks (KEEP)

### mozaiksai/ → packages/ai/
**Status:** Mature, keep and refactor

| Component | Location | Keep? | Notes |
|-----------|----------|-------|-------|
| AG2 Orchestration | `core/workflow/` | ✅ | Refactor to use core Event class |
| WebSocket Transport | `core/transport/` | ✅ | Move streaming to packages/ai |
| Auth Adapters | `core/auth/` | ✅ | Move to packages/core |
| Event System | `core/events/` | ⚠️ | Refactor to match `../foundations/event-contracts.md` |
| MongoDB Persistence | `core/data/` | ✅ | Move to packages/core |
| Multi-tenant | `core/multitenant/` | ✅ | Move to packages/core |
| Token Tracking | `core/observability/` | ✅ | Keep in packages/ai |
| Ports/Interfaces | `core/ports/` | ✅ | Move to packages/core |

### chat-ui/ → packages/ui/
**Status:** Mature React library

| Component | Location | Keep? | Notes |
|-----------|----------|-------|-------|
| Chat Components | `src/components/chat/` | ✅ | Core chat UI |
| Action Handling | `src/core/actions/` | ✅ | Tool execution |
| WebSocket Client | `src/services/` | ✅ | API communication |
| State Management | `src/state/` | ✅ | Keep as-is |
| Component Registry | `src/registry/` | ✅ | Dynamic component loading |
| Theme System | `src/theme/` | ✅ | Aligns with DESIGN_SYSTEM_SPEC |

### mozaiks_cli/ → packages/cli/
**Status:** Functional, extend

| Command | Status | Notes |
|---------|--------|-------|
| `mozaiks init` | ✅ | Project scaffolding |
| `mozaiks add` | ✅ | Add features |
| `mozaiks gen` | ✅ | AI code generation |
| `mozaiks dev` | ❌ | Need to add |
| `mozaiks build` | ❌ | Need to add |

### factory_app/workflows/ + app/workflows/
**Status:** Canonical workflow roots, keep clean

- Shared generation-core workflows live under `factory_app/workflows/`
- App-owned workflows live under the active app root's `workflows/`
- `factory_app/app/workflows/` should not be checked in until the factory app has a real app-owned overlay workflow
- No legacy demo/sample workflows in the canonical roots

### Layered FastAPI Hosts
**Status:** Canonical

The active hosts are `mozaiksai/hosts/runtime.py`, `mozaiksai/hosts/platform.py`, `mozaiksai/hosts/studio.py`, and `mozaiksai/hosts/mozaiks.py`.
Keep new server behavior in the lowest correct layer.

---

## 2. What Needs to Be Created (NEW)

### packages/core/ - NEW
**Purpose:** Shared primitives used by all packages

```
packages/core/
├── __init__.py
├── events.py              # Event class per canonical Event Contracts
├── interfaces.py          # Protocol classes (Executor, Storage, etc.)
├── context.py             # RequestContext, TenantContext
├── config.py              # YAML/env config loading
├── storage.py             # Storage abstractions
├── auth/                  # JWT utilities (from mozaiksai/core/auth/)
│   ├── __init__.py
│   └── jwt.py
└── db/                    # MongoDB utilities (from mozaiksai/core/data/)
    ├── __init__.py
    └── mongo.py
```

**Key Files to Create:**

| File | Purpose | Reference |
|------|---------|-----------|
| `events.py` | Event envelope class | `../foundations/event-contracts.md` |
| `interfaces.py` | Executor, EventBus protocols | MODULAR_ARCHITECTURE_V2.md |
| `context.py` | Request/Tenant context | RUNTIME_SPEC.md |

### packages/modules/ - NEW
**Purpose:** Module execution (canonical in-repo module contract)

```
packages/modules/
├── __init__.py
├── loader.py              # Load module.yaml definitions
├── executor.py            # Execute module actions
├── registry.py            # Module registry
└── events.py              # Emit domain events (contacts.created, etc.)
```

**Key Files to Create:**

| File | Purpose | Reference |
|------|---------|-----------|
| `loader.py` | Parse module.yaml files | - |
| `executor.py` | Run CRUD operations | - |
| `events.py` | Domain event emission | `../foundations/event-contracts.md` |

### packages/runtime/ - NEW
**Purpose:** App composition layer on top of the layered host contracts

```
packages/runtime/
├── __init__.py
├── app.py                 # Load app/app.json and discover bundle parts
├── router.py              # Request routing
├── executors.py           # Executor registry
├── middleware.py          # Auth, context injection
├── server.py              # Unified FastAPI server
└── events/
    ├── __init__.py
    ├── bus.py             # Event bus implementation
    └── router.py          # Event routing rules
```

**Key Files to Create:**

| File | Purpose | Reference |
|------|---------|-----------|
| `app.py` | App definition loading | RUNTIME_SPEC.md |
| `router.py` | Request dispatch | RUNTIME_SPEC.md |
| `executors.py` | Register AI/module executors | MODULAR_ARCHITECTURE_V2.md |
| `events/bus.py` | Pub/sub event bus | `../foundations/event-system.md` |

---

## 3. Implementation Order

### Phase 1: packages/core/ (Foundation)
**Priority:** CRITICAL - All other packages depend on this

- [ ] Create `packages/core/` directory structure
- [ ] Implement `Event` class per the canonical event envelope
- [ ] Define `Protocol` interfaces (Executor, EventBus, Storage)
- [ ] Implement `RequestContext` and `TenantContext`
- [ ] Move auth utilities from `mozaiksai/core/auth/`
- [ ] Move MongoDB utilities from `mozaiksai/core/data/`
- [ ] Create `pyproject.toml` for mozaiks-core package
- [ ] Unit tests for Event class and interfaces

**Acceptance Criteria:**
```python
# This must work:
from mozaiks_core import Event, Executor, RequestContext

event = Event.create(
    event_type="process.started",
    payload={"workflow": "AppGenerator"},
    tenant={"app_id": "app_123", "user_id": "user_456"}
)
```

### Phase 2: packages/ai/ (Refactor mozaiksai/)
**Priority:** HIGH - Refactor existing code to use core

- [ ] Create `packages/ai/` directory structure
- [ ] Move workflow execution from `mozaiksai/core/workflow/`
- [ ] Implement event adapter (AG2 → normalized events)
- [ ] Refactor to import Event from packages/core
- [ ] Ensure NO imports from packages/modules
- [ ] Move WebSocket transport
- [ ] Move token tracking
- [ ] Create `pyproject.toml` for mozaiks-ai package
- [ ] Unit tests (ai-only mode)

**Critical Adapter Pattern:**
```python
# packages/ai/adapter.py
async def run_workflow(self, workflow_id: str, input_data: dict):
    workflow = self.load_workflow(workflow_id)

    async for ag2_event in workflow.run_iter():
        # Normalize and yield IMMEDIATELY
        normalized = self._normalize_event(ag2_event)
        yield normalized

        if normalized.type == "process.decomposition_complete":
            yield Event.create(
                event_type="runtime.decomposition_planned",
                payload=normalized.payload
            )
```

**Acceptance Criteria:**
- Can run workflows without packages/modules
- Events emitted follow normalized vocabulary
- No imports from mozaiks_modules

### Phase 3: packages/modules/ (NEW)
**Priority:** HIGH - Replaces legacy plugin/runtime assumptions

- [ ] Create `packages/modules/` directory structure
- [ ] Implement module loader (parse module.yaml)
- [ ] Implement module executor (CRUD operations)
- [ ] Implement module registry
- [ ] Domain event emission on CRUD operations
- [ ] Ensure NO imports from packages/ai
- [ ] Create `pyproject.toml` for mozaiks-modules package
- [ ] Unit tests (modules-only mode)

**Module Definition Schema:**
```yaml
# module.yaml - Complete schema

# ============================================================================
# MODULE IDENTITY
# ============================================================================
name: contacts                     # Module name (used in data_source references)
version: "1.0"                     # Semantic version
description: "Contact management"  # Human-readable description

# ============================================================================
# DATA SOURCE
# ============================================================================
# For local modules (MongoDB)
collection: contacts               # MongoDB collection name

# For external modules (API wrappers like platform.users)
external: true                     # If true, no local collection
service: admin                     # Service identifier for SDK routing

# ============================================================================
# ACTIONS
# ============================================================================
actions:
  # QUERY: Read-only operations (no side effects)
  - name: list
    type: query
    description: "List contacts with filtering"
    params:
      - name: search
        type: string
        optional: true
        description: "Search by name or email"
      - name: status
        type: string
        optional: true
        enum: [active, archived]
      - name: page
        type: integer
        default: 1
      - name: limit
        type: integer
        default: 20
        max: 100
    returns:
      type: paginated
      item_type: Contact

  - name: get
    type: query
    description: "Get contact by ID"
    params:
      - name: id
        type: string
        required: true
    returns:
      type: Contact

  # MUTATION: Write operations (cause side effects, emit events)
  - name: create
    type: mutation
    description: "Create a new contact"
    params:
      - name: name
        type: string
        required: true
        min_length: 1
        max_length: 255
      - name: email
        type: string
        required: true
        format: email
      - name: phone
        type: string
        optional: true
      - name: tags
        type: array
        items: string
        optional: true
    returns:
      type: Contact
    emits:
      - contacts.created

  - name: update
    type: mutation
    description: "Update an existing contact"
    params:
      - name: id
        type: string
        required: true
      - name: name
        type: string
        optional: true
      - name: email
        type: string
        optional: true
        format: email
      - name: phone
        type: string
        optional: true
      - name: tags
        type: array
        items: string
        optional: true
    returns:
      type: Contact
    emits:
      - contacts.updated

  - name: delete
    type: mutation
    description: "Delete a contact"
    params:
      - name: id
        type: string
        required: true
    returns:
      type: boolean
    emits:
      - contacts.deleted

# ============================================================================
# TYPES (for documentation and validation)
# ============================================================================
types:
  Contact:
    id: string
    name: string
    email: string
    phone: string?
    tags: string[]?
    created_at: datetime
    updated_at: datetime

# ============================================================================
# EVENTS (domain events this module emits)
# ============================================================================
events:
  - contacts.created
  - contacts.updated
  - contacts.deleted

# ============================================================================
# ACCESS CONTROL (optional)
# ============================================================================
access:
  default: authenticated           # "public" | "authenticated" | "admin"
  actions:
    delete:
      roles: [admin, owner]        # Only admins or record owner can delete
```

**Action Types:**
| Type | Description | Side Effects | Events |
|------|-------------|--------------|--------|
| `query` | Read-only operation | None | Never |
| `mutation` | Write operation | Yes | Required |

**Parameter Types:**
| Type | Description | Validation |
|------|-------------|------------|
| `string` | Text value | `min_length`, `max_length`, `format`, `enum` |
| `integer` | Whole number | `min`, `max`, `default` |
| `number` | Decimal number | `min`, `max`, `default` |
| `boolean` | True/false | `default` |
| `array` | List of items | `items` (type of items), `min_items`, `max_items` |
| `object` | Nested structure | `properties` |

**Format Validators:**
- `email` - Valid email address
- `url` - Valid URL
- `uuid` - Valid UUID
- `date` - ISO date (YYYY-MM-DD)
- `datetime` - ISO datetime

**Acceptance Criteria:**
- Can run modules without packages/ai
- Domain events emitted per `../foundations/event-contracts.md`
- No imports from mozaiks_ai

### Phase 4: packages/runtime/ (NEW)
**Priority:** HIGH - Composes ai + modules

- [ ] Create `packages/runtime/` directory structure
- [ ] Implement app loader (parse `app/app.json` and discover bundle parts)
- [ ] Implement executor registry
- [ ] Implement request router
- [ ] Implement event bus with routing rules
- [ ] Keep runtime/platform/Studio/Mozaiks host responsibilities separated
- [ ] Context injection middleware
- [ ] Mode detection (ai-only, modules-only, full)
- [ ] Create `pyproject.toml` for mozaiks-runtime package
- [ ] Integration tests (all modes)

**App Definition Example:**
```json
{
  "appName": "my-app",
  "version": "1.0",
  "targets": {
    "web": true
  },
  "startup": {
    "landing_spot": "/"
  }
}
```

**Acceptance Criteria:**
- Can compose ai + modules packages
- Event routing works per `../foundations/event-system.md`
- All three execution modes work

### Phase 5: packages/ui/ (Refactor chat-ui/)
**Priority:** MEDIUM - Refactor existing React library

- [ ] Create `packages/ui/` directory structure
- [ ] Move chat-ui/ components
- [ ] Implement primitive registry per DESIGN_SYSTEM_SPEC.md
- [ ] Implement theme token system
- [ ] Page schema rendering
- [ ] Component registry for dynamic loading
- [ ] Create `package.json` for @mozaiks/ui package
- [ ] Storybook for component documentation

**Acceptance Criteria:**
- Components render from YAML page schemas
- Theme tokens resolve to CSS variables
- Chat UI and App UI work together

### Phase 6: packages/cli/ (Extend mozaiks_cli/)
**Priority:** MEDIUM - Add missing commands

- [ ] Create `packages/cli/` directory structure
- [ ] Keep existing commands (init, add, gen)
- [ ] Add `mozaiks dev` command (start dev server)
- [ ] Add `mozaiks build` command (production build)
- [ ] Add `mozaiks export` command (export to zip/git)
- [ ] Create `pyproject.toml` for mozaiks-cli package
- [ ] CLI tests

**Acceptance Criteria:**
- `mozaiks new` scaffolds a project
- `mozaiks dev` starts development server
- `mozaiks build` creates production bundle

---

## 4. Key Files Summary

### packages/core/ (Create All)
| File | Priority | Spec Reference |
|------|----------|----------------|
| `events.py` | P0 | `../foundations/event-contracts.md` |
| `interfaces.py` | P0 | MODULAR_ARCHITECTURE_V2.md |
| `context.py` | P0 | RUNTIME_SPEC.md |
| `config.py` | P1 | - |
| `auth/jwt.py` | P1 | - |
| `db/mongo.py` | P1 | - |

### packages/ai/ (Refactor from mozaiksai/)
| File | Priority | Source |
|------|----------|--------|
| `adapter.py` | P0 | NEW (critical for event-first) |
| `workflows/loader.py` | P0 | mozaiksai/core/workflow/ |
| `workflows/executor.py` | P0 | mozaiksai/core/workflow/ |
| `agents/factory.py` | P1 | mozaiksai/core/workflow/ |
| `transport/websocket.py` | P1 | mozaiksai/core/transport/ |

### packages/modules/ (Create All)
| File | Priority | Notes |
|------|----------|-------|
| `loader.py` | P0 | Parse module.yaml |
| `executor.py` | P0 | CRUD operations |
| `registry.py` | P1 | Module discovery |
| `events.py` | P1 | Domain event emission |

### packages/runtime/ (Create All + Preserve Layered Hosts)
| File | Priority | Source |
|------|----------|--------|
| `app.py` | P0 | NEW |
| `router.py` | P0 | From platform/runtime route contracts |
| `executors.py` | P0 | NEW |
| `events/bus.py` | P0 | From mozaiksai/core/events/ |
| `middleware.py` | P1 | From runtime auth/transport contracts |
| `server.py` | P1 | From layered host entrypoints |

---

## 5. Legacy Donor Archival Plan

### Do NOT Migrate (Build Fresh Instead)
| Component | Reason |
|-----------|--------|
| `plugin_manager.py` | Renamed to `operation_manager.py` |
| `director.py` | Runtime package replaces this |
| `ai_bridge.py` | Runtime handles AI ↔ modules |
| `subscription_manager.py` | Platform handles entitlements |
| `notifications_manager.py` | Platform handles notifications |
| React frontend | chat-ui already better |

### Reference Only (For Patterns)
| Component | What to Learn |
|-----------|---------------|
| Auth adapters | Multi-mode auth patterns |
| Event bus | Pub/sub patterns |
| WebSocket manager | Connection handling |
| Config loading | JSON config patterns |

### Deprecation Steps
1. [ ] Archive legacy donor repo material (make read-only)
2. [ ] Add deprecation notice to README
3. [ ] Update any docs referencing it
4. [ ] Remove from active development

---

## 6. Verification Checklist

### After Phase 1 (core)
- [ ] `from mozaiks_core import Event` works
- [ ] Event envelope matches JSON schema
- [ ] Unit tests pass

### After Phase 2 (ai)
- [ ] Workflows run in ai-only mode
- [ ] Events emitted follow vocabulary
- [ ] No imports from mozaiks_modules

### After Phase 3 (modules)
- [ ] Modules run in modules-only mode
- [ ] Domain events emitted
- [ ] No imports from mozaiks_ai

### After Phase 4 (runtime)
- [ ] All three modes work (ai-only, modules-only, full)
- [ ] Event routing works
- [ ] AI tools can call modules via context

### After Phase 5 (ui)
- [ ] Components render from schemas
- [ ] Theme tokens work
- [ ] Chat + App UI integrated

### After Phase 6 (cli)
- [ ] `mozaiks new` creates project
- [ ] `mozaiks dev` starts server
- [ ] `mozaiks build` creates bundle

### After Phase 7 (dogfooding) - PROVES THE ARCHITECTURE
- [ ] Platform SDK enhanced with all Admin.API endpoints
- [ ] `platform.users` module works (list, suspend, unsuspend)
- [ ] `platform.apps` module works (pending, approve, reject)
- [ ] `platform.stats` module returns dashboard data
- [ ] Admin dashboard page renders with real data
- [ ] User management page with filtering works
- [ ] App approval workflow completes end-to-end
- [ ] Platform admin runs on mozaiks runtime
- [ ] No hacks or workarounds needed

---

## 7. Phase 7: Platform Dogfooding (CRITICAL)

**Purpose:** Prove the architecture by building the platform's own admin dashboard using mozaiks.

### Platform SDK Enhancement
- [ ] Add `AdminClient.get_users()` with pagination
- [ ] Add `AdminClient.suspend_user()` / `unsuspend_user()`
- [ ] Add `AdminClient.get_pending_apps()`
- [ ] Add `AdminClient.approve_app()` / `reject_app()`
- [ ] Add `AdminClient.get_platform_stats()`
- [ ] Add `AppsClient` methods
- [ ] Add `GovernanceClient` methods
- [ ] Unit tests for all SDK methods

### Platform Modules
- [ ] Create hosted-product module package(s) only if packaging that consumer separately still makes sense
- [ ] Implement `platform.users` module
- [ ] Implement `platform.apps` module
- [ ] Implement `platform.governance` module
- [ ] Implement `platform.billing` module
- [ ] Implement `platform.stats` module
- [ ] All modules emit domain events
- [ ] Auth token passthrough works

### Admin Pages
- [ ] Create `pages/admin/dashboard.yaml`
- [ ] Create `pages/admin/users.yaml`
- [ ] Create `pages/admin/apps/pending.yaml`
- [ ] Create `pages/navigation.yaml`
- [ ] StatGroup renders with real stats
- [ ] DataTable with pagination works
- [ ] Modals open and submit correctly
- [ ] Role-based access enforced

### Integration
- [ ] Create `app/app.json` for platform-admin
- [ ] Deploy platform-admin on mozaiks runtime
- [ ] Actions trigger .NET service calls
- [ ] Performance <500ms page load
- [ ] Error handling works gracefully

**See:** [PLATFORM_DOGFOODING_SPEC.md](./PLATFORM_DOGFOODING_SPEC.md) for full details.

---

## Quick Reference

### Dependency Graph
```
cli → runtime → [ai, modules, ui] → core
```

### Import Rules
```python
# ✅ ALLOWED
from mozaiks_core import Event, Executor
from mozaiks_ai import WorkflowExecutor  # only in runtime
from mozaiks_modules import ModuleExecutor  # only in runtime

# ❌ FORBIDDEN
from mozaiks_modules import ...  # in packages/ai
from mozaiks_ai import ...       # in packages/modules
```

### Event Vocabulary
```
process.*    - Workflow lifecycle
task.*       - Task execution
artifact.*   - Generated content
chat.*       - User interaction
runtime.*    - Orchestration
```
