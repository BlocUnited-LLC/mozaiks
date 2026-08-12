# Canonical Schema Generation Policy

**Status:** Pre-1.0 implementation rule

**Authority:** This policy refines the structured-boundary and deterministic-generation rules in [Mozaiks OSS Software Design](MOZAIKS_OSS_SOFTWARE_DESIGN.md). It is not a second north star.

## Goal

Mozaiks generates applications through dynamic AG2 reasoning, but important application surfaces must cross into runtime through **small, strict, canonical structured schemas**.

The generation objective is not maximum declarative expressiveness. It is reliable composition.

> **Canonical schemas optimize for generation reliability, runtime determinism, and composability before expressiveness.**

A valid structured output should map predictably to one canonical runtime meaning.

## Core Rules

1. **Prefer small finite taxonomies.** If agents or loaders choose among known kinds, the choices must be declared as typed enums or equivalent finite vocabularies.
2. **One concept gets one canonical name.** Do not preserve multiple aliases for the same page, action, module, workflow, capability, route, event, or UI concept.
3. **Prefer shallow explicit contracts.** Avoid deeply nested free-form schemas when a small identifier plus typed fields can express the same runtime intent.
4. **Prefer references over duplication.** Page-to-module, route-to-component, workflow-to-capability, reaction-to-target, and capability-to-pack relationships should use explicit canonical identifiers validated before materialization.
5. **Unknown runtime-affecting values fail before promotion.** Do not allow an invented taxonomy value to survive materialization and become a runtime `404`, missing action, unresolved component, or silent fallback.
6. **YAML declares architecture and contracts; code implements customization.** Use Python and JS/React escape hatches for behavior that does not belong in the canonical schema rather than expanding the schema without bound.
7. **Escape hatches remain contract-bound.** Custom code must implement declared entry points, actions, handlers, components, adapters, or hooks. It must not create a second undeclared routing or schema system.
8. **Generator, schema, materializer, validator, and runtime change together.** A contract change is incomplete until all five agree.
9. **Deterministic validation precedes semantic evaluation.** AG2 or human evaluation may judge quality, but cannot replace schema/reference/runtime correctness.
10. **Factory must dogfood the canonical contract.** Generic schema/taxonomy behavior belongs in OSS and should be exercised by `factory_app` and/or generated-app acceptance where applicable.

## Pre-1.0 Replacement Rule

Mozaiks is not yet a production compatibility platform. When simplifying a canonical schema or taxonomy:

- replace the old shape;
- update all generators, structured-output models, templates, loaders, validators, fixtures, docs, and tests in the same change or coordinated change set;
- delete stale aliases, normalization branches, fallback names, compatibility shims, and retired tests;
- do not add dual-read or dual-write behavior merely to preserve an obsolete pre-1.0 contract;
- do not keep both old and new taxonomies active;
- do not preserve an older shape unless an explicit current external contract or user-approved migration requirement proves it is necessary.

The default pre-1.0 decision is **one canonical shape, not compatibility layers**.

## Taxonomy Design

A taxonomy should be introduced only when it has a deterministic runtime or generation meaning.

Good taxonomy properties:

- small;
- mutually distinguishable;
- stable enough to appear in structured outputs;
- directly mapped to materialization/runtime behavior;
- validated at generation time;
- documented in the same source of truth used by agents.

Avoid taxonomies that contain many overlapping synonyms or purely stylistic labels that do not change runtime semantics.

Examples of surfaces that should have finite canonical vocabularies where the current architecture needs them include:

- page/surface kinds;
- module archetypes;
- action kinds;
- workflow/orchestration kinds;
- UI primitives;
- reaction target kinds;
- capability kinds;
- shell/navigation roles;
- persistence/data relation kinds.

The exact values come from current source contracts. Do not invent a new vocabulary in prompts when a canonical enum already exists.

## 404 / Missing-Surface Prevention

Generated runtime failures should be prevented upstream through reference closure.

Before a generated bundle is accepted, Mozaiks should be able to prove the relevant declared graph closes, including as applicable:

```text
route
→ page/component
→ module/action
→ handler

workflow
→ capability/tool/action

emitted event
→ declared event schema
→ reaction target

CapabilityPack
→ declared outputs/dependencies
→ materialized files
```

A declared surface may intentionally return auth/entitlement responses, but it must not fail because generation silently dropped or renamed one of its canonical references.

Functional acceptance remains the runtime backstop. Schema simplicity and taxonomy enforcement exist to prevent these defects earlier.

## Schema-Native vs. Custom Code

Mozaiks should keep the canonical declarative layer intentionally smaller than the implementation layer.

```text
Structured YAML / structured outputs
= identity + topology + contracts + canonical references

Python / JS / React
= custom implementation behind those contracts
```

Schema-native UI and reusable CapabilityPacks should prefer canonical primitives and semantic tokens. Arbitrary custom React/Python remains a supported escape hatch when necessary, but it must register through the same route/module/workflow/runtime contracts and pass the same acceptance gates.

## Community Components

Community Components inherit this policy.

Factory should reason over validated component descriptors and finite canonical capability/taxonomy fields, not arbitrary prose or undeclared code shape.

A reusable component may provide custom implementation, but its integration surface must remain canonical and deterministic:

```text
validated descriptor
→ known capabilities/contracts
→ deterministic pack materialization
→ explicit routes/actions/events/workflows
→ generated-app validation
```

Community reuse must reduce arbitrary generation, not introduce another unbounded schema language.

## Change Review Checklist

For any schema, taxonomy, structured-output, or generated-surface change, verify:

- Is there already a canonical field/value that represents this concept?
- Can the schema become smaller instead of larger?
- Can free-form text become a finite typed field without losing necessary behavior?
- Are two names representing the same runtime meaning?
- Does every runtime-affecting identifier resolve before promotion?
- Does custom behavior belong in a `.py` / `.js` / React escape hatch instead of another schema branch?
- Have old pre-1.0 aliases and compatibility paths been removed?
- Do Factory prompts/structured outputs emit only the canonical shape?
- Do materializers and loaders consume only the canonical shape?
- Do validators fail unknown or unresolved values before runtime?
- Does generated-app functional acceptance prove the corresponding surface does not accidentally `404`, `501`, or return a placeholder?

If the answer reveals two competing shapes, simplify to one canonical contract before adding more behavior.
