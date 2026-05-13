"""Platform integration adapters for AppGenerator.

Keep this package import-light. Lifecycle tools are loaded by file path during
workflow startup, and eager imports here can create runtime circular imports.
"""

__all__ = ["BuildEventsClient", "BuildEventsProcessor"]


def __getattr__(name):
    if name == "BuildEventsClient":
        from .build_events_client import BuildEventsClient

        return BuildEventsClient
    if name == "BuildEventsProcessor":
        from .build_events_processor import BuildEventsProcessor

        return BuildEventsProcessor
    raise AttributeError(name)
