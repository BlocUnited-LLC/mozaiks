# Onboarding And Studio Product Spec

**Status:** Working specification
**Purpose:** Define the Mozaiks product layer that sits between blank scaffold creation and full app generation, using OpenClaw-style onboarding and dashboard lessons without inheriting its architectural weaknesses.
**Depends on:** [agentic-app-generation-strategy.md](./agentic-app-generation-strategy.md), [existing-app-augmentation-strategy.md](./existing-app-augmentation-strategy.md), [PLATFORM_DOGFOODING_SPEC.md](./PLATFORM_DOGFOODING_SPEC.md), [../foundations/admin-system.md](../foundations/admin-system.md)

## Why This Exists

Mozaiks already has strong lower layers:

- a real runtime
- app-bundle contracts
- workflow authoring contracts
- page and shell configuration
- a blank scaffold CLI path

What is still missing is the **product journey layer** between:

- "I created a valid Mozaiks app folder"
- and
- "I am now productively using a local dashboard to build, inspect, refine, and operate the app"

The OpenClaw guide is useful mainly as a signal for what users expect from an agent platform product:

- a clear onboarding wizard after install
- an immediate landing surface after setup
- a visible dashboard/control center
- a way to add capabilities without hand-editing internals
- a way to ask the system to build useful things right away

The Reddit comments are just as important as the guide itself. They highlight failure modes we should explicitly avoid:

- setup complexity pushes users toward untrusted managed shortcuts
- documentation often stops at install and does not explain what to do next
- cost expectations are unclear
- hosted shortcuts blur trust and ownership boundaries
- "install this skill" guidance is often hand-wavy and incomplete

Mozaiks should treat this as a product-design gap, not a runtime gap.

## Core Decision

Mozaiks should separate the user journey into three distinct layers:

1. `mozaiks init`
   - filesystem and contract bootstrap only
   - creates a blank but valid app bundle
   - does not pretend to know the product yet

2. `mozaiks onboard`
   - guided product and environment setup
   - asks the human what kind of app they are building, what should be connected, and how Mozaiks should help first
   - writes or updates only app-owned configuration surfaces

3. `mozaiks studio`
   - launches the local, private Studio create and operator control plane
   - becomes the main place where the user asks agents to build, installs capabilities, reviews diffs, checks runtime health, and iterates

This keeps the current blank-scaffold `init` decision intact while still giving Mozaiks the OpenClaw-like guided experience users expect.

## What To Replicate

These are the useful product ideas to copy from the OpenClaw pattern:

- a guided wizard immediately after install/bootstrap
- a named landing surface instead of dropping users back into a terminal dead-end
- visible runtime status and logs
- a clear next-step experience after setup
- a capability library users can browse and install from a UI
- conversational requests that lead into concrete build work
- a local-first control center that feels like the product, not just a repo

## What Not To Replicate

These are the patterns we should reject:

- assistant-first framing where the product is "your bot" instead of "your app"
- vague claims that installing a skill automatically makes everything work
- ambiguous billing guidance that confuses consumer subscriptions with API billing
- hidden trust boundaries around hosted shortcuts, community scripts, or unreviewed packages
- raw shell power presented as the main UX
- documentation that over-explains setup but under-explains the operating model after setup

## Canonical User Journey

The Mozaiks experience should read as one product journey:

1. Install Mozaiks tooling
2. Create or open an app workspace
3. Run guided onboarding
4. Land in Studio Home
5. Add or enable capabilities
6. Submit the first create request
7. Review proposed changes before write
8. Run, validate, and refine from the same surface

The user should never need to understand internal terms such as runtime, generator, extraction, handoff rules, or output assembly in order to complete that flow.

## Command Model

### `mozaiks init`

Responsibility:

- create a blank bundle scaffold
- establish canonical directories and config files
- optionally seed a starter workflow only when explicitly requested

Rules:

- no product heuristics
- no long wizard
- no fake pages or fake business logic

### `mozaiks onboard`

Responsibility:

- collect app intent in guided form
- configure AI provider, model defaults, auth/admin bootstrap, theme intent, and entry surfaces
- support both `new_app` and `existing_app` onboarding tracks

Outputs should be constrained to app-owned configuration and planning artifacts, not runtime internals.

### `mozaiks studio`

Responsibility:

- open the Studio create/operator control plane for the current workspace
- show dashboard, create requests, installed capabilities, validation, runtime state, and admin access
- keep the operations dashboard local-only and private for now

### `mozaiks add`

Responsibility:

- install deterministic capability bundles into the current workspace
- use Mozaiks vocabulary such as operations, pages, workflows, and capability packs rather than an unbounded "plugin" bucket

### `mozaiks gen`

Responsibility:

- execute build or refinement requests against the current workspace
- compile user intent into typed planning artifacts and then into owned file changes

## Studio Information Architecture

`mozaiks studio` should be the main home for both app authors and operators.

Recommended sections:

### 1. Home

Shows:

- app identity and current mode
- next recommended step
- runtime health summary
- recent runs and recent changes
- current provider and model profile

### 2. Create

Shows:

