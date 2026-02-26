# Event System Architecture

**Status:** Source of truth  
**Last updated:** 2026-02-26

## Purpose

This document defines the target and current architecture for event handling in the unified mozaiks runtime.

## Design Principles

1. Events are immutable facts.
2. Event names are dot-delimited runtime contracts.
3. Persistence and transport are separate concerns.
4. Frontend stream events and backend business events are distinct channels.
5. Event ownership follows layer boundaries (`contracts <- core <- orchestration`).

## Event Channels

### Channel 1: Run stream channel (frontend-facing)

- Carries run lifecycle and interaction events (`process.*`, `task.*`, `chat.*`, `artifact.*`, `ui.tool.*`, `transport.*`)
- Delivered over WebSocket from `WS /v1/runs/{run_id}/stream`
- Persisted in event store before publication

### Channel 2: Business dispatch channel (backend-local)

- Carries `subscription.*`, `notification.*`, `settings.*`, `entitlement.*`
- Routed in-process via `BusinessEventDispatcher`
- Used by declarative YAML-driven runtime subsystems

### Channel 3: Telemetry channel (cross-cutting)

- Namespace: `telemetry.*`
- Supports runtime measurement and quality scoring pipelines
- Persistence/consumption strategy can vary by deployment

## Core Components and Paths

| Component | Responsibility | Path |
|---|---|---|
| `BusinessEventDispatcher` | async in-process bus for business events | `src/mozaiks/core/events/dispatcher.py` |
| `EventRouter` | maps business events to notification triggers | `src/mozaiks/core/events/router.py` |
| `SubscriptionDispatcher` | `subscription.yaml` loading and limit events | `src/mozaiks/core/events/subscription_dispatcher.py` |
| `NotificationDispatcher` | `notifications.yaml` trigger/template delivery | `src/mozaiks/core/events/notification_dispatcher.py` |
| `SettingsDispatcher` | `settings.yaml` validation/storage/update events | `src/mozaiks/core/events/settings_dispatcher.py` |
| `ModularYAMLLoader` | monolithic/modular YAML section loading | `src/mozaiks/core/workflows/yaml_loader.py` |
| `RunStreamHub` | per-run pub/sub for streamed persisted events | `src/mozaiks/core/streaming/hub.py` |
| `SimpleTransport` | higher-level websocket transport helpers | `src/mozaiks/core/streaming/transport.py` |
| API event lifecycle | create/persist/publish/replay run events | `src/mozaiks/core/api/app.py` |

## Event Naming Contract

Canonical runtime event families:

- `process.*`
- `task.*`
- `chat.*`
- `artifact.*`
- `ui.tool.*`
- `transport.*`
- `replay.*`
- `subscription.*`
- `notification.*`
- `settings.*`
- `entitlement.*`
- `telemetry.*`

Event taxonomy and envelope details live in [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md).

## Flow A: Run Stream Events

1. Runtime operation emits event envelope.
2. Event is appended to the event store.
3. Persisted event is published through `RunStreamHub`.
4. WebSocket subscribers receive ordered events by sequence.
5. Resume/replay sends historical events, then `transport.replay_boundary`, then live events.

## Flow B: Business Events

1. Subscription/settings/notification logic emits business event.
2. `BusinessEventDispatcher` invokes registered handlers.
3. `EventRouter` optionally maps event to notification trigger.
4. `NotificationDispatcher` templates and sends through backend adapters.

## AG-UI Boundary

AG-UI compatibility is a protocol concern for frontend-streamed events.

- In scope: lifecycle and streaming event interoperability.
- Out of scope: business billing/subscription/settings logic.

Do not model business dispatch rules as AG-UI concerns.

## Conformance Checklist

- Event names follow runtime taxonomy.
- Every streamed event is persistable and replay-safe.
- Business dispatch remains in-process and declarative where possible.
- No `core -> orchestration` imports.
- Source-of-truth docs and runtime paths stay aligned.

## Cross References

- [EVENT_TAXONOMY.md](EVENT_TAXONOMY.md)
- [PROCESS_AND_EVENT_MAP.md](PROCESS_AND_EVENT_MAP.md)
- [WORKFLOW_ARCHITECTURE.md](WORKFLOW_ARCHITECTURE.md)
