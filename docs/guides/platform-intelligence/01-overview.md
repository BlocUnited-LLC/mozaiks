# Code Context

When you ask Mozaiks to change something in your app, you are not prompting a
generic coding assistant. You are asking a platform that already knows what your
app is made of.

Mozaiks keeps a live map of your app — every module, page, workflow, contract,
and data schema — and uses it to figure out exactly what a request means before
anything runs. That is what makes the difference between a tool that makes
its best guess and a platform that makes targeted, accurate changes.

## What You Actually Get

Most coding tools work at the file level. They read code, infer structure, and
hope they got the right files. Mozaiks works at the contract level.

Because every app Mozaiks generates is built from explicit contracts —
`module.yaml`, `data_contract`, page bindings, workflow tools — the platform
already understands the semantic structure of your app. When you ask for a
change, it reads contracts, not HTML.

In practice, this means:

- changes stay scoped to the right part of the app instead of drifting
- the platform can show you why a file was included in a change
- your existing build state is preserved unless a full rebuild is actually
  necessary

## How It Works In Practice

Every request goes through two quick steps before any code changes.

**Step 1: classify the request.**
Mozaiks decides whether this is a patch, a design change, a feature request, or
a concept-level change. That determines the path — a scoped code fix, a workflow
re-entry, or something larger.

**Step 2: scope the context.**
For patches and targeted fixes, Mozaiks builds a compact view of the files and
contracts most likely to be affected. The coding worker gets that scoped view —
not the whole workspace.

**Adding a feature:**

```text
You: "Add export controls to the projects table"

→ classified as: feature
→ route to app_revision sequence
→ workflow runs with routing context already set
```

Mozaiks re-enters the right workflow with context already loaded. You do not
have to re-describe the app.

**Fixing a bug:**

```text
You: "Fix the broken column header in the projects table"

→ classified as: patch
→ rank relevant files and contracts by proximity
→ coding worker runs against that scoped slice
→ auto-patches or asks to clarify if confidence is low
```

Mozaiks knows `projects` is a module, `column header` maps to the page YAML
binding, and the relevant files are the module handler, the page schema, and
their neighbors. A generic coding tool would have to guess all of that.

## Why This Matters For Building

This changes what iteration actually feels like.

Instead of re-describing your app every time you want to change something, you
just describe the change. Mozaiks already has the map. It uses that map to make
the smallest accurate change, validate it against the contracts, and keep
everything else intact.

See [Refinement Control Plane](./04-refinement-control-plane.md) for how
Mozaiks decides what level of change a request actually requires.

---

**Architecture references**

- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
