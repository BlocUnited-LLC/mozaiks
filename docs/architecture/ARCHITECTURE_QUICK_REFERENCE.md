# Architecture Quick Reference

This is a contributor quick reference, not another north-star document.

The authoritative OSS software-design document is
[Mozaiks OSS Software Design](MOZAIKS_OSS_SOFTWARE_DESIGN.md).

## Core Rules

* AG2 owns the agent runtime.
* Mozaiks owns canonical applications.
* Dynamic reasoning crosses structured boundaries.
* `build_context` projects reusable knowledge, contracts, catalogs, and assets into reasoning and deterministic materialization.
* `CapabilityPack` is the reusable generation-time unit.
* A Community Component extends `CapabilityPack` with versioned distribution, provenance, dependencies, trust/integrity, installability, and upgrade metadata.
* Events and reactions provide canonical loose coupling across modules, workflows, notifications, and app-owned adapters.
* Schema-native UI is preferred for reusable/community UI.
* Semantic-token React is conditionally portable.
* Custom React remains a supported escape hatch.
* `AppBuildPlan` is the post-reasoning deterministic materialization boundary.
* Validation remains deterministic.
* Evaluation complements validation; it does not replace validation.
* Refinement is part of the core application lifecycle.
* Operator intelligence enters through public OSS seams.
* Do not duplicate canonical owners.

## Ownership

| Area | Canonical Owner |
| --- | --- |
| Agent execution, agents, networks, tools, middleware, KnowledgeStore | AG2 |
| App manifests, modules, pages, workflows, events, reactions, data contracts | Mozaiks OSS |
| Deterministic materialization, validation, functional acceptance | Mozaiks OSS |
| One-app App Intelligence and brownfield adoption | Mozaiks OSS |
| Cross-app Build Intelligence, learned routing, operator evidence | Operator/private by default |

## Required Pre-Edit Architecture Check

Before editing Mozaiks OSS:

1. read this quick reference;
2. if the task changes, extends, replaces, or challenges architecture, read [Mozaiks OSS Software Design](MOZAIKS_OSS_SOFTWARE_DESIGN.md);
3. treat current source as final authority;
4. if current source contradicts the frozen north star, stop and report the contradiction.

## Agent Development Rule

Before introducing a subsystem:

1. identify the current owner;
2. inspect whether AG2 already owns the primitive;
3. inspect whether Mozaiks already has the canonical implementation;
4. extend or connect existing architecture first;
5. do not introduce a parallel subsystem without an ADR.
