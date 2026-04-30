"""Platform integration adapters (optional).

AppGenerator-specific placement.

These modules integrate the MozaiksAI runtime with MozaiksCore/.NET platform APIs.
They MUST be safe to disable via environment variables so the runtime remains
modular and open-source friendly.
"""

from .build_events_client import BuildEventsClient
from .build_events_processor import BuildEventsProcessor

__all__ = ["BuildEventsClient", "BuildEventsProcessor"]
