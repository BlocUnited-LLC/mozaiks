# Mozaiks Architecture - Documentation Index

**Status:** Master Index
**Created:** 2026-04-06
**Updated:** 2026-04-06

---

## Current Source of Truth

The current canonical architecture is the layered host model documented in
[`../../../ARCHITECTURE.md`](../../../ARCHITECTURE.md):

- `mozaiksai/hosts/runtime.py` — runtime substrate
- `mozaiksai/hosts/platform.py` — headless app host
- `mozaiksai/hosts/studio.py` — local/private builder host
- `mozaiksai/hosts/mozaiks.py` — hosted Mozaiks product host
- current repo layout includes `factory_app/app/` as the first-party Console
  app bundle served by the Studio host; hosted product workspaces are external
  to this repo

The package-splitting documents are retained as future packaging proposals, not
as the current source of truth.

**Start here:**
- [../../../ARCHITECTURE.md](../../../ARCHITECTURE.md) - canonical architecture
- [../foundations/distribution-and-workspace-model.md](../foundations/distribution-and-workspace-model.md) - canonical target distribution and workspace model
- [../foundations/canonical-app-structure.md](../foundations/canonical-app-structure.md) - active app root and hosted workspace layout
- [agentic-app-generation-strategy.md](./agentic-app-generation-strategy.md) - app-generation architecture
- [appgenerator-output-assembly-contract.md](./appgenerator-output-assembly-contract.md) - AppGenerator bundle output contract
- [agentgenerator-output-assembly-contract.md](./agentgenerator-output-assembly-contract.md) - AgentGenerator workflow output contract

Historical documents and package proposals are useful context, but they are
superseded wherever they conflict with `ARCHITECTURE.md`.

---

## Future Package Naming Conventions

If/when the repo is split into publishable packages, different contexts should
use different naming conventions for the same packages:

| Context | core | ai | modules | runtime | ui | cli |
|---------|------|----|---------|---------|----|-----|
| **File paths** | `packages/core/` | `packages/ai/` | `packages/modules/` | `packages/runtime/` | `packages/ui/` | `packages/cli/` |
| **Python imports** | `from mozaiks_core import ...` | `from mozaiks_ai import ...` | `from mozaiks_modules import ...` | `from mozaiks_runtime import ...` | - | - |
| **PyPI packages** | `mozaiks-core` | `mozaiks-ai` | `mozaiks-modules` | `mozaiks-runtime` | `@mozaiks/ui` | `mozaiks-cli` |
| **npm packages** | - | - | - | - | `@mozaiks/ui` | - |

**Rules:**
- File paths use **slashes**: `packages/core/`
- Python imports use **underscores**: `mozaiks_core`
- PyPI/npm packages use **hyphens**: `mozaiks-core`

---

## Glossary

| Term | Definition |
|------|------------|
| **Module** | Data operation unit (CRUD, domain events).|
| **Workflow** | AI-orchestrated multi-step process using AG2. |
| **Primitive** | UI building block (DataTable, Form, Card, etc.). |
| **Runtime** | The composition layer that orchestrates ai + modules + ui. |
| **Executor** | Interface that packages implement (WorkflowExecutor, ModuleExecutor). |
| **Event** | Normalized message with type, payload, tenant context. |
| **Page** | YAML-defined UI layout composed of primitives. |
| **App** | A complete application workspace rooted at `app/`, with `app.json` plus pages, modules, workflows, config, and brand assets. |

---

## Quick Reference

