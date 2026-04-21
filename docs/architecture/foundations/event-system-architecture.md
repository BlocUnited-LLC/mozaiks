# Event System Architecture

This document explains how the event families connect across the system.

## Core Model

Mozaiks has one event architecture with multiple layers:

1. app-side domain facts
2. runtime control facts
3. live workflow stream events

They may share plumbing, but they do not share ownership or meaning.

## The Main Automation Loop

```text
app action
  -> deterministic save
  -> app domain event emitted
  -> runtime ingress receives event
  -> workflow trigger matches
  -> workflow runs or resumes
  -> runtime stream updates UI
  -> workflow saves outcome through app backend
  -> app domain event emitted if app state changed
```

This loop is the core integration point between app logic and AI logic.

## Layer Ownership

### App backend

Owns:

- entities
- deterministic actions
- persistence
- post-commit app facts

The app backend never decides workflow names by embedding them in event types.

### AI runtime

Owns:

- trigger matching
- run and resume execution
- runtime control state
- live workflow stream delivery

The runtime should not pretend its internal stream events are the app's domain
model.

## The Important Boundary

Use this split:

- app events describe what happened in the product or business world
- triggers describe which workflows should react
- runtime stream events describe live execution

Do not merge those three concerns.

## What The Generator Should Produce

When building an app bundle or backend contract, the generator should produce:

- deterministic mutations and read models
- post-commit domain event emission points
- workflow `triggers:` declarations in `orchestrator.yaml`
- workflow tools that save results back into app state when needed

It should not produce:

- a separate automation-routing artifact outside workflow triggers
- event names that encode workflow identity
- an app architecture that depends on live chat events for business correctness

## Practical Authoring Rule

If a feature can be described as:

- user or integration changes state
- state commit creates a durable fact
- that fact may trigger automation

then the app should emit a domain event.

If the feature can only be described as:

- agent said something
- tool streamed an update
- artifact progress changed live

then it belongs to the workflow runtime stream, not the app event model.

## Cross References

- [event-system.md](event-system.md)
- [event-taxonomy.md](event-taxonomy.md)
- [workflow-architecture.md](workflow-architecture.md)
- [process-and-event-map.md](process-and-event-map.md)