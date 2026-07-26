# Mozaiks × YC RFS Alignment

Five YC Summer 2026 RFS categories describe Mozaiks directly. This document
maps each one to what the platform actually does today and what it is becoming.

---

## 1. Software for Agents (Aaron Epstein) — Primary

> "The next trillion users won't be people, they'll be AI agents. Rebuild
> software for agents as first-class citizens using APIs, MCPs, and CLIs —
> not buttons, forms, and dashboards."

Most platforms add an AI layer on top of existing software. Mozaiks generates
software that is agent-native by construction.

Every app Mozaiks generates is built from explicit contracts:

- `module.yaml` declares actions as typed API endpoints — not discovered by
  scraping UI, declared before the first line of code runs
- `data contracts` are structured schemas, not inferred from database tables
- `page bindings` are declarative references to module actions, not hardcoded API paths
- `workflow tools` are typed contracts that any agent can call

When an agent needs to operate a Mozaiks app — read data, take actions, modify
behavior — it reads contracts, not HTML. The app describes itself because it was
generated from a description.

This is not a retrofit. It is the default output of the generator.

**The market gap Mozaiks fills:**

Cursor, Copilot, and Claude Code help humans write software faster. They produce
code that agents must reverse-engineer to use. Mozaiks generates software that
agents can operate natively — because the contracts that govern the software were
the inputs to the generation, not the outputs.

**The open frontier:**

Epstein's framing includes agent discovery — agents need to "discover, sign up
for, and instantly start using new tools programmatically." Mozaiks solves the
per-app contract clarity problem: a generated app is fully machine-readable by
construction. Cross-app discovery and a registry story are the natural next layer
on top of that foundation.

---

## 2. The AI Operating System for Companies (Diana Hu) — Strong Primary

> "Create connective layers making companies legible to AI by default. Turn a
> company's own artifacts into a self-improving loop monitoring real-time
> execution against intended outcomes."

This is the sharpest current-state fit across all five categories. Hu's framing
maps directly to what Mozaiks already ships:

**Artifacts legible to AI by default.** The Mozaiks Context Graph is a
queryable runtime artifact — nodes for modules, pages, workflows, agents, tools,
schemas, and configs; edges for imports, declarations, action bindings, workflow
handoffs, and data dependencies. The refinement harness reads this graph to
make targeted code changes without ever reading a README.

**A self-improving loop.** The refinement loop is exactly what Hu describes:

1. User expresses intent ("add export controls to the projects table")
2. Classifier identifies change class (`feature`)
3. `ContractSurfacePlanner` queries the context graph and identifies which
   contract surfaces need updating and in what dependency order
   (`data_schema → module_action → page_binding`)
4. `SurfaceRegenerationWorker` executes surface by surface, accumulating file
   outputs so later surfaces see updated files from earlier ones
5. `SurfacePlanExecutionResult` carries the merged artifact set back to Studio