| I want to... | Read this |
|--------------|-----------|
| **Start implementing (coding agents)** | [AGENT_IMPLEMENTATION_PROMPT.md](./AGENT_IMPLEMENTATION_PROMPT.md) ⭐⭐ |
| **Track implementation progress** | [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md) ⭐⭐ |
| **Review future package-splitting proposal** | [MODULAR_ARCHITECTURE_V2.md](./MODULAR_ARCHITECTURE_V2.md) |
| **Review historical package-agent guidance** | [AGENTS_MD_V2.md](./AGENTS_MD_V2.md) |
| **Understand event-driven execution** | [Event System](../foundations/event-system.md) ⭐⭐ |
| **Understand how workflows are triggered** | [WORKFLOW_TRIGGERS_SPEC.md](./WORKFLOW_TRIGGERS_SPEC.md) ⭐ |
| **Understand post-generation refinement routing** | [REFINEMENT_CONTROL_PLANE_SPEC.md](./REFINEMENT_CONTROL_PLANE_SPEC.md) ⭐⭐ |
| **Understand both UI systems (App UI vs Agentic UI)** | [ui-systems.md](./ui-systems.md) ⭐⭐ |
| **Understand workflow routing transitions (between-workflow routing)** | [workflow-routing-gates.md](./workflow-routing-gates.md) ⭐⭐ |
| **Understand the App UI system (primitives, pages, schemas)** | [UI_SYSTEM_SPEC.md](./UI_SYSTEM_SPEC.md) ⭐ |
| **Understand the canonical app-generation model** | [agentic-app-generation-strategy.md](./agentic-app-generation-strategy.md) ⭐⭐⭐ |
| **Understand the canonical distribution/workspace model** | [../foundations/distribution-and-workspace-model.md](../foundations/distribution-and-workspace-model.md) ⭐⭐⭐ |
| **Track the app-generation implementation plan** | [agentic-app-generation-checklist.md](./agentic-app-generation-checklist.md) ⭐⭐⭐ |
| **Define the onboarding wizard and Build product flow** | [onboarding-and-build-product-spec.md](./onboarding-and-build-product-spec.md) ⭐⭐⭐ |
| **Understand how AppGenerator assembles app bundles** | [appgenerator-output-assembly-contract.md](./appgenerator-output-assembly-contract.md) ⭐⭐ |
| **Understand the existing-app adoption path** | [existing-app-augmentation-strategy.md](./existing-app-augmentation-strategy.md) ⭐⭐⭐ |
| **Understand the design system & theming** | [DESIGN_SYSTEM_SPEC.md](./DESIGN_SYSTEM_SPEC.md) ⭐ |
| **Understand the tool model** | [TOOLS_SPEC.md](./TOOLS_SPEC.md) ⭐ |
| **Understand runtime responsibilities** | [RUNTIME_SPEC.md](./RUNTIME_SPEC.md) ⭐ |
| **Understand OSS vs Platform strategy** | [PLATFORM_FRONTEND_STRATEGY.md](./PLATFORM_FRONTEND_STRATEGY.md) ⭐ |
| **Build platform admin (dogfooding)** | [PLATFORM_DOGFOODING_SPEC.md](./PLATFORM_DOGFOODING_SPEC.md) ⭐⭐ |
| **Understand YAML extraction from agents** | [STRUCTURED_OUTPUT_EXTRACTION_SPEC.md](./STRUCTURED_OUTPUT_EXTRACTION_SPEC.md) ⭐ |
| Understand event contracts | [Event Contracts](../foundations/event-contracts.md) |
| Use the Platform SDK | [PLATFORM_SDK_SPEC.md](../PLATFORM_SDK_SPEC.md) |
| Implement the admin dashboard | [ADMIN_DASHBOARD_SPEC.md](../ADMIN_DASHBOARD_SPEC.md) |
| See the original unified architecture (historical) | [MOZAIKS_UNIFIED_ARCHITECTURE.md](../MOZAIKS_UNIFIED_ARCHITECTURE.md) |
| See the original implementation plan (historical) | [IMPLEMENTATION_PHASES.md](../IMPLEMENTATION_PHASES.md) |

---

## Document Summary

### [MODULAR_ARCHITECTURE_V2.md](./MODULAR_ARCHITECTURE_V2.md) (Future Packaging Proposal)

A future package-splitting proposal aligned to the current layered-host system:

- **Design Philosophy:** Composition, not merging
- **Package Structure:** core, ai, modules, runtime, ui, cli
- **Core Interfaces:** EventBus, Executor, RequestContext, Storage protocols
- **Layered Hosts:** runtime, platform, Studio, and hosted-product boundaries
- **App Contract:** canonical app-root bundle built around `app/app.json`
- **Execution Modes:** ai-only, modules-only, full
- **Dependency Rules:** Strict boundaries (ai ❌ modules)
- **Migration Plan:** From current state to new architecture
- **Risks:** Known challenges and mitigations

