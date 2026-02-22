"""Optional AG2 adapter.

This module is safe to import without AG2 installed.
"""

from .ag2_runner import AG2AgentFactory, AG2UnavailableError, AG2VendorEventAdapter
from .runner import AG2AIWorkflowRunner

__all__ = [
    "AG2AIWorkflowRunner",
    "AG2AgentFactory",
    "AG2UnavailableError",
    "AG2VendorEventAdapter",
]
