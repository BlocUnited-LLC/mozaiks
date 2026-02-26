# Declarative Runtime System

**Status:** Informational reference  
**Last updated:** 2026-02-26

## Purpose

This document summarizes the declarative runtime configuration pattern in unified mozaiks.

## Runtime Components

Declarative runtime behavior is implemented in `src/mozaiks/core/events/`:

- `SubscriptionDispatcher` (`subscription.yaml`)
- `NotificationDispatcher` (`notifications.yaml`)
- `SettingsDispatcher` (`settings.yaml`)
- `BusinessEventDispatcher` (in-process event bus)
- `EventRouter` (business-event trigger routing)

## Config Loading

`ModularYAMLLoader` (`src/mozaiks/core/workflows/yaml_loader.py`) supports:

1. monolithic files (`notifications.yaml`, `subscription.yaml`, `settings.yaml`)
2. modular directories (`notifications/*.yaml`, `subscription/*.yaml`, `settings/*.yaml`)

## Event Families

Common business event namespaces:

- `subscription.*`
- `notification.*`
- `settings.*`
- `entitlement.*`

These remain backend-local by default.

## Design Guardrails

1. Keep business dispatch declarative where feasible.
2. Keep event names explicit and taxonomy-aligned.
3. Keep frontend stream concerns separate from backend business dispatch.
4. Keep runtime ownership in `core`; workflow files are inputs.

## Canonical References

- `docs/architecture/source-of-truth/EVENT_SYSTEM_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/EVENT_TAXONOMY.md`
- `docs/architecture/source-of-truth/WORKFLOW_ARCHITECTURE.md`