**Key Decisions:**
- AI runtime and modules NEVER import each other
- Runtime is the only package that can compose both
- Each package can run standalone
- Events for async communication
- Context injection for security

### [AGENTS_MD_V2.md](./AGENTS_MD_V2.md) (Historical Package-Agent Guidance)

Agent instructions for the modular architecture:

- Repository structure diagram
- Dependency graph with allowed/forbidden imports
- Import rules by package
- How AI and modules communicate (runtime composition, HTTP fallback, events)
- Package responsibilities (what goes where)
- Decision tree for placing new code
- Red flags to watch for
- Testing requirements

**Copy this file to:** `mozaiks/.claude/AGENTS.md`

### ⭐⭐ [AGENT_IMPLEMENTATION_PROMPT.md](./AGENT_IMPLEMENTATION_PROMPT.md) (START HERE)

Comprehensive prompt for coding agents to begin implementation:

- **Architecture overview** with visual dependency graph
- **Implementation order** (Phase 1-6: core → ai → modules → ui → runtime → cli)
- **Key files to create** for each package
- **Critical constraints** (event-first, no transcript parsing, immediate event emission)
- **Event-first orchestration** rules (what's right, what's wrong)
- **Event layer separation** (domain, runtime execution, control-plane)
- **Normalized event vocabulary** (process, task, artifact, chat, runtime)
- **Testing checklist** before committing
- **Self-check questions** before writing code
- **Document references** for deep dives

**Use this document to onboard new coding agents.**

### ⭐⭐ [IMPLEMENTATION_CHECKLIST.md](./IMPLEMENTATION_CHECKLIST.md) (TRACK PROGRESS)

Comprehensive implementation checklist for the modular architecture:

- **What exists in mozaiks today:** mozaiksai/, chat-ui/, mozaiks_cli/, factory_app/workflows/
- **What needs to be created:** packages/core/, packages/modules/, packages/runtime/
- **Implementation order:** 6 phases (core → ai → modules → runtime → ui → cli)
- **Key files to create:** Priority-ordered file lists for each package
- **Deprecation plan:** legacy donor repo archival steps
- **Verification checklist:** Acceptance criteria for each phase

**Use this document to track implementation progress and know what to build next.**

---

## Specification Documents

### ⭐⭐ [Event System](../foundations/event-system.md) (CRITICAL)

Event-first execution model - the foundational orchestration pattern:

- **Core Principle:** Runtime is event-first, not output-first
- **Event Layer Separation:** Domain events, Runtime execution events, Control-plane events
- **Normalized Event Vocabulary:** Complete event families (process, task, artifact, chat, runtime)
- **MFJ Event Flow:** How decomposition triggers fan-out via explicit events
- **Build Pipeline Events:** Required events for all builder workflows
- **Revision Events:** Control-plane events for change routing
- **Adapter Responsibility:** Real-time event iteration, not black-box execution
- **Source of Truth:** Events, not transcript or structured outputs
- **E2B Integration:** Preview updates via artifact events

### ⭐ [WORKFLOW_TRIGGERS_SPEC.md](./WORKFLOW_TRIGGERS_SPEC.md)

Complete specification for how workflows are triggered:

- **Trigger Types:** Chat, Event, Route, Action, Schedule
- **Event Triggers:** How system events automatically start workflows
- **Route Triggers:** HTTP endpoints mapped to workflow execution
- **Action Triggers:** UI buttons that invoke workflows
- **Trigger Resolution:** How the runtime resolves and dispatches triggers
- **Condition Expressions:** Filtering events before triggering
- **Runtime Execution Events:** MFJ orchestration via explicit events
- **Revision Events:** Control-plane events for change routing
- **Trigger Events:** Observability for all trigger executions

### ⭐⭐ [REFINEMENT_CONTROL_PLANE_SPEC.md](./REFINEMENT_CONTROL_PLANE_SPEC.md) (POST-GENERATION RE-ENTRY)

