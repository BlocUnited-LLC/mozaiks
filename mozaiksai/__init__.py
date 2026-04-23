"""Public package surface for the runtime substrate.

`mozaiksai` owns the reusable AI execution layer in the four-host architecture.
The canonical repo entrypoints are the root hosts:

- `runtime_app.py`   - runtime substrate host
- `platform_app.py`  - headless app host
- `studio_app.py`    - local/private builder host
- `mozaiks_app.py`   - hosted product host

`create_mozaiks_app()` remains available as a convenience factory for isolated
runtime-only embeddings, smoke tests, and scripts. It is not the canonical
full-stack entrypoint for this repo.
"""

from mozaiksai.factory import create_mozaiks_app
from mozaiksai.trigger import trigger_workflow

__all__ = ["create_mozaiks_app", "trigger_workflow"]
