"""Public package surface for the runtime substrate.

`mozaiksai` owns the reusable AI execution layer in the layered host architecture.
The canonical host entrypoints live in ``mozaiksai.hosts``:

- ``mozaiksai.hosts.runtime``   — runtime substrate host
- ``mozaiksai.hosts.platform``  — headless app host
- ``mozaiksai.hosts.studio``    — local/private Studio management host

Every host authenticates its HTTP and WebSocket routes through the configured
auth adapter. Start one via the CLI::

    mozaiks serve ./my-app
    mozaiks serve ./my-app --host studio
    mozaiks serve ./my-app --host runtime
"""

from mozaiksai.version import __version__

__all__ = ["__version__"]