Real execution (the current app contracts) is continuously compared against
intended outcomes (the user's refinement request). The loop is not a promise —
it is the implemented behavior of the harness today.

**Why this framing matters:**

Blomfield's Company Brain category (below) is about capturing organizational
knowledge. Hu's AI OS category is about making what a company *builds and
ships* legible to AI in real time. Mozaiks occupies Hu's layer directly: the
software that runs the company is generated from contracts, and those contracts
are the substrate the AI operates on.

---

## 3. Dynamic Software Interfaces (Ankit Gupta) — Direct Fit

> "AI-powered radical UI customization where users modify interfaces dramatically
> per use case, requiring a rethought software delivery stack."

Gupta's premise is that UI customization requires rethinking the delivery stack —
you cannot bolt dynamic interfaces onto static HTML/CSS. The stack has to change.

Mozaiks is that rethought stack.

Because every page is a declarative binding to a module action — not a static
component tree — the presentation layer can be restructured without losing
semantic meaning. The module action still exists. The data contract still holds.
The page can be regenerated for a different use case while the backend stays
intact.

The refinement loop makes this concrete:

- "Make this table view a kanban board" — harness classifies as `design`,
  identifies the `page_binding` contract surface, regenerates only the page YAML
  and its UI bindings, leaves the module and data contract untouched
- "Add a mobile-optimized view" — identifies `shell_preset` + `page_binding`
  surfaces, generates the mobile layout declarations, does not touch business logic

This is dynamic interfaces at the delivery stack level — not a theming system,
not a CSS override, but targeted regeneration of the contract surfaces that
govern presentation. This path is implemented: the `ContractSurfacePlanner` and
`SurfaceRegenerationWorker` execute exactly this logic today.

---

## 4. SaaS Challengers (Jared Friedman) — Strong Secondary

> "Clone existing products at one-tenth price or build AI-native workflows from
> scratch. The next generation will replace legacy SaaS with AI-native software."

Friedman's category is about the companies attacking incumbent SaaS. Mozaiks is
the generation platform that makes those attacks viable at non-engineer cost.

A non-technical founder who wants to build an AI-native CRM, compliance tool,
project management system, or client intake workflow does not need a three-person
engineering team to produce the first version. They generate it from contracts.
The 10x cost reduction is not undercutting incumbents on pricing — it is
eliminating the engineering phase for the initial build entirely.

**The structural advantage:**

A Mozaiks-generated app is not a static export. It is a live contract graph that
agents can modify, extend, and operate. When the founder needs to add a new
feature, the refinement loop targets only the contract surfaces that need to
change. When a competitor adds a capability, the founder does not open a code
editor — they open Studio and describe the change.

This positions Mozaiks one layer above the SaaS challenger market: not a
challenger itself, but the platform that removes the engineering barrier to entry
for every challenger in Friedman's target categories.

---

## 5. Company Brain (Tom Blomfield) — Secondary

> "A living map of how a company works enabling agents to execute consistently
> across operations — across Slack, Linear, GitHub, recordings, customer
> interactions."

Blomfield identifies the gap as fragmented knowledge preventing reliable agent
automation. The solution is a structured, queryable map of how a company
operates.

**What Mozaiks delivers today:** The Context Graph is a living map of how a
generated app works. It is not documentation. It is a queryable runtime artifact
updated with every build. The same infrastructure that lets the Refinement Engine
harness make targeted code changes — without ever reading a README — is the
infrastructure that lets agents understand and operate a company's software
without reverse-engineering it.

**What is planned:** The expansion from software-layer brain to full company
operations — capturing active workflows, running agents, live data state, Slack
threads, and incident history — is the natural next layer. That expansion is not
yet implemented. Today's fit is the software map, not the full operational map.

**Why the software layer matters:**

Mozaiks-generated apps are the operational layer of companies that need software
but cannot afford engineering teams. A project management app, a customer intake
system, a compliance workflow — these are where company operations actually
execute. Because they were generated from contracts, they are queryable by agents
from day one. The operational map and the software map converge on the same
underlying object.

---

## What Makes This a Unique Position

All five RFS categories converge on the same underlying bet:

> Software built from explicit contracts — rather than inferred from files — can
> be understood, operated, and modified by AI at the contract level, not just
> the code level.

Epstein, Hu, Gupta, Friedman, and Blomfield are attacking this from five angles:
agent-first APIs, company artifact legibility, dynamic UI stacks, AI-native SaaS
economics, and organizational knowledge graphs. Mozaiks is building the substrate
that makes all five possible for generated software.

**The demo that proves the thesis:**

A user says: "Add export controls to the projects table."

A generic coding agent reads files, guesses what to change, writes code that may
or may not update `module.yaml`, may or may not add the new action to
`handler.py`, probably does not update the page YAML binding to expose the new
endpoint.

Mozaiks recognizes this as a `module_action` contract surface update on the
`projects` module, resolves the complete set of files that surface requires
(`module.yaml → handler.py → service.py → schemas.py → page YAML`), generates
each one in dependency order, and validates against the Mozaiks contracts.

No other tool can do this because no other tool generated the app from contracts
in the first place.

---

## Current State vs Target State

| Capability | Today | Target |
|---|---|---|
| Agent-first app generation | ✓ ¹ | ✓ |
| Declarative module action contracts | ✓ | ✓ |
| Context graph (living code map) | ✓ | ✓ |
| Harness classifies change intent | ✓ | ✓ |
| Patch coding worker (narrow file patches) | ✓ | ✓ |
| Contract surface planner (feature/design) | ✓ | ✓ |
| Targeted regeneration by surface | ✓ | ✓ |
| Studio diff grouped by contract surface | Planned | ✓ |
| Operational graph (company ops layer) | Planned | ✓ |

¹ **Generation scope note.** AppGenerator produces the full contract skeleton
(`module.yaml`, `data_contract`, page bindings) and the standard CRUD action
implementations (`handler.py`, `service.py`, `repo.py`, `schemas.py`,
`policy.py`) as working code. The gap is action-level completeness for
non-CRUD patterns: settings persistence, complex aggregations, and
cross-module operations may generate as `pass` stubs rather than complete
logic. The structure and contracts are always correct; the bodies of
non-standard actions require review. This is a generator prompt and
structured-output coverage gap, not a missing pipeline stage.

---

## Why YC, Why Now

The platform is pre-production by design — intentionally not published. The
core generation, runtime, and harness infrastructure are working. The
contract-surface refinement loop is implemented and tested.

The timing is right because:

1. The coding agent category is crowded but all competitors operate at the file
   level. Contract-level intelligence is not yet claimed.
2. The agent-first software category is early. The tooling to build agent-first
   apps does not exist. Mozaiks is that tooling.
3. The AI OS for companies category (Hu) is new and unoccupied at the software
   generation layer. Mozaiks is already the thing Hu is asking for — applied to
   the apps a company builds and runs.
4. The SaaS challenger opportunity is real and the barrier is engineering cost.
   Mozaiks removes that barrier structurally, not just by making coding faster.
5. Generated apps as company brains positions Mozaiks above "app builder" and
   into infrastructure.

The question is not whether to build this. It is being built. The question is
whether to build it as an OSS framework or as a funded product with a hosted
layer on top of the OSS core.