Authoritative contract for how Mozaiks handles changes after initial generation:

- **Generation vs Refinement:** Why first-pass compilation and post-generation edits are separate modes
- **Change Classification:** `patch`, `design`, `feature`, `core`
- **Re-entry Matrix:** Which upstream phase owns which class of change
- **E2B Contract:** Workspace and validator, never source of truth
- **Refinement Units:** Why `owned_paths` and `acceptance_criteria` matter
- **Prompt Implications:** How generator prompts stay clean while emitting refinement-ready metadata
- **Persistence Model:** Change requests, artifact versions, and refinement sessions

### ⭐⭐ [workflow-routing-gates.md](./workflow-routing-gates.md) (WORKFLOW ROUTING LAYER)

Universal workflow-routing layer — transitions and workflow sequences that sit above individual workflows:

- **Three routing layers** — agent routing vs workflow routing vs workflow sequencing
- **Data model** — `WorkflowTransition`, `TransitionUIBinding`, `TransitionOption`, `GlobalPackGraph` v3
- **`extension_registry.json` schema** — workflows, dependencies, transitions, and optional sequences
- **Shell wire-up contract** — navigation routes point directly at transition ids
- **AgentGenerator integration** — semantic rules for when/how to emit routing config

### ⭐⭐ [ui-systems.md](./ui-systems.md) (READ FIRST FOR ANY UI WORK)

Canonical separation of the two UI systems — the most common source of confusion:

- **App UI vs Agentic UI:** side-by-side comparison table
- **App UI:** AppPageSchema → PageRenderer pipeline, file locations, rules
- **Agentic UI:** bidirectional agent-driven components, Python tool + React component pattern
- **Primitives:** how the same vocabulary is used differently in each system
- **Decision guide:** "where does this UI belong?"

### ⭐ [UI_SYSTEM_SPEC.md](./UI_SYSTEM_SPEC.md)

Specification for the non-chat UI system:

- **UI Primitives:** DataTable, Form, Card, Stat, etc.
- **Page Definitions:** YAML schema for declaring pages
- **Data Bindings:** How UI connects to modules and workflows
- **Component Registry:** Built-in components (pre-installed)
- **Navigation System:** How navigation is built from page definitions
- **Chat UI vs App UI:** Clear separation and interaction patterns
- **AI-Generated UI:** Constraints on what AI can/cannot generate

### ⭐ [DESIGN_SYSTEM_SPEC.md](./DESIGN_SYSTEM_SPEC.md)

Complete design system and theming specification:

- **UI Abstraction Layer:** 4-layer pipeline (Schema → Primitive → Component → HTML)
- **Primitive Registry:** Complete vocabulary of UI building blocks
  - Layout: Page, Section, Card, Grid, Stack, Divider, Spacer
  - Data: DataTable, List, DetailView, Timeline, Tree
  - Dashboard: Stat, StatGroup, Chart, ProgressRing, Sparkline
  - Form: Form, FormField, FormSection, FormActions
  - Input: TextInput, Select, Checkbox, DatePicker, FileUpload, etc.
  - Overlay: Modal, Drawer, Popover, Tooltip, DropdownMenu
  - Action: Button, IconButton, ButtonGroup, ActionBar
  - Feedback: Alert, Toast, Banner, Progress, Spinner, Skeleton
  - Navigation: NavBar, Sidebar, Breadcrumb, Tabs, Stepper
  - Content: Text, Heading, Badge, Avatar, Icon, Image, Code
  - Chat: ChatContainer, MessageList, Message, MessageInput, Artifact
- **Theming System:** Structured theme configuration (primary, variant, radius, appearance, font, density)
- **Typography System:** Font family definitions and role mappings
- **Customization Modes:** OSS/CLI (full control) vs Platform (constrained)
- **Rendering Pipeline:** How schemas become rendered components
- **AI Constraints:** What AI can and cannot generate (schemas only, never raw React)

### ⭐ [TOOLS_SPEC.md](./TOOLS_SPEC.md)

Complete specification for the tool model:

- **Tool Categories:** System (module), Integration (external), AI (LLM)
- **Tool Definition:** YAML and Python implementation patterns
- **Tool Registration:** How tools are loaded and registered
- **Tool Execution:** Context, validation, security
- **System Tools:** Thin wrappers over module executor
- **Tool Constraints:** What tools should and should NOT do
- **Built-in Tools:** Memory, UI artifacts, utilities
- **Tool Security:** Consent, rate limits, role restrictions

### ⭐ [RUNTIME_SPEC.md](./RUNTIME_SPEC.md)

Complete specification for runtime responsibilities:

- **Core Responsibilities:** 8 areas the runtime manages
- **Application Loading:** Load sequence, validation, mode detection
- **Executor Registry:** How executors are initialized and coordinated
- **Request Routing:** Resolution priority, dispatch logic
- **Context Management:** Auth extraction, context building, injection
- **Event Coordination:** Routing events, forwarding to platform
- **Event-First Orchestration:** React to explicit events, not transcript parsing
- **Lifecycle Management:** Startup sequence, health checks, shutdown
- **Single Entry Point:** All requests flow through runtime

### ⭐ [PLATFORM_FRONTEND_STRATEGY.md](./PLATFORM_FRONTEND_STRATEGY.md)

Platform and frontend strategy for OSS vs hosted:

- **Frontend Asset Provisioning:** What's pre-bundled vs configurable vs customizable
- **Font & Theming Strategy:** Token resolution, font handling by mode (platform/OSS/E2B)
- **UI Generation Boundaries:** 4-layer stack (components → primitives → pages → app)
- **OSS CLI Story:** Complete command set, project structure, customization levels
- **Hosted Platform Advantages:** Enhanced intelligence, managed services, team features
- **E2B Runtime Model:** Pre-provisioned template, fast preview startup, no per-preview installs
- **Key Principles:**
  - Agents describe and configure, never provision infrastructure
  - OSS = portable, self-hostable base
  - Platform = better intelligence + managed experience
  - Fonts/themes are tokens resolved at runtime

### ⭐⭐⭐ [onboarding-and-build-product-spec.md](./onboarding-and-build-product-spec.md)

Product blueprint for the missing layer between blank scaffolding and productive app-building:

- **Three-layer journey:** `mozaiks init` for blank scaffold, `mozaiks onboard` for guided setup, `mozaiks studio` for the local/private builder host
- **OpenClaw lessons translated to Mozaiks:** copy the wizard and dashboard pattern, reject vague skill magic and unclear trust/cost boundaries
- **Console information architecture:** Apps, Overview, Build, Pages and Shell, Workflows, Runtime, Admin
- **Command boundaries:** keep `init` structure-first, move product questions into onboarding, keep generation and installation explicit
- **Capability model:** operations, pages, workflows, and capability packs instead of one generic plugin bucket
- **Guardrails:** cost visibility, trust metadata, no dead-end post-setup state, no public admin by default

**Use this document when shaping the next CLI and Console product surface.**

### ⭐⭐ [PLATFORM_DOGFOODING_SPEC.md](./PLATFORM_DOGFOODING_SPEC.md) (CRITICAL)

Blueprint for building the platform admin dashboard using mozaiks:

- **Dogfooding principle:** If we can't build our own admin with mozaiks, customers can't build their apps
- **Platform Modules:** Python modules wrapping .NET service APIs (users, apps, governance, billing)
- **Admin Pages:** YAML-defined pages for dashboard, user management, app approvals
- **Module definitions:** Complete YAML + Python implementation examples
- **Page examples:** Dashboard with stats, DataTables, modals, forms
- **Migration path:** SDK enhancement → modules → pages → deployment
- **Success criteria:** Functional admin, architecture proven, good DX

**Use this document to understand the full dogfooding strategy and implementation plan.**

### ⭐ [STRUCTURED_OUTPUT_EXTRACTION_SPEC.md](./STRUCTURED_OUTPUT_EXTRACTION_SPEC.md)

Specification for extracting agent outputs into consistent YAML configuration files:

