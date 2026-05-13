# Onboarding And Build Product Spec

**Status:** Working specification
**Purpose:** Define the Mozaiks product layer that sits between blank scaffold creation and full app generation, using OpenClaw-style onboarding and dashboard lessons without inheriting its architectural weaknesses.
**Depends on:** [agentic-app-generation-strategy.md](./agentic-app-generation-strategy.md), [existing-app-augmentation-strategy.md](./existing-app-augmentation-strategy.md), [PLATFORM_DOGFOODING_SPEC.md](./PLATFORM_DOGFOODING_SPEC.md), [../foundations/admin-system.md](../foundations/admin-system.md)

Terminology note:

- `Studio` remains the current internal host and command name.
- customer-facing UX should prefer `Apps`, `Usage`, `Health`, `Billing`, `Hosting`, and
   `Integrations`
- `Build` is the workflow-owned agent sequence for create/refinement, not a
   standalone persistent console page
- the long-term visible model is a Workspace Console plus an App Console, not a
  top-level product area called `Studio`

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
    - launches the local, private workspace console and the workflow-owned build
       sequence through the current Studio host
   - becomes the main place where the user asks agents to build, installs
     capabilities, reviews diffs, checks runtime health, and iterates

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
4. Land in `Apps`
5. Add or enable capabilities
6. Create or open an app record
7. Launch the build workflow sequence
8. Submit the first build request through that workflow sequence
9. Review proposed changes before write
10. Run, validate, and refine from the same surface

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
- support both `greenfield_app` and `brownfield_app` onboarding tracks

Outputs should be constrained to app-owned configuration and planning artifacts, not runtime internals.

### `mozaiks studio`

Responsibility:

- open the current workspace console and the workflow-owned build/refinement
   entrypoints for the active workspace
- show apps, build requests, installed capabilities, validation, runtime state,
  and admin access
- keep operator-only operations views local-only and private for now

### `mozaiks add`

Responsibility:

- install deterministic capability bundles into the current workspace
- use Mozaiks vocabulary such as operations, pages, workflows, and capability packs rather than an unbounded "plugin" bucket

### `mozaiks gen`

Responsibility:

- execute build or refinement requests against the current workspace
- compile user intent into typed planning artifacts and then into owned file changes

## Workspace Console And Workflow-Owned Build Information Architecture

`mozaiks studio` is the current command and host entry point, but the visible
production-ready model should be:

- `Apps` as the workspace-level landing area
- `Usage`, `Health`, `Billing`, and `Hosting` as workspace portfolio summaries
- `Overview`, `Health`, `Users`, `Integrations`, `Usage`, `Billing`, and `Hosting` as
   app-console sections
- the build/refinement experience launched into the workflow-owned agent
   sequence from create and app-context actions, not exposed as a persistent
   standalone React page

Recommended production-ready sections:

### 1. Apps

Shows:

- the app directory for the current workspace
- create-app entry
- next recommended step
- runtime health summary
- recent runs and recent changes
- current provider and model profile

### 2. Usage

Shows:

- workspace usage signals across multiple workflows
- input and output token posture
- totals and averages at the top of the surface
- recent portfolio activity that is already production-ready to expose

### 3. Health

Shows:

- overall app and workspace health posture
- runtime readiness and workflow reliability
- hosting and integration blockers that need intervention

### 4. Billing

Shows:

- revenue posture
- recurring value
- commercial readiness by app
- finance follow-up signals that are already live

### 5. Hosting

Shows:

- managed hosting posture in a provider-style control center
- domains, email, DNS, SSL, storage, and backup posture
- release handoff state that is already production-ready to expose

### 6. App Console

Shows:

- overview and current app posture
- health and app reliability posture
- users and participation
- integrations and credential posture
- usage, billing, and hosting summaries for the current app

### 7. Workflow-Owned Build Sequence

Shows:

- the active build or refinement request
- current create/refinement plan
- owned paths and acceptance criteria
- approval state, diffs, and validation outcomes
- sequence-driven next steps managed by agents rather than a standalone page

This is where the user says things like:

- build a lead intake flow
- add marketplace listing management
- connect this existing backend first
- redesign the app shell for a finance brand

Capabilities, deeper workflow tooling, and operator/admin controls may still
exist, but they should not be documented as current production-ready persistent
console pages unless they are actually shipped.

## Build Workflow Request Flow

The build request loop should be artifact-first, not transcript-first.

Canonical flow:

1. user enters a build or refinement request
2. system classifies the request: `greenfield_app`, `brownfield_app`, or `refinement`
3. system produces typed planning artifacts
4. the workflow-owned build sequence renders a proposed plan:
   - owned paths
   - affected capabilities
   - approvals required
   - cost and runtime implications
5. user approves
6. execution writes changes
7. the workflow-owned build sequence shows:
   - diff summary
   - validation results
   - preview or next-step actions

This preserves the core Mozaiks model:

- artifacts first
- deterministic product surfaces first
- agentic augmentation second

## Canonical Create Journeys

Before additional routing or selector work lands, Mozaiks should treat the
workflow-owned build sequence as three different user journeys that happen to
share one entry box.

The important rule is: the user should feel one product, but the system should not
pretend all create requests mean the same kind of work.

### 1. `greenfield_app`

This is the Mozaiks-native build path.

Use this when the user is asking Mozaiks to create the first canonical app bundle.

Typical asks:

