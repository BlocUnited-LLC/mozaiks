# App Intelligence And Context Graph

App Intelligence is the app map Mozaiks uses to understand what exists before
agents change it. The Context Graph is the relationship layer inside that
system.

It is not documentation in the usual sense. It is a set of versioned artifacts
built from the code and contracts already in the app. The Refinement Engine
reads it to answer questions like:

- what modules exist?
- what pages are bound to those modules?
- which workflows, tools, and schemas connect them?
- what is the smallest relevant surface for this request?

## What It Contains

Source-backed indexing produces:

- `SourceContextBundle`: selected redacted source files, chunks, symbols,
  imports, parser status, scan health, and source retrieval data.
- `AppContextGraph`: files, symbols, modules, pages, routes, workflows,
  integrations, data entities, risks, and relationships.
- `AppIntelligenceSnapshot`: compact architecture, capability, ownership,
  integration, data, risk, and agent-context policy summaries.
- `AppContextVersion`: the current versioned handle that points to the artifact
  set.

The graph tracks nodes such as:

- modules
- pages
- workflows
- agents
- tools
- schemas
- configs

It tracks edges such as:

- imports
- action bindings
- workflow handoffs
- data dependencies
- route bindings

When source files are available, Mozaiks also stores a `SourceContextBundle`
behind the graph. That bundle contains selected redacted source files, chunks,
symbols, imports, parser health, and scan warnings. Agents retrieve exact code
from that bundle through tools instead of receiving a full repository dump in
their prompt.

The `AppIntelligenceSnapshot` is the compact summary agents see first. It does
not contain raw source contents.

During existing-app discovery, the preload registers a current source-backed
`AppContextVersion` before the first agent turn. The chat overview shows the
compact catalog and durable context refs; exact source remains behind retrieval
tools.

## Why It Matters

App Intelligence is what lets Mozaiks act like it understands the app before it
starts changing it.

Without it, change requests become file guessing. With it, Mozaiks can map a
request to the exact contracts and files that matter.

Tree-sitter parser packages are installed with Mozaiks and are the baseline
parser path for source-backed indexing. FalkorDB is recommended for
production-scale graph querying and Studio visualization, but it mirrors the
canonical artifacts; it does not become the source of truth.

## Context Given To Agents

Mozaiks stages code context by workflow/checkpoint:

| Surface | Context given |
| --- | --- |
| Existing-app discovery | App Intelligence catalog, compact graph pack, source catalog, retrieval tools, and repo/API/runtime evidence as fallback diagnostics |
| App and workflow generation revisions | App Intelligence catalog and compact graph pack from the current AppContext or selected artifact |
| Refinement classification | revision state, artifact summary, stale families, App Intelligence freshness, and context freshness |
| Scope selection | App Intelligence catalog, graph catalog, workspace catalog, and bounded source search |
| Contract surface selection | contract-aware graph context, App Intelligence catalog, and bounded source search |
| Coding refinement | explicit scoped files, graph scope, exact source reads, and related files as read-only context |

This keeps prompt context small while still letting agents inspect real code
when they need it.

## How To Think About It

Use App Intelligence as the compact answer to "what is in this app?" Use the
Context Graph to answer "how does it connect?" Use source retrieval tools for
the exact code evidence.

For the deeper architecture, see [App Intelligence Plane](../../architecture/foundations/app-intelligence-plane.md) and [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md).
