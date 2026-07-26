# Prism — The Universal Software Factory

**Structure that scales. Intelligence that creates.**

---

## What Is Prism?

Prism is the build intelligence layer inside Mozaiks. You describe what you want
to build — in plain language — and Prism designs, architects, and generates a
production-ready software product.

The key word is factory. Not an assistant. Not a code completer. A factory — with
modular, swappable tooling, deterministic build contracts, and AI agents that
operate within a structure that knows exactly what it is building and why.

Today Prism builds web applications. The architecture we are building next makes
Prism capable of building any software — mobile apps, desktop tools, game clients,
developer APIs, internal platforms — from the same factory, using the same session
experience, driven by the same structural guarantees.

---

## The Problem We Are Solving

AI has transformed how fast code gets written. It has not transformed how
reliably software gets built.

The gap between writing code and building software is structural. Requirements
drift from implementation. Design decisions get made inconsistently. AI tools
generate plausible code that does not fit together. The more autonomous the
generation, the harder it is to trust the output.

The industry response has been to add more AI — more review loops, more context
windows, more agents. But the root problem is not speed or volume. It is that
unconstrained AI generation has no ground truth to reason from. It infers. It
estimates. It fills gaps with plausible-sounding choices.

Prism takes a different position. **The structure comes first. The AI works
within it.**

---

## The Design Principle: Structured Freedom

The best software development processes combine two things that seem to be in
tension: rigorous structure that makes the result reliable, and creative freedom
that makes the result valuable.

Prism is built on the observation that AI is genuinely excellent at one of these
and genuinely unreliable at the other.

AI is extraordinary at creative reasoning — interpreting intent, generating
domain-specific implementation, translating a business requirement into code,
proposing solutions within a defined space. These are things that would take a
skilled engineer hours; AI does them in seconds.

AI is unreliable as a structural architect. Left to infer the contracts,
connections, and constraints of a system on its own, it will produce something
plausible that breaks in subtle ways. Not because the model is weak — because
this is a problem that requires ground truth, not inference.

Prism's design is the synthesis: **deterministic structure that the factory
controls, creative generation that AI performs within it.**

The factory declares what surfaces exist, what data they own, what events they
emit, and what contracts they must satisfy. AI agents do what they are best at
— implementing those surfaces with domain expertise, creative judgment, and
speed — inside a scaffold that ensures the pieces connect.

The result is not a constrained AI. It is an AI with a foundation to stand on.

---

## How It Works: The Modular Build Pipeline

Prism does not generate software in one step. It runs a structured build
sequence — a series of declared stages where each stage produces a validated
artifact that the next stage builds on.

**Concept and requirements** — the user describes what they want to build. Prism
captures structured intent: the product's purpose, the audiences it serves, the
capabilities it needs. This is not a prompt. It is a typed specification.

**Design** — Prism produces a machine-readable architecture: every surface in
the product, every piece of data it owns, every event it can emit, every
external system it touches. This is validated before any code is written.

**Generation** — AI agents implement each declared surface. They receive exactly
the context they need for their scope — the contract, the domain patterns, the
file shapes, the integration requirements. They have creative latitude within
that scope. They cannot contradict what the architecture declared.

**Refinement** — when requirements change, the Refinement Engine routes the request
to the correct build stage. A small change touches only what it needs to. A
conceptual change restarts from design. The history is preserved.

Each stage is modular. The pipeline is declared. The AI operates at the
generation stage — where creative reasoning produces the most value — not at the
structural stages where determinism matters most.

---

## The Architecture Behind It

Four coupled layers make Prism's modular structure work:

**Domain Packs** — swappable build configurations for different software
categories. A Domain Pack tells the factory what it is building: the file
shapes, the language idioms, the capability vocabulary, the valid output
patterns. The factory does not guess the domain. The pack declares it. Swapping
the pack changes what the factory builds while the pipeline stays the same.

**Build Context and Context Variables** — the information infrastructure of the
build. Context variables carry typed workflow state from concept through design
through generation. Build context packs inject domain knowledge — catalogs,
contracts, patterns — into the right agents at the right stage. Every agent
sees exactly what it needs. Nothing more.

