"""Public package surface for the runtime substrate.

`mozaiksai` owns the reusable AI execution layer in the four-host architecture.
The canonical host entrypoints live in ``mozaiksai.hosts``:

- ``mozaiksai.hosts.runtime``   — runtime substrate host
- ``mozaiksai.hosts.platform``  — headless app host
- ``mozaiksai.hosts.studio``    — local/private Studio management host
- ``mozaiksai.hosts.mozaiks``   — hosted product host

Start via the CLI::

    mozaiks serve ./my-app
    mozaiks serve ./my-app --host studio

`create_mozaiks_app()` remains available as a convenience factory for isolated
runtime-only embeddings, smoke tests, and scripts. It is not the canonical
full-stack entrypoint.
"""

from mozaiksai.factory import create_mozaiks_app
from mozaiksai.version import __version__

__all__ = ["__version__", "create_mozaiks_app"]
