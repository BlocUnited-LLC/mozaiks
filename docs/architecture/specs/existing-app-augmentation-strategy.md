# Existing App Augmentation Strategy

**Status:** Canonical adoption strategy
**Purpose:** Define how Mozaiks should onboard and augment existing products without pretending every customer needs a rewrite.

## Core Position

For existing apps, Mozaiks should optimize for **augmentation first**, not full regeneration first.

That means:

- start by understanding what product already exists
- expose selected host capabilities to Mozaiks safely
- add Mozaiks surfaces where they create value
- migrate only the surfaces that are worth making Mozaiks-native later

This is the correct default for:

- third-party customers with mature apps
- internal dogfooding for Mozaiks itself

## The Existing-App Adoption Ladder

Mozaiks should treat existing-app onboarding as a staged ladder:

1. `Embed`
   - add a Mozaiks workspace, side panel, or dedicated page
   - no backend bridge yet
   - fastest way to create user-visible value

2. `Bridge`
   - connect selected existing APIs, hubs, or event flows
   - let agents read or write through the host system
   - keep core product behavior host-owned

3. `Ecosystem`
   - bridge the host app and attach Mozaiks ecosystem modules
   - examples: discovery, campaigns, hosting, payments, notifications

4. `Native Migration`
   - selectively rebuild chosen surfaces into Mozaiks-native artifacts
   - only where generator ownership and refinement safety are worth the effort

The ladder is intentionally progressive. Customers should not need to commit to full migration on day one.

## Guided Onboarding, Not Operator Intake

Existing-app onboarding should default to a guided product walkthrough, not a technical setup checklist.

That means the default UX should ask in plain language:

- where the app currently lives
- whether the user has a frontend, backend, live URL, or codebase
- what they want Mozaiks to help with first
- what should stay host-owned vs what should become agent-accessible

The platform may still collect technical evidence such as repo paths, backend URLs, or OpenAPI specs, but those are implementation details.
They should be:

- auto-detected when possible
- hidden behind guided copy by default
- exposed directly only in advanced mode

This matters because first users are often vibe coders or product builders, not operators who already know terms like Swagger or backend base URL.

Theme capture belongs in that same guided path. For existing apps, Mozaiks should collect host brand evidence from a live URL, screenshots, repo summaries, or an existing `theme_config.json`, then normalize that evidence into a canonical theme artifact for Mozaiks-owned surfaces.
That artifact is about **matching host brand tokens**, not claiming full host-shell ownership.

In practice, ExistingAppDiscovery should preload and preserve:

- `brand_theme_summary`
- lightweight `brand_theme_evidence` such as appearance, top colors, fonts, and layout hints
- `theme_adaptation_strategy`
- `embed_theme_ready`

That keeps theme intent available to downstream planning without pretending discovery already rebuilt the host shell.

## App Shell Boundary

Mozaiks should be explicit about app-shell ownership for existing apps:

- Mozaiks can provide its own header, footer, dashboard routes, transition screens, and workflow workspace chrome
- Mozaiks can also embed into a host page, side panel, or dedicated workspace inside the existing app
- Mozaiks should not promise automatic 1:1 recreation of a bespoke host app header, footer, or layout language

That means existing-app adoption normally starts with **a Mozaiks-owned app shell around Mozaiks surfaces**, not with full host-UI cloning.
If a customer wants Mozaiks surfaces to match a host design system more closely, that is an explicit integration/customization project, not something discovery or generation should pretend to solve automatically.

For embedded mode specifically:

- the host app keeps its own header, footer, and navigation
- Mozaiks renders only the embedded workspace surface
- the embed surface must use the same canonical runtime contract as the full app: `startChat` first, then the workflow websocket/session path returned by the runtime

## Canonical Artifact Flow

Existing-app discovery should produce these artifacts:

1. `ExistingProductSpec`
2. `CapabilitySpec[]`
3. `AgentAugmentationPlan`

These artifacts then join the main planning path:

`RequestIntent(existing_app) -> ExistingProductSpec -> CapabilitySpec[] -> ExperienceSpec + AgentAugmentationPlan -> BuildGraph`

### ExistingProductSpec

Describes the host system that already exists today.

It should include:

- app identity
- current stack
- hosting model
- auth model
- current frontend experience summary
- service surfaces
- route surfaces
- key entities
- integration constraints

### CapabilitySpec[]

Existing-app capability mapping should remain product-facing.

Good examples:

- direct messaging
- app hosting management
- marketplace discovery
- funding rounds
- notifications
- governance chat

Each capability should say:

- what users can do
- where that capability currently lives
- what entities it touches
- whether it is technically ready for agent access

### AgentAugmentationPlan

Defines the first realistic Mozaiks adoption move.

It should include:

- adoption level
- why that level is correct
- which capabilities are AI-accessible first
- where the Mozaiks surface should live
- how auth delegation works
- which workflows attach first
- which ecosystem bindings are worth enabling next

## What ExistingAppDiscovery Should Not Pretend To Do

ExistingAppDiscovery should not imply:

- automatic conversion of a hand-built frontend into a Mozaiks-native app
- safe full-app refinement ownership from day one
- automatic decomposition of every legacy surface into declarative page artifacts

That is a migration problem, not a discovery problem.

## Mozaiks Dogfood Case

Mozaiks itself should be treated as the first real existing-app augmentation case:

- host frontend: `MOZ-UI`
- host backend: `mozaiks-platform/services`

That system already has:

- route modules
- service APIs
- SignalR hubs
- payment surfaces
- discovery/marketplace surfaces
- hosting and governance capabilities

So the first internal dogfood objective is not “rebuild Mozaiks inside Mozaiks.”
It is:

- identify the real existing product surfaces
- bridge them cleanly
- attach Mozaiks agentic capabilities where they create leverage

The first-class onboarding shortcut for this case should be a generic host-source seed:

- `host_app_source = "workspace_host"`
- frontend repo defaults to `MOZ-UI`
- backend repo defaults to `mozaiks-platform/services`
- discovery mode defaults to `guided`

That source should also preload host-brand evidence from the MOZ-UI repo itself:

- `theme_config.json` if the host already has one
- otherwise deterministic CSS/Tailwind signals such as fonts, colors, gradients, and shell-layout hints

The resulting augmentation artifact should say whether Mozaiks can already theme an embedded surface faithfully enough to dogfood inside MOZ-UI, or whether ThemeCapture refinement is still required.

That workspace-host path exists to prove the real onboarding path on a complex host product before asking outside users to trust it.

## Recommended First Functional Goal

For existing apps, the first functional milestone should prove:

1. deterministic discovery of host product surfaces
2. one recommended adoption level
3. one bridged host capability
4. one attached agentic workflow
5. one Mozaiks UI surface living inside the host app

Suggested proof for Mozaiks itself:

- host capability: direct messaging
- Mozaiks augmentation: thread summary or moderation assist
- host placement: side panel or dedicated workspace

## Decision Summary

The best strategy for existing apps is:

- augment first
- bridge second
- ecosystem-bind third
- migrate selectively

Mozaiks should sell and build this as a gradual adoption platform, not as a forced rewrite engine.