- **Extraction Pipeline:** Intent → Agent Output → Extractor → YAML Files
- **Contract Registry:** Maps agent output types to target files (ContextVariablesPlanOutput → context_variables.yaml)
- **Target File Schemas:** Canonical schemas for context_variables.yaml, tools.yaml, orchestrator.yaml, etc.
- **Transformation Rules:** identity, flatten, map, rename, conditional, with_defaults
- **Validation Rules:** required_fields, unique_names, valid_enum, cross_reference, schema
- **Base Extractor Class:** Python implementation pattern for all extractors
- **Module/Page Extraction:** How platform YAML files (module.yaml, page.yaml) are generated
- **File Writer:** Consistent YAML/JSON serialization

**Use this document to ensure consistency when generating YAML files from agent structured outputs.**

---

## Supporting Documents

### [Event Contracts](../foundations/event-contracts.md)

Complete event specification:

- Event envelope schema (JSON Schema)
- Platform-routed events (Commerce, Observability, Learning)
- Local-only events (App domain, Notification, Hosting)
- Event routing configuration
- Event versioning rules

### [PLATFORM_SDK_SPEC.md](../PLATFORM_SDK_SPEC.md)

Python SDK for .NET services:

- Service clients (Hosting, Payment, Apps, etc.)
- Usage examples in workflows
- Testing patterns

### [ADMIN_DASHBOARD_SPEC.md](../ADMIN_DASHBOARD_SPEC.md)

Built-in admin dashboard:

- Dashboard sections (Users, modules, Events, etc.)
- API endpoints
- Data collection hooks

---

## Historical Documents (Superseded by V2)

### [MOZAIKS_UNIFIED_ARCHITECTURE.md](../MOZAIKS_UNIFIED_ARCHITECTURE.md) (Historical)

The original master architecture document covering:

- Repository structure (single canonical mozaiks repo)
- Package architecture (core, modules, ai, bundle)
- Dependency graph and boundary rules
- Event model
- Authentication architecture
- Build time vs run time separation
- Integration with .NET services
- CLI architecture
- Configuration schema

**Key Decisions:**
- One repo, multiple packages
- Hard package boundaries enforced by pyproject.toml
- Events as the communication mechanism between modules and AI
- Platform events for data collection
- Admin dashboard built into mozaiks-modules

### 2. [IMPLEMENTATION_PHASES.md](../IMPLEMENTATION_PHASES.md)

Detailed implementation guide with:

- **Phase 0:** Preparation & Audit (1 week)
- **Phase 1:** Core Package Extraction (2 weeks)
- **Phase 2:** Package Separation (2 weeks)
- **Phase 3:** CLI & Scaffolding (1 week)
- **Phase 4:** Platform Integration (2 weeks)
- **Phase 5:** Admin Dashboard (1 week)
- **Phase 6:** Data Collection Pipeline (1 week)
- **Phase 7:** Migration & Deprecation (1 week)

Each phase includes:
- Detailed task lists
- File structure specifications
- Code examples
- Acceptance criteria
- Checklists

### 3. [AGENTS_MD_TEMPLATE.md](../AGENTS_MD_TEMPLATE.md)

Template for `.claude/AGENTS.md` to guide AI coding agents:

- Package boundary rules (what can import what)
- Red flags to watch for
- How modules and AI communicate
- Decision tree for placing new code
- Common tasks and their locations
- Testing requirements
- Prohibited actions

**Copy this file to:**
- `mozaiks/.claude/AGENTS.md`

### 4. Event Contracts

Complete event specification including:

- Event envelope schema (JSON Schema)
- Platform-routed events:
  - Commerce events (billing)
  - Observability events (metrics)
  - Learning events (AI improvement)
  - Evaluation events (quality)
  - Entitlement events (access control)
- Local-only events:
  - App domain events
  - Notification events
  - Hosting events
- Event routing configuration
- Event versioning rules

### 5. [PLATFORM_SDK_SPEC.md](../PLATFORM_SDK_SPEC.md)

Python SDK for .NET services:

- Package structure
- Base client implementation
- Service clients:
  - HostingClient
  - PaymentClient
  - AppsClient
  - NotificationClient
  - DiscoveryClient
  - GovernanceClient
  - TeamsClient
  - AdminClient
