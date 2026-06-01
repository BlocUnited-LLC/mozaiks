# Mozaiks Harness

The Mozaiks Harness is the layer between what you ask and what the agent does.

A generic AI agent runs free against your files — it reads code, infers
structure, and makes its best guess at what to change. The Mozaiks Harness does
something fundamentally different: it takes your request, classifies what kind
of change it is, maps it to the contracts your app was built from, scopes the
relevant surface, and directs a targeted worker. The AI is not guessing. It is
operating against a known map.

That is what a harness is: not raw AI capability, but AI that is directed,
constrained, and purposeful.

## What The Harness Knows

Every app Mozaiks generates is built from explicit contracts — `module.yaml`,
`data_contract`, page bindings, workflow tools. The Harness keeps a live map of
those contracts and uses it before anything runs.

When you ask for a change, the Harness already knows:

- what modules exist and what actions they expose
- what pages are bound to which module actions
- what data schemas underpin each module
- what workflows are active and where they connect

So when you say "add export controls to the projects table," the Harness
recognizes `projects` as a module, identifies `module_action` as the contract
surface that needs updating, resolves the complete set of files that surface
requires, and generates each one in dependency order. A generic tool would have
to guess all of that.

## How It Works In Practice

Every request goes through two steps before any code changes.

**Step 1: classify the request.**
The Harness decides whether this is a patch, a design change, a feature
request, or a concept-level change. That determines the path — a scoped code
fix, a workflow re-entry, or something larger.

**Step 2: scope the context.**
For patches and targeted fixes, the Harness builds a compact view of the files
and contracts most likely to be affected. The coding worker gets that scoped
view — not the whole workspace.

**Adding a feature:**

```text
You: "Add export controls to the projects table"

→ classified as: feature
→ route to app_revision sequence
→ workflow runs with routing context already set
```

The Harness re-enters the right workflow with context already loaded. You do not
have to re-describe the app.

**Fixing a bug:**

```text
You: "Fix the broken column header in the projects table"

→ classified as: patch
→ rank relevant files and contracts by proximity
→ coding worker runs against that scoped slice
→ auto-patches or asks to clarify if confidence is low
```

## What Makes This Different

No other tool can do this because no other tool generated the app from contracts
in the first place.

Cursor, Copilot, and Claude Code help humans write software faster. They produce
code that an AI must reverse-engineer to change accurately. The Mozaiks Harness
operates against the contracts that were the inputs to generation — so every
change is a contract-level operation, not a file-level guess.

In practice:

- changes stay scoped to the right part of the app instead of drifting
- your existing build state is preserved at every level above the change
- you do not re-describe the app every time you want to change something

See [Refinement Control Plane](./04-refinement-control-plane.md) for how
the Harness decides what level of change a request actually requires.

---

**Architecture references**

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
