# Context Graph

The Context Graph is the app map Mozaiks uses to understand what exists and
how it is connected.

It is not documentation in the usual sense. It is a runtime artifact built from
the code and contracts already in the app. The Refinement Engine reads it to answer
questions like:

- what modules exist?
- what pages are bound to those modules?
- which workflows, tools, and schemas connect them?
- what is the smallest relevant surface for this request?

## What It Contains

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

## Why It Matters

The Context Graph is what lets Mozaiks act like it understands the app before
it starts changing it.

Without it, change requests become file guessing. With it, Mozaiks can map a
request to the exact contracts and files that matter.

## How To Think About It

Use the Context Graph as the source of truth for "what is in this app and how
does it connect?" The Refinement Engine uses that answer to decide what happens
next.

For the deeper architecture, see [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md).