- Usage examples in workflows
- Testing patterns

### 6. [ADMIN_DASHBOARD_SPEC.md](../ADMIN_DASHBOARD_SPEC.md)

Built-in admin dashboard specification:

- Access control
- Dashboard sections:
  - Overview
  - Users
  - Modules
  - Workflows
  - Events
  - Settings
  - Logs
  - Analytics
  - Billing
- API endpoints
- Data collection hooks
- Configuration options

---

## Architecture Summary (V2)

### Repository Structure

```
CURRENT STATE:
├── mozaiksai/hosts/runtime.py              # runtime substrate
├── mozaiksai/hosts/platform.py             # headless app host
├── mozaiksai/hosts/studio.py               # local/private builder host
├── mozaiksai/hosts/mozaiks.py              # hosted product host
├── factory_app/app/            # first-party Console app bundle served by the Studio host
└── external hosted product workspace
    └── app/                    # hosted product app root

TARGET STATE (MODULAR):
├── mozaiks/                    # Modular packages (NOT merged!)
│   ├── packages/
│   │   ├── core/               # Shared primitives (interfaces, types)
│   │   ├── ai/                 # AI workflow execution (standalone)
│   │   ├── modules/            # Module execution (standalone)
│   │   ├── runtime/            # NEW: App composition layer
│   │   ├── ui/                 # UI rendering
│   │   └── cli/                # CLI tool
│   ├── templates/              # App templates
│   └── examples/               # Example apps
└── hosted-product/
    ├── app/                    # hosted product app root
    └── generated/
```

### Package Dependency Graph (V2)

```
                          ┌─────────┐
                          │   cli   │
                          └────┬────┘
                               │
                               ▼
                          ┌─────────┐
                          │ runtime │  ← App composition layer
                          └────┬────┘
                               │
                ┌──────────────┼──────────────┐
                │              │              │
                ▼              ▼              ▼
          ┌─────────┐    ┌─────────┐    ┌─────────┐
          │   ai    │    │   ui    │    │ modules │
          └────┬────┘    └────┬────┘    └────┬────┘
               │              │              │
               └──────────────┼──────────────┘
                              │
                              ▼
                         ┌─────────┐
                         │  core   │  ← Interfaces only
                         └─────────┘

ALLOWED:
✅ cli → runtime
✅ runtime → ai, modules, ui, core
✅ ai → core
✅ modules → core
✅ ui → core

FORBIDDEN:
❌ ai → modules (NEVER)
❌ modules → ai (NEVER)
❌ ai → runtime
❌ modules → runtime
❌ core → anything
```

### Execution Modes

| Mode | Description | Packages Active |
|------|-------------|-----------------|
| `ai_only` | AI workflows without modules | core, ai, runtime |
| `modules_only` | CRUD modules without AI | core, modules, runtime |
| `full` | Complete app with both | core, ai, modules, runtime |

### User Installation Options

| User Needs | Install | Includes |
|------------|---------|----------|
| AI workflows only | `pip install mozaiks-ai` | core + AI runtime |
| Modules/CRUD only | `pip install mozaiks-modules` | core + module runtime |
| Full app runtime | `pip install mozaiks-runtime` | core + ai + modules + runtime |
| CLI | `pip install mozaiks-cli` | project scaffolding |

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    DEPLOYED APPS                                 │
│                                                                  │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐           │
│  │   App A     │   │   App B     │   │   App C     │           │
│  │ (mozaiks)   │   │(mozaiks-ai) │   │(modules)    │           │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘           │
│         │                 │                 │                   │
│         └─────────────────┴─────────────────┘                   │
│                           │                                     │
│                  Platform Events                                │
│         (Commerce.*, Observability.*, Learning.*)               │
│                           │                                     │
└───────────────────────────┼─────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────────┐
│                    MOZAIKS-PLATFORM (.NET)                        │
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ Payment.API  │  │  Analytics   │  │   Learning   │            │
│  │  (billing)   │  │  (metrics)   │  │  (AI impr.)  │            │
│  └──────────────┘  └──────────────┘  └──────────────┘            │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

---

## Getting Started

### For Implementers