- freeform request box
- current create plan
- owned paths and acceptance criteria
- approval state
- recent create history

This is where the user says things like:

- build a lead intake flow
- add marketplace listing management
- connect this existing backend first
- redesign the app shell for a finance brand

### 3. Capability Library

Shows:

- shipped capability packs
- installed capability bundles
- trust level and source of each bundle
- dependencies and required external credentials

User-facing label can be "Capabilities" even if the implementation assembles operations, pages, workflows, and config changes underneath.

### 4. Pages And Shell

Shows:

- navigation and shell summary
- installed pages
- theme and brand config
- entry points and app modes

This is where `app/config/shell.json`, `app/brand/theme_config.json`, and related shell surfaces become inspectable instead of hidden files.

### 5. Workflows

Shows:

- installed workflows
- their entry points
- required tools and structured outputs
- recent runs and validation status

### 6. Runtime

Shows:

- logs
- sessions and runs
- token and cost telemetry
- health checks
- validation results

### 7. Admin

Shows:

- local/private admin controls
- framework admin access status
- app-admin settings when available

This should follow the two-tier admin boundary already defined in `admin-system.md`.

## Studio Create Request Flow

The Studio create loop should be artifact-first, not transcript-first.

Canonical flow:

1. user enters a build or refinement request
2. system classifies the request: `new_app`, `existing_app`, or `refinement`
3. system produces typed planning artifacts
4. Studio renders a proposed plan:
   - owned paths
   - affected capabilities
   - approvals required
   - cost and runtime implications
5. user approves
6. execution writes changes
7. Studio shows:
   - diff summary
   - validation results
   - preview or next-step actions

This preserves the core Mozaiks model:

- artifacts first
- deterministic product surfaces first
- agentic augmentation second

## Capability Model

OpenClaw uses the language of skills and tools. Mozaiks should not copy that model directly.

Mozaiks should expose a clearer, more auditable bundle model:

- **operations** for deterministic actions and CRUD
- **pages** for persistent UI
- **workflows** for reasoning and orchestration
- **capability packs** for shipped multi-surface bundles

From the user's perspective, these can still be installed from one place. Internally, the system should keep the deterministic and agentic pieces distinct.

Every installable capability should declare:

- source
- trust level
- files it owns
- external services it requires
- whether it adds operations, pages, workflows, or all three
- whether human approval is required before activation

## Config Ownership

Onboarding and Studio should operate on app-bundle surfaces, not runtime internals.

Primary writable surfaces:

- `app/app.json`
- `app/config/ai.json`
- `app/config/shell.json`
- `app/config/admin.json`
- `app/brand/theme_config.json`
- `app/ui/route_manifest.json`

Optional supporting surfaces:

- local environment or secret references
- starter capability metadata
- build history or local control-plane metadata

They should not invent alternate sources of truth when canonical bundle files already exist.

## Existing-App Track

The Studio flow must support the existing-app adoption ladder already defined elsewhere:

- `Embed`
- `Bridge`
- `Ecosystem`
- `Native Migration`

That means `mozaiks onboard` should offer two high-level starting paths:

1. build a new Mozaiks-first app
2. connect and augment an existing app

For the existing-app path, the onboarding experience should ask product questions first and technical questions second.

Good questions:

- what does the existing app already do well
- what should Mozaiks help with first
- what must remain host-owned
- where should the first Mozaiks surface live

Bad questions as the first screen:

- give me your OpenAPI URL
- paste your backend base path
- pick your transport contract before we know the use case

## Guardrails

The Reddit feedback makes several guardrails non-optional.

### Cost Clarity

Studio should show:

- provider and model currently selected
- whether usage is API-billed
- estimated cost sensitivity before long-running builds
- token/cost telemetry after runs

### Trust Clarity

Studio should show:

- whether a capability is first-party, local, or third-party
- whether source code is present locally
- whether activation grants filesystem, network, or external API power

### Post-Setup Guidance

After onboarding, the user should never face a dead-end state.

Home should always recommend a next step such as:

- install your first capability
- connect your existing backend
- make your first build request
- validate the workspace
- open the local admin panel

### No Magic Installs

If a capability still requires:

- API keys
- provider-specific setup
- external credentials
- manual browser pairing

the UI must say so before install or activation.

## What Success Looks Like

This product layer is correct when:

1. a user can go from blank scaffold to first meaningful output in one guided path
2. the user always knows what to do immediately after setup
3. the system stays app-centric, not bot-centric
4. deterministic product surfaces remain explicit and inspectable
5. costs and trust boundaries are visible before surprises happen
6. the same Studio model works for both new apps and existing-app augmentation
7. local/private operation remains the default until remote platform behavior is intentionally productized

## Implementation Sequence

Recommended sequence:

1. Keep `mozaiks init` as the blank scaffold command
2. Add `mozaiks onboard` as the guided setup layer
3. Add `mozaiks studio` as the local/private control plane
4. Move first-run guidance from terminal-only copy into Studio Home
5. Add capability library and install metadata
6. Add build-plan approval UX before writes
7. Add existing-app onboarding branch inside the same Studio flow

This sequence improves the product experience without requiring a runtime rewrite.