- build a CRM for startup fundraising
- create a marketplace app for equipment rentals
- generate an operations dashboard for internal staff

Default posture:

- Mozaiks owns the generated app artifacts
- Mozaiks owns the first-pass planning and generation flow
- hosted deployments should default to Mozaiks-managed runtime, MongoDB, and secret management unless the user explicitly chooses an advanced external path

Canonical internal stages today:

- concept and value definition
- theme and brand capture
- product design docs
- workflow and app generation

The user-facing questions for this path should be about:

- what they are building
- how guided vs autonomous the build should be
- whether data should use the default managed MongoDB path, a known external MongoDB, or a deferred setup choice

The user should not be asked discovery-style questions about an existing host app unless they explicitly indicate a bring-your-own system.

### 2. `brownfield_app`

This is the augmentation and adoption-planning path.

Use this when the user already has a product and wants Mozaiks to augment it,
embed into it, bridge to it, or selectively migrate parts of it later.

Typical asks:

- connect our current app and add an AI workspace
- let agents read and update selected host capabilities
- embed Mozaiks into our existing frontend

Default posture:

- the host product already owns core behavior
- discovery comes before generation
- adoption should default to the smallest useful move: embed or bridge before ecosystem or migration

Canonical internal stages today:

- existing product discovery
- capability mapping
- adoption-level recommendation
- augmentation artifact assembly

The user-facing questions for this path should be about:

- where the current app lives
- whether Mozaiks is embedding, bridging, or planning later migration
- what should stay host-owned vs become AI-accessible
- how Mozaiks should authenticate against the host system

This path should not begin by asking which new database Mozaiks should create.
In many cases, the correct first move is no direct database ownership at all.
It may be API bridge first, host capability bridge first, or a Mozaiks-owned
embedded workspace with no backend bridge yet.

The companion strategy for this path is [existing-app-augmentation-strategy.md](./existing-app-augmentation-strategy.md).

### 3. `refinement`

This is the change-management path for a Mozaiks-owned artifact that already exists.

Use this when the user is not starting from zero and is not discovering an unknown
external product. They are modifying a known app bundle or workflow bundle that
Mozaiks already generated or already treats as the active artifact baseline.

Typical asks:

- add a new field to the contacts flow
- redesign the dashboard layout
- add a reporting feature to the current app
- change the app concept enough that the build must be reconsidered

Default posture:

- start from the persisted artifact version
- classify the change first
- re-enter at the smallest valid boundary instead of replaying the full intake journey

Canonical refinement classifications:

- `patch`
- `design`
- `feature`
- `core`

This path is not discovery. It already has a known baseline.

### Journey Boundary Rules

These boundaries should stay explicit across routing, prompts, and UI:

1. `greenfield_app` is initial creation of a Mozaiks-native app.
2. `brownfield_app` is discovery and augmentation of a host system that already exists.
3. `refinement` is scoped change-management against a known artifact version.

Practical implications:

- `brownfield_app` should route into discovery before planning generation
- `refinement` should not re-enter the same intake selectors as `greenfield_app`
- database setup questions differ by journey:
   - `greenfield_app`: choose managed MongoDB vs external MongoDB vs defer
   - `brownfield_app`: decide host access mode first, not new database ownership first
   - `refinement`: ask only when the requested change actually affects data/storage

The system may still present all three from one build entrypoint, but the
classifier and transition graph must treat them as different request kinds with
different next questions.

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

Onboarding and the workflow-owned build sequence should operate on app-bundle surfaces, not runtime internals.

Primary writable surfaces:

- `app/app.json`
- `app/config/ai.json`
- `app/config/shell.json`
- `app/brand/theme_config.json`
- `app/ui/route_manifest.json`

Admin bootstrap lives in `app/app.json` `admins`; there is no separate
`app/config/admin.json` surface.

Optional supporting surfaces:

- local environment or secret references
- starter capability metadata
- build history or local control-plane metadata

They should not invent alternate sources of truth when canonical bundle files already exist.

## Existing-App Track

The build workflow sequence must support the existing-app adoption ladder already defined elsewhere:

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

The workflow-owned build sequence and the production console should show:

- provider and model currently selected
- whether usage is API-billed
- estimated cost sensitivity before long-running builds
- token/cost telemetry after runs

### Trust Clarity

The workflow-owned build sequence should show:

- whether a capability is first-party, local, or third-party
- whether source code is present locally
- whether activation grants filesystem, network, or external API power

### Post-Setup Guidance

After onboarding, the user should never face a dead-end state.

The `Apps` landing area or active app console should always recommend a
next step such as:

- install your first capability
- connect your existing backend
- make your first build request
- validate the workspace
- open the relevant production-ready app or workspace section

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
6. the same workspace-console and Build model works for both new apps and
   existing-app augmentation
7. local/private operation remains the default until remote platform behavior is intentionally productized

## Implementation Sequence

Recommended sequence:

1. Keep `mozaiks init` as the blank scaffold command
2. Add `mozaiks onboard` as the guided setup layer
3. Keep `mozaiks studio` as the current local/private host entry command, but
   align visible UX around `Apps` and `Build`
4. Move first-run guidance from terminal-only copy into the `Apps` landing area
5. Add capability library and install metadata
6. Add build-plan approval UX before writes
7. Add existing-app onboarding branch inside the same Build flow

This sequence improves the product experience without requiring a runtime rewrite.
