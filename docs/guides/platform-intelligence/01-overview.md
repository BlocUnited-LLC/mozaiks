# Mozaiks Harness

The Mozaiks Harness is the intelligence layer that sits between your request and
the agent that acts on it.

A generic AI coding tool reads your files, infers structure, and guesses what to
change. The Mozaiks Harness does not guess — it operates against the contracts
your app was built from. Every app Mozaiks generates is defined by explicit
contracts: `module.yaml`, `data_contract`, page bindings, workflow tools. The
Harness keeps a live map of those contracts and uses it to classify, scope, and
direct every change request before any code runs.

No other tool can do this because no other tool generated the app from contracts
in the first place.

## What The Harness Is Made Of

The Harness is not a single AI call. It is a pipeline of specialized components,
each with a defined job.

### Context Graph

The Context Graph is the live map of your app. It is built from the contracts
and code already in the app — every module, page, workflow, data schema, and
binding is a node. Relationships between them are edges: imports, action
bindings, workflow handoffs, data dependencies.

Every other component in the Harness reads from this graph. It is why the
Harness can answer "what files does changing this module action affect?" without
scanning the whole workspace.

### Classifier

Every request you make is classified before anything else runs. The Classifier
reads your request against the current app context and assigns one of four
change classes:

- `patch` — a small, localized fix
- `design` — a visual or structural change without a new capability
- `feature` — a new capability within the same product direction
- `core` — a concept-level change that affects the product fundamentally

The classification determines which path the Harness takes next.

### Refinement Router

The Router takes the classification and the current artifact state and picks the
right workflow sequence: `app_revision`, `design_revision`, `theme_revision`,
`full_rebuild`, and so on. It uses which parts of the build are stale to decide
the smallest valid re-entry point — so a `design` change never triggers a full
concept rebuild.

### Scope Proposer

For `patch` class changes, the Scope Proposer queries the Context Graph to
identify the smallest relevant set of files. The coding worker gets that scoped
slice — not the whole workspace. This is what keeps patch changes fast and
contained.

### Contract Surface Planner

For `feature` and `design` class changes, the Contract Surface Planner takes
over. Instead of selecting files by proximity, it identifies which contract
surfaces need updating and in what dependency order:

```
data_schema → module_action → page_binding
```

If you add a new field to a module, the data schema is updated first, then the
action handler, then the page that exposes it — each step seeing the output of
the previous one.

### Surface Regeneration Worker

The Surface Regeneration Worker executes the Contract Surface Plan
surface-by-surface, accumulating file outputs as it goes. Later surfaces always
see the updated files from earlier ones. This is what makes targeted
regeneration accurate instead of just fast.

### Coding Worker

For `patch` changes, the Coding Worker runs against the scoped file set the
Scope Proposer identified. It does not touch anything outside that scope.

### Harness Decision Policy

Before the Harness applies any change, the Decision Policy checks confidence.
High-confidence changes are auto-applied. Medium-confidence changes are
presented for your confirmation first. Low-confidence changes prompt for
clarification. You always see the proposed scope before anything is written.

---

## How It All Flows

```text
Your request
  → Classifier assigns change class (patch / design / feature / core)
  → Refinement Router selects workflow sequence or coding path
  → Context Graph queried for relevant contracts and files
  → Scope Proposer (patch) or Contract Surface Planner (feature / design)
      builds the targeted work set
  → Coding Worker or Surface Regeneration Worker executes
  → Harness Decision Policy: auto-apply, confirm, or clarify
```

For a `feature` request like "add export controls to the projects table," the
Harness identifies `projects` as a module, maps `module_action` as the contract
surface, resolves the full dependency chain
(`module.yaml → handler.py → service.py → schemas.py → page YAML`), and
generates each in dependency order. A generic agent would have to guess all of
that.

For a `patch` request like "fix the broken column header in the projects table,"
the Harness queries the Context Graph, scopes to the page binding and its
nearest module contract, runs the Coding Worker against only those files, and
auto-applies if confidence is high.

---

## Read More

- [Refinement Control Plane](./04-refinement-control-plane.md) — how the four
  change classes work and what each one means for your build
- [Control-Plane Harness Architecture](../../architecture/workflows/control-plane-harness-architecture.md)
- [Context Graph and Code Intelligence](../../architecture/foundations/context-graph-and-code-intelligence.md)
