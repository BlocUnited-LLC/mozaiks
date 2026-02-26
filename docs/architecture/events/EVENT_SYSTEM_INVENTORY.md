# Event System Inventory

**Status:** Informational snapshot  
**Last updated:** 2026-02-26

This inventory lists active event-system modules in the unified `mozaiks` repository.

## Active Core Modules

| Capability | Module |
|---|---|
| Business event bus | `src/mozaiks/core/events/dispatcher.py` |
| Event routing | `src/mozaiks/core/events/router.py` |
| Subscription dispatch | `src/mozaiks/core/events/subscription_dispatcher.py` |
| Notification dispatch | `src/mozaiks/core/events/notification_dispatcher.py` |
| Settings dispatch | `src/mozaiks/core/events/settings_dispatcher.py` |
| Declarative YAML loading | `src/mozaiks/core/workflows/yaml_loader.py` |
| Stream pub/sub hub | `src/mozaiks/core/streaming/hub.py` |
| Transport helpers | `src/mozaiks/core/streaming/transport.py` |
| API event lifecycle | `src/mozaiks/core/api/app.py` |

## Event Families in Use

- Run stream: `process.*`, `task.*`, `chat.*`, `artifact.*`, `ui.tool.*`, `transport.*`
- Business dispatch: `subscription.*`, `notification.*`, `settings.*`, `entitlement.*`
- Telemetry: `telemetry.*` (reserved for quality/analytics flows)

## Notes

- This file is an inventory, not the normative contract.
- Source-of-truth contract docs are under `docs/architecture/source-of-truth/`.
