# Pipeline Architecture Refactor Plan

**Status:** Historical plan / archival note  
**Last updated:** 2026-03-12  
**Original plan date:** 2026-03-05

---

## Purpose

This document is retained as a historical record of a major runtime refactor
proposal.

It should not be used as the primary guide for current architecture work.

The repo has since moved to stronger architecture foundations and current-state
reference docs that describe the live runtime more accurately than this phased
plan.

Use this file only when you need:

- historical context on why the refactor was proposed
- a snapshot of the intended decomposition direction at that time
- a reminder of the risks the refactor was trying to reduce

For current implementation guidance, prefer the documents linked below.

---

## What This Plan Was Trying To Solve

The original plan targeted a set of concrete runtime problems:

- oversized orchestration and transport files
- mixed responsibilities across routing, transport, persistence, and execution
- circular dependencies between orchestration and transport layers
- AG2-specific event types leaking across non-engine boundaries
- weakly typed shared connection state

Those concerns remain useful historical context.

What is outdated is the status framing and the phase-by-phase checklist as a
source of truth for the current repo.

---

## How To Read This Now

Treat the original phases as design history, not as an active roadmap.

The stable takeaways are:

1. keep runtime boundaries explicit
2. keep engine-specific concerns isolated from runtime contracts when practical
3. avoid hidden shared mutable transport state
4. prefer current architecture references over migration notes

Do not assume every proposed module split in the original plan still maps 1:1
to the current codebase.

---

## Current Docs To Use Instead

For live runtime architecture, use:

1. [Architecture Index](index.md)
2. [Workflow Architecture](foundations/workflow-architecture.md)
3. [Declarative Runtime System](events/declarative-runtime-system.md)
4. [Event System Architecture](foundations/event-system-architecture.md)
5. [Process and Event Map](foundations/process-and-event-map.md)

These documents describe the current runtime model more accurately than the
original refactor plan.

---

## Historical Snapshot

At the time of the original plan, the intended direction was:

- thinner orchestration entrypoints
- more explicit transport responsibilities
- clearer event normalization boundaries
- better decomposition around workflow execution and event handling

The original plan also recorded that the `GroupChatExecutor` extraction had been
completed while later phases remained open.

That status should now be interpreted only as a snapshot from the plan date, not
as a current task tracker.

---

## Why This File Still Exists

Deleting the file entirely would remove useful context for reviewers trying to
understand why the runtime architecture was pushed toward stronger boundaries.

Keeping it as an archival note preserves:

- the original motivations
- the refactor vocabulary used in prior discussions
- a linkable historical record for past code-review or planning references

---

## Guardrails

1. Do not use this document as an implementation checklist.
2. Do not cite its proposed module layout as the authoritative current repo shape.
3. Do use the foundations and event architecture docs for present-tense work.
4. Do update or remove this archival note if it stops adding historical value.

---

## Superseding References

- [Workflow Architecture](foundations/workflow-architecture.md)
- [Event System Architecture](foundations/event-system-architecture.md)
- [Process and Event Map](foundations/process-and-event-map.md)
- [Declarative Runtime System](events/declarative-runtime-system.md)