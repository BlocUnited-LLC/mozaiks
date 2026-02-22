"""
Mozaiks Stack
=============

Runtime, orchestration, and contracts for AI-native applications.

Package Structure
-----------------
- mozaiks.contracts: Event envelopes, domain events, and port interfaces
- mozaiks.core: Runtime infrastructure (API, persistence, streaming, auth)
- mozaiks.orchestration: AI workflow execution (scheduling, AG2 adapters, tools)

Quick Start
-----------
>>> from mozaiks import create_app
>>> app = create_app()

>>> from mozaiks.contracts import EventEnvelope, DomainEvent
>>> from mozaiks.core.auth import get_user_context
>>> from mozaiks.orchestration import KernelAIWorkflowRunner
"""

__version__ = "0.1.0"

# Convenience re-exports from contracts
from mozaiks.contracts import (
    DomainEvent,
    EventEnvelope,
)

# Re-export the main app factory
from mozaiks.core import create_app

# Re-export the main workflow runner
from mozaiks.orchestration.runner import KernelAIWorkflowRunner

__all__ = [
    # Version
    "__version__",
    # Contracts
    "EventEnvelope",
    "DomainEvent",
    # Core
    "create_app",
    # Orchestration
    "KernelAIWorkflowRunner",
]
