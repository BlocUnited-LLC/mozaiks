# Refinement Control Plane

Generating your app is the starting point. The refinement control plane is what
makes every change after that accurate and fast.

When you ask Mozaiks to change something — rename a field, restructure a page,
add a feature, rethink the product direction — you should not have to re-run
everything from scratch. Mozaiks figures out how large the change really is and
does only what the change actually requires.

## The Four Types of Change

Every request you make falls into one of four classes. Mozaiks classifies it
automatically — you do not need to tell it which one applies.

| Class | What it means | What Mozaiks does |
|---|---|---|
| **Patch** | A small, localized fix | Scoped code edit against the affected files only |
| **Design** | A visual or layout change without a new capability | Page schema and UI binding regeneration, module untouched |
| **Feature** | A new capability within the same product | Targeted workflow re-entry with updated planning context |
| **Core** | A fundamental change to the product direction | Restart from the concept and value planning stage |

## What This Looks Like For You

**Renaming a label on a form:**
Mozaiks classifies it as `patch`, identifies the page binding and the module
schema that own that label, patches only those, and closes the loop. You do not
wait for a full rebuild.

**Changing the app from a card layout to a table view:**
Mozaiks classifies it as `design`, regenerates the page schema and layout
bindings, and leaves the module actions and data contract completely untouched.
The backend is not touched because it does not need to be.

**Adding an approval workflow to an existing feature:**
Mozaiks classifies it as `feature`, re-enters the planning workflow with the
existing app context already loaded, and generates only the new contract surfaces
that the capability requires.

**Pivoting from B2C to enterprise:**
Mozaiks classifies it as `core` and takes you back to the value and concept
stage — because a change at that level affects everything downstream, and a
partial patch would be wrong.

## Why This Approach Matters

Most AI tools treat every request as an edit against a pile of files. Mozaiks
treats every request as a change against a structured app with known contracts.

That means:

- changes are as small as the request allows
- the existing app state is preserved at every level above the change
- you are always working forward from your current build, not repeating it

## How To Use It

You do not configure the refinement control plane directly. It is always on for
apps running through Studio.

Just describe what you want to change in plain language. Mozaiks classifies it,
shows you the proposed scope, and either auto-applies it (for clear patches) or
asks for confirmation before proceeding.

If a change is classified in a way that surprises you, you can adjust the scope
before it runs.

---

**Architecture reference**

- [Refinement Control Plane](../../architecture/workflows/refinement-control-plane.md)