**Middleware and Hooks** — deterministic injection points that fire before agents
run. Domain vocabulary, file contracts, quality gate criteria, and capability
routing are supplied from catalogs at these points. The AI reasons from
grounded, domain-specific context rather than from general knowledge and
inference.

**Refinement Engine** — the sequencing and routing layer above individual stages. It
classifies what changed, determines which stages need to run, seeds the correct
context, and enforces the build sequence. The Refinement Engine is what makes
refinement surgical rather than destructive.

---

## What We Are Building Next: Domain Packs

The current factory builds web applications. The architecture, the pipeline, the
validation, and the refinement loop are all working.

What we are building next makes the factory domain-agnostic. The pipeline stays
the same. The Domain Pack changes.

A **Domain Pack** is a complete, swappable build configuration:

- What the output looks like in this domain (files, structure, language)
- What agents know about building in this domain
- What contracts define a valid build in this domain
- What patterns and idioms ground the AI's generation

| Domain Pack | What It Builds |
|---|---|
| **WebApp** (current) | Full-stack web applications — React frontend, Python backend, MongoDB |
| **MobileApp** (planned) | iOS and Android — Swift/Kotlin or cross-platform React Native |
| **DesktopApp** (planned) | Native desktop — Electron, Tauri, or platform-native |
| **GameClient** (planned) | 2D/3D game clients — Godot, Unity, or custom engine targets |
| **DeveloperAPI** (planned) | Backend-only API services — REST, GraphQL, gRPC |

The session experience is identical across domains. The interview, the design
review, the refinement loop — the same. What changes is what the factory knows
how to build.

---

## Why This Matters Commercially

### The market

AI-assisted software development is one of the fastest-growing markets in
technology. The current leaders — code assistants, AI editors, prompt-to-app
tools — have strong adoption and a structural ceiling. They are fast. They are
not factories. The output requires significant human judgment to ship.

Prism's position is above that ceiling. It is not competing on generation speed.
It is competing on structural reliability — the thing the market has not solved.

### The moat

A modular software factory is not something that can be replicated by tuning a
prompt or adding an agent. It requires a purpose-built build system: structured
output contracts, a context system that carries typed state, a domain pack
architecture that separates what changes from what stays stable, and a control
plane that routes change intelligently.

These compound. A competitor can build a web app generator. They cannot easily
build a system where the same factory pipeline, the same context infrastructure,
and the same refinement routing apply equally to web apps, mobile apps, and
games — with swappable Domain Packs, not forked codebases.

### The operator model

Domain Packs are a product surface, not just internal architecture.

An operator — a company, a consultancy, a specialized platform — can register
their own Domain Pack. A game studio deploys a Pack tuned to their engine and
asset conventions. An enterprise deploys a Pack enforcing their internal
architecture standards. A mobile agency deploys a Pack generating to their
preferred component library.

Prism becomes a platform for building factories, not just a factory.

---

## What "Production-Ready" Means Here

Prism-generated applications are built to a declared contract. Before code is
written, the product's surfaces, data ownership, event flows, and external
integrations are typed and validated as a machine-readable specification.
Generation agents work against that specification.

What this means in practice:

- Every declared surface has an implementation
- Every piece of data has a declared owner
- Every third-party dependency is surfaced for configuration before deployment
- The build is reproducible — the same inputs produce the same structural output

This is not a claim that every business edge case is handled or that the product
is complete in every sense. It is a claim about the foundation: structurally
correct, connected, and built to the contract that was designed.

Customization and extension happen on top of something that works, not
alongside something that might.

---

## The Vision

Software development has a paradox at its center. The parts that are most
expensive — the infrastructure, the data modeling, the event wiring, the
integration contracts — are almost identical across products. The parts that
are most valuable — the unique logic, the product experience, the creative
decisions — get less attention because the common ground consumes so much of
the budget.

Prism's vision is to invert that ratio.

Make the common ground structural and deterministic — handled by the factory,
driven by domain packs and build contracts, validated before it reaches a human.
Free the creative work — the product decisions, the domain logic, the experience
design — to be done by people and AI working at the level where they create the
most value.

A modular factory for any software. Deterministic where it matters. Intelligent
where it counts.

That is Prism.
