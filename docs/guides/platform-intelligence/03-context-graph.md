# Context Graph

The Context Graph is the part of Mozaiks that helps the platform understand
which code and contracts matter before it asks a coding worker to make a
change.

It gives Mozaiks a working map of the app instead of forcing it to reason from a
raw file dump.

## What The Context Graph Is

The Context Graph is Mozaiks' canonical code-context intelligence layer. It
captures relationships across files, modules, pages, workflows, tools, symbols,
contracts, and artifacts so refinement and coding can stay scoped.

It is how Mozaiks avoids dumping an entire workspace into a prompt just to fix a
small issue.

In practice, the flow looks like this:

```text
deterministic syntax extraction
-> Mozaiks contract mapping
-> bounded LLM semantic annotation
-> graph-aware retrieval
-> scoped refinement and coding context
```

## Why It Matters

The graph gives the control plane compact, auditable context packs instead of a
raw workspace dump.

That lets Mozaiks:

- rank likely affected files before coding starts
- explain why a file was included in scope
- carry contract-boundary and risk hints with the context pack
- keep deterministic facts separate from advisory semantic interpretation

So when a request comes in, the graph helps answer a simple question:

"What is the smallest, most relevant slice of this workspace for the change I
need to make?"

## What It Does Not Do

The Context Graph is not the owner of:

- workflow routing
- module execution
- billing, permissions, or secret enforcement
- artifact lifecycle state
- UI rendering

Those remain owned by the runtime, control-plane policy, and deterministic app
contracts.

## Canonical Model

`AppContextGraph` is the source of truth. Graph databases may help later with
retrieval or visualization, but they are not the product contract.

That distinction matters because the graph is a derived artifact from the code
and contracts already in the workspace. It helps with retrieval and scoping, but
it does not become the authority over runtime behavior.

## How To Think About It

The clean flow is:

1. scan the workspace deterministically
2. map what each file means in Mozaiks terms
3. rank the files and relationships most relevant to the request
4. send only that scoped context into the next step

The goal is not to know everything. The goal is to know enough to act
accurately.

## Go Deeper

For the full canonical architecture contract, read:

- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)