1. Read [MODULAR_ARCHITECTURE_V2.md](./MODULAR_ARCHITECTURE_V2.md) for the architecture
2. Use the active repo guidance in [../../../AGENTS.md](../../../AGENTS.md)
3. Implement events per [Event Contracts](../foundations/event-contracts.md)
4. Build SDK per [PLATFORM_SDK_SPEC.md](./PLATFORM_SDK_SPEC.md)
5. Build admin per [ADMIN_DASHBOARD_SPEC.md](./ADMIN_DASHBOARD_SPEC.md)

### For AI Coding Agents

1. Read the active repo guidance in [../../../AGENTS.md](../../../AGENTS.md)
2. **NEVER** import between `ai` and `modules` packages
3. Use runtime composition for cross-system calls
4. Use events for async communication
5. Run boundary checks before committing (`python scripts/check_boundaries.py`)
6. Ask when unsure about where code should go

---

## Key Principles (V2)

### 1. Composition, Not Merging

AI runtime and module runtime remain independent. The App Runtime orchestrates them without merging.

### 2. Package Boundaries Are Law

The dependency graph is immutable. AI ❌ modules, modules ❌ ai. Violations fail CI.

### 3. Each Package Runs Standalone

`mozaiks-ai` can run without modules. `mozaiks-modules` can run without AI. Test them independently.

### 4. Runtime Injects Context

Security context (app_id, user_id, executors) flows from runtime. Packages don't fetch this themselves.

### 5. Events for Async Communication

When modules and AI need to communicate asynchronously, they use events (not imports, not direct calls).

### 6. Platform Owns Billing/Analytics

All monetization and analytics data flows to the hosted product workspace via
platform-routed events.

### 7. .NET Services Subscribe

.NET services consume events and keep their existing implementations. No gutting required.

---

## Implementation Checklist (V2)

### Phase 1: Core Package
- [ ] Create `packages/core/` structure
- [ ] Define Protocol interfaces (EventBus, Executor, Context)
- [ ] Implement Event envelope and types
- [ ] Implement RequestContext
- [ ] Implement Config loader
- [ ] Implement Auth/JWT utilities
- [ ] Implement MongoDB utilities
- [ ] Unit tests passing

### Phase 2: AI Package
- [ ] Create `packages/ai/` structure
- [ ] Migrate AG2 execution from mozaiks
- [ ] Implement WorkflowExecutor conforming to Executor protocol
- [ ] Ensure NO imports from modules
- [ ] Unit tests passing (ai-only mode)

### Phase 3: Modules Package
- [ ] Create `packages/modules/` structure
- [ ] Port selected module contract concepts from legacy donor material
- [ ] Implement ModuleExecutor conforming to Executor protocol
- [ ] Ensure NO imports from ai
- [ ] Unit tests passing (modules-only mode)

### Phase 4: Runtime Package
- [ ] Create `packages/runtime/` structure
- [ ] Implement AppLoader (reads `app/app.json` and discovers bundle families)
- [ ] Implement ExecutorRegistry
- [ ] Implement request router
- [ ] Implement context injection middleware
- [ ] Support all three execution modes
- [ ] Integration tests passing

### Phase 5: UI Package
- [ ] Create `packages/ui/` structure
- [ ] Implement page rendering
- [ ] Implement component registry
- [ ] React frontend surfaces from chat-ui and current app shell

### Phase 6: CLI Package
- [ ] Create `packages/cli/` structure
- [ ] Implement `mozaiks init`
- [ ] Implement `mozaiks dev`
- [ ] Implement `mozaiks build`
- [ ] Create app templates

### Phase 7: Platform Integration
- [ ] Create Python SDK for .NET services
- [ ] Create events ingest endpoint on platform
- [ ] Platform-routed events flowing
- [ ] Admin dashboard working
- [ ] End-to-end tests passing

### Phase 8: Migration
- [ ] Update all documentation
- [ ] Migrate existing platform workflows
- [ ] Deprecate old repos
- [ ] Publish packages to PyPI

---

## Contact

For questions about this architecture, see the conversation that generated it
or create an issue in the hosted product workspace repo that consumes it.
