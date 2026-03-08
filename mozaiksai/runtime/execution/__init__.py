"""Runtime execution core — capability-oriented run management.

This module provides the core execution infrastructure:

- RunSupervisor: Owns run lifecycle (start, pause, resume, cancel)
- EventBus: Distributes DomainEvents to subscribers
- CapabilityRegistry: Maps capabilities to handlers

The execution core is engine-agnostic — it delegates actual execution
to workers via the WorkerPort protocol.
"""

from __future__ import annotations

from mozaiksai.runtime.execution.event_bus import EventBus, get_event_bus
from mozaiksai.runtime.execution.capability_registry import (
    CapabilityRegistry,
    get_capability_registry,
)
from mozaiksai.runtime.execution.run_supervisor import RunSupervisor, get_run_supervisor

__all__ = [
    "EventBus",
    "get_event_bus",
    "CapabilityRegistry",
    "get_capability_registry",
    "RunSupervisor",
    "get_run_supervisor",
]
