# Event Architecture Notes (Legacy Context)

**Status:** Informational only (non-authoritative)  
**Last updated:** 2026-02-26

This directory contains supplemental event-architecture notes.

Authoritative architecture for the unified OSS runtime lives in:

- `docs/architecture/source-of-truth/EVENT_SYSTEM_ARCHITECTURE.md`
- `docs/architecture/source-of-truth/EVENT_TAXONOMY.md`
- `docs/architecture/source-of-truth/PROCESS_AND_EVENT_MAP.md`

Use this directory only for additional context and migration history.
Do not treat files here as the canonical contract.

## Current Unified Runtime References

- Event dispatcher: `src/mozaiks/core/events/dispatcher.py`
- Event router: `src/mozaiks/core/events/router.py`
- Declarative dispatchers: `src/mozaiks/core/events/{subscription_dispatcher.py,notification_dispatcher.py,settings_dispatcher.py}`
- YAML loader: `src/mozaiks/core/workflows/yaml_loader.py`
- Stream hub: `src/mozaiks/core/streaming/hub.py`
- Transport helper: `src/mozaiks/core/streaming/transport.py`
